---
name: chatbot-onenote-differential-update
description: >
  OneNote の差分更新スキル。
  前回実行時の manifest と今回の PA 出力を比較し、
  new / changed / deleted / unchanged を判定して効率的に AI Search を更新する。
  1ページ = 1ナレッジ = 1URLリンクを厳守する。
  KM は Step 2（きれい化）まで使用。Step 3 は使用しない。
  Use when user says "OneNote 差分更新", "onenote differential", "onenote 更新",
  "manifest 比較", "差分のみナレッジ化", "A-1-2", "OneNote 差分".
---

# OneNote 差分更新スキル

Power Automate で全ページを取得した後、前回との差分だけを処理して AI Search を効率的に更新する。

**鉄則: 1ページ = 1ナレッジ = 1URLリンク**
**KM は Step 2（きれい化）まで使用。Step 3 は使用しない。**

## 設計思想

`page_id`（OneNote の一意 ID）と `content_hash`（HTML の SHA-256）で差分を判定し、
区分ごとにシンプルな完全入れ替え方式で処理する。

```
[今回の PA 出力]          [前回の manifest]
page_html_mapping.json   _manifest_prev.json
         ↓
  compare_manifest.py
         ↓
  ┌──────────────────────────────────────────────────────────────┐
  │ new      → Step 0→1→2 で formatted.md を生成 → chunk 追加    │
  │ changed  → Step 0→1→2 で formatted.md を再生成 → chunk 入替  │
  │ deleted  → pre-knowledge/ から削除 → chunk から削除           │
  │ unchanged→ スキップ                                           │
  └──────────────────────────────────────────────────────────────┘
         ↓
  _manifest_current.json を保存（次回の prev になる）
```

### changed の処理方針

「200円 → 250円」のような細かい変化も確実に反映するため、
**changed ページは Step 0→1→2 で formatted.md を完全に再生成して入れ替える**。

AI によるマージは行わない。OneNote の内容が正なので、最新の HTML から生成した
`formatted.md` でそのまま上書きする。

## ファイル配置

```
tool-knowledge-maker/
├── yyyymmdd_onenote/            ← 今回の PA 出力
│   ├── page_html_mapping.json
│   └── *.html
├── _manifest_prev.json          ← 前回実行時に保存した manifest（初回は存在しない）
├── input/                       ← new + changed ページの PDF のみ配置
├── pre-knowledge/               ← Step 1-2 の出力（最終成果物）
└── scripts/
    └── compare_manifest.py
```

## manifest の仕様

### _manifest_prev.json / _manifest_current.json

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

| フィールド | 用途 |
|-----------|------|
| `page_id` | OneNote 固有 ID。比較キー。ページ名変更でも変わらない |
| `content_hash` | HTML の SHA-256。変われば「更新」 |
| `pre_knowledge_folder` | `pre-knowledge/` 配下のフォルダ名。deleted 時に削除に使う |
| `chunk_ids` | AI Search 上のチャンク ID。deleted / changed 時に削除に使う |

## Cursor エージェントとしての動作方針

ユーザーが「OneNote のナレッジを差分更新して」と指示した場合、
Claude は **Shell ツールで Step 0〜3 を自律的に実行**する。

### 自律実行（Claude が自分で実行）
- Step 0: 差分比較・diff_plan.json 生成・new+changed の PDF 変換
- Step 1: KM 文字起こし
- Step 2: KM きれい化
- Step 3（deleted のみ）: pre-knowledge/ 削除

### ユーザー確認が必要
- **deleted ページ**: 削除対象を提示してユーザーが承認してから削除
- **changed ページ**: 何が変わったか（新旧 formatted.md の差分）を要約して提示

### 処理後の報告
```
更新サマリー:
  新規追加: 3ページ
  内容更新: 5ページ（完全再生成）
  削除:     1ページ（確認済み）
  スキップ: 142ページ

次のアクション（本プロジェクト側）:
  pre-knowledge/ を data/onenote/pre-knowledge/ にコピー
  onenote_mapping.json を data/onenote/mapping.json に配置
  build_chunks_from_onenote.py --diff-plan diff_plan.json を実行
  build_index_and_upload.py を実行
  _manifest_current.json を _manifest_prev.json にリネームして保存
```

---

## ワークフロー手順

### Step 0: 差分比較

```bash
cd <km-repo>
python scripts/compare_manifest.py \
  --new-mapping   yyyymmdd_onenote/page_html_mapping.json \
  --html-dir      yyyymmdd_onenote/ \
  --prev-manifest _manifest_prev.json \
  --output-dir    input/ \
  --plan-output   diff_plan.json
```

**出力**: `diff_plan.json`、new + changed ページのみ `input/` に PDF 配置

### Step 1: 文字起こし（new + changed のみ）

```bash
python -m src.km.cli.convert --input input --output pre-knowledge
```

### Step 2: きれい化（new + changed のみ）

```bash
python -m src.km.cli.enhance --target pre-knowledge/
```

**この時点で new + changed ページの最新 `formatted.md` が `pre-knowledge/` に揃う。**

### Step 3: deleted ページの削除（削除ページがある場合のみ）

削除前に Claude がユーザーに対象を提示して確認を取る。

```
例:
  以下のページが OneNote から削除されています。pre-knowledge/ とチャンクから削除しますか？
  - 契約T手順書 / 廃止セクション / 古いページ
    → pre-knowledge/契約T手順書__廃止セクション__古いページ/ を削除
    → AI Search チャンク ID: onenote-0015 を削除
```

### Step 4: manifest 更新 & mapping.json 生成

```bash
python scripts/update_manifest.py \
  --diff-plan       diff_plan.json \
  --prev-manifest   _manifest_prev.json \
  --pre-knowledge   pre-knowledge/ \
  --output-manifest _manifest_current.json \
  --output-mapping  onenote_mapping.json
```

**この後**:
- `_manifest_current.json` を `_manifest_prev.json` にリネームして保存
- `onenote_mapping.json` と `pre-knowledge/` をチャットボットプロジェクトに渡す

## 初回実行（manifest がない場合）

`_manifest_prev.json` が存在しない場合は全ページが `new` 扱いになる。
`chatbot-onenote-knowledge-maker` スキルの初回処理と同じ動作をする。

## スクリプト一覧

| スクリプト | ステップ | 説明 |
|-----------|---------|------|
| `scripts/compare_manifest.py` | Step 0 | 差分比較 + new・changed の PDF 生成 |
| `scripts/update_manifest.py` | Step 4 | manifest 更新 + mapping.json 生成 |

詳細仕様は [references/diff-spec.md](references/diff-spec.md) を参照。