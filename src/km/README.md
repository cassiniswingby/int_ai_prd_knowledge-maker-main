# Knowledge Maker - ソースコード

このディレクトリには Knowledge Maker のソースコードが含まれています。

## 📖 ドキュメント

**詳細なドキュメントは [プロジェクトルートの README.md](../../README.md) を参照してください。**

- 全体フロー図
- 各ステップの処理詳細
- AIモデル・プロンプトの設定
- 環境変数一覧
- トラブルシューティング

## 📁 ディレクトリ構成

```
src/km/
├── cli/                    # CLIエントリーポイント
│   ├── convert.py          # Step1: 文字起こし
│   ├── enhance.py          # Step2: きれい化
│   └── deploy.py           # Step3: ナレッジ化
├── converters/             # ファイル形式別変換器
│   ├── pdf_converter.py    # PDF処理
│   ├── xlsx_converter.py   # Excel処理
│   ├── docx_converter.py   # Word処理（PDF経由）
│   ├── ppt_converter.py    # PowerPoint処理
│   └── csv_converter.py    # CSV処理
├── pipeline/               # パイプライン処理
│   ├── knowledge_converter.py   # Step1: 文字起こしパイプライン
│   ├── knowledge_enhancer.py    # Step2: きれい化パイプライン
│   └── knowledge_deployer.py    # Step3: ナレッジ化パイプライン
├── core/                   # コア機能
│   └── libreoffice_bridge.py    # LibreOffice変換
├── utils/                  # ユーティリティ
│   └── ocr_client.py       # Vision API (AI-OCR) クライアント
└── templates/              # きれい化テンプレート
    └── default.md          # デフォルトテンプレート
```

## 🤖 使用AIモデル

| ステップ | 処理 | モデル |
|---------|------|--------|
| Step1 | AI-OCR | `gpt-5-mini` |
| Step2 | きれい化 | `gpt-5.1` |
| Step3 | ナレッジ化 | `gpt-5.1` |

詳細は [README.md の 8.3 セクション](../../README.md#83-aiモデルプロンプト詳細) を参照してください。
