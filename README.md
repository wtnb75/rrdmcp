# rrdmcp

MuninのRRDメトリックデータをLLMに公開するMCPサーバ(stdio transport)。

## セットアップ

```bash
uv sync
```

実行環境に`rrdtool`コマンドがPATH上にあること(Muninが動いているホストであれば、通常は依存パッケージとして既に入っています)。

## 設定(環境変数)

| 変数名 | 既定値 | 説明 |
|---|---|---|
| `MUNIN_RRD_BASE_PATH` | `/var/lib/munin` | RRDファイルのルートディレクトリ |
| `MUNIN_DATAFILE_PATH` | `${MUNIN_RRD_BASE_PATH}/datafile` | Muninのdatafile(設定キャッシュ)の場所 |

## 起動

```bash
uv run rrdmcp
```

## MCPクライアント設定例

```json
{
  "mcpServers": {
    "rrdmcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/rrdmcp", "rrdmcp"],
      "env": {
        "MUNIN_RRD_BASE_PATH": "/var/lib/munin"
      }
    }
  }
}
```

## Dockerでの実行

```bash
docker build -t rrdmcp .
```

stdio transportなので、Muninの実データが置かれているディレクトリをread-onlyでマウントして`-i`(標準入力を開いたまま)で起動します。

```bash
docker run --rm -i -v /var/lib/munin:/var/lib/munin:ro rrdmcp
```

MCPクライアント設定例(Docker版):

```json
{
  "mcpServers": {
    "rrdmcp": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "/var/lib/munin:/var/lib/munin:ro",
        "rrdmcp"
      ]
    }
  }
}
```

`MUNIN_RRD_BASE_PATH`が別パスをマウントする場合は`docker run`に`-e MUNIN_RRD_BASE_PATH=...`を追加してください(既定値はイメージ内で`/var/lib/munin`)。

## ツール

- `list_hosts` — 発見された全`(group, host)`を列挙
- `list_plugins(group, host)` — ホストのプラグイン一覧
- `list_fields(group, host, plugin)` — プラグインのフィールド一覧(ラベル・型・閾値等)
- `get_metadata(group, host, plugin, field?)` — プラグイン全体、または単一フィールドの詳細メタデータ
- `fetch_series(group, host, plugin, field, start, end, resolution?, summary?, top_n?, top_by?, order?)` — 時系列データ取得。`start`/`end`はunixタイムスタンプまたは`rrdtool`が解釈できる文字列(`-1d`, `now`等)
  - 何も指定しなければ生の`points`をそのまま返す
  - `resolution`(秒)を指定すると、UTC epoch境界のバケットに集計した`buckets`(avg/min/max/count)を返す
  - `summary=true`は範囲全体を単一の`summary`(avg/min/max/count)に集計する。`resolution`とは併用不可
  - `top_n`は`resolution`指定時のみ有効で、`top_by`("avg"/"min"/"max", 既定"avg")で`order`("desc"/"asc", 既定"desc")にソートした上位N件のバケットだけを`buckets`として返し、フィルタ前の総数を`total_buckets`に含める
- `render_graph(group, host, plugin, fields, start, end, width?, height?)` — 指定フィールドを重ね描きしたPNGグラフ

## 既知の制約

- `datafile`が存在しない場合はファイル名からのベストエフォート推定にフォールバックし、host/plugin境界の精度とメタデータが失われる
- グラフ描画はMunin本家のような閾値バンド・stack・cdef等は再現しない簡易版
- stdio transportのみ(v1)
