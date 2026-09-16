# sysstat(sar)データソース対応 設計仕様

- 日付: 2026-09-16
- ステータス: 承認済み(実装プラン作成へ進む)

## 背景・目的

既存の`rrdmcp`はMuninのRRDファイルのみを対象としている。sysstat(`sar`)が収集するシステムリソースの時系列データ(`/var/log/sa/saXX`)も、同じMCPツール群から横断的に扱えるようにしたい。sarはMuninとは異なるファイル形式・ディレクトリ構造・データモデルを持つため、既存の抽象を壊さずに**第2のデータソース**として統合する。

## スコープ

- 対象: `sadf`(sysstatパッケージ付属)コマンド経由で読み出せるsarログ。`SAR_BASE_PATH`配下に`<group>/<host>/saXX`の形でディレクトリ集約されている前提(リモートホストからの`rsync`等での事前集約は運用側の責務とする)
- 対象範囲: 既存の`group/host/plugin/field`モデルに「activity=plugin」「指標=field」としてマッピングし、既存の6ツール(`list_hosts`, `list_plugins`, `list_fields`, `get_metadata`, `fetch_series`, `render_graph`)から横断的に扱えること
- 非対象:
  - sarバイナリファイル(`saXX`)の直接パース(`sadf`非依存の実装)
  - リモートホストのsarログのライブ収集
  - Munin本家同様の忠実なグラフ描画(既にMuninでもスコープ外)
  - munin/sarを跨いだ単一グラフでの重ね描画(`render_graph`は1つのplugin配下のfieldのみを対象にする既存制約を踏襲)

## 全体アーキテクチャ

```
src/rrdmcp/
├── server.py          # 既存: fetch_series/render_graphをsourceで分岐するよう変更
├── discovery.py        # 既存: NormalizedFieldにsource追加、build_indexでmunin+sarをマージ
├── munin_datafile.py   # 既存: 変更なし
├── rrd.py               # 既存: 変更なし(muninバックエンド)
├── sar_index.py         # 新規: sarログディレクトリのスキャン+plugin/field発見
└── sar.py                # 新規: sadf CLIラッパー(fetch/render_graph、rrd.pyと対になるモジュール)
```

設定は環境変数で追加する:

- `SAR_BASE_PATH`(既定: 未設定=sar機能を無効化) … sarログのルートディレクトリ(`<base>/<group>/<host>/saXX`)

Muninとsarは同一サーバーインスタンスで同時に有効化でき、discoveryの結果(entries)をマージして扱う。どちらか一方の環境変数のみが設定されていれば、そのソースだけが動作する。

## データモデル

`discovery.NormalizedField`に`source: Literal["munin", "sar"]`フィールドを追加する。`meta`/`plugin_meta`は既存の`FieldMeta`/`PluginMeta`dataclassをそのまま再利用し(sar側は静的メタデータテーブルから値を詰める)、新しいdataclassは作らない。

`path: Path`フィールドの意味はsourceにより異なる:

- munin: 1メトリクス=1RRDファイルのパス(既存の挙動のまま)
- sar: そのホストのsarログディレクトリ(`SAR_BASE_PATH/group/host/`)。実際に読む`saXX`ファイル群はfetch時にstart/endから逆算する(1メトリクス=複数ファイルにまたがるため)

## sarディスカバリ(`sar_index.py`)

### ディレクトリスキャン

`SAR_BASE_PATH/{group}/{host}/saXX`という構成を前提に、`group`/`host`のペアを列挙する。

### plugin/fieldの決定

各hostディレクトリの最新の`saXX`ファイル1つを`sadf -j -- -A <file>`で読み、そのJSON構造からplugin/fieldを機械的に導出する。

Debian 12 + sysstat 12.6.1で実際に確認したところ、`sadf -j`の1統計ブロック(`statistics[i]`)は以下のようにトップレベルだけでなく1〜2段ネストした構造を持つ(`timestamp`のみキーだが除外対象):

```
cpu-load: [{"cpu": "all", "usr": .., "sys": .., ...}, {"cpu": "0", ...}, ...]
memory: {"memfree": .., "avail": .., ...}                      # 全部スカラー
io: {"tps": .., "io-reads": {"rtps": .., "bread": ..}, ...}    # スカラーとネストが混在
disk: [{"disk-device": "vda", "tps": .., ...}, ...]
network: {
  "net-dev": [{"iface": "eth0", "rxpck": .., ...}, ...],
  "net-nfs": {"call": .., "retrans": .., ...},                 # ネスト先も全部スカラー
  ...
}
power-management: {"cpu-frequency": [{"number": "all", "frequency": ..}, ...]}
filesystems: [{"filesystem": "/dev/vdb1", "MBfsfree": .., ...}, ...]
```

したがって「トップレベルキー=activity」という単純な決め打ちではなく、**再帰的にたどる**必要がある。`_walk(node: dict, path: str)`を次のルールで定義する:

- `node`の各`key, value`について:
  - `value`が配列で、各要素が辞書かつ既知の**インスタンスキー**(`cpu`, `disk-device`, `iface`, `filesystem`, `number`のホワイトリスト)のいずれかを持つ場合 → 要素ごとに`plugin = "{path}.{key}.{instance値}"`、インスタンスキー以外の残りのキーが`field`になる(例: `cpu-load.cpu0`, `disk.vda`, `network.net-dev.eth0`, `power-management.cpu-frequency.all`, `filesystems./dev/vdb1`)。インスタンス値はplugin名の一部になるため`rrd.sanitize_name`と同じ規則でサニタイズする
  - `value`が配列だが上記に当てはまらない場合 → 未対応構造としてスキップする(v1では扱わない)
  - `value`が辞書の場合 → その中のスカラー値(int/float)だけを集めて`plugin = "{path}.{key}"`のfieldとし、辞書/配列の値を持つキーがあれば`path = "{path}.{key}"`として同じ関数を再帰する(例: `io`は`tps`等のスカラーで`plugin=io`を作りつつ、`io-reads`/`io-writes`/`io-discard`はさらに`plugin=io.io-reads`等を作る)
  - `value`がスカラーの場合は無視する(通常起こらないが、トップレベル直下に将来スカラーキーが増えても安全に無視する)
- 最上位の`_walk`呼び出しでは`timestamp`と`restarts`キーを最初に除外する

### メタデータ補完

Muninの`datafile`に相当するメタデータ宣言ファイルがsarには存在しないため、activity名・field名をキーにした静的辞書`SAR_ACTIVITY_META`/`SAR_FIELD_META`(主要なactivityのみ人力整備)から`graph_title`/`graph_vlabel`/`label`/`type`を補完する。辞書に無いキーは`label=field名`のままフォールバックする。`metadata_available`は「静的辞書にヒットしたか」を表し、`rrd_available`相当のフィールド(sarでは対応する`saXX`ファイルが実在するか)とは独立に扱う。

## `sar.py`(fetch / render_graph)

### 時刻正規化

`rrd.py`の`_normalize_time`と同じロジックを共通化(切り出し)し、start/endを常にunix epochへ変換する。sarは`rrdtool`の相対表現(`-1d`, `now`等)を理解しないため、`fetch_series`/`render_graph`のdocstringにsourceによる違いを明記する。

### 複数日ファイルの結合(fetch)

sarは1日1ファイル(`saXX`)なので、`fetch()`は以下の流れになる:

1. start/end(epoch)から対象日付のリストを算出し、各日付に対応する`saXX`パスを組み立てる
2. 各ファイルについて`sadf -j -s HH:MM:SS -e HH:MM:SS -- -A <file>`を実行する(初日は`start`の時刻から、最終日は`end`の時刻まで、中間の日は終日)
3. 各ファイルのJSONから対象activity/instance/fieldの値をtimestamp付きで抽出する
4. 全ファイル分を時刻順に結合し、`rrd.FetchResult`と同じ型(`step, ds_names, points`)で返す

対象日のファイルが存在しない場合はスキップし、欠損として扱う(全日分無ければ空の`points`を返す)。`ds_names`はsarに複数DSの概念が薄いため`[field名]`固定で返す。

### render_graph

`rrd.render_graph`は`paths_and_labels: list[tuple[Path, str]]`を受け取りrrdtool側でDEFを組み立てるが、sarには対応するファイル1つ=1フィールドという構造がないため、同じ形にはできない。`sar.render_graph`は`points_and_labels: list[tuple[list[tuple[int, float | None]], str]]`(=事前に`fetch()`しておいた各fieldのpoints列とラベルの組)を受け取り、matplotlibで重ね書きしてPNGバイト列を返す。`start, end, title, vlabel, width, height`の引数は`rrd.render_graph`と同名・同意味を保つ。配色は既存の`_GRAPH_COLORS`をそのまま再利用する。

## `server.py`の変更

- `SAR_BASE_PATH`環境変数を追加(未設定時はsarディスカバリをスキップし、munin単独動作を維持する)
- `_load_entries()`はmunin側`discovery.build_index()`とsar側`sar_index.build_index()`の結果を結合する
- `fetch_series`/`render_graph`は`resolved.source`に応じて`rrd`または`sar`モジュールの同名関数を呼び分ける
- 各ツールのdocstringに、sarソース時の時刻表現の制約(相対表現不可)を追記する

## エラーハンドリング

- `errors.py`に既存の`RrdToolNotFoundError`等に倣い、`SarToolNotFoundError`/`SarFileNotAvailableError`/`SarToolTimeoutError`を追加する
- `sadf`がPATH上にない場合: サーバはクラッシュさせず、該当ツール呼び出し時にエラー結果を返す
- 対象日の`saXX`ファイルが存在しない: スキップして欠損扱いとする(致命的エラーにはしない)
- `sadf`のsubprocess呼び出しには既存と同じ30秒タイムアウトを設定する

## テスト方針

既存の`test_rrd.py`/`munin_root`フィクスチャは実際に`rrdtool`コマンドでRRDファイルを生成し、`shutil.which("rrdtool")`が無ければスキップする方式を採っている。sar側もこれに倣う:

- `sar_index.py`/`sar.py`のJSONパースロジックは、`sadf -j`の**サンプル出力文字列を固定したfixture**を使ったユニットテストで検証する(sysstat未インストールの環境でも実行可能)
- 実際に`sadf`コマンドを呼ぶ結合テストは`shutil.which("sadf")`でスキップ判定し、sysstatが入っているCI等でのみ動作確認する
- `server.py`側は、munin/sar両方のentriesが揃った状態でのマージ・`fetch_series`/`render_graph`分岐についてもテストを追加する

## 将来的な拡張候補(v1では実装しない)

- sarバイナリファイルの直接パース(`sadf`非依存の実装)
- ディレクトリ集約を前提としない、単一ホストのローカル`/var/log/sa`直接参照モード
- munin/sarを跨いだ単一グラフでの重ね描画
