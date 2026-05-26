# Knowledge Maker プロジェクト概要

**パス:** `/Users/higuchikaito/Documents/code/technopro/int_ai_prd_knowledge-maker-main`

---

## 概要

社内に散在する業務マニュアル等のドキュメントを、AIを活用して統一されたMarkdown形式に変換し、GitHubのナレッジリポジトリにまとめるための前処理ツール。

チャットボット（Cursor等）用ナレッジベース構築を最終目的とする。

---

## ディレクトリ構成

```
knowledge-maker/
├── README.md                          # 詳細ドキュメント（169KB、2610行）
├── requirements.txt                   # Python依存パッケージ
├── .env.example                       # 環境変数設定テンプレート
│
├── input/                             # 入力ドキュメント置き場
├── pre-knowledge/                     # 作業フォルダ（中間出力）
├── knowledge/                         # 最終出力フォルダ
├── tmp/                               # 一時ファイル
│
├── scripts/
│   ├── fetch_onenote.py              # OneNote PDF一括フェッチ（40KB）
│   ├── quality_check.py              # 品質チェックスクリプト（16KB）
│   └── README.md
│
└── src/km/
    ├── cli/                           # コマンドライン エントリポイント
    │   ├── convert.py                 # Step 1: 文字起こしCLI
    │   ├── enhance.py                 # Step 2: きれい化CLI
    │   ├── deploy.py                  # Step 3: ナレッジ化CLI
    │   ├── extract.py                 # 画像・コンテンツ抽出
    │   ├── inventory.py               # ドキュメント在庫管理
    │   └── structure.py               # ナレッジ構造ユーティリティ
    │
    ├── converters/                    # ファイル形式変換モジュール
    │   ├── pdf_converter.py           # PDF → Markdown（33KB）
    │   ├── ppt_converter.py           # PowerPoint → Markdown（28KB）
    │   ├── xlsx_converter.py          # Excel → Markdown（21KB）
    │   ├── docx_converter.py          # Word → Markdown
    │   ├── doc_converter.py           # 旧形式Word
    │   ├── xls_converter.py           # 旧形式Excel
    │   └── csv_converter.py           # CSV → Markdown
    │
    ├── pipeline/                      # 処理パイプライン（最大コード量）
    │   ├── knowledge_converter.py     # Step 1（21KB）
    │   ├── knowledge_enhancer.py      # Step 2（137KB、最大ファイル）
    │   ├── knowledge_deployer.py      # Step 3（125KB）
    │   ├── knowledge_updater.py       # 差分更新（50KB）
    │   ├── proposal_generator.py      # フォルダ構成提案（97KB）
    │   ├── quality_checker.py         # 品質検証（17KB）
    │   └── ...（その他ステージ別処理）
    │
    ├── core/                          # コアユーティリティ
    │   ├── factory.py                 # コンバーターファクトリー
    │   ├── libreoffice_bridge.py      # LibreOffice連携（15KB）
    │   └── ...
    │
    ├── utils/
    │   ├── openai_client.py           # OpenAI/Azure APIクライアント（17KB）
    │   ├── ocr_client.py              # Vision APIクライアント
    │   └── ...
    │
    └── templates/
        └── default.md                 # デフォルト整形テンプレート（5.5KB）
```

---

## 3ステップ処理フロー

```
input/              →  [Step 1: convert]  →  pre-knowledge/{doc}/02_transcribed_markdown/
(元ドキュメント)                               (テキスト抽出済みMarkdown)

                       [Step 2: enhance]  →  pre-knowledge/{doc}/03_formatted_markdown/
                                              (AI整形済みMarkdown + terms.json + quality_report.json)

                       [Step 3: deploy]   →  knowledge/
                                              (統合・再構成されたナレッジベース)
```

### Step 1: 文字起こし（convert）

- **コマンド:** `python -m src.km.cli.convert`
- PDF/Excel/Word/PPT等からテキスト・画像・表を抽出
- 図・表はAI OCR（gpt-4o-mini等）でテキスト化
- 画像は `04_images/` に保存
- **出力:** `transcribed.md`

**対応フォーマット:**

| 形式 | 処理方法 | 必須ツール |
|------|---------|----------|
| PDF | PyMuPDF でテキスト+画像+表抽出 | なし |
| Excel (.xlsx) | openpyxl → Markdown表 | なし |
| Excel (.xls) | LibreOffice → xlsx変換 | LibreOffice |
| Word (.docx/.doc) | LibreOffice → PDF → PyMuPDF | LibreOffice |
| PowerPoint (.pptx/.ppt) | LibreOffice → PDF → AI OCR | LibreOffice |
| CSV | pandas → Markdown表 | なし |

### Step 2: きれい化（enhance）

- **コマンド:** `python -m src.km.cli.enhance --target pre-knowledge/`
- テンプレートに沿ってAIが整形（`default.md`）
- サマリー・目次を自動生成
- 品質チェック（文字数・表の行数検証）
- 3万文字以上は自動分割 → 3並列処理
- **AIモデル:** `gpt-5.1`（temperature=0, max_tokens=100,000）
- **出力:** `formatted.md`, `terms.json`, `chapter_summaries.json`, `quality_report.json`

### Step 3: ナレッジ化（deploy）

- **コマンド:** `python -m src.km.cli.deploy --target pre-knowledge/ --output knowledge/`
- 複数の `formatted.md` を業務単位で統合・再構成
- AIがフォルダ構成を提案（初回構築時）
- 用語集・マッピング情報（traceability）を自動生成
- 更新モード: 既存ナレッジと照合し差分検出
- **出力:** `00_用語集.md`, `mapping.json`, `readme.md`, カテゴリフォルダ群

---

## 技術スタック

| 層 | 技術 |
|----|------|
| 言語 | Python 3.9+ |
| PDF処理 | PyMuPDF (fitz), pdfplumber, PyPDF2 |
| Excel処理 | openpyxl, pandas |
| Word/PPT処理 | python-docx, python-pptx, LibreOffice（PDFブリッジ） |
| AI/ML | OpenAI, Azure OpenAI, AWS Bedrock, Anthropic |
| 画像処理 | Pillow |
| データ処理 | pandas, numpy |
| 自動化 | Playwright（OneNoteフェッチ） |
| 設定管理 | python-dotenv |
| 監視 | psutil（メモリ監視） |

---

## セットアップ

```bash
# 1. 依存パッケージインストール
pip install -r requirements.txt

# 2. LibreOffice インストール（.doc, .xls, .ppt 変換に必須）
brew install libreoffice  # macOS

# 3. OneNote用ブラウザ（必要な場合）
playwright install msedge

# 4. .env 作成
cp .env.example .env
# 必要なAIキーを設定
```

### 最小限の環境変数設定（いずれか1つ）

```bash
# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=xxxxxxxx
AZURE_OPENAI_DEPLOYMENT_CHAT=gpt5.1
AZURE_OPENAI_DEPLOYMENT_VISION=gpt5-mini

# または OpenAI
OPENAI_API_KEY=sk-proj-xxxxxxxx

# または AWS Bedrock
AWS_BEARER_TOKEN_BEDROCK=bedrock-api-key-xxx
AWS_DEFAULT_REGION=us-east-1
BEDROCK_ENHANCE_MODEL=us.anthropic.claude-sonnet-4-6
```

**AIプロバイダー優先順位:** OpenAI > AWS Bedrock > Anthropic > Azure OpenAI

---

## 主なCLIオプション

**convert:**
```bash
python -m src.km.cli.convert \
  --input ./documents \
  --output ./pre-knowledge \
  --keep-input \
  --no-ai-description \
  --parallel-workers 4
```

**enhance:**
```bash
python -m src.km.cli.enhance \
  --target pre-knowledge/ \
  --template src/km/templates/custom.md \
  --model gpt-5.1 \
  --retry-failed \
  --skip-quality-check
```

**deploy:**
```bash
python -m src.km.cli.deploy \
  --target pre-knowledge/ \
  --output knowledge/ \
  --force \
  --validate-links
```

---

## 特徴的な機能

1. **多形式対応** - PDF, Excel, Word, PowerPoint, CSV を統一処理
2. **大規模ファイル自動分割** - 3万文字超は自動分割 → 並列処理
3. **AI-OCR統合** - 表・図をテキスト化し、チャットボットが理解できる形に
4. **品質チェック自動化** - 文字数・表の行数を自動検証
5. **Traceability対応** - `mapping.json` で元ファイルとの対応を記録
6. **差分更新対応** - 既存ナレッジとの照合機能
7. **OneNote統合** - SharePoint/OneNote から自動フェッチ可能
8. **マルチAI対応** - OpenAI, Azure, AWS Bedrock, Anthropic に対応

---

## OneNoteフェッチ（スクリプト）

```bash
# ページURL収集
python scripts/fetch_onenote.py fastdiscover \
  --url "https://tokyogasgroup-my.sharepoint.com/..." \
  --output data/onenote_XXX

# バルク取得
python scripts/fetch_onenote.py fetch \
  --manifest data/onenote_XXX/manifest.json \
  --tabs 12

# 品質チェック
python scripts/quality_check.py check \
  --manifest data/onenote_XXX/manifest.json
```

---

## 最終成果物の構造例

```
knowledge/
├── 00_用語集.md
├── mapping.json
├── readme.md
├── 01_業務マニュアル/
│   ├── 01_概要.md
│   ├── 02_見積.md
│   └── images/
└── 02_チャット運用/
    └── 01_基本.md
```

この `knowledge/` フォルダを別リポジトリにコピーし、Cursor等のチャットボットのナレッジソースとして利用する。
