# wtmp/btmp(ログイン履歴)データソース対応 設計仕様

- 日付: 2026-09-17
- ステータス: 承認済み(実装プラン作成へ進む)

## 背景・目的

既存の`rrdmcp`はMuninのRRDファイルとsysstat(sar)の数値時系列データを扱えるが、いずれも「plugin/field」で表現できる数値時系列を前提としたモデルである。`/var/log/wtmp`(ログイン/ログアウト/reboot履歴)・`/var/log/btmp`(ログイン失敗履歴)は1レコード=1イベント(誰が・いつ・どこから)という性質のデータであり、数値時系列モデルには馴染まない。

集計値(例: 時間帯ごとのログイン失敗数)だけを見せるより、異常を見つけたときにそのまま生イベントへ深掘りできる方が実用上便利という判断のもと、**集計は行わず生イベントをそのまま返す新しいツール群**を追加する。

## スコープ

- 対象: `utmpdump`(util-linuxパッケージ付属)コマンド経由で読み出せるwtmp/btmp形式のログ。`WTMP_BASE_PATH`配下に`<group>/<host>/{wtmp,btmp}`の形でディレクトリ集約されている前提(sarと同様、リモートホストからの事前集約は運用側の責務とする)
- ローテートされたファイル(`wtmp.1`, `wtmp.2.gz`等)も対象に含める
- 生イベントをそのまま返す新ツール(`list_login_sources`, `list_login_events`)を追加する。既存の`list_hosts`/`list_plugins`/`list_fields`/`fetch_series`/`render_graph`(plugin/field前提の数値時系列モデル)には一切手を入れない
- 非対象:
  - wtmp/btmpバイナリファイルの直接パース(struct直接デコード。必ず`utmpdump`経由とする)
  - ログイン/ログアウトのセッションペアリング・在籍時間計算(`last`が行っている処理。今回は生レコード列挙のみ)
  - リモートホストのライブ収集
  - `wtmpdb`(SQLiteベースの後継フォーマット)対応
  - ホスト名の逆引き(DNS lookup)

## 全体アーキテクチャ

```
src/rrdmcp/
├── server.py         # 既存: WTMP_BASE_PATH環境変数 + list_login_sources/list_login_events追加
├── errors.py           # 既存: Wtmp系エラークラス追加
├── timeutil.py          # 既存: sar.pyの_normalize_time_to_epochをここに移し共有関数化
├── wtmp_index.py        # 新規: WTMP_BASE_PATH配下のスキャン→(group, host, kind)一覧発見
└── wtmp.py                # 新規: utmpdumpラッパー(ローテートファイル横断fetch)
```

設定は環境変数で追加する:

- `WTMP_BASE_PATH`(既定: 未設定=機能無効) … wtmp/btmpログのルートディレクトリ(`<base>/<group>/<host>/{wtmp,btmp}`)

Munin/sar/wtmpは同一サーバーインスタンスで同時に有効化でき、各データソースの環境変数のうち設定されているものだけが動作する。既存の`discovery.NormalizedField`(plugin/field前提のモデル)は変更しない。

## 時刻正規化の共通化

`sar.py`の`_normalize_time_to_epoch`(unixタイムスタンプまたはISO 8601のみを受け付け、rrdtool形式の相対表現は非対応)を`timeutil.py`に移し、`normalize_time_to_epoch`として公開する。`sar.py`と`wtmp.py`の両方がこれを利用し、ロジックの重複を避ける。`sar.py`側の呼び出し箇所も新しい共通関数を参照するよう変更する(挙動・エラーメッセージは変更しない)。

## `wtmp_index.py`(ディスカバリ)

### ディレクトリスキャン

`WTMP_BASE_PATH/{group}/{host}/`という構成を前提に、`group`/`host`のペアを列挙する。各`host`ディレクトリについて、`wtmp`または`wtmp.[0-9]+`(`.gz`含む)に一致するファイルが1つでも存在すれば`kind="wtmp"`を、`btmp`または`btmp.[0-9]+`(`.gz`含む)が1つでもあれば`kind="btmp"`を利用可能として扱う。

### `list_login_sources() -> list[dict]`相当のデータ

```python
[{"group": "web", "host": "app01", "kind": "wtmp"}, {"group": "web", "host": "app01", "kind": "btmp"}]
```

`build_index(base_path: Path) -> list[LoginSource]`は`base_path`が存在しない場合、空リストを返す(例外を投げない。sarの`sar_index.build_index`と同じ「未設定・利用不可なら無音でno-op」方針)。

## `wtmp.py`(fetch)

### ローテートファイルの列挙

`saXX`と異なり、wtmp/btmpのローテートファイル名(`wtmp`, `wtmp.1`, `wtmp.2.gz`...)からは対象期間を特定できない。そのため世代数を決め打ちせず、`host_dir`内で以下のglobパターンに一致する全ファイルを対象にする:

- `{kind}`
- `{kind}.[0-9]+`
- `{kind}.[0-9]+.gz`

### `utmpdump`の実行

- 非圧縮ファイル: `utmpdump <file>`をそのまま実行
- `.gz`ファイル: Python側(`gzip`標準モジュール)で解凍したバイト列を`utmpdump`の標準入力に渡して実行する(標準入力からの読み込み可否・オプション指定方法は実装時にLinux実機で確認し、必要なら設計を調整する)
- タイムアウトは既存の`RRDTOOL`/`sadf`と同じ30秒とする(`WTMPDUMP_TIMEOUT_SECONDS`)
- 個別ファイルの実行が失敗(非0終了・タイムアウト・パース不能な出力)した場合はそのファイルをスキップし(致命的エラーにしない)、他のファイルの処理を継続する

### パース

`utmpdump`の出力を1行1レコードとしてパースし、以下のフィールドを取り出す:

- `ut_type`(数値) → 人間可読名にマッピング: `EMPTY`, `RUN_LVL`, `BOOT_TIME`, `NEW_TIME`, `OLD_TIME`, `INIT_PROCESS`, `LOGIN_PROCESS`, `USER_PROCESS`, `DEAD_PROCESS`, `ACCOUNTING`(Linuxの`<utmp.h>`定義に準拠)
- `ut_pid`
- `ut_line`
- `ut_user`
- `ut_host`
- タイムスタンプ(epoch秒に変換)

`type == "EMPTY"`(未使用スロット)のレコードはノイズなので除外する。正確な行フォーマット(区切り文字・カラム順)は実装時にLinux実機で`utmpdump`の実出力を確認して確定させる。

### `fetch(host_dir: Path, kind: str, start: str, end: str, limit: int | None) -> FetchResult`

1. `start`/`end`を`timeutil.normalize_time_to_epoch`でepochへ変換
2. 対象ファイルを列挙し、各ファイルを`utmpdump`でテキスト化→パース
3. `start_epoch <= ts <= end_epoch`のレコードのみ残す
4. 全ファイル分を結合し、timestamp昇順にソートする
5. `limit`が指定されている場合、末尾(直近側)から`limit`件を残す。切り詰め前の件数を`total_events`として保持する
6. `FetchResult(total_events: int, events: list[dict])`を返す

## `server.py`の変更

- `WTMP_BASE_PATH`環境変数を追加(未設定時はwtmpディスカバリをスキップする)
- `list_login_sources() -> list[dict] | dict`: `wtmp_index.build_index()`の結果を返す
- `list_login_events(group: str, host: str, kind: str, start: str, end: str, limit: int | None = None) -> dict`:
  - `group`/`host`/`kind`の組み合わせが`list_login_sources`の結果に存在しない場合は`WtmpSourceNotFoundError`
  - 存在すれば`wtmp.fetch`を呼び、`{"total_events": ..., "events": [...]}`を返す
  - `start`/`end`はunixタイムスタンプまたはISO 8601のみ受け付ける旨をdocstringに明記(sarと同じ制約)

## エラーハンドリング(`errors.py`)

- `WtmpToolNotFoundError`: `utmpdump`がPATH上にない
- `WtmpToolTimeoutError`: `utmpdump`のsubprocess呼び出しがタイムアウト
- `WtmpSourceNotFoundError`: 指定した`group`/`host`/`kind`が`list_login_sources`の結果に存在しない
- `WtmpInvalidTimeError`: `start`/`end`がunixタイムスタンプ/ISO 8601として解釈できない

いずれも既存の`RrdMcpError`を継承し、`server.py`側の各ツールで捕捉して`{"error": str(exc)}`を返す既存パターンに従う。個別ローテートファイルの読み取り失敗は例外にせずスキップする(上記`wtmp.py`のfetch参照)。

## テスト方針

sarと同じ二段構えにする:

- **ユニットテスト**: `utmpdump`実機出力のサンプルテキストを固定したfixtureでパースロジックを検証する。このdev環境(macOS)には`utmpdump`が無いため、実装時にLinux実機(またはコンテナ)で実際に`utmpdump`を実行して出力サンプルを採取し、fixture化する(TODO: 実装フェーズの最初のステップとして扱う)
- **結合テスト**: 実際に`utmpdump`コマンドを呼ぶテストは`shutil.which("utmpdump")`でスキップ判定し、util-linuxが入っている環境でのみ動作確認する
- ローテートファイル(`.N`, `.N.gz`)の結合・ソート・`limit`切り詰めロジックは、fixtureベースのユニットテストで複数ファイルにまたがるケースを検証する
- `server.py`側は、`list_login_sources`/`list_login_events`のツール登録・`WtmpSourceNotFoundError`等のエラー分岐についてテストを追加する

## 将来的な拡張候補(v1では実装しない)

- ログイン/ログアウトのセッションペアリング・在籍時間計算
- `wtmpdb`(SQLiteベース)対応
- ホスト名の逆引き
- `group`/`host`をまたいだ横断検索(例: 特定ユーザ名の全ホスト分ログイン履歴)
