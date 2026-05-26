# OneNote PDF フェッチツール 運用ガイド

OneNote ノートブックのページを PDF として一括取得するツールの運用手順書です。  
複数メンバーで分担する場合も、この手順書に沿ってコマンドを実行するだけで作業が完結します。

---

## 前提条件

| 項目 | 内容 |
|------|------|
| Python | 3.9 以上 |
| ブラウザ | Microsoft Edge（社内 SSO 認証に使用） |
| 依存ライブラリ | `requirements.txt` を参照 |
| Playwright ブラウザ | 初回のみ `playwright install msedge` を実行 |

```bash
# 初回セットアップ
pip install -r requirements.txt
playwright install msedge
```

---

## マルチパス運用フロー

```
Step 1: fastdiscover    ページ URL 一覧の収集         (~10分)
Step 2: fetch           高速バルクフェッチ             (~70分)
Step 3: quality check   白ボックス自動検出             (~3分)
Step 4: quality mark    NG を manifest に書き戻し      (~1分)
Step 5: fetch --retry   NG ページのみ丁寧にリトライ    (~20分)
Step 6: Step 3〜5 を繰り返してNG が 0 になったら完了
```

---

## Step 1: fastdiscover（ページ URL 一覧の収集）

OneNote を巡回してページ単位の URL（deep link）を収集し、`manifest.json` を生成します。  
**1ノートブックにつき1回だけ実行します。**

```bash
python scripts/fetch_onenote.py fastdiscover \
    --url "https://tokyogasgroup-my.sharepoint.com/..." \
    --output data/onenote_XXX
```

| 引数 | 説明 |
|------|------|
| `--url` | OneNote ノートブックの URL（ブラウザのアドレスバーからコピー） |
| `--output` | 出力ディレクトリ（自動作成される） |
| `--section-limit` | テスト用：処理するセクション数を制限（例: `--section-limit 2`） |

**実行後:** `data/onenote_XXX/manifest.json` が生成されます。  
ブラウザが開いて OneNote が表示されるのでそのまま待機してください（約 10 分）。

---

## Step 2: fetch（高速バルクフェッチ）

manifest.json に記録された全ページを並列で取得し PDF を生成します。

```bash
python scripts/fetch_onenote.py fetch \
    --manifest data/onenote_XXX/manifest.json \
    --tabs 12
```

| 引数 | 説明 |
|------|------|
| `--manifest` | Step 1 で生成した manifest.json のパス |
| `--tabs` | 並列実行タブ数（推奨: 12、不安定な場合は 8 に下げる） |
| `--limit` | テスト用：最初の N 件だけ取得（例: `--limit 20`） |

**実行後:** `data/onenote_XXX/pdfs/` に PDF が生成されます。  
ブラウザは画面外（-2000,0）で動作するのでバックグラウンド作業が可能です。

> **注意:** Step 3 で品質チェックするまで全量確認は不要です。

---

## Step 3: quality check（品質チェック）

全 PDF を自動分析して白ボックス（ナビパネル残留）や空ページを検出します。

```bash
python scripts/quality_check.py check \
    --manifest data/onenote_XXX/manifest.json
```

**出力:**
- `data/onenote_XXX/quality_check/auto_check_report.txt` — 人間向けレポート
- `data/onenote_XXX/quality_check/check_results.json` — 機械判定結果（Step 4 で使用）

**判定分類:**

| 判定 | 意味 | 対処 |
|------|------|------|
| CLEAN | 問題なし | そのまま |
| WHITE_BOX | 白箱がコンテンツを覆っている | リトライ |
| EMPTY | コンテンツが読み込まれていない | リトライ |
| TINY | ファイルが 5KB 未満（ほぼ空） | リトライ |
| ERROR | PDF を開けない | 個別確認 |

---

## Step 4: quality mark（NG を manifest に書き戻し）

Step 3 の NG 判定ページを manifest の `status=retry` に更新します。

```bash
python scripts/quality_check.py mark \
    --manifest data/onenote_XXX/manifest.json
```

実行後、manifest の該当ページが `"status": "retry"` になり、Step 5 の対象になります。

---

## Step 5: fetch --retry（丁寧なリトライ）

`status=retry` のページのみを、低速・確実モードで再取得します。

```bash
python scripts/fetch_onenote.py fetch \
    --manifest data/onenote_XXX/manifest.json \
    --retry
```

`--retry` モードでは自動的に `tabs=2`（並列数を下げる）・`quiet_ms=3000`（待機を2倍に延長）が適用されます。

---

## Step 6: 繰り返し

Step 3 → Step 4 → Step 5 を繰り返して、NG 件数が 0 になったら完了です。  
通常 2〜3 回のパスで収束します。

---

## サンプル確認

品質確認のためサンプル PDF を `quality_check/` にコピーする場合:

```bash
python scripts/quality_check.py samples \
    --manifest data/onenote_XXX/manifest.json
```

各セクション代表・最小サイズ5件・最大サイズ5件が `quality_check/sample_*.pdf` としてコピーされます。

---

## 別のノートブックに適用する場合

`--url` と `--output` を変えるだけで別のノートブックにも使えます。

```bash
# ノートブック A
python scripts/fetch_onenote.py fastdiscover \
    --url "https://tokyogasgroup-my.sharepoint.com/...notebookA..." \
    --output data/onenote_notebookA

# ノートブック B
python scripts/fetch_onenote.py fastdiscover \
    --url "https://tokyogasgroup-my.sharepoint.com/...notebookB..." \
    --output data/onenote_notebookB
```

それぞれの `manifest.json` で fetch / quality_check を独立して実行できます。

---

## 認証・Cookie について

- 初回実行時にブラウザが開きます。**SharePoint / OneNote で社内 SSO ログイン**してください。
- ログイン後は Cookie が `data/.onenote_cookies.json` に保存され、以降の実行では自動ログインされます。
- Cookie の有効期限は数時間です。期限切れの場合は再度ブラウザが開くのでログインしてください。

> **セキュリティ注意:** `data/.onenote_cookies.json` は `.gitignore` に含まれています。Git にコミットしないでください。

---

## ファイル構成

```
data/
└── onenote_XXX/              ← --output で指定したディレクトリ
    ├── manifest.json         ← discover が生成するページ一覧（フェッチ進捗も管理）
    ├── fastdiscover_log.txt  ← discover 実行ログ
    ├── fetch_log.txt         ← fetch 実行ログ
    ├── pdfs/                 ← 取得した PDF ファイル
    │   ├── 0001_セクション名_ページ名.pdf
    │   └── ...
    └── quality_check/        ← 品質チェック結果
        ├── auto_check_report.txt   ← 人間向けレポート
        ├── check_results.json      ← 機械判定結果（mark コマンドが参照）
        └── sample_*.pdf            ← サンプル確認用 PDF
```

---

## トラブルシューティング

### ブラウザが開かない / 認証がループする

Cookie が壊れている可能性があります。Cookie ファイルを削除して再実行してください:

```powershell
del data\.onenote_cookies.json
```

### `playwright install msedge` が失敗する

社内プロキシ環境では以下のように環境変数を設定してから実行:

```powershell
$env:HTTPS_PROXY = "http://your-proxy:port"
playwright install msedge
```

### fetch が途中で止まる（tabs を下げたい）

`--tabs 8` や `--tabs 4` で並列数を下げると安定することがあります:

```bash
python scripts/fetch_onenote.py fetch \
    --manifest data/onenote_XXX/manifest.json \
    --tabs 8
```

### 特定ページだけ繰り返しNGになる

`manifest.json` を開いて該当ページの `deep_link` が正しい URL になっているか確認してください。  
`deep_link` が空の場合は `fastdiscover` をやり直す必要があります。

### Permission denied エラー

PDF を開いているアプリ（Adobe Acrobat 等）を閉じてから再実行してください。

---

## スクリプト一覧

| スクリプト | 用途 |
|-----------|------|
| `scripts/fetch_onenote.py` | fastdiscover / fetch の本体 |
| `scripts/quality_check.py` | check / mark / samples の本体 |
