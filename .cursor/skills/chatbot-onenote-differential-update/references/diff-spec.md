# 差分更新仕様: OneNote 差分更新パイプライン

## 設計原則

**1ページ = 1ナレッジ = 1URLリンク** を厳守する。
**KM は Step 2（きれい化）まで使用。Step 3 は使用しない。**

- `page_id` をキーに前回と今回を突き合わせる
- `content_hash`（HTML の SHA-256）で内容変化を検出
- 変化があったページは `formatted.md` を完全再生成して入れ替える（AI マージなし）

## 区分別の処理方針

| 区分 | 処理 | 理由 |
|------|------|------|
| `new` | Step 0→1→2 で formatted.md を生成 → chunk 追加 | 前回存在しない |
| `changed` | Step 0→1→2 で formatted.md を**完全再生成**して入れ替え → chunk も入れ替え | OneNote の最新内容が正。細かい変化も確実に反映 |
| `unchanged` | スキップ | hash が同一 |
| `deleted` | pre-knowledge/ から削除 → chunk から削除 | ページが OneNote から消えた |

## manifest のライフサイクル

```
初回実行
  → _manifest_prev.json なし
  → 全ページが new
  → Step 0→1→2 で全ページ処理
  → _manifest_current.json 生成
  → mv _manifest_current.json _manifest_prev.json

2回目以降
  → _manifest_prev.json あり
  → page_id + content_hash で差分検出
  → new / changed のみ Step 0→1→2 に投入
  → deleted は pre-knowledge/ から削除
  → _manifest_current.json 生成
  → mv _manifest_current.json _manifest_prev.json
```

## フォルダ構成

```
tool-knowledge-maker/
├── _manifest_prev.json          ← 前回の manifest（Git 管理 or OneDrive 保存）
├── _manifest_current.json       ← 今回生成（→ prev にリネーム）
├── diff_plan.json               ← 差分計画（デバッグ用）
├── yyyymmdd_onenote/            ← 今回の PA 出力（都度 OneDrive からダウンロード）
│   ├── page_html_mapping.json
│   └── *.html
├── input/                       ← new + changed の PDF のみ配置（毎回クリア推奨）
└── pre-knowledge/               ← 最終成果物（累積。deleted 分は削除する）
```

## diff_plan.json の仕様

```json
{
  "generated_at": "2026-03-07T10:00:00Z",
  "run_folder": "20260307_onenote",
  "summary": {
    "new":       3,
    "changed":   5,
    "unchanged": 142,
    "deleted":   1
  },
  "new": [
    {
      "page_id": "xxxx",
      "notebook": "契約T手順書",
      "section": "新セクション",
      "page_title": "新規ページ",
      "page_url": "https://...",
      "last_modified_time": "2026-03-07T09:00:00Z",
      "content_hash": "sha256:abc123...",
      "html_file_name": "契約T手順書__新セクション__xxxx.html",
      "pdf_file": "契約T手順書__新セクション__新規ページ.pdf"
    }
  ],
  "changed": [...],
  "unchanged": [...],
  "deleted": [
    {
      "page_id": "zzzz",
      "notebook": "契約T手順書",
      "section": "廃止セクション",
      "page_title": "古いページ",
      "pre_knowledge_folder": "契約T手順書__廃止セクション__古いページ",
      "chunk_ids": ["onenote-0015"]
    }
  ]
}
```

## _manifest_prev.json の仕様

```json
{
  "generated_at": "2026-03-07T10:00:00Z",
  "run_folder": "20260307_onenote",
  "pages": [
    {
      "page_id": "ac504714-69bc-41ac-b911-395fa6fa859c",
      "notebook": "契約T手順書",
      "section": "基本ルール",
      "page_title": "ノート運用ルール",
      "page_url": "https://...deep-link...",
      "last_modified_time": "2026-02-01T09:00:00Z",
      "content_hash": "sha256:a3f4b2...",
      "html_file": "契約T手順書__基本ルール__ac504714-....html",
      "pdf_file": "契約T手順書__基本ルール__ノート運用ルール.pdf",
      "pre_knowledge_folder": "契約T手順書__基本ルール__ノート運用ルール",
      "chunk_ids": ["onenote-0001"]
    }
  ]
}
```

## スクリプト一覧

| スクリプト | ステップ | 説明 |
|-----------|---------|------|
| `compare_manifest.py` | Step 0 | 差分比較 + new・changed の PDF 生成 |
| `update_manifest.py` | Step 4 | manifest 更新 + mapping.json 生成 |

## 注意事項

- `_manifest_prev.json` は **KM リポジトリに Git コミットして保管**するか、
  OneDrive の決まった場所に保存し、毎回ダウンロードして使う
- `input/` は毎回クリアしてから差分 PDF だけを配置することを推奨
- `pre-knowledge/` は**累積**で管理する（deleted 分だけ削除、unchanged はそのまま残す）
- chunk の入れ替えは `build_chunks_from_onenote.py --diff-plan diff_plan.json` で行う
