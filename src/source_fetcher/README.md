# Source Fetcher

OneNote / SharePoint からファイルを取得し、KM の `input/` に PDF + メタデータを供給するツール。

Playwright（headed / 画面外配置）でブラウザをレンダリングし、認証付き画像を含む高品質な PDF を生成する。

---

## ディレクトリ構成

```
src/source_fetcher/
├── fetch_onenote.py             # OneNote Web を Playwright で巡回し、ページ単位の PDF + URL を取得
├── fetch_wiki.py                # SP サイトページの PDF 一括取得（TGCS-wiki 等）
├── schema.py                    # registry / manifest の Pydantic スキーマ
├── common/
│   ├── cookie_manager.py        # Cookie の JSON 保存/復元
│   ├── pdf_generator.py         # page.pdf() + UI 除去 + レイアウト最適化
│   └── manifest.py              # input_manifest.json の生成・更新
├── pa_flows/                    # PA フロー定義（将来の自動化用、保管のみ）
│   ├── flow_a_onenote/workflow.json
│   └── flow_b_sp_files/workflow.json
└── pa_solution_unpacked/        # pac solution unpack の出力（Git 管理対象）
    ├── Other/
    │   ├── Solution.xml
    │   └── Customizations.xml
    └── Workflows/
        └── Test_OneNote_Connection-*.json   # 接続確認用テストフロー
```

---

## 実行方法

```bash
cd <project-dir>/   # 例: knowledge-concierge
python -m source_fetcher.fetch_onenote --registry ./source_registry.json --output ./input/
python -m source_fetcher.fetch_wiki    --registry ./source_registry.json --output ./input/
```

`source_registry.json` には取得対象のノートブック・サイトを定義する（プロジェクトディレクトリ側に配置）。

---

## 技術方針

### なぜ Playwright を使うか

| 観点 | Playwright 直接 |
|------|----------------|
| 画像取得 | `page.pdf()` でブラウザレンダリング結果がそのまま画像込み PDF 化 |
| 恒久性 | そのまま本番になる |
| ツールチェーン | Playwright のみ（シンプル） |
| 認証 | Cookie 保存で再利用（手動実行前提なら問題なし） |
| `python fetch.py` 完結 | 可能 |

### headed + 画面外配置

```python
browser = await pw.chromium.launch(
    channel="msedge",
    headless=False,                      # headed が必須
    args=["--window-position=-2000,0"],  # 画面外に配置
)
```

headless では `blob:` URL 画像が読み込めず、認証付き画像リソースのレンダリングも不安定になる。
TGCS-wiki（532ページ）の PDF 取得で実証済み。

### Cookie 管理

```python
# 保存
cookies = await context.cookies()
with open(COOKIE_FILE, "w") as f:
    json.dump(cookies, f)

# 復元
with open(COOKIE_FILE) as f:
    cookies = json.load(f)
await context.add_cookies(cookies)
```

- Cookie ファイル: `data/.onenote_cookies.json`（wiki は `data/.cookies.json`）
- 初回のみ手動ログイン。以降は Cookie 復元で自動ログイン
- M365 の Cookie 有効期限は実測で確認が必要

### ページ待機（`networkidle` 不使用）

SharePoint / OneNote Web の SPA では `networkidle` がハングするケースがある。
要素の出現を `wait_for_function` で検知するパターンを採用する。

```python
await page.wait_for_function(
    '() => document.querySelectorAll("[data-automation-id]").length > 0',
    timeout=180_000,
)
```

### UI 除去

`el.remove()` で DOM から完全削除する（CSS `display:none` では PDF に残るため）。

```python
REMOVE_UI_JS = """() => {
    ['#SuiteNavWrapper', '#spSiteHeader', '.sp-appBar',
     '[data-automation-id="pageCommandBar"]',
     '#spBottomPlaceholder'].forEach(sel => {
        document.querySelectorAll(sel).forEach(el => el.remove());
    });
}"""
```

OneNote Web 固有の除去対象は V1 DOM 分析（`scripts/onenote_dom_analysis.py`）の結果で確定する。

### page.pdf() パラメータ

```python
await page.emulate_media(media="print")
await page.pdf(
    path=str(pdf_path),
    format="A4",
    scale=0.7,
    print_background=True,
    margin={"top": "4mm", "right": "4mm", "bottom": "4mm", "left": "4mm"},
    display_header_footer=False,
)
```

### 並列実行（asyncio + Semaphore）

```python
sem = asyncio.Semaphore(4)   # OneNote は 4 並列（重い SPA のため控えめに）

async def fetch_one(ctx, page_info, sem):
    async with sem:
        page = await ctx.new_page()
        try:
            ...
        finally:
            await page.close()

tasks = [fetch_one(ctx, p, sem) for p in pages]
await asyncio.gather(*tasks)
```

---

## input_manifest.json スキーマ（出力）

```json
{
  "generated_at": "2026-02-20T10:00:00",
  "files": [
    {
      "filename": "契約T手順書.pdf",
      "source_type": "onenote",
      "source_url": "https://...notebook-url...",
      "acquired_by": "playwright",
      "acquired_at": "2026-02-20T10:00:12",
      "page_urls": {
        "本ワンノートについて": "https://...deep-link...",
        "ノート運用ルール": "https://...deep-link..."
      }
    }
  ]
}
```

`page_urls` は OneNote のページ単位ディープリンク（V1 DOM 分析で URL 形式を確認済みの場合のみ付与）。

---

## 開発進捗

| ファイル | 状態 | 内容 |
|----------|------|------|
| `fetch_wiki.py` | ✅ 実装済 | TGCS-wiki 532ページの PDF + URL 取得。`ref_scraiping-tool` ベース |
| `scripts/fetch_onenote.py` | ✅ 実装済・本番稼働中 | OneNote Web 巡回・ページ単位 PDF 取得。マルチパス方式で安定運用 |
| `scripts/quality_check.py` | ✅ 実装済 | PDF 品質チェック（白ボックス検出）・manifest retry 書き戻し |
| `common/cookie_manager.py` | ⏳ 共通化予定 | fetch_wiki / fetch_onenote の Cookie 処理を統合 |
| `common/pdf_generator.py` | ⏳ 共通化予定 | UI 除去・コンパクト CSS・page.pdf() を統合 |
| `common/manifest.py` | ⏳ 新規 | input_manifest.json の生成・更新 |

### fetch_onenote.py 実装内容（2026-02-25 現在）

**実績:** 料金T業務ノートブック 439ページ を取得（進行中）

**主要コマンド:**

```bash
# Phase 1: ページ URL 収集（~10分）
python scripts/fetch_onenote.py fastdiscover --url "<URL>" --output data/onenote_XXX

# Phase 2: PDF バルク取得（~70分）
python scripts/fetch_onenote.py fetch --manifest data/onenote_XXX/manifest.json --tabs 12

# Phase 3: リトライ（品質 NG ページのみ）
python scripts/fetch_onenote.py fetch --manifest data/onenote_XXX/manifest.json --retry
```

**技術的な工夫:**

| 課題 | 解決策 |
|------|--------|
| OneNote SPA の非同期コンテンツロード | MutationObserver（DOM 変更静止 1.5秒）+ img.complete 待機 |
| ナビパネルの白箱残留 | expand_containers_async() の inline style + print CSS injection |
| コンテンツロード前の PDF 化 | wait_for_content_stable_async() で DOM 静止まで待機 |
| 品質問題の自動検出 | PDF レンダリング後の左 30% 白色率をピクセル分析（閾値 90%） |
| 品質 NG のリトライ | --retry フラグで tabs=2, quiet_ms=3000ms の低速確実モード |

**詳細な運用手順:** [scripts/README.md](../../scripts/README.md) を参照

---

## 参考資料

| ファイル | 内容 |
|----------|------|
| [`docs/ref_scraiping-tool/scripts/download_wiki_pdfs.py`](../../docs/ref_scraiping-tool/scripts/download_wiki_pdfs.py) | Phase 1/2 分離・Cookie 管理・並列実行・UI 除去・PDF 最適化の実装例 |
| [`scripts/onenote_dom_analysis.py`](../../scripts/onenote_dom_analysis.py) | OneNote Web DOM 分析スクリプト（V1） |
| [`docs/plan_grounding-funcitons/plan-grounding-feature.md`](../../docs/plan_grounding-funcitons/plan-grounding-feature.md) | グラウンディング機能 全体設計書 |
| [`.cursor/rules/playwright-rpa-skill.mdc`](../../.cursor/rules/playwright-rpa-skill.mdc) | Playwright 自動化スキル |

---

## PA フロー定義（将来の自動化用、現時点では未使用）

2026-02-19 の検証で作成済みの PA 資産は `pa_flows/` と `pa_solution_unpacked/` に保管。
将来 PA に移行する際のベースとして利用可能。

- `flow_a_onenote/workflow.json` — OneNote ページ取得フロー定義
- `flow_b_sp_files/workflow.json` — SP ファイル取得フロー定義
- `pa_solution_unpacked/` — Solution 構造（接続参照 `cr23a_sharedonenote_da28f` 含む）

### 確認済みの接続参照（Connection Reference）

| コネクタ | connectionReferenceLogicalName |
|---------|-------------------------------|
| OneNote (Business) | `cr23a_sharedonenote_da28f` |
| OneDrive for Business | `cr23a_sharedonedriveforbusiness`（要追加） |
| SharePoint | `cr23a_sharedsharepointonline`（要追加） |
