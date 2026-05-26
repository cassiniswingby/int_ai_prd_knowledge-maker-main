"""Stage 2: Knowledge Enhancer - Format documents with templates.

This module handles the second stage of the knowledge conversion pipeline:
- Read transcribed.md from 02_transcribed_markdown/
- Apply template-based formatting using AI
- Extract technical terms for glossary generation
- Save to 03_formatted_markdown/formatted.md and terms.json

大規模ファイル（3万文字以上）は自動的にチャンク分割処理を行い、
並列できれい化した後、サマリーと目次を生成して結合します。
用語はきれい化と同時に抽出し、Step3でマージして用語集を生成します。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Iterable

from .knowledge_config import (
    DocumentFolder,
    DocumentFolderManager,
    FOLDER_REPORTS,
)
from .quality_checker import QualityChecker, QualityReport


# チャンク分割の閾値（文字数）
CHUNK_THRESHOLD = 30000  # 通常ドキュメント: 3万文字以上で分割
CHUNK_SIZE = 30000  # 通常ドキュメント: 各チャンクの目標サイズ
EXCEL_CHUNK_THRESHOLD = 60000  # Excel: 6万文字以上で分割
MAX_PARALLEL_WORKERS = 15  # チャンク単位の並列処理ワーカー数
MAX_DOCUMENT_PARALLEL_WORKERS = 30  # ドキュメント間並列処理のデフォルトワーカー数


@dataclass
class ChunkResult:
    """チャンクの処理結果."""
    chunk_index: int
    summary: str  # このチャンクのサマリー部分
    toc: str  # このチャンクの目次部分
    body_content: str  # サマリー・目次を除いた本文
    full_content: str  # AI出力全体（バックアップ用）
    success: bool
    error: Optional[str] = None
    terms: List[Dict] = field(default_factory=list)  # 抽出された用語リスト


logger = logging.getLogger(__name__)


class KnowledgeEnhancer:
    """Stage 2: Format documents with AI-powered templates.
    
    Reads transcribed markdown and applies template-based formatting.
    """
    
    def __init__(
        self,
        knowledge_dir: Path,
        template: str,
        *,
        logger: Optional[logging.Logger] = None,
        auto_fix: bool = False,
        max_retries: int = 2,
        skip_quality_check: bool = False,
        parallel_workers: int = 1,
    ) -> None:
        """Initialize the Knowledge Enhancer.
        
        Args:
            knowledge_dir: Path to the knowledge output directory
            template: Template content for formatting
            logger: Optional logger instance
            auto_fix: Whether to automatically retry on quality check failure
            max_retries: Maximum number of retries for auto_fix
            skip_quality_check: Whether to skip quality check
            parallel_workers: ドキュメント間並列処理のワーカー数 (1=逐次)
        """
        self.knowledge_dir = Path(knowledge_dir).resolve()
        self.template = template
        self.logger = logger or self._build_logger()
        
        self.folder_manager = DocumentFolderManager(self.knowledge_dir)
        self._client = None
        self._client_lock = threading.Lock()
        
        self.progress = self._load_progress()
        self.results = self._fresh_results()
        
        # Quality check settings
        self.quality_checker = QualityChecker(logger=self.logger)
        self.auto_fix = auto_fix
        self.max_retries = max_retries
        self.skip_quality_check = skip_quality_check
        self.quality_reports: List[QualityReport] = []
        self.failed_documents: List[str] = []  # Documents that failed quality check

        # 並列処理設定
        env_workers = os.getenv("ENHANCE_DOCUMENT_PARALLEL_WORKERS")
        self.parallel_workers: int = int(env_workers) if env_workers else parallel_workers

        self.logger.info(
            f"KnowledgeEnhancer initialized: dir={self.knowledge_dir}, "
            f"parallel_workers={self.parallel_workers}"
        )
    
    def _build_logger(self) -> logging.Logger:
        """Build a logger for the enhancer."""
        log = logging.getLogger("km.knowledge_enhancer")
        if not log.handlers:
            log.setLevel(logging.INFO)
            formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

            # Windows コンソールの cp932 問題を回避するため stderr を utf-8 に設定
            import sys
            if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
                try:
                    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass

            console = logging.StreamHandler()
            console.setFormatter(formatter)
            console.setLevel(logging.INFO)
            log.addHandler(console)
        
        log.propagate = False
        return log
    
    def _get_openai_client(self):
        """Lazy-load OpenAI or Azure OpenAI client（スレッドセーフ）."""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    try:
                        from ..utils.openai_client import get_openai_client, get_model_name

                        self._client, self._is_azure = get_openai_client(timeout=1500.0, purpose="chat")
                        self._model_name = get_model_name(purpose="chat", is_azure=self._is_azure)
                        self.logger.info(f"Using model: {self._model_name}, azure={self._is_azure}")

                    except Exception as e:
                        self.logger.error(f"Failed to initialize OpenAI client: {e}")
                        raise RuntimeError(
                            "API key is required for enhancement. "
                            "Set OPENAI_API_KEY or AZURE_OPENAI_API_KEY environment variable."
                        )

        return self._client
    
    def _get_model_name(self) -> str:
        """Get model/deployment name for chat completions."""
        if hasattr(self, '_model_name'):
            return self._model_name
        # フォールバック: クライアント初期化前に呼ばれた場合
        from ..utils.openai_client import get_model_name
        return get_model_name(purpose="chat", is_azure=getattr(self, '_is_azure', False))
    
    def _load_progress(self) -> Dict:
        """Load progress from previous runs."""
        progress_path = self.knowledge_dir / FOLDER_REPORTS / "format_progress.json"
        if progress_path.exists():
            try:
                with open(progress_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        
        return {
            "processed": [],
            "timestamp": None,
        }
    
    def _save_progress(self) -> None:
        """Save current progress."""
        self.progress["timestamp"] = datetime.now().isoformat()
        progress_path = self.knowledge_dir / FOLDER_REPORTS / "format_progress.json"
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(progress_path, "w", encoding="utf-8") as f:
            json.dump(self.progress, f, ensure_ascii=False, indent=2)
    
    def _fresh_results(self) -> Dict:
        """Create fresh results structure."""
        return {
            "metadata": {
                "start_time": datetime.now().isoformat(),
            },
            "statistics": {
                "total": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
            },
            "processed_documents": [],
            "failed": [],
        }
    
    def _save_results(self) -> None:
        """Save results."""
        self.results["metadata"]["end_time"] = datetime.now().isoformat()
        results_path = self.knowledge_dir / FOLDER_REPORTS / "format_results.json"
        
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
    
    def _format_images_info(self, doc_folder: DocumentFolder) -> str:
        """Format images directory info for prompt.
        
        Args:
            doc_folder: Document folder
            
        Returns:
            Formatted string with image information
        """
        images_dir = doc_folder.images_dir
        
        if not images_dir.exists():
            return "（画像なし）"
        
        image_files = sorted(images_dir.glob("*"))
        if not image_files:
            return "（画像なし）"
        
        lines = []
        for img_path in image_files:
            if img_path.is_file() and img_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                lines.append(f"- `{img_path.name}`: ../04_images/{img_path.name}")
        
        if not lines:
            return "（画像なし）"
        
        return "\n".join(lines)
    
    def _build_prompt(
        self,
        transcribed_content: str,
        doc_folder: DocumentFolder,
    ) -> str:
        """Build the prompt for AI formatting.
        
        Args:
            transcribed_content: Content from transcribed.md
            doc_folder: Document folder
            
        Returns:
            Complete prompt for AI
        """
        images_info = self._format_images_info(doc_folder)
        
        prompt = f"""以下のテンプレートに従って、業務マニュアルを整形し、専門用語も抽出してください。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【テンプレート（整形ルール）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{self.template}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【元のドキュメント（文字起こし）】
ドキュメント名: {doc_folder.document_name}
文字数: {len(transcribed_content):,}文字
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{transcribed_content}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【利用可能な画像ファイル】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{images_info}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【出力指示（重要）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**出力形式: JSON**

以下のJSON形式で出力してください（説明不要、JSONのみ）：

{{
  "formatted_markdown": "整形されたMarkdown（全文）",
  "terms": [
    {{"term": "用語1", "description": "説明（50文字以内）", "flag": 0}},
    {{"term": "用語2", "description": "説明", "flag": 1}}
  ]
}}

【Markdown整形ルール - 文字数維持が最重要】
⚠️ **出力文字数は入力文字数の80%～120%の範囲で維持すること（絶対条件）**
⚠️ **内容の要約・省略は絶対禁止**
⚠️ **内容の過剰な展開・加筆も禁止（元にない説明文を追加しない）**

1. テンプレートの構造・ルールに従って整形
2. **内容を省略・要約しない（すべての情報を維持）**
3. **内容を過剰に展開しない（元の文章をそのまま使う、説明文を追加しない）**
4. **表のデータは全行維持する（省略禁止）**
5. 箇条書きで構造的に記載（読みやすく）
6. 表のスクショ（table_xxx.png）→ Markdownの表に変換
7. 図のスクショ（img_xxx.png）→ そのまま残す
8. **図・表の説明は02の内容をもとに情報を落とさず整形（「図の説明:」等のラベルは不要）**
9. **装飾的な画像は省略する**（表紙デザイン、ロゴ、背景パターン、カラータイル、レイアウト要素等）
10. 引用記法 `>` は使用しない
11. ドキュメント情報・改訂履歴は削除
12. 目次はページ内リンク形式で作成
13. **用語集は作成しない**（termsフィールドで抽出するため）
14. **関連資料は「なし」と記載（適当に作成しない）**
15. **表をコードブロック（```）で囲まない**（直接Markdown表として出力）
16. **#N/A、空白行、重複行はそのまま維持**（Excelデータは元のまま保持）
17. **「...」や「（省略）」等の省略表現は使用禁止**
18. **全てのスライド・ページ・セクションの内容を出力する**

【見出しレベル規約 - 最重要】
| レベル | 用途 | 例 |
|--------|------|-----|
| `#` | ドキュメントタイトル（1つのみ） | `# 業務設計資料` |
| `##` | 固定セクション（サマリー/目次/関連資料のみ） | `## サマリー`, `## 目次` |
| `###` | 本文の大項目（章）**番号なし** | `### 見積作成の概要` |
| `####` | 本文の中項目 **番号なし** | `#### 見積入力ルール` |
| `#####` | **使用禁止** | - |

- **本文では `###`（大項目）と `####`（中項目）のみ使用**
- **番号は付けない**（番号は後処理で自動付与される）
- 見出しは内容を反映した意味のある名前にする
- 関連するスライドは1つの大項目にまとめて良い

【大項目のグループ化 - 最重要】
- **大項目（`###`）は必ず10〜20個の範囲に収める**（20個を超えてはならない）
- 関連する内容（複数のSlide）は1つの大項目に積極的にグループ化する
- 大項目名は抽象度を高くする（Step3でディレクトリ構成に使うため重要）
- 細かい手順や操作は中項目（`####`）として大項目の配下に入れる
- 例: Slide 1〜30が「見積作成」に関するなら `### 見積作成` としてまとめ、個別手順は `#### 見積入力ルール` などの中項目にする

【用語抽出ルール】
1. 業務で使用される専門用語・略語を抽出
2. 一般的な用語は含めない
3. 説明は簡潔に（50文字以内）
4. **定義が不明な用語は無理に説明しない**
5. **ドキュメント内に説明がある用語のみ記載**
6. **推測で説明を作成しない（嘘の情報は絶対禁止）**

【flagのルール】
- ドキュメント内の情報から意味・役割が明確に分かる用語 → 0
- 社内略称などで定義・意味があいまい／推測を含む可能性がある用語 → 1
"""
        
        return prompt
    
    def _get_system_prompt(self) -> str:
        """Get the system prompt for AI formatting.
        
        Returns:
            System prompt string
        """
        return """あなたは業務マニュアルを整形する専門家です。

【最重要ルール - 文字数維持（80%～120%）】
⚠️ **出力文字数は入力文字数の80%～120%の範囲で維持すること**
⚠️ **内容の要約・省略は絶対禁止**
⚠️ **内容の過剰な展開・加筆も禁止（元にない説明文を追加しない）**
⚠️ **「...」「（省略）」等の省略表現は使用禁止**

【役割】
- 文字起こしされたドキュメントを「見やすく構造化」する
- **情報を省略せず、すべて維持する**
- **元の文章をそのまま使い、勝手に説明文を追加しない**
- テンプレートのフォーマットに従う
- **全てのスライド・ページ・セクションの内容を漏れなく出力する**

【整形ルール】
- 本文は箇条書きで構造的に記載
- **表のデータは全行維持する（1行も省略しない）**
- **表の内容を説明文に展開しない（そのまま表として維持）**
- 表のスクショ（table_xxx.png）はMarkdownの表に変換
- 図のスクショ（img_xxx.png）はそのまま残す
- **図・表の説明は02の内容をもとに情報を落とさず整形（「図の説明:」ラベルは不要）**
- **装飾的な画像は省略**（表紙デザイン、ロゴ、背景パターン、カラータイル等）
- 引用記法 `>` は使用しない
- ドキュメント情報・改訂履歴は出力しない
- **用語集は作成しない**
- **関連資料は「なし」と記載（適当に作成しない）**

【見出しレベル規約 - 最重要】
以下のルールを厳守すること：

| レベル | 用途 | 例 |
|--------|------|-----|
| `#` | ドキュメントタイトル（1つのみ） | `# 業務設計資料` |
| `##` | 固定セクション（サマリー/目次/関連資料） | `## サマリー`, `## 目次` |
| `###` | 本文の大項目（章）**番号なし** | `### 見積作成の概要` |
| `####` | 本文の中項目 **番号なし** | `#### 見積入力ルール` |
| `#####` | **使用禁止** | - |

- **本文では `###`（大項目）と `####`（中項目）のみ使用**
- **番号は付けない**（番号は後処理で自動付与される）
- 見出しは内容を反映した意味のある名前にする
- **「Slide X」形式の見出しは禁止**
- 関連するスライドは1つの見出しにまとめて良い

【大項目のグループ化 - 最重要】
- **大項目（`###`）は必ず10〜20個の範囲に収める**（20個を超えてはならない）
- 関連する内容（複数のSlide）は1つの大項目に積極的にグループ化する
- 大項目名は抽象度を高くする（Step3でディレクトリ構成に使うため重要）
- 細かい手順や操作は中項目（`####`）として大項目の配下に入れる
- 例: Slide 1〜30が「見積作成」に関するなら `### 見積作成` としてまとめ、個別手順は `#### 見積入力ルール` などの中項目にする

【禁止事項】
- **内容の省略・要約（絶対禁止）**
- **表の行の省略（絶対禁止）**
- **「Slide X」形式の見出し（絶対禁止）**
- **`#####` 以下の見出し使用（絶対禁止）**
- 元にない情報の追加
- 画像パスの変更（../04_images/xxx.png形式を維持）
- 専門用語・固有名詞の変更
- 関連資料の架空リンク作成
- 「図の説明:」「表の説明:」等のラベル追加
"""
    
    def format_document(self, doc_folder: DocumentFolder, retry_count: int = 0) -> bool:
        """Format a document using the template.
        
        大規模ファイル（3万文字以上）は自動的にチャンク分割処理を行います。
        Excelファイル（テーブル主体）はシート単位で分割処理します。
        
        Args:
            doc_folder: Document folder to process
            retry_count: Current retry attempt number
            
        Returns:
            True if successful
        """
        content_path = doc_folder.content_md_path
        
        if not content_path.exists():
            self.logger.warning(f"No transcribed.md found in {doc_folder.document_name}")
            return False
        
        # Read content
        input_content = content_path.read_text(encoding="utf-8")
        
        # Excelファイル（テーブル主体）の判定
        is_excel = self._is_excel_document(input_content)
        
        # 文字数に応じて処理方法を分岐
        if len(input_content) >= CHUNK_THRESHOLD:
            if is_excel:
                # Excel: シート単位で分割処理
                self.logger.info(f"  📊 大規模Excelファイル検出 ({len(input_content):,}文字) → シート単位分割処理")
                return self._format_excel_chunked(doc_folder, input_content, retry_count)
            else:
                # 非Excel: 既存のチャンク分割処理
                self.logger.info(f"  📦 大規模ファイル検出 ({len(input_content):,}文字) → チャンク分割処理")
                return self._format_document_chunked(doc_folder, input_content, retry_count)
        else:
            # 小規模: 単一処理
            if is_excel:
                self.logger.info(f"  📊 Excelファイル検出 ({len(input_content):,}文字) → テーブル維持処理")
            return self._format_document_single(doc_folder, input_content, retry_count, is_table_heavy=is_excel)
    
    def _is_excel_document(self, content: str) -> bool:
        """Excelファイル（テーブル主体）かどうかを判定.
        
        Args:
            content: ドキュメントの内容
            
        Returns:
            Excelファイル（テーブル主体）の場合True
        """
        # 元ファイルがxlsx/xlsの場合
        if '**ファイル形式:** xlsx' in content or '**ファイル形式:** xls' in content:
            return True
        
        # シート構造がある場合
        if '## シート:' in content:
            return True
        
        # テーブル行が多い場合（|で始まる行が全体の30%以上）
        lines = content.split('\n')
        table_lines = sum(1 for line in lines if line.strip().startswith('|'))
        if len(lines) > 0 and table_lines / len(lines) > 0.3:
            return True
        
        return False
    
    def _format_document_single(
        self,
        doc_folder: DocumentFolder,
        input_content: str,
        retry_count: int = 0,
        is_table_heavy: bool = False
    ) -> bool:
        """単一処理でドキュメントをきれい化（従来の処理）.
        
        JSON形式で出力し、formatted_markdownとtermsを同時に取得します。
        
        Args:
            doc_folder: Document folder to process
            input_content: Content from transcribed.md
            retry_count: Current retry attempt number
            is_table_heavy: テーブル主体のドキュメント（Excel等）の場合True
            
        Returns:
            True if successful
        """
        try:
            client = self._get_openai_client()
            
            # Build prompt with template
            prompt = self._build_prompt(input_content, doc_folder)
            
            # テーブル主体のドキュメントは特別指示を追加
            if is_table_heavy:
                prompt += """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【⚠️ 重要: テーブル主体ドキュメントの処理ルール】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

このドキュメントはExcel/テーブル形式のデータです。以下のルールを厳守してください：

1. **テーブル構造は絶対に維持する**（要約しない、省略しない）
2. **全ての行・列を保持する**（1行・1列も削除しない）
3. **セル内の`<br>`タグはそのまま維持する**（改行情報を保持）
4. **テーブルデータを箇条書きに変換しない**
5. テーブルは元の形式のまま出力（Markdown表形式）
6. サマリーはテーブル全体の概要を3〜5個の箇条書きで記述
7. 目次は「## シート: シート名」形式の見出しをリスト化
"""
            
            # リトライ時は失敗理由をプロンプトに追加
            if retry_count > 0 and self.quality_reports:
                last_report = self.quality_reports[-1]
                if last_report.document_name == doc_folder.document_name:
                    prompt += f"""

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【⚠️ 前回の品質チェック失敗: 修正してください】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{last_report.get_failure_summary()}

上記の問題を解消するように、より丁寧に整形してください。
特に「情報を省略しない」「表の行を減らさない」ことを厳守してください。
"""
            
            system_prompt = self._get_system_prompt()
            
            # Use model from environment (OpenAI or Azure OpenAI)
            model = self._get_model_name()
            
            # gpt-5系/Azure OpenAI uses max_completion_tokens instead of max_tokens
            if "gpt-5" in model or "o1" in model or "o3" in model or getattr(self, '_is_azure', False):
                # gpt-5.1: reasoning=noneでtemperature=0が使用可能
                params = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    "max_completion_tokens": 100000,
                }
                # gpt-5.1の場合のみreasoning=noneとtemperature=0を追加
                if "5.1" in model or "5-1" in model:
                    params["temperature"] = 0
                response = client.chat.completions.create(**params)
            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0,
                    max_completion_tokens=16384,
                )
            
            response_text = response.choices[0].message.content.strip()
            
            # JSONをパース（formatted_markdownとtermsを分離）
            formatted_content, terms = self._parse_json_response(response_text, doc_folder.document_name)

            # 目次↔本文整合のポストプロセス（保存前に正規化）
            formatted_content = self._postprocess_formatted_markdown(
                formatted_content=formatted_content,
                document_name=doc_folder.document_name,
            )
            
            # === 品質チェック ===
            if not self.skip_quality_check:
                report = self.quality_checker.check(
                    input_content=input_content,
                    output_content=formatted_content,
                    document_name=doc_folder.document_name,
                )
                self.quality_reports.append(report)
                
                # 品質レポートを表示
                self.quality_checker.print_report(report)
                
                # 失敗時の処理
                if not report.passed:
                    if self.auto_fix and retry_count < self.max_retries:
                        self.logger.warning(f"  🔄 自動リトライ ({retry_count + 1}/{self.max_retries})...")
                        return self._format_document_single(doc_folder, input_content, retry_count + 1)
                    else:
                        self.failed_documents.append(doc_folder.document_name)
            
            # Save to 03_formatted_markdown/ (警告があっても保存する)
            self._save_formatted_output(doc_folder, formatted_content, input_content, terms)
            
            self.logger.info(f"✅ Formatted: {doc_folder.document_name} (用語: {len(terms)}件)")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Formatting failed for {doc_folder.document_name}: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            return False
    
    def _parse_json_response(self, response_text: str, document_name: str) -> Tuple[str, List[Dict]]:
        """AIのJSON出力をパースしてMarkdownと用語を分離.
        
        Args:
            response_text: AI出力のテキスト
            document_name: ドキュメント名（エラーログ用）
            
        Returns:
            (formatted_markdown, terms) のタプル
        """
        # コードブロックのラッパーを除去
        cleaned = response_text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[len("```json"):].strip()
        if cleaned.startswith("```"):
            cleaned = cleaned[3:].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
        
        try:
            data = json.loads(cleaned)
            formatted_markdown = data.get("formatted_markdown", "")
            terms = data.get("terms", [])
            
            # formatted_markdownのコードブロックラッパーも除去
            formatted_markdown = self._strip_code_block(formatted_markdown)
            
            return formatted_markdown, terms
            
        except json.JSONDecodeError as e:
            self.logger.warning(f"  ⚠️ JSONパース失敗: {e}")
            self.logger.warning(f"  → Markdownとして処理し、用語は後で抽出します")
            
            # JSONパース失敗時はテキストをそのままMarkdownとして扱い、用語は空
            formatted_markdown = self._strip_code_block(response_text)
            return formatted_markdown, []
    
    def _format_document_chunked(
        self,
        doc_folder: DocumentFolder,
        input_content: str,
        retry_count: int = 0
    ) -> bool:
        """チャンク分割処理でドキュメントをきれい化.
        
        1. セクション（##）単位でチャンクに分割
        2. 各チャンクを並列できれい化（ミニサマリー付き）
        3. 結合して全体サマリー・目次を生成
        4. サマリー+目次から用語を抽出
        
        Args:
            doc_folder: Document folder to process
            input_content: Content from transcribed.md
            retry_count: Current retry attempt number
            
        Returns:
            True if successful
        """
        try:
            # Step 1: チャンク分割
            chunks = self._split_into_chunks(input_content)
            self.logger.info(f"  📦 {len(chunks)}チャンクに分割")
            
            # Step 2: 並列きれい化
            images_info = self._format_images_info(doc_folder)
            chunk_results = self._process_chunks_parallel(
                chunks, doc_folder.document_name, images_info
            )
            
            # 成功したチャンクをチェック
            successful_results = [r for r in chunk_results if r.success]
            if not successful_results:
                self.logger.error(f"❌ すべてのチャンク処理が失敗しました")
                return False
            
            self.logger.info(f"  ✅ {len(successful_results)}/{len(chunks)}チャンク成功")
            
            # Step 3: 結合＆最終整形
            formatted_content = self._merge_chunks(
                doc_folder.document_name,
                chunk_results
            )
            
            # デバッグ: マージ後の文字数を確認
            self.logger.debug(f"  [DEBUG] マージ後: {len(formatted_content):,}文字")

            # 目次↔本文整合のポストプロセス（保存前に正規化）
            formatted_content = self._postprocess_formatted_markdown(
                formatted_content=formatted_content,
                document_name=doc_folder.document_name,
            )
            
            # デバッグ: ポストプロセス後の文字数を確認
            self.logger.debug(f"  [DEBUG] ポストプロセス後: {len(formatted_content):,}文字")
            
            # Step 4: サマリー+目次から用語を抽出（追加のAPI呼び出しを避けるため、サマリー部分のみ）
            terms = self._extract_terms_from_content(formatted_content, doc_folder.document_name)
            
            # === 品質チェック ===
            if not self.skip_quality_check:
                report = self.quality_checker.check(
                    input_content=input_content,
                    output_content=formatted_content,
                    document_name=doc_folder.document_name,
                )
                self.quality_reports.append(report)
                
                # 品質レポートを表示
                self.quality_checker.print_report(report)
                
                # 失敗時の処理（分割処理ではリトライしない）
                if not report.passed:
                    self.failed_documents.append(doc_folder.document_name)
            
            # Save
            self._save_formatted_output(doc_folder, formatted_content, input_content, terms)
            
            self.logger.info(f"✅ Formatted (chunked): {doc_folder.document_name} (用語: {len(terms)}件)")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Chunked formatting failed for {doc_folder.document_name}: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            return False
    
    def _format_excel_chunked(
        self,
        doc_folder: DocumentFolder,
        input_content: str,
        retry_count: int = 0
    ) -> bool:
        """Excel（シート構造）ドキュメントをシート単位で分割処理.
        
        シートの途中で分割しないため、テーブルの整合性が保たれます。
        大きすぎるシートはカテゴリ/業務名の変更点で分割します。
        
        Args:
            doc_folder: Document folder to process
            input_content: Content from transcribed.md
            retry_count: Current retry attempt number
            
        Returns:
            True if successful
        """
        try:
            # Step 1: シート単位で分割
            sheets = self._split_excel_by_sheet(input_content)
            self.logger.info(f"  📊 {len(sheets)}シートに分割")
            
            # シートが1つ以下の場合は単一処理にフォールバック
            if len(sheets) <= 1:
                self.logger.info(f"  📊 シートが1つ以下のため単一処理にフォールバック")
                return self._format_document_single(
                    doc_folder, input_content, retry_count, is_table_heavy=True
                )
            
            # 大きすぎるシートの警告（10万文字超）
            for i, sheet in enumerate(sheets):
                if len(sheet) > 100000:
                    self.logger.warning(
                        f"  ⚠️ シート{i+1}が大きすぎます ({len(sheet):,}文字) - "
                        f"APIトークン上限でエラーになる可能性があります"
                    )
            
            # Step 2: 並列きれい化（テーブル維持指示付き）
            images_info = self._format_images_info(doc_folder)
            chunk_results = self._process_chunks_parallel_excel(
                sheets, doc_folder.document_name, images_info
            )
            
            # 成功したチャンクをチェック
            successful_results = [r for r in chunk_results if r.success]
            if not successful_results:
                self.logger.error(f"❌ すべてのシート処理が失敗しました")
                return False
            
            self.logger.info(f"  ✅ {len(successful_results)}/{len(sheets)}シート成功")
            
            # Step 3: 結合＆最終整形（既存の_merge_chunksを使用）
            formatted_content = self._merge_chunks(
                doc_folder.document_name,
                chunk_results
            )
            
            # 目次↔本文整合のポストプロセス
            formatted_content = self._postprocess_formatted_markdown(
                formatted_content=formatted_content,
                document_name=doc_folder.document_name,
            )
            
            # Step 4: 用語抽出
            terms = self._extract_terms_from_content(
                formatted_content, doc_folder.document_name
            )
            
            # 品質チェック
            if not self.skip_quality_check:
                report = self.quality_checker.check(
                    input_content=input_content,
                    output_content=formatted_content,
                    document_name=doc_folder.document_name,
                )
                self.quality_reports.append(report)
                self.quality_checker.print_report(report)
                
                if not report.passed:
                    self.failed_documents.append(doc_folder.document_name)
            
            # Save
            self._save_formatted_output(doc_folder, formatted_content, input_content, terms)
            
            self.logger.info(
                f"✅ Formatted (Excel/sheets): {doc_folder.document_name} (用語: {len(terms)}件)"
            )
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Excel sheet formatting failed for {doc_folder.document_name}: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            return False
    
    def _split_excel_by_sheet(self, content: str) -> List[str]:
        """Excelファイルを## シート: で分割（大きすぎるシートは更に分割）.
        
        Args:
            content: Excelから変換されたMarkdown
            
        Returns:
            シートごとのチャンクリスト（大きいシートは分割済み）
        """
        lines = content.split('\n')
        
        sheets = []
        current_sheet = []
        header_lines = []  # ファイルヘッダー（タイトル、メタ情報）
        found_first_sheet = False
        
        for line in lines:
            # シート見出しを検出
            if line.strip().startswith('## シート:'):
                if current_sheet:
                    if found_first_sheet:
                        sheets.append('\n'.join(current_sheet))
                    else:
                        # 最初のシート前はヘッダーとして保存
                        header_lines = current_sheet.copy()
                current_sheet = [line]
                found_first_sheet = True
            else:
                current_sheet.append(line)
        
        # 最後のシートを追加
        if current_sheet and found_first_sheet:
            sheets.append('\n'.join(current_sheet))
        
        # 空シート・小さすぎるシート（100文字未満）は前のシートとマージ
        merged_sheets = []
        for sheet in sheets:
            sheet_stripped = sheet.strip()
            # 空シートまたは「(空のシート)」のみのシート
            if len(sheet_stripped) < 100 or '*(空のシート)*' in sheet:
                if merged_sheets:
                    merged_sheets[-1] += '\n\n' + sheet
                else:
                    merged_sheets.append(sheet)
            else:
                merged_sheets.append(sheet)
        
        # 各シートにヘッダー情報を付与（ドキュメント名等の情報を維持）
        if header_lines:
            header = '\n'.join(header_lines)
            merged_sheets = [header + '\n\n' + sheet for sheet in merged_sheets]
        
        # 大きすぎるシート（6万文字超）をカテゴリ/業務名で分割
        final_chunks = []
        for i, sheet in enumerate(merged_sheets):
            if len(sheet) > EXCEL_CHUNK_THRESHOLD:
                self.logger.info(f"  📊 シート{i+1}が大きい ({len(sheet):,}文字) → カテゴリ単位で分割")
                sub_chunks = self._split_large_sheet_by_category(sheet)
                self.logger.info(f"    → {len(sub_chunks)}チャンクに分割")
                final_chunks.extend(sub_chunks)
            else:
                final_chunks.append(sheet)
        
        # シートサイズのログ出力
        for i, chunk in enumerate(final_chunks):
            self.logger.debug(f"  チャンク{i+1}: {len(chunk):,}文字")
        
        return final_chunks
    
    def _split_large_sheet_by_category(self, sheet: str) -> List[str]:
        """大きなシートをカテゴリ/業務名の変更点で分割.
        
        テーブルのカテゴリ列または業務名列が変わる箇所を区切りとして分割します。
        テーブルヘッダーは各チャンクに付与されます。
        
        Args:
            sheet: シートの内容
            
        Returns:
            分割されたチャンクのリスト
        """
        lines = sheet.split('\n')
        
        # テーブルヘッダー行を検出（最初の|で始まる2行）
        header_lines = []
        meta_lines = []
        data_lines = []
        in_table = False
        header_count = 0
        
        for i, line in enumerate(lines):
            if line.strip().startswith('|'):
                if header_count < 2:
                    header_lines.append(line)
                    header_count += 1
                else:
                    data_lines.append((i, line))
                in_table = True
            elif not in_table:
                meta_lines.append(line)
            else:
                # テーブル外の行（画像説明など）
                data_lines.append((i, line))
        
        # データ行が少ない場合は分割しない
        if len(data_lines) < 10:
            return [sheet]
        
        # カテゴリ/業務名の変更点を検出
        break_points = []  # (行インデックス, 理由, 名前)
        
        for idx, (line_num, line) in enumerate(data_lines):
            if not line.strip().startswith('|'):
                continue
            
            cells = [c.strip() for c in line.split('|')]
            if len(cells) < 4:
                continue
            
            # cells[0]は空文字（|の前）、cells[1]も空の場合が多い
            # カテゴリ列は通常2番目、業務名列は3番目
            category = cells[2] if len(cells) > 2 else ""
            task_name = cells[3] if len(cells) > 3 else ""
            
            # カテゴリが空でない = 新しいカテゴリ開始
            if category and category.strip() not in ('', '-', '　'):
                break_points.append((idx, 'category', category[:20]))
            # 業務名が空でない = 新しい業務開始
            elif task_name and task_name.strip() not in ('', '-', '　'):
                break_points.append((idx, 'task', task_name[:20]))
        
        # 分割点が少ない場合は均等分割
        if len(break_points) < 2:
            return self._split_sheet_by_size(sheet, meta_lines, header_lines, data_lines)
        
        # チャンクに分割（3万文字 + 次の区切り点で分割）
        chunks = []
        current_chunk_start = 0
        current_size = len('\n'.join(meta_lines + header_lines))
        
        for bp_idx, (data_idx, bp_type, bp_name) in enumerate(break_points):
            # 現在位置までのサイズを計算
            chunk_lines = [line for (_, line) in data_lines[current_chunk_start:data_idx]]
            chunk_size = sum(len(line) + 1 for line in chunk_lines)
            
            if current_size + chunk_size >= EXCEL_CHUNK_THRESHOLD and current_chunk_start < data_idx:
                # チャンクを保存
                chunk_data = [line for (_, line) in data_lines[current_chunk_start:data_idx]]
                chunk_content = '\n'.join(meta_lines + header_lines + chunk_data)
                chunks.append(chunk_content)
                
                # リセット
                current_chunk_start = data_idx
                current_size = len('\n'.join(meta_lines + header_lines))
        
        # 残りを最後のチャンクに
        if current_chunk_start < len(data_lines):
            chunk_data = [line for (_, line) in data_lines[current_chunk_start:]]
            chunk_content = '\n'.join(meta_lines + header_lines + chunk_data)
            chunks.append(chunk_content)
        
        # チャンクが1つしかない場合は元のシートをそのまま返す
        if len(chunks) <= 1:
            return [sheet]
        
        return chunks
    
    def _split_sheet_by_size(
        self,
        sheet: str,
        meta_lines: List[str],
        header_lines: List[str],
        data_lines: List[Tuple[int, str]]
    ) -> List[str]:
        """シートをサイズで均等分割（カテゴリ分割が使えない場合のフォールバック）.
        
        Args:
            sheet: 元のシート
            meta_lines: メタ情報行
            header_lines: テーブルヘッダー行
            data_lines: データ行のリスト
            
        Returns:
            分割されたチャンクのリスト
        """
        if not data_lines:
            return [sheet]
        
        # ヘッダーサイズ
        header_size = len('\n'.join(meta_lines + header_lines))
        target_size = EXCEL_CHUNK_THRESHOLD - header_size
        
        chunks = []
        current_chunk = []
        current_size = 0
        
        for line_num, line in data_lines:
            line_size = len(line) + 1
            
            if current_size + line_size > target_size and current_chunk:
                chunk_content = '\n'.join(meta_lines + header_lines + current_chunk)
                chunks.append(chunk_content)
                current_chunk = [line]
                current_size = line_size
            else:
                current_chunk.append(line)
                current_size += line_size
        
        if current_chunk:
            chunk_content = '\n'.join(meta_lines + header_lines + current_chunk)
            chunks.append(chunk_content)
        
        return chunks if chunks else [sheet]
    
    def _process_chunks_parallel_excel(
        self,
        sheets: List[str],
        document_name: str,
        images_info: str
    ) -> List[ChunkResult]:
        """Excelシートを並列で処理（テーブル維持指示付き）.
        
        Args:
            sheets: シートのリスト
            document_name: ドキュメント名
            images_info: 画像情報
            
        Returns:
            ChunkResultのリスト
        """
        results: List[ChunkResult] = [None] * len(sheets)
        
        def process_sheet(index: int, sheet: str) -> ChunkResult:
            """単一シートを処理."""
            try:
                return self._format_single_chunk_excel(
                    index, sheet, document_name, images_info
                )
            except Exception as e:
                self.logger.warning(f"  ⚠️ Sheet {index + 1} failed: {e}")
                return ChunkResult(
                    chunk_index=index,
                    summary="",
                    toc="",
                    body_content="",
                    full_content="",
                    success=False,
                    error=str(e)
                )
        
        # 並列処理
        max_workers = int(os.getenv("ENHANCE_PARALLEL_WORKERS", str(MAX_PARALLEL_WORKERS)))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(process_sheet, i, sheet): i
                for i, sheet in enumerate(sheets)
            }
            
            completed = 0
            for future in as_completed(futures):
                index = futures[future]
                try:
                    result = future.result()
                    results[index] = result
                    completed += 1
                    self.logger.info(f"  📊 Sheet {index + 1}/{len(sheets)} 完了")
                except Exception as e:
                    self.logger.warning(f"  ⚠️ Sheet {index + 1} error: {e}")
                    results[index] = ChunkResult(
                        chunk_index=index,
                        summary="",
                        toc="",
                        body_content="",
                        full_content="",
                        success=False,
                        error=str(e)
                    )
        
        return results
    
    def _format_single_chunk_excel(
        self,
        chunk_index: int,
        chunk_content: str,
        document_name: str,
        images_info: str
    ) -> ChunkResult:
        """単一Excelシートをきれい化（テーブル維持指示付き）.
        
        Args:
            chunk_index: シートのインデックス
            chunk_content: シートの内容
            document_name: ドキュメント名
            images_info: 画像情報
            
        Returns:
            ChunkResult
        """
        client = self._get_openai_client()
        model = self._get_model_name()
        
        # 入力文字数と期待出力文字数を計算
        input_chars = len(chunk_content)
        expected_min = int(input_chars * 0.80)
        expected_max = int(input_chars * 1.20)
        
        # Excel/テーブル専用のプロンプト
        prompt = f"""以下のテンプレートに従って、Excelシートの内容を整形してください。

⚠️⚠️⚠️ 最重要: 出力は{expected_min:,}〜{expected_max:,}文字の範囲にすること ⚠️⚠️⚠️
（入力: {input_chars:,}文字 → 出力も同等の長さを維持）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【⚠️ 重要: テーブル主体ドキュメントの処理ルール】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

このドキュメントはExcel/テーブル形式のデータです。以下のルールを厳守してください：

1. **テーブル構造は絶対に維持する**（要約しない、省略しない）
2. **全ての行・列を保持する**（1行・1列も削除しない）
3. **セル内の`<br>`タグはそのまま維持する**（改行情報を保持）
4. **テーブルデータを箇条書きに変換しない**
5. テーブルは**正しいMarkdown表形式**で出力：
   - **ヘッダー行がない表には、1行目の内容からヘッダーを推定して追加**
   - ヘッダー行の直後に区切り行（`|---|---|---|`）を必ず追加
   - BFCの場合の典型的なヘッダー例：
     `| チャネル | カテゴリ | 業務名 | No. | 備考 | 業務概要 | ... |`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【⚠️ 最重要: 見出しレベルのルール（ナレッジ化のため）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

6. **`###`（h3）はシート名のみに使用**
   - 1シートにつき1つの`###`のみ
   - 例: `### 訪問対面申込（代行書面申込）フロー`
7. **シート内のセクションは`####`（h4）以下で出力**
   - 例: `#### 業務一覧`、`#### 申込可否チェック`
8. **`###`を複数使わない**（後続のナレッジ化処理で`###`〜`###`の範囲を1単位として取得するため）
9. サマリーはシートの概要を3〜5個の箇条書きで記述
10. 「## シート: シート名」は「### シート名」に変換（シート名から日付を除去: `_240816`等は削除）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【テンプレート（整形ルール）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{self.template}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【元のドキュメント（Excelシート）】
ドキュメント名: {document_name}（シート {chunk_index + 1}）
入力文字数: {input_chars:,}文字（出力も{expected_min:,}〜{expected_max:,}文字にすること）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{chunk_content}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【利用可能な画像ファイル】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{images_info}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【出力指示】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ **テーブルの全行・全列を維持すること（絶対条件）**
⚠️ **出力文字数は入力の80%～120%を維持（絶対条件）**
⚠️ **全てのテーブルにヘッダー行と区切り行を付与すること（Markdown表形式）**
⚠️ **`###`はシート名のみ、シート内は`####`以下を使用（絶対条件）**

1. サマリー（このシートの概要を3〜5個の箇条書き）
2. 目次（このシート内の`####`見出し一覧、`###`は使わない）
3. 本文:
   - 最初に`### シート名`を1つだけ出力
   - シート内のセクションは`####`で出力
   - テーブルをそのまま維持して整形、ヘッダー行+区切り行を必ず追加
4. 関連資料は「なし」と記載

【正しい見出し構造の例】
```markdown
### 訪問対面申込フロー

#### 業務一覧
| チャネル | カテゴリ | 業務名 | No. | 業務概要 |
|----------|----------|--------|-----|----------|
| 訪問     | 申込受付 | 書面申込 | 1  | 申込書を受付 |

#### 申込可否チェック
...
```
"""
        
        system_prompt = self._get_system_prompt()
        
        # APIコール（gpt-5系は max_completion_tokens を使用）
        if "gpt-5" in model or "o1" in model or "o3" in model:
            params = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "max_completion_tokens": 100000,
            }
            # gpt-5.1の場合のみtemperature=0を追加
            if "5.1" in model or "5-1" in model:
                params["temperature"] = 0
            response = client.chat.completions.create(**params)
        else:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                max_completion_tokens=16384,
            )
        
        response_text = response.choices[0].message.content.strip()
        
        # コードブロックのラッパーを除去
        cleaned_content = self._strip_code_block(response_text)
        
        # サマリー・目次・本文を分離
        summary, toc, body = self._extract_sections(cleaned_content)
        
        return ChunkResult(
            chunk_index=chunk_index,
            summary=summary,
            toc=toc,
            body_content=body,
            full_content=cleaned_content,
            success=True
        )
    
    def _extract_terms_from_content(self, formatted_content: str, document_name: str) -> List[Dict]:
        """整形済みコンテンツのサマリー+目次から用語を抽出.
        
        チャンク処理の場合、各チャンクで用語を抽出すると重複が多くなるため、
        最終的なformatted.mdのサマリー+目次部分から用語を抽出します。
        
        Args:
            formatted_content: 整形済みのMarkdown
            document_name: ドキュメント名
            
        Returns:
            用語リスト
        """
        try:
            client = self._get_openai_client()
            model = self._get_model_name()
            
            # サマリーと目次の部分を抽出（本文全体は渡さない）
            summary_and_toc = self._extract_summary_and_toc_section(formatted_content)
            
            if not summary_and_toc:
                self.logger.warning(f"  ⚠️ サマリー・目次が見つかりません。用語抽出をスキップします。")
                return []
            
            prompt = f"""以下のドキュメントのサマリーと目次から、専門用語・略語を抽出してください。

【ドキュメント名】
{document_name}

【サマリー・目次】
{summary_and_toc}

【出力形式（JSON配列のみ）】
[
    {{"term": "用語1", "description": "説明（50文字以内）", "flag": 0}},
    {{"term": "用語2", "description": "説明", "flag": 1}}
]

【用語抽出ルール】
1. 業務で使用される専門用語・略語を抽出
2. 一般的な用語は含めない
3. 説明は簡潔に（50文字以内）
4. **定義が不明な用語は無理に説明しない**
5. **推測で説明を作成しない（嘘の情報は絶対禁止）**

【flagのルール】
- 意味・役割が明確に分かる用語 → 0
- 定義・意味があいまい／推測を含む可能性がある用語 → 1

JSON配列のみを出力してください。
"""
            
            if "gpt-5" in model or "o1" in model or "o3" in model or getattr(self, '_is_azure', False):
                params = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_completion_tokens": 2000,
                }
                if "5.1" in model or "5-1" in model:
                    params["temperature"] = 0
                response = client.chat.completions.create(**params)
            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_completion_tokens=2000,
                )
            
            result_text = response.choices[0].message.content.strip()
            
            # JSONパース
            if result_text.startswith("```"):
                result_text = re.sub(r"^```(?:json)?\n?", "", result_text)
                result_text = re.sub(r"\n?```$", "", result_text)
            
            terms = json.loads(result_text)
            return terms if isinstance(terms, list) else []
            
        except Exception as e:
            self.logger.warning(f"  ⚠️ 用語抽出失敗: {e}")
            return []
    
    def _extract_summary_and_toc_section(self, content: str) -> str:
        """コンテンツからサマリーと目次の部分だけを抽出.
        
        Args:
            content: 整形済みのMarkdown
            
        Returns:
            サマリーと目次の部分
        """
        lines = content.split('\n')
        result_lines = []
        in_section = False
        section_count = 0
        
        for line in lines:
            stripped = line.strip().lower()
            
            # サマリーまたは目次セクションの開始
            if stripped.startswith('## サマリー') or stripped.startswith('## 目次'):
                in_section = True
                result_lines.append(line)
                section_count += 1
                continue
            
            # 他の## セクションの開始で終了
            if in_section and stripped.startswith('## ') and not stripped.startswith('## サマリー') and not stripped.startswith('## 目次'):
                in_section = False
                # 両方取得したら終了
                if section_count >= 2:
                    break
                continue
            
            # --- で区切られている場合も終了判定
            if in_section and stripped == '---':
                in_section = False
                if section_count >= 2:
                    break
                continue
            
            if in_section:
                result_lines.append(line)
        
        return '\n'.join(result_lines)
    
    def _split_into_chunks(self, content: str) -> List[str]:
        """コンテンツをセクション（##）単位でチャンクに分割.
        
        Args:
            content: 分割するコンテンツ
            
        Returns:
            チャンクのリスト
        """
        # ## で始まるセクションを検出
        section_pattern = r'^## '
        lines = content.split('\n')
        
        sections = []
        current_section = []
        
        for line in lines:
            if re.match(section_pattern, line) and current_section:
                sections.append('\n'.join(current_section))
                current_section = [line]
            else:
                current_section.append(line)
        
        if current_section:
            sections.append('\n'.join(current_section))
        
        # セクションが少ない場合や、各セクションが大きすぎる場合は文字数で分割
        if len(sections) <= 1 or any(len(s) > CHUNK_SIZE * 2 for s in sections):
            return self._split_by_size(content)
        
        # セクションをチャンクサイズに収まるようにグループ化
        chunks = []
        current_chunk = []
        current_size = 0
        
        for section in sections:
            section_size = len(section)
            
            if current_size + section_size > CHUNK_SIZE and current_chunk:
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = [section]
                current_size = section_size
            else:
                current_chunk.append(section)
                current_size += section_size
        
        if current_chunk:
            chunks.append('\n\n'.join(current_chunk))
        
        return chunks
    
    def _split_by_size(self, content: str) -> List[str]:
        """文字数でコンテンツを分割.
        
        Args:
            content: 分割するコンテンツ
            
        Returns:
            チャンクのリスト
        """
        chunks = []
        lines = content.split('\n')
        current_chunk = []
        current_size = 0
        
        for line in lines:
            line_size = len(line) + 1  # +1 for newline
            
            if current_size + line_size > CHUNK_SIZE and current_chunk:
                chunks.append('\n'.join(current_chunk))
                current_chunk = [line]
                current_size = line_size
            else:
                current_chunk.append(line)
                current_size += line_size
        
        if current_chunk:
            chunks.append('\n'.join(current_chunk))
        
        return chunks
    
    def _process_chunks_parallel(
        self,
        chunks: List[str],
        document_name: str,
        images_info: str
    ) -> List[ChunkResult]:
        """チャンクを並列で処理.
        
        Args:
            chunks: チャンクのリスト
            document_name: ドキュメント名
            images_info: 画像情報
            
        Returns:
            ChunkResultのリスト
        """
        results: List[ChunkResult] = [None] * len(chunks)
        
        def process_chunk(index: int, chunk: str) -> ChunkResult:
            """単一チャンクを処理."""
            try:
                return self._format_single_chunk(index, chunk, document_name, images_info)
            except Exception as e:
                self.logger.warning(f"  ⚠️ Chunk {index + 1} failed: {e}")
                return ChunkResult(
                    chunk_index=index,
                    summary="",
                    toc="",
                    body_content="",
                    full_content="",
                    success=False,
                    error=str(e)
                )
        
        # 並列処理
        max_workers = int(os.getenv("ENHANCE_PARALLEL_WORKERS", str(MAX_PARALLEL_WORKERS)))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(process_chunk, i, chunk): i
                for i, chunk in enumerate(chunks)
            }
            
            completed = 0
            for future in as_completed(futures):
                index = futures[future]
                try:
                    result = future.result()
                    results[index] = result
                    completed += 1
                    self.logger.info(f"  📝 Chunk {index + 1}/{len(chunks)} 完了")
                except Exception as e:
                    self.logger.warning(f"  ⚠️ Chunk {index + 1} error: {e}")
                    results[index] = ChunkResult(
                        chunk_index=index,
                        summary="",
                        toc="",
                        body_content="",
                        full_content="",
                        success=False,
                        error=str(e)
                    )
        
        return results
    
    def _format_single_chunk(
        self,
        chunk_index: int,
        chunk_content: str,
        document_name: str,
        images_info: str
    ) -> ChunkResult:
        """単一チャンクをきれい化.
        
        元のテンプレートを使用し、Markdown直接出力（JSON不使用）。
        サマリー・目次も含めて生成し、後処理で分離する。
        
        Args:
            chunk_index: チャンクのインデックス
            chunk_content: チャンクの内容
            document_name: ドキュメント名
            images_info: 画像情報
            
        Returns:
            ChunkResult
        """
        client = self._get_openai_client()
        model = self._get_model_name()
        
        # 入力文字数と期待出力文字数を計算
        input_chars = len(chunk_content)
        expected_min = int(input_chars * 0.80)
        expected_max = int(input_chars * 1.20)
        
        # 元のテンプレートを使用したプロンプト
        prompt = f"""以下のテンプレートに従って、業務マニュアルを整形してください。

⚠️⚠️⚠️ 最重要: 出力は{expected_min:,}〜{expected_max:,}文字の範囲にすること ⚠️⚠️⚠️
（入力: {input_chars:,}文字 → 出力も同等の長さを維持）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【テンプレート（整形ルール）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{self.template}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【元のドキュメント（文字起こし）】
ドキュメント名: {document_name}（チャンク {chunk_index + 1}）
入力文字数: {input_chars:,}文字（出力も{expected_min:,}〜{expected_max:,}文字にすること）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{chunk_content}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【利用可能な画像ファイル】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{images_info}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【出力指示（重要）- 文字数維持が最重要】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ **出力文字数は入力文字数の80%～120%の範囲で維持すること（絶対条件）**
⚠️ **内容の要約・省略は絶対禁止**
⚠️ **内容の過剰な展開・加筆も禁止（元にない説明文を追加しない）**
⚠️ **「...」「（省略）」等の省略表現は使用禁止**

1. テンプレートの構造・ルールに従って整形
2. **内容を省略・要約しない（すべての情報を維持）**
3. **内容を過剰に展開しない（元の文章をそのまま使う）**
4. **表のデータは全行維持する（省略禁止）**
5. **表の内容を説明文に展開しない（そのまま表として維持）**
6. 箇条書きで構造的に記載（読みやすく）
7. 表のスクショ（table_xxx.png）→ Markdownの表に変換
8. 図のスクショ（img_xxx.png）→ そのまま残す
9. **図・表の説明は02の内容をもとに情報を落とさず整形（「図の説明:」等のラベルは不要）**
10. **装飾的な画像は省略する**（表紙デザイン、ロゴ、背景パターン、カラータイル、レイアウト要素等）
11. 引用記法 `>` は使用しない
10. ドキュメント情報・改訂履歴は削除
11. **用語集は作成しない**
12. **関連資料は「なし」と記載（適当に作成しない）**
13. Markdownのみを出力（説明不要）
14. **表をコードブロック（```）で囲まない**（直接Markdown表として出力）
15. **#N/A、空白行、重複行はそのまま維持**（Excelデータは元のまま保持）
16. **全てのスライド・ページ・セクションの内容を漏れなく出力する**

【見出しレベル規約 - 最重要】
| レベル | 用途 | 例 |
|--------|------|-----|
| `###` | 本文の大項目（章）**番号なし** | `### 見積作成の概要` |
| `####` | 本文の中項目 **番号なし** | `#### 見積入力ルール` |
| `#####` | **使用禁止** | - |

- **本文では `###`（大項目）と `####`（中項目）のみ使用**
- **`##` は固定セクション（サマリー/目次）のみ**
- **番号は付けない**（番号は後処理で自動付与される）
- **「Slide X」形式の見出しは禁止**（内容を反映した意味のある名前にする）
- 関連するスライドは1つの大項目（`###`）にまとめて良い

【大項目のグループ化 - 最重要】
- **このチャンク内で大項目（`###`）は2〜4個に抑える**（全体で10〜20個にするため）
- 関連する内容（複数のSlide）は1つの大項目に積極的にグループ化する
- 大項目名は抽象度を高くする（例: 「見積作成」「施工依頼」「アフター対応」など）
- 細かい手順や操作は中項目（`####`）として大項目の配下に入れる

【出力構成 - 必須】
このチャンクの出力は以下の2部構成にすること：

1. **サマリー（## サマリー）**: このチャンクの内容を3〜5個の箇条書きで要約（5行程度）
2. **本文（### 見出し〜）**: 元のドキュメントの全ての内容を整形して出力

⚠️⚠️⚠️ **超重要: 本文は絶対に省略しないこと** ⚠️⚠️⚠️
- 本文の文字数は入力の80%以上を維持すること
- サマリーは5行程度で短くても良いが、本文は元のドキュメントの全内容を含むこと
- 「本文が長すぎるので省略」などは絶対禁止
- 全てのテキスト、表、リストを出力すること
⚠️ **目次（## 目次）は出力しない**（後処理で自動生成される）
"""
        
        system_prompt = self._get_system_prompt()
        
        if "gpt-5" in model or "o1" in model or "o3" in model or getattr(self, '_is_azure', False):
            params = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "max_completion_tokens": 100000,
            }
            if "5.1" in model or "5-1" in model:
                params["temperature"] = 0
            response = client.chat.completions.create(**params)
        else:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                max_completion_tokens=16384,
            )
        
        response_text = response.choices[0].message.content.strip()
        
        # コードブロックのラッパーを除去
        cleaned_content = self._strip_code_block(response_text)
        
        # サマリー・目次・本文を分離
        summary, toc, body = self._extract_sections(cleaned_content)
        
        return ChunkResult(
            chunk_index=chunk_index,
            summary=summary,
            toc=toc,
            body_content=body,
            full_content=cleaned_content,
            success=True
        )
    
    def _merge_chunks(
        self,
        document_name: str,
        chunk_results: List[ChunkResult]
    ) -> str:
        """チャンクを結合し、統合サマリーを生成.
        
        新方針（2024-12-14）:
        - サマリー: 各チャンクのサマリーを統合してAIで再生成
        - 目次+本文: 単純縦積み（AI再生成なし）
        - 最終目次は生成しない（Step3で再生成）
        
        Args:
            document_name: ドキュメント名
            chunk_results: チャンク処理結果のリスト
            
        Returns:
            最終的なMarkdown
        """
        # 成功したチャンクのみ
        successful = [r for r in chunk_results if r.success]
        
        if not successful:
            return ""
        
        # 各チャンクのサマリーを収集（統合サマリー生成用）
        chunk_summaries = []
        for r in sorted(successful, key=lambda x: x.chunk_index):
            if r.summary:
                chunk_summaries.append(f"【チャンク{r.chunk_index + 1}】\n{r.summary}")
        
        # 統合サマリーを生成（サマリーのみ、目次は生成しない）
        unified_summary = self._generate_unified_summary(
            document_name,
            "\n\n".join(chunk_summaries)
        )
        
        # 各チャンクの「目次+本文」を単純縦積み
        chunk_contents = []
        for r in sorted(successful, key=lambda x: x.chunk_index):
            # 各チャンクのfull_contentからサマリー部分を除去
            chunk_body = self._extract_toc_and_body(r.full_content)
            if chunk_body:
                chunk_contents.append(chunk_body)
        
        # 目次+本文を区切り線で連結
        body_content = "\n\n---\n\n".join(chunk_contents)
        
        # 最終組み立て
        final_content = f"""# {document_name}

## サマリー
{unified_summary}

---

{body_content}

---

## 関連資料

なし
"""
        
        return final_content
    
    def _add_table_headers(self, content: str) -> str:
        """テーブルにヘッダー行と区切り行を追加する後処理.
        
        ヘッダー行（| --- |）がないテーブルを検出し、
        適切なヘッダー行を推定して追加する。
        
        Args:
            content: Markdown内容
            
        Returns:
            ヘッダー行が追加されたMarkdown
        """
        lines = content.split('\n')
        result = []
        i = 0
        
        while i < len(lines):
            line = lines[i]
            
            # テーブル行の開始を検出（|で始まる行）
            if line.strip().startswith('|') and not line.strip().startswith('| ---'):
                # 次の行が区切り行かチェック
                next_line = lines[i + 1] if i + 1 < len(lines) else ''
                
                if next_line.strip().startswith('| ---'):
                    # 既にヘッダー行がある → そのまま追加
                    result.append(line)
                else:
                    # ヘッダー行がない → 追加が必要
                    # テーブルの列数をカウント
                    col_count = line.count('|') - 1
                    if col_count < 1:
                        col_count = 1
                    
                    # BFCテーブルかどうかを判定（列数が多い場合）
                    if col_count >= 10:
                        # BFCメインテーブル用のヘッダー
                        header = self._generate_bfc_header(col_count)
                        separator = '| ' + ' | '.join(['---'] * col_count) + ' |'
                        result.append(header)
                        result.append(separator)
                    else:
                        # 通常のテーブル：最初の行をヘッダーとして扱い、区切り行を追加
                        result.append(line)
                        separator = '| ' + ' | '.join(['---'] * col_count) + ' |'
                        result.append(separator)
                        i += 1
                        continue
                    
                    result.append(line)
            else:
                result.append(line)
            
            i += 1
        
        return '\n'.join(result)
    
    def _generate_bfc_header(self, col_count: int) -> str:
        """BFCテーブル用のヘッダー行を生成.
        
        Args:
            col_count: 列数
            
        Returns:
            ヘッダー行の文字列
        """
        # BFCの典型的なヘッダー（列数に応じて調整）
        bfc_headers = [
            'チャネル', 'カテゴリ', '業務名', '備考', 'No.', '次No.',
            '業務概要', '新規/既存', '担当', '実施時期', '何を', '実施場所',
            'どのように', 'col14', 'col15', 'col16', 'col17', 'col18', 'col19', 'コメント',
            'システム1', 'システム2', 'システム3', 'システム4', 'システム5',
            'システム6', 'システム7', 'システム8', 'システム9', 'システム10',
        ]
        
        # 列数に合わせてヘッダーを生成
        headers = []
        for j in range(col_count):
            if j < len(bfc_headers):
                headers.append(bfc_headers[j])
            else:
                headers.append(f'col{j+1}')
        
        return '| ' + ' | '.join(headers) + ' |'
    
    def _extract_toc_and_body(self, chunk_content: str) -> str:
        """チャンクから目次と本文を抽出（サマリーを除去）.
        
        Args:
            chunk_content: チャンクの全内容
            
        Returns:
            目次+本文（サマリー部分を除去したもの）
        """
        lines = chunk_content.split('\n')
        result_lines = []
        in_summary = False
        skip_until_next_section = False
        
        for line in lines:
            stripped = line.strip()
            
            # タイトル行（# で始まる、ただし##や###は除く）はスキップ
            if stripped.startswith('# ') and not stripped.startswith('##'):
                continue
            
            # サマリーセクションの開始
            if stripped.lower().startswith('## サマリー') or stripped.lower() == '## summary':
                in_summary = True
                skip_until_next_section = True
                continue
            
            # サマリーセクションの終了条件:
            # 1. ## で始まる次のセクション（目次など）
            # 2. --- セパレータの後の本文開始
            # 3. ### で始まる本文の見出し
            if skip_until_next_section:
                # ## で始まるセクション（目次など）
                if stripped.startswith('## '):
                    skip_until_next_section = False
                    in_summary = False
                    result_lines.append(line)
                    continue
                # --- セパレータはスキップするが、次の行からは本文として扱う
                if stripped == '---':
                    skip_until_next_section = False
                    in_summary = False
                    continue
                # ### で始まる本文の見出し
                if stripped.startswith('### '):
                    skip_until_next_section = False
                    in_summary = False
                    result_lines.append(line)
                    continue
                # まだサマリー内なのでスキップ
                continue
            
            result_lines.append(line)
        
        return '\n'.join(result_lines).strip()
    
    def _generate_unified_summary(
        self,
        document_name: str,
        summaries_text: str
    ) -> str:
        """各チャンクのサマリーを統合して全体サマリーを生成.
        
        新方針（2024-12-14）:
        - サマリーのみ生成（目次は生成しない）
        - 目次は各チャンクのものがそのまま残る
        
        Args:
            document_name: ドキュメント名
            summaries_text: 各チャンクのサマリーの集約テキスト
            
        Returns:
            統合サマリー（箇条書き形式、## サマリーヘッダなし）
        """
        client = self._get_openai_client()
        model = self._get_model_name()
        
        prompt = f"""以下の各セクションのサマリーを統合して、ドキュメント全体の要約を生成してください。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【ドキュメント名】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{document_name}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【各セクションのサマリー】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{summaries_text if summaries_text else "（サマリー情報なし）"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【出力指示】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

以下の形式で出力してください（ヘッダなし、箇条書きのみ）：

- 要点1（ドキュメント全体の要点を3〜5個）
- 要点2
- 要点3
- 要点4（必要に応じて）
- 要点5（必要に応じて）

【ルール】
1. 各セクションのサマリーを統合して、ドキュメント全体の要点を抽出
2. 3〜5個の箇条書きで簡潔にまとめる
3. 具体的な内容を含める（抽象的すぎる表現は避ける）
4. 「## サマリー」などのヘッダは出力しない
5. 箇条書き（- で始まる行）のみを出力する
"""
        
        if "gpt-5" in model or "o1" in model or "o3" in model or getattr(self, '_is_azure', False):
            params = {
                "model": model,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "max_completion_tokens": 2000,
            }
            if "5.1" in model or "5-1" in model:
                params["temperature"] = 0
            response = client.chat.completions.create(**params)
        else:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                max_completion_tokens=1000,
            )
        
        result = response.choices[0].message.content.strip()
        return self._strip_code_block(result)
    
    def _strip_code_block(self, content: str) -> str:
        """Markdownコードブロックのラッパーを除去.
        
        Args:
            content: コンテンツ
            
        Returns:
            ラッパー除去後のコンテンツ
        """
        if content.startswith("```markdown"):
            content = content[len("```markdown"):].strip()
        if content.startswith("```"):
            content = content[3:].strip()
        if content.endswith("```"):
            content = content[:-3].strip()
        return content
    
    def _postprocess_formatted_markdown(self, formatted_content: str, document_name: str) -> str:
        """formatted.mdを保存前に正規化し、目次↔本文の対応を保証する.

        目的:
        - 見出しレベル規約を強制（# / ## / ### / ####）
        - 本文の見出しを再採番（1, 2, 3... / 1.1, 1.2...）
        - 目次を本文から再生成（目次にあるものは必ず本文にある）
        - 目次の大項目/中項目をリンク化する（クリック遷移可能）

        注意:
        - 情報を削らない（内容の省略は禁止）
        - ここでは主に「見出し階層」「目次」「リンク」を整える

        Args:
            formatted_content: AIが生成したMarkdown
            document_name: ドキュメント名（フォールバックタイトル等に使用）

        Returns:
            正規化後のMarkdown
        """
        if not formatted_content.strip():
            return formatted_content

        lines = formatted_content.splitlines()

        # 1) 先頭のH1（タイトル）を確定
        title_line = None
        for line in lines:
            if line.startswith("# "):
                title_line = line.strip()
                break
        if not title_line:
            title_line = f"# {document_name}"

        # 2) サマリー/目次/本文をセクション分割（既存の構造を尊重）
        def find_line_index(predicate) -> Optional[int]:
            for i, l in enumerate(lines):
                if predicate(l):
                    return i
            return None

        i_summary = find_line_index(lambda l: l.strip().lower().startswith("## サマリー") or l.strip().lower() == "## summary")
        i_toc = find_line_index(lambda l: l.strip().lower().startswith("## 目次") or l.strip().lower() == "## table of contents")

        if i_summary is None or i_toc is None or i_toc <= i_summary:
            # 期待する構造が無い場合は、既存ロジックで分離して最低限の整形のみ行う
            summary, toc, body = self._extract_sections(formatted_content)
            # 本文を再採番して正規化
            renumbered_body = self._renumber_body_headings(body)
            # 目次を本文から再生成
            toc_struct = self._derive_toc_from_renumbered_body(renumbered_body)
            toc_text = self._render_toc(toc_struct)
            return self._assemble_document(title_line, summary, toc_text, renumbered_body)

        # Summary section ends at toc start (exclusive), but skip separators and empty
        summary_lines = [l for l in lines[i_summary + 1:i_toc] if l.strip() != "---"]
        summary_text = "\n".join(summary_lines).strip()

        # TOC section ends at first '---' or first '###' after i_toc
        toc_end = None
        for j in range(i_toc + 1, len(lines)):
            stripped = lines[j].strip()
            if stripped == "---":
                toc_end = j
                break
            # 目次に `###` が含まれる場合があるので、リンクなしの `###` を本文開始とみなす
            if stripped.startswith("### ") and not stripped.startswith("### ["):
                toc_end = j
                break
        
        # それでも見つからない場合は、最初の本文見出し（番号付き###）を探す
        if toc_end is None:
            for j in range(i_toc + 1, len(lines)):
                stripped = lines[j].strip()
                # `### 1.` のような番号付き見出しを本文開始とみなす
                if re.match(r"^###\s+\d+\.", stripped):
                    toc_end = j
                    break
        
        if toc_end is None:
            toc_end = len(lines)

        body_lines = lines[toc_end:] if toc_end < len(lines) else []
        body_text_raw = "\n".join(body_lines).strip()

        # 3) 本文を再採番して正規化（チャンク結合時の番号重複を解消）
        renumbered_body = self._renumber_body_headings(body_text_raw)

        # 4) 目次を本文から再生成（目次にあるものは必ず本文にある状態を保証）
        toc_struct = self._derive_toc_from_renumbered_body(renumbered_body)
        toc_text = self._render_toc(toc_struct)

        # 5) 組み立て（サマリー/目次/本文の骨格は維持）
        return self._assemble_document(title_line, summary_text, toc_text, renumbered_body)

    def _assemble_document(self, title_line: str, summary_text: str, toc_text: str, body_text: str) -> str:
        """最終的なformatted.mdを組み立てる（骨格固定）。"""
        parts: List[str] = []
        parts.append(title_line.strip())
        parts.append("")
        parts.append("## サマリー")
        if summary_text.strip():
            parts.append(summary_text.strip())
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append("## 目次")
        parts.append("")
        if toc_text.strip():
            parts.append(toc_text.strip())
        parts.append("")
        parts.append("---")
        parts.append("")
        if body_text.strip():
            parts.append(body_text.strip())
        return "\n".join(parts).rstrip() + "\n"

    def _parse_toc_structure(self, toc_text: str) -> List[Dict]:
        """目次から大項目/中項目構造を抽出する.

        想定:
        - 大項目: `### 1. タイトル` または `### [1. タイトル](#...)`
        - 中項目: `- [1.1 タイトル](#...)`
        """
        if not toc_text.strip():
            return []

        struct: List[Dict] = []
        current = None

        big_pat = re.compile(r"^###\s+(?:\[(?P<label>.+?)\]\([^)]+\)|(?P<plain>.+))\s*$")
        sub_pat = re.compile(r"^\-\s+\[(?P<label>.+?)\]\([^)]+\)\s*$")
        num_pat = re.compile(r"^(?P<num>\d+)(?:\.(?P<sub>\d+))?\s*(?P<title>.*)$")

        for raw_line in toc_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            m_big = big_pat.match(line)
            if m_big:
                label = (m_big.group("label") or m_big.group("plain") or "").strip()
                m_num = num_pat.match(label)
                if not m_num or m_num.group("sub") is not None:
                    # 大項目は "1." のように小数部なしを期待
                    continue
                major = int(m_num.group("num"))
                title = (m_num.group("title") or "").strip()
                # 先頭の記号（"."等）を除去して見た目を正す（例: "1. . タイトル"）
                title = title.lstrip(".・-–— ").strip()
                current = {"major": major, "title": title, "subs": []}
                struct.append(current)
                continue

            m_sub = sub_pat.match(line)
            if m_sub and current is not None:
                label = (m_sub.group("label") or "").strip()
                m_num = num_pat.match(label)
                if not m_num or m_num.group("sub") is None:
                    continue
                major = int(m_num.group("num"))
                minor = int(m_num.group("sub"))
                title = (m_num.group("title") or "").strip()
                title = title.lstrip(".・-–— ").strip()
                # 番号が大項目と一致しない場合でも、情報は保持（後で探索に使う）
                current["subs"].append({"major": major, "minor": minor, "title": title})
                continue

        # major順に安定化
        struct.sort(key=lambda x: x["major"])
        for g in struct:
            g["subs"].sort(key=lambda s: (s["major"], s["minor"]))
        return struct

    def _render_toc(self, toc_struct: List[Dict]) -> str:
        """目次構造から、リンク付き目次テキストを生成する.

        仕様:
        - 大項目: `### [1. タイトル](#sec-1)`
        - 中項目: `- [1.1 タイトル](#sec-1-1)`
        """
        lines: List[str] = []
        for g in sorted(toc_struct, key=lambda x: x["major"]):
            major = g["major"]
            title = g.get("title", "").strip()
            label = f"{major}. {title}".strip()
            lines.append(f"### [{label}](#sec-{major})")
            for s in g.get("subs", []):
                if s.get("major") != major:
                    continue
                minor = s["minor"]
                stitle = s.get("title", "").strip()
                slabel = f"{major}.{minor} {stitle}".strip()
                lines.append(f"- [{slabel}](#sec-{major}-{minor})")
            lines.append("")
        return "\n".join(lines).rstrip()

    def _derive_toc_from_body_ranges(self, normalized_body: str, toc_struct: List[Dict]) -> List[Dict]:
        """正規化後本文から、目次構造（大項目/中項目）を再導出する.

        目的:
        - 目次に書いた中項目が本文に存在しない（リンク切れ）問題を防ぐ
        - 同じ sec-1-3 のような重複IDが本文内に複数あっても、
          「該当大項目の範囲内」にあるものだけを採用する

        仕様:
        - 大項目の範囲: `<a id="sec-M"></a>` から次の `<a id="sec-(M+1)"></a>`（または次の大項目）まで
        - その範囲内にある `<a id="sec-M-N"></a>` + 直後の `#### M.N ...` を中項目として採用

        Args:
            normalized_body: `_normalize_body_with_toc()` 後の本文
            toc_struct: 期待する大項目（major番号・タイトル）

        Returns:
            本文に実在する項目だけで構成した toc_struct
        """
        if not normalized_body.strip() or not toc_struct:
            return []

        lines = normalized_body.splitlines()

        # majorアンカー位置を取得（同一majorが複数回出る場合は最初の出現のみ採用）
        major_first_pos: Dict[int, int] = {}
        major_pat = re.compile(r'^<a id="sec-(?P<maj>\d+)"></a>$')
        for i, line in enumerate(lines):
            m = major_pat.match(line.strip())
            if m:
                maj = int(m.group("maj"))
                if maj not in major_first_pos:
                    major_first_pos[maj] = i

        if not major_first_pos:
            return []

        # major順に並べ、rangeを作る
        major_positions: List[Tuple[int, int]] = sorted(major_first_pos.items(), key=lambda x: x[1])
        # toc_structのタイトルを引けるようにする
        major_title_map = {g["major"]: g.get("title", "").strip() for g in toc_struct}

        # sub抽出
        sub_anchor_pat = re.compile(r'^<a id="sec-(?P<maj>\d+)-(?P<min>\d+)"></a>$')
        sub_heading_pat = re.compile(r"^####\s+(?P<maj>\d+)\.(?P<min>\d+)\s+(?P<title>.+)\s*$")

        derived: List[Dict] = []
        for idx, (maj, start_i) in enumerate(major_positions):
            end_i = major_positions[idx + 1][1] if idx + 1 < len(major_positions) else len(lines)

            # 大項目タイトルを本文から取れれば優先（`### M. タイトル`）
            title = major_title_map.get(maj, "").strip() or f"{maj}章"
            for j in range(start_i, min(start_i + 20, end_i)):
                if lines[j].strip().startswith(f"### {maj}."):
                    title = lines[j].strip()[len(f"### {maj}."):].strip()
                    break

            subs: List[Dict] = []
            seen_subs: set[Tuple[int, int]] = set()
            k = start_i
            while k < end_i:
                m = sub_anchor_pat.match(lines[k].strip())
                if m and int(m.group("maj")) == maj:
                    mi = int(m.group("min"))
                    # 次の行（または数行先）に該当見出しがあるか確認
                    found_title = None
                    for look_ahead in range(1, 6):
                        if k + look_ahead >= end_i:
                            break
                        mh = sub_heading_pat.match(lines[k + look_ahead].strip())
                        if mh and int(mh.group("maj")) == maj and int(mh.group("min")) == mi:
                            found_title = mh.group("title").strip()
                            break
                    if found_title is not None and (maj, mi) not in seen_subs:
                        subs.append({"major": maj, "minor": mi, "title": found_title})
                        seen_subs.add((maj, mi))
                k += 1

            if subs:
                derived.append({"major": maj, "title": title, "subs": subs})
            else:
                # 中項目が無い場合でも大項目だけは残す（章として使える）
                derived.append({"major": maj, "title": title, "subs": []})

        # 本文の出現順を維持（rangeベースの意図に合わせる）
        return derived

    def _normalize_body_with_toc(self, body_text: str, toc_struct: List[Dict]) -> Tuple[str, List[Dict]]:
        """本文を正規化する（見出しレベル・大項目挿入・アンカー付与）。

        Args:
            body_text: `---` 以降の本文
            toc_struct: 目次から抽出した構造（空なら本文から導出する）

        Returns:
            (normalized_body, derived_toc_struct)
        """
        if not body_text.strip():
            return body_text, []

        lines = body_text.splitlines()

        # 注意: チャンク処理では各チャンク内に「## 関連資料」が混入することがあり、
        # それをここで「文末」と誤判定すると、後続チャンクが未正規化になる。
        # そのため、本文中の「## 関連資料」ブロックは原則除去する（文末に1回だけ置く運用は別途）。
        main_lines = lines
        footer_lines: List[str] = []

        # 1) 本文内のH1/H2を排除（ルール違反を解消）
        cleaned: List[str] = []
        i = 0
        while i < len(main_lines):
            l = main_lines[i]
            if l.startswith("# "):
                # チャンクタイトル等の重複H1は除去（内容ではないため）
                i += 1
                continue

            if l.strip().startswith("## 関連資料"):
                # 「## 関連資料」ブロックをスキップ（次の見出しが来るまで）
                i += 1
                while i < len(main_lines):
                    nxt = main_lines[i]
                    if nxt.startswith("# ") or nxt.startswith("## ") or nxt.startswith("### ") or nxt.startswith("#### "):
                        break
                    i += 1
                continue

            if l.startswith("## "):
                # 本文に##は使わない → ####へ降格（情報は維持）
                cleaned.append("#### " + l[3:].strip())
                i += 1
                continue

            cleaned.append(l)
            i += 1

        # 2) 目次構造が無い場合、本文から暫定構造を導出（###/####の番号から）
        derived_struct: List[Dict] = []
        if not toc_struct:
            derived_struct = self._derive_toc_from_body("\n".join(cleaned))
            toc_struct = derived_struct

        # 3) 既存の中項目見出し（### 1.1 ...）を####へ降格し、番号に合うアンカーを付与
        num_sub_h3 = re.compile(r"^###\s+(?P<maj>\d+)\.(?P<min>\d+)\s+(?P<title>.+)\s*$")
        num_sub_h4 = re.compile(r"^####\s+(?P<maj>\d+)\.(?P<min>\d+)\s+(?P<title>.+)\s*$")

        def ensure_anchor_before(out: List[str], anchor_id: str) -> None:
            if out and out[-1].strip() == f'<a id="{anchor_id}"></a>':
                return
            out.append(f'<a id="{anchor_id}"></a>')

        out_lines: List[str] = []

        # index for insertion by first subheading occurrence
        # We'll do a single pass, inserting big headings when we encounter first sub for that major.
        inserted_big: set[int] = set()
        majors_in_toc = [g["major"] for g in toc_struct]
        major_to_title = {g["major"]: g.get("title", "").strip() for g in toc_struct}

        # Track if we've seen any sub of a major to know insertion point
        for l in cleaned:
            m3 = num_sub_h3.match(l.strip())
            m4 = num_sub_h4.match(l.strip())
            if m3 or m4:
                maj = int((m3 or m4).group("maj"))
                mi = int((m3 or m4).group("min"))
                title = (m3 or m4).group("title").strip()

                # 大項目を本文へ挿入（目次にあるもののみ）
                if maj in majors_in_toc and maj not in inserted_big:
                    inserted_big.add(maj)
                    ensure_anchor_before(out_lines, f"sec-{maj}")
                    big_title = major_to_title.get(maj, "").strip() or f"{maj}章"
                    out_lines.append(f"### {maj}. {big_title}")
                    out_lines.append("")

                ensure_anchor_before(out_lines, f"sec-{maj}-{mi}")
                out_lines.append(f"#### {maj}.{mi} {title}")
                continue

            out_lines.append(l)

        # 4) 大項目が一度も挿入されなかった major があれば、末尾に追加（安全策）
        for maj in majors_in_toc:
            if maj not in inserted_big:
                ensure_anchor_before(out_lines, f"sec-{maj}")
                big_title = major_to_title.get(maj, "").strip() or f"{maj}章"
                out_lines.append(f"### {maj}. {big_title}")
                out_lines.append("")

        normalized = "\n".join(out_lines).strip()
        return normalized, derived_struct

    def _derive_toc_from_body(self, body_text: str) -> List[Dict]:
        """本文から暫定の目次構造を導出する（番号付き####を基準）。"""
        struct_map: Dict[int, Dict] = {}
        pat = re.compile(r"^####\s+(?P<maj>\d+)\.(?P<min>\d+)\s+(?P<title>.+)\s*$")
        for line in body_text.splitlines():
            m = pat.match(line.strip())
            if not m:
                continue
            maj = int(m.group("maj"))
            mi = int(m.group("min"))
            title = m.group("title").strip()
            g = struct_map.setdefault(maj, {"major": maj, "title": f"{maj}章", "subs": []})
            g["subs"].append({"major": maj, "minor": mi, "title": title})
        struct = list(struct_map.values())
        struct.sort(key=lambda x: x["major"])
        for g in struct:
            g["subs"].sort(key=lambda s: (s["major"], s["minor"]))
        return struct

    def _renumber_body_headings(self, body_text: str) -> str:
        """本文の見出しを再採番し、アンカーを付与する.

        目的:
        - チャンク結合時の番号重複を解消（各チャンクで1.1, 1.2...が繰り返されるのを防ぐ）
        - 見出しレベルを正規化（## → ####、### で番号なし → 大項目として採番）
        - 各見出しの直前にアンカー（<a id="sec-N"></a> 等）を挿入

        Args:
            body_text: 本文テキスト

        Returns:
            再採番後の本文テキスト
        """
        if not body_text.strip():
            return body_text

        lines = body_text.splitlines()
        out_lines: List[str] = []

        # 現在の大項目番号と中項目番号
        current_major = 0
        current_minor = 0
        # 大項目のタイトルを保持（中項目を総称する名前として使う）
        major_titles: Dict[int, str] = {}

        # 見出しパターン
        # ### で始まる大項目（番号付き or 番号なし）
        # リンク形式も対応: ### [1. タイトル](#sec-1) → タイトル を抽出
        h3_numbered = re.compile(r"^###\s+\d+\.?\s*(?P<title>.+)$")
        h3_link = re.compile(r"^###\s+\[(?P<num>\d+)\.?\s*(?P<title>[^\]]+)\]\([^)]+\)\s*$")
        h3_plain = re.compile(r"^###\s+(?P<title>.+)$")
        # #### で始まる中項目（番号付き or 番号なし）
        h4_numbered = re.compile(r"^####\s+\d+\.\d+\s*(?P<title>.+)$")
        h4_plain = re.compile(r"^####\s+(?P<title>.+)$")
        # 箇条書き形式の中項目リンク: - [1.1 タイトル](#sec-1-1)
        sub_link = re.compile(r"^-\s+\[(?P<maj>\d+)\.(?P<min>\d+)\s*(?P<title>[^\]]+)\]\([^)]+\)\s*$")
        # ## で始まる見出し（本文では禁止 → #### に降格）
        h2_pattern = re.compile(r"^##\s+(?P<title>.+)$")
        # # で始まるタイトル（本文では除去）
        h1_pattern = re.compile(r"^#\s+.+$")
        # アンカーパターン（既存のアンカーは除去して再付与）
        anchor_pattern = re.compile(r'^<a id="[^"]+"></a>$')
        # 関連資料セクション
        related_pattern = re.compile(r"^##?\s*関連資料\s*$", re.IGNORECASE)

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # 既存のアンカーは除去（再付与するため）
            if anchor_pattern.match(stripped):
                i += 1
                continue

            # # タイトル行は除去（重複防止）
            if h1_pattern.match(stripped):
                i += 1
                continue

            # 関連資料セクションはスキップ（末尾に1回だけ置く運用）
            if related_pattern.match(stripped):
                # 関連資料セクション全体をスキップ
                i += 1
                while i < len(lines):
                    nxt = lines[i].strip()
                    if nxt.startswith("#"):
                        break
                    i += 1
                continue

            # ## 見出し → #### に降格して中項目として扱う
            m_h2 = h2_pattern.match(stripped)
            if m_h2:
                title = m_h2.group("title").strip()
                # 固定セクション（サマリー/目次）はスキップ
                if title.lower() in ("サマリー", "summary", "目次", "table of contents"):
                    i += 1
                    continue
                # 大項目がまだない場合は作成
                if current_major == 0:
                    current_major = 1
                    current_minor = 0
                    out_lines.append(f'<a id="sec-{current_major}"></a>')
                    out_lines.append(f"### {current_major}. {title}")
                    out_lines.append("")
                    major_titles[current_major] = title
                else:
                    # 中項目として追加
                    current_minor += 1
                    out_lines.append(f'<a id="sec-{current_major}-{current_minor}"></a>')
                    out_lines.append(f"#### {current_major}.{current_minor} {title}")
                i += 1
                continue

            # ### 大項目（リンク形式も対応）
            m_h3_link = h3_link.match(stripped)
            m_h3_num = h3_numbered.match(stripped) if not m_h3_link else None
            m_h3_plain = h3_plain.match(stripped) if not m_h3_num and not m_h3_link else None
            if m_h3_link or m_h3_num or m_h3_plain:
                if m_h3_link:
                    title = m_h3_link.group("title").strip()
                else:
                    title = (m_h3_num or m_h3_plain).group("title").strip()
                # 先頭の記号を除去
                title = title.lstrip(".・-–— ").strip()
                # リンク部分を除去（残っている場合）
                if "](#" in title:
                    title = title.split("](#")[0].strip()
                # 新しい大項目
                current_major += 1
                current_minor = 0
                out_lines.append(f'<a id="sec-{current_major}"></a>')
                out_lines.append(f"### {current_major}. {title}")
                out_lines.append("")
                major_titles[current_major] = title
                i += 1
                continue

            # #### 中項目
            m_h4_num = h4_numbered.match(stripped)
            m_h4_plain = h4_plain.match(stripped) if not m_h4_num else None
            if m_h4_num or m_h4_plain:
                title = (m_h4_num or m_h4_plain).group("title").strip()
                title = title.lstrip(".・-–— ").strip()
                # 大項目がまだない場合は作成
                if current_major == 0:
                    current_major = 1
                    current_minor = 0
                    out_lines.append(f'<a id="sec-{current_major}"></a>')
                    out_lines.append(f"### {current_major}. 概要")
                    out_lines.append("")
                    major_titles[current_major] = "概要"
                current_minor += 1
                out_lines.append(f'<a id="sec-{current_major}-{current_minor}"></a>')
                out_lines.append(f"#### {current_major}.{current_minor} {title}")
                i += 1
                continue

            # 箇条書き形式の中項目リンク: - [1.1 タイトル](#sec-1-1)
            m_sub_link = sub_link.match(stripped)
            if m_sub_link:
                title = m_sub_link.group("title").strip()
                # 大項目がまだない場合は作成
                if current_major == 0:
                    current_major = 1
                    current_minor = 0
                    out_lines.append(f'<a id="sec-{current_major}"></a>')
                    out_lines.append(f"### {current_major}. 概要")
                    out_lines.append("")
                    major_titles[current_major] = "概要"
                current_minor += 1
                out_lines.append(f'<a id="sec-{current_major}-{current_minor}"></a>')
                out_lines.append(f"#### {current_major}.{current_minor} {title}")
                i += 1
                continue

            # その他の行はそのまま
            out_lines.append(line)
            i += 1

        return "\n".join(out_lines).strip()

    def _derive_toc_from_renumbered_body(self, renumbered_body: str) -> List[Dict]:
        """再採番後の本文から目次構造を導出する.

        Args:
            renumbered_body: _renumber_body_headings() で処理済みの本文

        Returns:
            目次構造（大項目/中項目のリスト）
        """
        if not renumbered_body.strip():
            return []

        struct: List[Dict] = []
        current_group: Optional[Dict] = None

        # パターン
        h3_pattern = re.compile(r"^###\s+(?P<num>\d+)\.\s+(?P<title>.+)$")
        h4_pattern = re.compile(r"^####\s+(?P<maj>\d+)\.(?P<min>\d+)\s+(?P<title>.+)$")

        for line in renumbered_body.splitlines():
            stripped = line.strip()

            # 大項目
            m_h3 = h3_pattern.match(stripped)
            if m_h3:
                major = int(m_h3.group("num"))
                title = m_h3.group("title").strip()
                current_group = {"major": major, "title": title, "subs": []}
                struct.append(current_group)
                continue

            # 中項目
            m_h4 = h4_pattern.match(stripped)
            if m_h4 and current_group is not None:
                maj = int(m_h4.group("maj"))
                minor = int(m_h4.group("min"))
                title = m_h4.group("title").strip()
                if maj == current_group["major"]:
                    current_group["subs"].append({
                        "major": maj,
                        "minor": minor,
                        "title": title
                    })

        return struct

    def _extract_sections(self, content: str) -> Tuple[str, str, str]:
        """AIの出力からサマリー・目次・本文を分離.
        
        Args:
            content: AI出力の全文
            
        Returns:
            (summary, toc, body_content) のタプル
        """
        lines = content.split('\n')
        
        summary_lines = []
        toc_lines = []
        body_lines = []
        
        current_section = None  # None, 'summary', 'toc', 'body'
        
        for line in lines:
            stripped = line.strip().lower()
            
            # セクション検出
            if stripped.startswith('## サマリー') or stripped.startswith('## summary'):
                current_section = 'summary'
                continue
            elif stripped.startswith('## 目次') or stripped.startswith('## table of contents'):
                current_section = 'toc'
                continue
            elif stripped.startswith('## ') or stripped.startswith('# '):
                # サマリー・目次以外の見出しは本文
                if current_section in ('summary', 'toc', None):
                    current_section = 'body'
                body_lines.append(line)
                continue
            elif stripped == '---':
                # 区切り線は本文の開始を示すことが多い
                if current_section in ('summary', 'toc'):
                    current_section = 'body'
                elif current_section == 'body':
                    body_lines.append(line)
                continue
            
            # ### や #### で始まる行は本文の見出し
            if stripped.startswith('### ') or stripped.startswith('#### '):
                current_section = 'body'
                body_lines.append(line)
                continue
            
            # 内容を追加
            if current_section == 'summary':
                summary_lines.append(line)
            elif current_section == 'toc':
                toc_lines.append(line)
            elif current_section == 'body':
                body_lines.append(line)
            else:
                # タイトル行（# で始まる1レベル見出し）はスキップ
                if line.strip().startswith('# ') and not line.strip().startswith('## '):
                    continue
                # それ以外は本文に
                body_lines.append(line)
        
        summary = '\n'.join(summary_lines).strip()
        toc = '\n'.join(toc_lines).strip()
        body = '\n'.join(body_lines).strip()
        
        return summary, toc, body
    
    def _generate_chapter_summaries(
        self,
        formatted_content: str,
        document_name: str
    ) -> List[Dict]:
        """formatted.mdから各章のサマリーを生成.
        
        Step3のディレクトリ構成精度向上のため、各###章の内容を要約する。
        
        Args:
            formatted_content: formatted.mdの内容
            document_name: ドキュメント名
            
        Returns:
            [{"title": "章タイトル", "summary": ["要点1", "要点2", ...], "line_start": N, "line_end": M}, ...]
        """
        lines = formatted_content.split('\n')
        
        # 本文の###を抽出（目次セクション外）
        chapters = []
        in_toc = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # 目次セクションの検出
            if stripped.lower().startswith('## 目次') or stripped.lower() == '## table of contents':
                in_toc = True
                continue
            
            # 目次セクション終了（---または他の##セクション）
            if in_toc:
                if stripped == '---':
                    in_toc = False
                    continue
                if stripped.startswith('## ') and '目次' not in stripped.lower():
                    in_toc = False
            
            # 本文の### を見つける（リンク形式 ### [...] はスキップ）
            if not in_toc and stripped.startswith('### ') and not stripped.startswith('### ['):
                title = stripped[4:].strip()
                chapters.append({
                    "title": title,
                    "line_start": i,
                    "line_end": None
                })
        
        # line_endを設定
        for i, ch in enumerate(chapters):
            if i + 1 < len(chapters):
                ch["line_end"] = chapters[i + 1]["line_start"] - 1
            else:
                ch["line_end"] = len(lines) - 1
        
        if not chapters:
            return []
        
        # 各章の冒頭を抽出してAIでサマリーを生成
        chapter_previews = []
        for ch in chapters:
            start = ch["line_start"]
            end = min(ch["line_end"], start + 30)  # 最大30行
            preview = '\n'.join(lines[start:end])
            chapter_previews.append({
                "title": ch["title"],
                "preview": preview[:1500],  # 最大1500文字
                "line_start": ch["line_start"] + 1,  # 1-indexed
                "line_end": ch["line_end"] + 1
            })
        
        # 章が多い場合は分割してAIに渡す
        if len(chapter_previews) <= 20:
            return self._summarize_chapters_batch(chapter_previews, document_name)
        else:
            # 20章ずつ処理
            all_summaries = []
            for i in range(0, len(chapter_previews), 20):
                batch = chapter_previews[i:i+20]
                batch_summaries = self._summarize_chapters_batch(batch, document_name)
                all_summaries.extend(batch_summaries)
            return all_summaries
    
    def _summarize_chapters_batch(
        self,
        chapter_previews: List[Dict],
        document_name: str
    ) -> List[Dict]:
        """章のバッチに対してサマリーを生成.
        
        Args:
            chapter_previews: [{"title": "...", "preview": "...", "line_start": N, "line_end": M}, ...]
            document_name: ドキュメント名
            
        Returns:
            [{"title": "...", "summary": ["要点1", "要点2", ...], "line_start": N, "line_end": M}, ...]
        """
        # まずバッチ全体で試行
        result = self._try_summarize_batch(chapter_previews, document_name)
        if result:
            return result
        
        # 失敗した場合、1章ずつ処理
        self.logger.info(f"  バッチ処理失敗、1章ずつ処理に切り替え...")
        results = []
        for ch in chapter_previews:
            single_result = self._try_summarize_batch([ch], document_name)
            if single_result:
                results.extend(single_result)
            else:
                # 最終フォールバック
                results.append({
                    "title": ch["title"],
                    "summary": ["（サマリー生成失敗）"],
                    "line_start": ch["line_start"],
                    "line_end": ch["line_end"]
                })
        return results
    
    def _try_summarize_batch(
        self,
        chapter_previews: List[Dict],
        document_name: str
    ) -> Optional[List[Dict]]:
        """バッチのサマリー生成を試行（失敗時はNone）.
        
        Args:
            chapter_previews: 章プレビューのリスト
            document_name: ドキュメント名
            
        Returns:
            成功時はサマリーリスト、失敗時はNone
        """
        client = self._get_openai_client()
        model = self._get_model_name()
        
        # プロンプト作成
        chapters_text = ""
        for i, ch in enumerate(chapter_previews):
            chapters_text += f"""
【章{i+1}】{ch['title']}
{ch['preview']}
---
"""
        
        prompt = f"""以下の{len(chapter_previews)}個の章について、それぞれのサマリーを生成してください。

【ドキュメント名】{document_name}

{chapters_text}

【出力形式（JSON）】
[
  {{
    "title": "章タイトル",
    "summary": [
      "この章で説明している主な内容（1行）",
      "具体的な操作や手順があれば記載（1行）",
      "関連するトピック・対象者（1行）"
    ]
  }},
  ...
]

【ルール】
1. 各章のサマリーは3項目の配列で出力（多すぎず少なすぎず）
2. 具体的な内容を含める（抽象的すぎる表現は避ける）
3. 章の内容を正確に反映する
4. JSONのみを出力（説明不要）
"""
        
        try:
            if "gpt-5" in model or "o1" in model or "o3" in model or getattr(self, '_is_azure', False):
                params = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_completion_tokens": 4000,
                }
                if "5.1" in model or "5-1" in model:
                    params["temperature"] = 0
                response = client.chat.completions.create(**params)
            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_completion_tokens=4000,
                )
            
            result_text = response.choices[0].message.content.strip()
            
            # JSONを抽出（複数パターン対応）
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                parts = result_text.split("```")
                if len(parts) >= 2:
                    result_text = parts[1].strip()
                    # 先頭に言語タグがあれば除去
                    if result_text.startswith("json"):
                        result_text = result_text[4:].strip()
            
            # JSON配列の開始位置を探す
            if not result_text.startswith("["):
                start_idx = result_text.find("[")
                if start_idx != -1:
                    result_text = result_text[start_idx:]
            
            summaries = json.loads(result_text)
            
            # line_start, line_end を追加
            for i, s in enumerate(summaries):
                if i < len(chapter_previews):
                    s["line_start"] = chapter_previews[i]["line_start"]
                    s["line_end"] = chapter_previews[i]["line_end"]
            
            return summaries
            
        except Exception as e:
            self.logger.warning(f"  章サマリー生成失敗: {e}")
            return None
    
    def _save_formatted_output(
        self,
        doc_folder: DocumentFolder,
        formatted_content: str,
        input_content: str,
        terms: Optional[List[Dict]] = None
    ) -> None:
        """整形結果を保存.
        
        Args:
            doc_folder: Document folder
            formatted_content: 整形されたコンテンツ
            input_content: 入力コンテンツ（品質レポート用）
            terms: 抽出された用語リスト
        """
        doc_folder.formatted_markdown_dir.mkdir(parents=True, exist_ok=True)
        
        # formatted.mdを保存
        output_path = doc_folder.enhanced_md_path
        output_path.write_text(formatted_content, encoding="utf-8")
        
        # terms.jsonを保存
        terms_path = doc_folder.formatted_markdown_dir / "terms.json"
        terms_data = {
            "extracted_at": datetime.now().isoformat(),
            "document_name": doc_folder.document_name,
            "terms": terms if terms else []
        }
        with open(terms_path, "w", encoding="utf-8") as f:
            json.dump(terms_data, f, ensure_ascii=False, indent=2)
        
        # chapter_summaries.jsonを生成・保存（Step3のディレクトリ構成精度向上用）
        try:
            chapter_summaries = self._generate_chapter_summaries(
                formatted_content, doc_folder.document_name
            )
            summaries_path = doc_folder.formatted_markdown_dir / "chapter_summaries.json"
            summaries_data = {
                "extracted_at": datetime.now().isoformat(),
                "document_name": doc_folder.document_name,
                "chapters": chapter_summaries
            }
            with open(summaries_path, "w", encoding="utf-8") as f:
                json.dump(summaries_data, f, ensure_ascii=False, indent=2)
            self.logger.info(f"  ✅ chapter_summaries.json 生成完了（{len(chapter_summaries)}章）")
        except Exception as e:
            self.logger.warning(f"  ⚠️ chapter_summaries.json 生成失敗: {e}")
        
        # 品質レポートも保存
        if not self.skip_quality_check and self.quality_reports:
            report_path = doc_folder.formatted_markdown_dir / "quality_report.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(self.quality_reports[-1].to_dict(), f, ensure_ascii=False, indent=2)
    
    def run(self, document_names: Optional[List[str]] = None) -> int:
        """Run the formatting process.
        
        Args:
            document_names: Optional list of specific documents to process.
                           If None, processes all documents.
            
        Returns:
            Exit code (0 for success)
        """
        self.logger.info("=" * 60)
        self.logger.info("KNOWLEDGE FORMATTER - Stage 2")
        self.logger.info("=" * 60)
        
        # Get documents to process
        if document_names:
            documents = [
                self.folder_manager.get_document_folder(name)
                for name in document_names
            ]
        else:
            documents = self.folder_manager.list_documents()
        
        # Filter to only those with:
        # - 02_transcribed_markdown/transcribed.md (or content.md for backwards compatibility)
        # - 04_images/ exists
        # - 03_formatted_markdown/formatted.md does NOT exist (not yet processed)
        def needs_processing(d: DocumentFolder) -> bool:
            has_transcribed = d.content_md_path.exists()
            has_formatted = d.enhanced_md_path.exists()
            return has_transcribed and not has_formatted
        
        documents = [d for d in documents if needs_processing(d)]
        
        self.results["statistics"]["total"] = len(documents)
        
        if not documents:
            self.logger.info("No documents to process")
            self._save_results()
            return 0

        workers = max(1, self.parallel_workers)
        self.logger.info(f"Processing {len(documents)} documents (workers={workers})")

        # 共有状態を保護するロック
        state_lock = threading.Lock()
        # 完了カウンタ（チェックポイント保存用）
        completed_count = 0

        def _process_one(args: tuple) -> None:
            nonlocal completed_count
            index, doc_folder = args
            self.logger.info(f"[{index}/{len(documents)}] {doc_folder.document_name}")

            # NOTE:
            # 進捗ファイル(format_progress.json)は「途中再開」用途だったが、
            # 03_formatted_markdown/formatted.md を再生成したいケースでは邪魔になる。
            # 既に上で「formatted.mdが存在しない」ものだけを抽出しているため、
            # progressのprocessed判定ではスキップしない（存在ベースを正とする）。
            success = self.format_document(doc_folder)

            with state_lock:
                if success:
                    self.results["statistics"]["success"] += 1
                    self.progress.setdefault("processed", []).append(doc_folder.document_name)
                    self.results["processed_documents"].append({
                        "document": doc_folder.document_name,
                        "status": "success",
                    })
                else:
                    self.results["statistics"]["failed"] += 1
                    self.results["failed"].append(doc_folder.document_name)
                    self.results["processed_documents"].append({
                        "document": doc_folder.document_name,
                        "status": "failed",
                    })

                completed_count += 1
                # チェックポイント保存（5件ごと）
                if completed_count % 5 == 0:
                    self._save_progress()
                    self._save_results()

        if workers == 1:
            # 逐次処理（従来の動作）
            for args in enumerate(documents, 1):
                _process_one(args)
        else:
            # ドキュメント間並列処理
            # 最初にクライアントを初期化してからスレッドを起動（競合を避ける）
            self._get_openai_client()
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_process_one, (i, doc)): doc
                    for i, doc in enumerate(documents, 1)
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        doc = futures[future]
                        self.logger.error(f"Unexpected error for {doc.document_name}: {exc}")
                        with state_lock:
                            self.results["statistics"]["failed"] += 1
                            self.results["failed"].append(doc.document_name)
                            self.results["processed_documents"].append({
                                "document": doc.document_name,
                                "status": "failed",
                            })

        # Final save
        self._save_progress()
        self._save_results()
        self._save_failed_list()
        self._print_summary()

        return 0
    
    def _save_failed_list(self) -> None:
        """Save list of failed documents for retry."""
        if self.failed_documents:
            failed_path = self.knowledge_dir / FOLDER_REPORTS / "quality_issues.json"
            failed_path.parent.mkdir(parents=True, exist_ok=True)
            
            data = {
                "timestamp": datetime.now().isoformat(),
                "failed_documents": self.failed_documents,
                "hint": "Run with --retry-failed to reprocess these documents",
            }
            with open(failed_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            self.logger.info(f"📝 Failed documents saved: {failed_path}")
    
    def _print_summary(self) -> None:
        """Print summary of the formatting."""
        stats = self.results["statistics"]
        
        print("\n" + "=" * 60)
        print("FORMATTING COMPLETE")
        print("=" * 60)
        
        print(f"\n📊 Statistics:")
        print(f"  Total documents: {stats['total']}")
        print(f"  ✅ Success: {stats['success']}")
        print(f"  ⏭️ Skipped: {stats['skipped']}")
        print(f"  ❌ Failed: {stats['failed']}")
        
        # 品質チェック結果サマリー
        if self.quality_reports:
            passed = sum(1 for r in self.quality_reports if r.passed)
            failed = len(self.quality_reports) - passed
            print(f"\n📋 Quality Check:")
            print(f"  ✅ Passed: {passed}")
            print(f"  ❌ Failed: {failed}")
            
            if self.failed_documents:
                print(f"\n⚠️ 品質チェック失敗ドキュメント:")
                for name in self.failed_documents[:10]:
                    print(f"  - {name}")
                if len(self.failed_documents) > 10:
                    print(f"  ... and {len(self.failed_documents) - 10} more")
                print(f"\n💡 --retry-failed で再処理できます")
        
        if self.results["failed"]:
            print(f"\n⚠️ 処理失敗ドキュメント:")
            for name in self.results["failed"][:10]:
                print(f"  - {name}")
            if len(self.results["failed"]) > 10:
                print(f"  ... and {len(self.results['failed']) - 10} more")
        
        print("\n" + "=" * 60)
