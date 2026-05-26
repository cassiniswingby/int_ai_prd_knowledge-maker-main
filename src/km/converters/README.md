# Converters

ドキュメント変換モジュール。各ファイル形式に対応したコンバーターを提供します。

## 対応形式

| コンバーター | 対応形式 | 説明 |
|-------------|---------|------|
| `PDFConverter` | .pdf | PDFテキスト抽出 |
| `XLSXConverter` | .xlsx | Excel 2007以降 |
| `XLSConverter` | .xls | Excel 97-2003（LibreOffice経由） |
| `DOCXConverter` | .docx | Word 2007以降 |
| `DOCConverter` | .doc | Word 97-2003（LibreOffice経由） |
| `PPTConverter` | .ppt, .pptx, .odp | PowerPoint（OCR対応） |

---

## PPTConverter - OCR機能

PowerPointファイルの変換時に、テキストが少ないスライド（図表中心など）を自動検出し、OpenAI Vision APIでOCR処理を実行します。

### 動作フロー

```
PPT/PPTX
    ↓ LibreOffice
PDF
    ↓ PyMuPDF
ページ単位テキスト抽出
    ↓ 閾値判定
[テキスト十分] → そのまま出力
[テキスト不足] → OCR処理 → マージして出力
```

### 環境変数

| 環境変数 | 説明 | デフォルト |
|----------|------|-----------|
| `OPENAI_API_KEY` | OpenAI APIキー（必須） | - |
| `OPENAI_VISION_MODEL` | Visionモデル名 | `gpt-5-mini` |
| `OPENAI_OCR_PROMPT` | OCRプロンプト | デフォルトプロンプト |
| `SLIDE_OCR_THRESHOLD` | スライド単位のOCR閾値（文字数） | `30` |
| `OCR_ALL_SLIDES` | 全スライドをOCR処理する | `false` |

### OCR判定ロジック

1. **PDF変換**: LibreOfficeでPPT/PPTXをPDFに変換
2. **テキスト抽出**: PyMuPDFで各ページからテキストを抽出
3. **本文抽出**: ヘッダー/フッター/テンプレートテキストを除外
   - ページ番号のみの行
   - 日付パターン（YYYY/MM/DD等）
   - 著作権表記（Copyright, ©等）
   - ナビゲーションテキスト（「全体目次へ」等）
4. **閾値判定**: 本文が`SLIDE_OCR_THRESHOLD`（デフォルト30文字）以下 → OCR対象
5. **OCR処理**: OpenAI Vision APIで画像をMarkdown化
6. **マージ**: OCR結果と通常テキストを結合

### 使用例

```python
from pathlib import Path
import os

# 環境変数設定
os.environ["OPENAI_API_KEY"] = "sk-xxx"
os.environ["SLIDE_OCR_THRESHOLD"] = "30"  # 30文字以下でOCR
os.environ["OCR_ALL_SLIDES"] = "false"    # 閾値判定を使用

# 変換実行
import src.km.converters
from src.km.core.factory import ConverterFactory

converter = ConverterFactory.get_converter(Path("presentation.pptx"))
success, text, message = converter.convert(Path("presentation.pptx"))

if success:
    print(text)
    print(f"Message: {message}")  # "OCR applied to 3/25 slides" など
```

### 全スライドOCRモード

図表が多い資料など、全スライドをOCR処理したい場合：

```bash
export OCR_ALL_SLIDES=true
```

### カスタムプロンプト

用途に応じてプロンプトをカスタマイズできます：

```bash
# ワークフロー・組織図向け
export OPENAI_OCR_PROMPT="このスライドの内容をMarkdown形式でテキスト化してください。フローチャートや組織図の場合は、階層構造をインデント付き箇条書きで表現してください。"

# 写真・封筒等の説明向け
export OPENAI_OCR_PROMPT="この画像に何が写っているか、どのような状況・文脈かを要約してください。文字情報がある場合は併せて記載してください。"
```

### 注意事項

- **GPT-5系の制約**: `temperature`パラメータは固定値のため指定できません
- **APIコスト**: OCR処理はVision APIを使用するため、処理するスライド数に応じてコストが発生します
- **依存関係**: `openai`パッケージと`PyMuPDF`が必要です

---

## ConverterFactory

ファイル拡張子に基づいて適切なコンバーターを自動選択します。

```python
import src.km.converters  # コンバーター自動登録
from src.km.core.factory import ConverterFactory

converter = ConverterFactory.get_converter(Path("document.pdf"))
success, text, error = converter.convert(Path("document.pdf"))
```

### カスタムコンバーターの登録

```python
from src.km.core.factory import BaseConverter, ConverterFactory

class MyConverter(BaseConverter):
    def convert(self, file_path):
        # 変換ロジック
        return True, "extracted text", ""

ConverterFactory.register_converter('.myext', MyConverter)
```

