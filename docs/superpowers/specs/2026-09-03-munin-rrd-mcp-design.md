# Munin RRD MCPサーバ 設計仕様

- 日付: 2026-09-03
- ステータス: 承認済み(実装プラン作成へ進む)

## 背景・目的

サーバ上でMuninが収集しているメトリック(CPU、メモリ、ディスク、ネットワーク等の時系列データ)をLLMに見せ、分析させたい。データはRRDtool形式(`.rrd`)で保存されており、そのままではLLMが読めない。これをMCPツールとして公開するサーバを新規に構築する。

「分析スクリプトを書く」という当初のイメージは、MCPサーバ側でスクリプト実行環境を持つのではなく、**MCPツールで生の時系列データを取得し、LLM自身がその場で計算・要約する**方式とする。MCPサーバの責務はデータ取得・提供に専念する。

## スコープ

- 対象: ローカルファイルシステム上のMuninのRRDデータ(標準的な`/var/lib/munin/`配下の構成、パスは設定可能)
- 対象範囲: 全ホスト・全プラグインを横断的に発見・取得できること
- 非対象:
  - Munin master/nodeプロトコルによるリモート・ライブ値取得
  - ネットワーク越しのリモートRRDアクセス(将来的にHTTP/SSEトランスポートへ拡張する余地は残すが、v1はstdioのみ)
  - Munin本家同等の忠実なグラフ描画(閾値バンド、stack、cdef、negative-pairingなど)
  - LLMが書いた任意コードをサーバ側で実行する機能

## 全体アーキテクチャ

Python製のMCPサーバ(stdio transport)。`uv`でプロジェクト管理し、`rrdtool`公式CLIバイナリをsubprocessで呼び出す(Pythonバインディングやネイティブビルドは使わない。対象ホストに`rrdtool`コマンドがPATH上にあることだけを前提とする)。

```
rrdmcp/
├── pyproject.toml
├── src/rrdmcp/
│   ├── __init__.py
│   ├── server.py          # FastMCP(mcp公式Python SDK)でツールを登録
│   ├── munin_datafile.py  # /var/lib/munin/datafile のパーサ
│   ├── rrd.py              # rrdtool CLIラッパー(info/fetch/graph、パス解決)
│   └── discovery.py        # datafile + RRD実在確認を突き合わせてhost/plugin/field一覧を構築
└── tests/
    ├── conftest.py          # Munin風ディレクトリ+datafileを作るfixture
    ├── test_datafile.py
    ├── test_discovery.py
    └── test_tools.py
```

設定は環境変数で与える:
- `MUNIN_RRD_BASE_PATH`(既定: `/var/lib/munin`) … RRDファイルのルートディレクトリ
- `MUNIN_DATAFILE_PATH`(既定: `${MUNIN_RRD_BASE_PATH}/datafile`) … datafileの場所

## データモデル・発見(discovery)ロジック

MuninのRRDディレクトリ構成は `<base_path>/<group>/<host>-<plugin>-<field>-<type>.rrd`。host名やplugin名にハイフンが含まれ得るため、**ファイル名からの逆算(右からのハイフン分割等)は本質的に曖昧**。そこで`datafile`を正とする方式を採る。

### 1. datafileのパース(`munin_datafile.py`)

`datafile`は `<group>;<host>:<key> <value>` 形式の行の集合。`<key>`は以下のいずれか:

- グラフ単位の属性: `<plugin>.graph_title` / `graph_vlabel` / `graph_category` / `graph_args` / `graph_info` など(`graph_`で始まる)
- フィールド単位の属性: `<plugin>.<field>.<attribute>`(`label`, `type`, `min`, `max`, `warning`, `critical`, `info`, `draw`, ...)

マルチグラフプラグイン(例: `diskstats_iops.sda`)ではplugin名自体に`.`を含むため、キー全体を`.`で分割したうえで:

- 末尾の要素が`graph_`で始まる → `plugin = 先頭からその手前まで結合`, `attribute = 末尾`, `field = なし`
- それ以外 → `plugin = 先頭から末尾2つ手前まで結合`, `field = 末尾から2番目`, `attribute = 末尾`

この規則はplugin名に何個`.`が含まれていても機械的に解決できる。

パース結果は `{(group, host): {plugin: {graph_attrs: {...}, fields: {field: {attrs...}}}}}` の入れ子dictに正規化する。

### 2. RRDファイルパスの解決(`rrd.py`)

datafileから得た `(group, host, plugin, field, type)` に対し、Muninの命名規則(host名 + サニタイズ済みplugin名 + サニタイズ済みfield名 + typeの頭文字 + `.rrd`)でパスを構築し、実在確認する。サニタイズは「英数字と`_`と`.`以外を`_`に置換」とする。

### 3. フォールバック(datafileが無い/読めない場合)

`<base_path>/**/*.rrd` を再帰的にスキャンし、ファイル名を右からハイフンで分割して `type`(1文字)、`field`、`plugin-host`(残り、境界は不明)をベストエフォートで推定する。この場合:
- メタデータ(タイトル・単位・閾値)は付与できない
- host/plugin境界の精度が落ちる可能性がある
ことを`get_metadata`等のツール応答に明記する。

## MCPツール仕様

| ツール | 引数 | 返り値概要 |
|---|---|---|
| `list_hosts` | なし | `[{group, host}]` |
| `list_plugins` | `group, host` | `[{plugin, graph_title, graph_category, graph_vlabel}]` |
| `list_fields` | `group, host, plugin` | `[{field, label, type, min, max, warning, critical, info, rrd_available}]` |
| `get_metadata` | `group, host, plugin, field?` | plugin全体、または単一fieldの詳細メタデータ |
| `fetch_series` | `group, host, plugin, field, start, end` | `{step, cf, points: [{timestamp, value}]}` |
| `render_graph` | `group, host, plugin, fields[], start, end, width?, height?` | PNG画像(MCP image content, base64) |

補足:
- `start`/`end`はUnix epoch整数、または`rrdtool`が受け付ける相対指定文字列(`-1d`, `now`等)をそのまま`rrdtool fetch`/`graph`に渡す。独自の日時パーサは実装しない。
- `fetch_series`は1フィールド=1 RRDファイルというMuninの構造に対応する単一フィールド取得ツール。複数フィールドが必要な場合はLLMが複数回呼び出す(v1ではバッチAPIを作らない)。
- `render_graph`は各fieldをDEF+LINE1として重ね描きする簡易版。タイトル/vlabelはdatafileの`graph_title`/`graph_vlabel`から補う。Munin本家の閾値バンドやstack表現は再現しない(意図的なスコープ縮小)。

## エラーハンドリング

- `rrdtool`がPATH上にない: サーバ起動時にログへ警告を出し、各ツール呼び出し時はMCPのエラー結果(`isError`)で明示する(サーバ自体はクラッシュさせない)
- 指定した`group`/`host`/`plugin`/`field`が存在しない: 例外にせず、エラーメッセージを含むツール結果を返す
- datafile上は存在するがRRDファイルが実在しないfield: `list_fields`で`rrd_available: false`として返し、`fetch_series`/`render_graph`呼び出し時はエラー結果を返す
- `rrdtool`のsubprocess呼び出しには30秒のタイムアウトを設定し、超過時はエラー結果を返す

## テスト方針

- `pytest`(`uv run pytest`)
- `conftest.py`のfixtureで`tmp_path`配下にMunin風ディレクトリ構成を作成:
  - `rrdtool create`+`rrdtool update`でダミーの`cpu`プラグイン(user/system/idleの3フィールド)などのRRDファイルを生成
  - 対応する`datafile`テキスト(graph_title, graph_vlabel, label, type, warning, critical等を含む)を生成
- `shutil.which("rrdtool")`が見つからない実行環境では該当テストを`pytest.skip`し、テストスイート自体は壊さない
- テスト内容:
  - `munin_datafile.py`: サンプルテキストからの正規化dict構築(通常プラグイン・マルチグラフプラグイン双方)
  - `discovery.py`: host/plugin/field一覧の解決、RRDファイル実在確認、フォールバックモード
  - 各MCPツール: fixtureデータに対して実行し、値の妥当性・`render_graph`のPNGマジックバイト(`\x89PNG`)等を検証

## 将来的な拡張候補(v1では実装しない)

- HTTP/SSEトランスポートでのリモート公開
- `fetch_series`のバッチ化(複数field一括取得)
- Munin本家同等の忠実なグラフ描画(閾値バンド、stack、cdef等)
