---
name: chatbot-onenote-knowledge-maker
description: >
  OneNote データ（Power Automate で取得した HTML + page_html_mapping.json）を
  Knowledge Maker でナレッジ化するスキル。
  HTML→PDF 変換、KM パイプライン実行（Step 0→1→2）、OneNote 用 mapping.json 生成を行う。
  KM は Step 2（きれい化）まで使用。Step 3（ナレッジ化）は使用しない。
  Use when user says "OneNote ナレッジ化", "OneNote HTML を PDF に変換",
  "onenote knowledge", "onenote ナレッジ", "ナレッジメーカーで OneNote を処理",
  "A-1-7", "A-1-8", "A-1-9", "A-1-10".
---

# OneNote データ取り込み & ナレッジ化（初回）

Power Automate で取得した OneNote ページの HTML を、Knowledge Maker（KM）で
**Step 2（きれい化）まで**処理し、チャットボット用の `formatted.md` と `mapping.json` を生成する。

**KM の Step 3（ナレッジ化・構造整理）は使用しない。**
1ページ = 1ファイルの構造を崩さないため、`pre-knowledge/` の `formatted.md` をそのまま使う。

## 前提

- KM リポジトリ（`tool-knowledge-maker`）のローカルクローンがある
- KM の `.env` が設定済み（Azure OpenAI 接続情報）
- Playwright がインストール済み（`pip install playwright && playwright install chromium`）

## PA 出力データの配置

OneDrive から `yyyymmdd_onenote/` フォルダをダウンロードし、KM リポジトリのルートに配置する。

```
tool-knowledge-maker/
├── 20260307_onenote/          ← PA 出力をここに配置
│   ├── page_html_mapping.json
│   ├── run_context.json
│   ├── pages_index.csv
│   └── *.html                 ← 各ページの HTML
├── input/                     ← Step 0 で PDF が出力される
├── pre-knowledge/             ← Step 1-2 の出力（最終成果物）
└── src/
```

## ワークフロー概要

```
[PA出力] yyyymmdd_onenote/
  ├── page_html_mapping.json
  ├── *.html
       ↓  Step 0: HTML → PDF 変換
[KM] input/
  ├── {notebook}__{section}__{safe_title}.pdf
  ├── _onenote_manifest.json
       ↓  Step 1: 文字起こし
[KM] pre-knowledge/
       ↓  Step 2: きれい化
[KM] pre-knowledge/{pdf_stem}/03_formatted_markdown/formatted.md  ← 最終成果物
       ↓  Step 3（スキップ）
       ↓  Step 4: OneNote 用 mapping.json 生成
[出力] onenote_mapping.json     ← chatbot プロジェクトに渡す
```

## Step 0: HTML → PDF 変換

```bash
cd <km-repo>
python scripts/onenote_html_to_pdf.py \
  --mapping 20260307_onenote/page_html_mapping.json \
  --html-dir 20260307_onenote/ \
  --output input/
```

出力:
- `input/{notebook}__{section}__{page_title}.pdf` — 1 ページ = 1 PDF
- `input/_onenote_manifest.json` — 変換結果のメタデータ

## Step 1: 文字起こし

```bash
python -m src.km.cli.convert --input input --output pre-knowledge
```

## Step 2: きれい化

```bash
python -m src.km.cli.enhance --target pre-knowledge/
```

**この時点で `pre-knowledge/{pdf_stem}/03_formatted_markdown/formatted.md` が最終成果物。**
Step 3 は実行しない。

## Step 3（スキップ）

KM の Step 3（ナレッジ化・カテゴリ整理）は**使用しない**。

理由:
- Step 3 は複数ページを統合・再構成する可能性がある
- 「1ページ = 1ナレッジ = 1URLリンク」を保証するために `pre-knowledge/` の構造を維持する
- 差分更新時も `formatted.md` を直接入れ替えることで対応する

## Step 4: OneNote 用 mapping.json 生成

```bash
python scripts/generate_onenote_mapping.py \
  --manifest input/_onenote_manifest.json \
  --pre-knowledge pre-knowledge/ \
  --output onenote_mapping.json
```

出力形式（`build_chunks_from_onenote.py` が読む形式）:

```json
[
  {
    "id": "onenote_0001",
    "onenote": {
      "notebook": "契約T手順書",
      "section": "基本ルール",
      "page_name": "ノート運用ルール",
      "link": "https://...deep-link..."
    },
    "pages": [
      {
        "pdf_page_num": 1,
        "original_pdf_path": "input/契約T手順書__基本ルール__ノート運用ルール.pdf",
        "pre_knowledge_folder": "契約T手順書__基本ルール__ノート運用ルール",
        "formatted_path": "pre-knowledge/契約T手順書__基本ルール__ノート運用ルール/03_formatted_markdown/formatted.md"
      }
    ]
  }
]
```

## 成果物の受け渡し（チャットボットプロジェクトへ）

1. `pre-knowledge/` を `data/onenote/pre-knowledge/` にコピー
2. `onenote_mapping.json` を `data/onenote/mapping.json` に配置
3. `scripts/build_chunks_from_onenote.py --replace` を実行（全件新規登録）
4. `scripts/build_index_and_upload.py` を実行
5. `_manifest_prev.json` を生成して KM リポジトリルートに保存（次回差分更新の基準）:

```bash
python scripts/create_initial_manifest_prev.py \
  --manifest input/_onenote_manifest.json \
  --mapping  yyyymmdd_onenote/page_html_mapping.json \
  --html-dir yyyymmdd_onenote/ \
  --pre-knowledge pre-knowledge/ \
  --output _manifest_prev.json
```

## 入出力仕様の詳細

詳細は [references/io-spec.md](references/io-spec.md) を参照。