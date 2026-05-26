# 入出力仕様: OneNote ナレッジ化パイプライン

## 1. PA フロー出力（入力データ）

### page_html_mapping.json

```json
{
  "generated_at": "2026-03-07T02:18:12Z",
  "run_folder": "20260307_onenote",
  "pages": [
    {
      "notebook": "契約T手順書",
      "section": "ルール",
      "page_title": "このノートブックのルール",
      "page_id": "ac504714-69bc-41ac-b911-395fa6fa859c",
      "page_url": "https://tokyogasgroup-my.sharepoint.com/.../Doc.aspx?...",
      "content_url": "https://www.onenote.com/api/...",
      "created_time": "2024-07-01T03:34:00Z",
      "last_modified_time": "2024-07-01T03:34:00Z",
      "html_file_name": "契約T手順書__ルール__ac504714-69bc-41ac-b911-395fa6fa859c.html",
      "html_path": "/20260307_onenote/契約T手順書__ルール__ac504714-69bc-41ac-b911-395fa6fa859c.html"
    }
  ]
}
```

### HTML ファイル

各ページの HTML 本文（OneNote コネクタの GetPageContent 戻り値）。

## 2. Step 0 出力: _onenote_manifest.json

`onenote_html_to_pdf.py` が `input/` に生成する中間メタデータ。

```json
{
  "generated_at": "2026-03-07T10:00:00Z",
  "source_mapping": "yyyymmdd_onenote/page_html_mapping.json",
  "pages": [
    {
      "notebook": "契約T手順書",
      "section": "基本ルール",
      "page_title": "ノート運用ルール",
      "page_id": "ac504714-...",
      "page_url": "https://...deep-link...",
      "html_file": "契約T手順書__基本ルール__ac504714-....html",
      "pdf_file": "契約T手順書__基本ルール__ノート運用ルール.pdf",
      "pdf_path": "input/契約T手順書__基本ルール__ノート運用ルール.pdf"
    }
  ]
}
```

## 3. KM パイプライン出力

### pre-knowledge/ の構造（Step 1-2 後）

```
pre-knowledge/
└── 契約T手順書__基本ルール__ノート運用ルール/
    ├── 01_input/
    │   └── 契約T手順書__基本ルール__ノート運用ルール.pdf
    ├── 02_transcribed_markdown/
    │   └── transcribed.md
    ├── 03_formatted_markdown/
    │   ├── formatted.md
    │   ├── terms.json
    │   ├── chapter_summaries.json
    │   └── quality_report.json
    └── 04_images/
        └── (あれば)
```

## 4. 最終出力: onenote_mapping.json

`generate_onenote_mapping.py` が生成する。`tool-ec-chatbot` の `build_chunks_from_onenote.py` が直接読める形式。

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
        "transcribed_path": "pre-knowledge/契約T手順書__基本ルール__ノート運用ルール/02_transcribed_markdown/transcribed.md",
        "formatted_path": "pre-knowledge/契約T手順書__基本ルール__ノート運用ルール/03_formatted_markdown/formatted.md",
        "content_preview": "（先頭200文字）"
      }
    ]
  }
]
```

### フィールド説明

| フィールド | 説明 |
|-----------|------|
| `id` | `onenote_NNNN` 形式の連番 |
| `onenote.notebook` | ノートブック名 |
| `onenote.section` | セクション名 |
| `onenote.page_name` | ページタイトル |
| `onenote.link` | OneNote ページのディープリンク URL |
| `pages[].formatted_path` | `build_chunks_from_onenote.py` が読む `formatted.md` のパス |

## 5. ノートブック名 → チームマッピング

`build_chunks_from_onenote.py` 側に定義済み:

| ノートブック名 | チーム |
|---------------|--------|
| `【EC】料金Tからのお知らせ` | 料金T |
| `【料金Ｔ】業務` | 料金T |
| `契約T手順書` | 契約T |
| `★２契約チームからのお知らせ` | 契約T |
| その他 | 共通 |

## 6. ファイル名の正規化

PDF ファイル名は `{notebook}__{section}__{page_title}.pdf` 形式。
以下の文字は `_` に置換:

```
/ \ : * ? " < > |
```

スペースはそのまま保持。連続する `_` は 1 つに正規化。
