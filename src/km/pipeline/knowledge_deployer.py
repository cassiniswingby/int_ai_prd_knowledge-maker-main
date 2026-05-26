"""Stage 3: Knowledge Deployer - Integrate and deploy knowledge.

新設計版（2025年12月）:
- フォルダ構成: カテゴリ=フォルダ、ナレッジ=mdファイル
- 大容量ファイル: 章ごとに分割（AIなし、そのまま切り出し）
- マージ時: AIリライト（情報保持必須）
- 出力: mapping.json, readme.md, YAMLフロントマター付きmd
- 分割時: 02の内容をそのまま保持（AIなし）

主な変更点:
- ナレッジ単位: 1ナレッジ = 1 mdファイル（フォルダではない）
- 章フォルダ: 人間がわかりやすいカテゴリ名
- _global/, _chapter.md, _sources.md: 廃止 → readme.md, mapping.jsonに統合
- 画像: カテゴリフォルダ内の images/ に集約
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .knowledge_config import (
    FOLDER_FORMATTED_MARKDOWN,
    FOLDER_IMAGES,
    FILE_ENHANCED_MD,
)


logger = logging.getLogger(__name__)


# Output file constants
FILE_GLOSSARY = "00_用語集.md"
FILE_MAPPING = "mapping.json"
FILE_README = "readme.md"

# 統合処理の文字数閾値
MAX_INTEGRATION_CHARS = 80000


class KnowledgeDeployer:
    """Stage 3: Knowledge Deployer - 新設計版
    
    主な機能:
    - formatted.mdの分析（サマリー・目次の抽出）
    - カテゴリ・構成の提案（AI使用）
    - 大容量ファイルの章分割（AIなし）
    - 複数ファイルのマージ（AIリライト、情報保持必須）
    - mapping.json, readme.md, 用語集の生成
    """
    
    def __init__(
        self,
        target_dir: Path,
        output_dir: Path,
        *,
        generate_glossary: bool = True,
        generate_split_summary: bool = True,
        skip_confirmation: bool = False,
        include_existing_knowledge: bool = False,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Initialize the Knowledge Deployer.
        
        Args:
            target_dir: Path to the pre-knowledge directory
            output_dir: Path to the output knowledge directory
            generate_glossary: Whether to generate glossary
            generate_split_summary: Whether to generate summary for split chapters
            skip_confirmation: Whether to skip user confirmation (--force mode)
            include_existing_knowledge: Whether to include existing knowledge in restructure
            logger: Optional logger instance
        """
        self.target_dir = Path(target_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.generate_glossary = generate_glossary
        self.generate_split_summary = generate_split_summary
        self.skip_confirmation = skip_confirmation
        self.include_existing_knowledge = include_existing_knowledge
        self.logger = logger or self._build_logger()
        
        self._client = None
        self._model_name = None
        self._is_azure = False
        self._chapter_id_map: Dict[str, Dict] = {}  # 章IDマップ
        
        # 結果格納用
        self.results = self._fresh_results()
        self.mappings: List[Dict] = []
        self.categories: List[Dict] = []
        
        self.logger.info(
            f"KnowledgeDeployer initialized: target={self.target_dir}, output={self.output_dir}, include_existing={self.include_existing_knowledge}"
        )
    
    def _build_logger(self) -> logging.Logger:
        """Build a logger for the deployer."""
        log = logging.getLogger("km.knowledge_deployer")
        if not log.handlers:
            log.setLevel(logging.INFO)
            formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            
            console = logging.StreamHandler()
            console.setFormatter(formatter)
            console.setLevel(logging.INFO)
            log.addHandler(console)
        
        log.propagate = False
        return log
    
    def _get_openai_client(self):
        """Lazy-load OpenAI or Azure OpenAI client."""
        if self._client is None:
            try:
                from ..utils.openai_client import get_openai_client, get_model_name
                
                self._client, self._is_azure = get_openai_client(timeout=1500.0, purpose="chat")
                self._model_name = get_model_name(purpose="chat", is_azure=self._is_azure)
                self.logger.info(f"Using model: {self._model_name}, azure={self._is_azure}")
                
            except Exception as e:
                self.logger.error(f"Failed to initialize OpenAI client: {e}")
                raise RuntimeError(
                    "API key is required for deployment. "
                    "Set OPENAI_API_KEY or AZURE_OPENAI_API_KEY environment variable."
                )
        
        return self._client
    
    def _get_model_name(self) -> str:
        """Get model/deployment name for chat completions."""
        if self._model_name:
            return self._model_name
        from ..utils.openai_client import get_model_name
        return get_model_name(purpose="chat", is_azure=self._is_azure)
    
    def _fresh_results(self) -> Dict:
        """Create fresh results structure."""
        return {
            "metadata": {
                "start_time": datetime.now().isoformat(),
            },
            "statistics": {
                "total_files": 0,
                "total_categories": 0,
                "total_knowledge": 0,
                "split_files": 0,
                "merged_files": 0,
            },
            "errors": [],
        }
    
    def _compute_hash(self, content: str) -> str:
        """Compute MD5 hash of content."""
        return hashlib.md5(content.encode('utf-8')).hexdigest()
    
    # =========================================================================
    # Step 1: ファイル収集・分析
    # =========================================================================
    
    def _list_formatted_files(self) -> List[Tuple[str, Path]]:
        """List all formatted.md files in pre-knowledge directory.
        
        If include_existing_knowledge is True, also includes existing knowledge files.
        
        Returns:
            List of (document_name, formatted_md_path) tuples
        """
        files = []
        
        # pre-knowledge のファイルを収集
        for item in sorted(self.target_dir.iterdir()):
            if item.is_dir() and not item.name.startswith("_"):
                formatted_dir = item / FOLDER_FORMATTED_MARKDOWN
                formatted_md = formatted_dir / FILE_ENHANCED_MD
                if formatted_md.exists():
                    files.append((item.name, formatted_md))
        
        # 既存ナレッジも含める場合（抜本的変更モード）
        if self.include_existing_knowledge and self.output_dir.exists():
            self.logger.info("  既存ナレッジも入力に含めます")
            for md_file in sorted(self.output_dir.rglob("*.md")):
                # 特殊ファイルはスキップ
                if md_file.name in ["readme.md", "UPDATE_REPORT.md", "link_check_report.md", "00_用語集.md"]:
                    continue
                if md_file.name.startswith("00_"):
                    continue
                if "PR/" in str(md_file):
                    continue
                
                # 既存ナレッジをformatted.mdとして扱う
                doc_name = md_file.stem
                files.append((doc_name, md_file))
        
        return files
    
    def _extract_toc_structure(self, content: str) -> List[Dict]:
        """formatted.mdの目次から章構造を抽出.
        
        Args:
            content: formatted.mdの内容
            
        Returns:
            章のリスト [{title, level, line_start, line_end}, ...]
        """
        lines = content.split('\n')
        chapters = []
        
        # ### で始まる大見出しを探す（目次内の章番号付き見出し）
        in_toc = False
        toc_ended = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # 目次セクションの検出
            if stripped.lower().startswith('## 目次') or stripped.lower() == '## table of contents':
                in_toc = True
                continue
            
            # 目次セクション終了の検出（複数の条件）
            if in_toc and not toc_ended:
                # 条件1: 別の ## セクションが始まった
                if stripped.startswith('## ') and '目次' not in stripped.lower():
                    in_toc = False
                    toc_ended = True
                # 条件2: --- が見つかった（本文開始の区切り）
                elif stripped == '---':
                    in_toc = False
                    toc_ended = True
                # 条件3: アンカータグが見つかった（本文開始）
                elif stripped.startswith('<a id='):
                    in_toc = False
                    toc_ended = True
                # 条件4: リンクなしの ### が見つかった（本文の見出し）
                elif stripped.startswith('### ') and '[' not in stripped:
                    in_toc = False
                    toc_ended = True
            
            # 目次外の本文で ### を見つける（リンク付きは目次内なのでスキップ）
            if not in_toc and stripped.startswith('### ') and '[' not in stripped:
                # "### 1. 業務設計資料の概要" → "1. 業務設計資料の概要"
                title = stripped[4:].strip()
                # 番号を除去して章名を取得
                clean_title = re.sub(r'^\d+\.\s*', '', title).strip()
                
                chapters.append({
                    "original_title": title,
                    "clean_title": clean_title,
                    "level": 3,
                    "line_start": i + 1,  # 1-indexed
                    "line_end": None,  # 後で設定
                })
        
        # line_endを設定
        for i, ch in enumerate(chapters):
            if i + 1 < len(chapters):
                ch["line_end"] = chapters[i + 1]["line_start"] - 1
            else:
                ch["line_end"] = len(lines)
        
        return chapters
    
    def _extract_summary_from_formatted(self, content: str, file_name: str) -> Dict:
        """formatted.mdからサマリーと目次をパースして抽出（AI不要）.
        
        Args:
            content: Content of the formatted.md file
            file_name: Name of the file
            
        Returns:
            Dictionary with summary, chapters, keywords, char_count, hash
        """
        result = {
            "file_name": file_name,
            "summary": "",
            "chapters": [],
            "keywords": [],
            "char_count": len(content),
            "hash": self._compute_hash(content),
        }
        
        lines = content.split('\n')
        
        # サマリーセクションを抽出
        in_summary = False
        summary_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.lower().startswith('## サマリー') or stripped.lower() == '## summary':
                in_summary = True
                continue
            if in_summary:
                if stripped.startswith('## ') or stripped == '---':
                    break
                if stripped.startswith('-'):
                    summary_lines.append(stripped[1:].strip())
                elif stripped:
                    summary_lines.append(stripped)
        
        result["summary"] = '\n'.join(summary_lines)
        
        # 章構造を抽出
        result["chapters"] = self._extract_toc_structure(content)
        
        # キーワード抽出（システム名、略語、カタカナ語）
        keywords_pattern = r'[A-Z]{2,}|[A-Za-z]+(?:システム|ツール)|[ァ-ヴー]{3,}'
        found_keywords = re.findall(keywords_pattern, result["summary"])
        result["keywords"] = list(set(found_keywords))[:10]
        
        self.logger.debug(
            f"  Extracted from {file_name}: {result['char_count']:,} chars, "
            f"{len(result['chapters'])} chapters"
        )
        
        return result
    
    # =========================================================================
    # Step 2: カテゴリ・構成提案（AI使用）
    # =========================================================================
    
    def _propose_structure(
        self, 
        summaries: List[Dict], 
        file_contents: Dict[str, str],
        user_feedback: Optional[str] = None
    ) -> Dict:
        """AIを使ってカテゴリと構成を提案.
        
        統合版アプローチ（v2）:
        1. 章IDマップを作成
        2. AIにフォルダ構成と章の範囲割り当てを一括で聞く
        3. chapter_rangeに基づいてPythonで章を割り当て（漏れなし保証）
        
        Args:
            summaries: 各ファイルのサマリー情報
            file_contents: ファイル名→内容のマップ
            user_feedback: ユーザーからの構成に関するフィードバック（再構成時に使用）
            
        Returns:
            構成提案のDict
        """
        # Step 1: 章IDマップを作成
        self._build_chapter_id_map(summaries)
        
        # Step 2: AIにフォルダ構成と章の範囲割り当てを一括で聞く
        if user_feedback:
            self.logger.info("  ユーザーフィードバックを考慮して再構成中...")
        else:
            self.logger.info("  構成案と章割り当てを取得中...")
        structure_with_ranges = self._ask_ai_for_unified_structure(summaries, user_feedback)
        
        # Step 3: chapter_rangeに基づいてPythonで章を割り当て（漏れなし保証）
        self.logger.info("  章の割り当てを反映中...")
        proposal = self._assign_all_chapters(structure_with_ranges, summaries)
        
        return proposal
    
    def _build_chapter_id_map(self, summaries: List[Dict]) -> None:
        """全ファイルの章IDマップを構築."""
        chapter_id_map = {}
        
        for s in summaries:
            file_name = s['file_name']
            for i, ch in enumerate(s.get('chapters', [])):
                ch_id = self._make_chapter_id(file_name, i)
                chapter_id_map[ch_id] = {
                    "file_name": file_name,
                    "chapter": ch,
                    "index": i
                }
        
        self._chapter_id_map = chapter_id_map
        self.logger.info(f"  章IDマップ構築完了: {len(chapter_id_map)}章")

    def _make_chapter_id(self, file_name: str, index: int) -> str:
        """章IDを生成する。先頭20文字が同じファイル名でも衝突しないようハッシュを含める."""
        prefix = re.sub(r'[^\w]', '_', file_name[:20]).strip('_') or "doc"
        digest = hashlib.md5(file_name.encode("utf-8")).hexdigest()[:8]
        return f"{prefix}_{digest}_{index + 1:03d}"
    
    def _ask_ai_for_unified_structure(
        self, 
        summaries: List[Dict],
        user_feedback: Optional[str] = None
    ) -> Dict:
        """AIにフォルダ構成と章の範囲割り当てを一括で聞く（統合版）.
        
        改修（2024-12-14）:
        - サマリー情報を入力に含める
        - 各ナレッジファイルが3万文字以下になるように分割条件を追加
        
        Args:
            summaries: 各ファイルのサマリー情報
            user_feedback: ユーザーからの再構成に関するフィードバック
        """
        client = self._get_openai_client()
        model = self._get_model_name()
        
        # サマリー情報をプロンプト用に整形（サマリー + 全章タイトルを表示）
        summaries_text = ""
        for s in summaries:
            char_count = s.get('char_count', 0)
            file_name = s['file_name']
            chapters = s.get('chapters', [])
            summary_text = s.get('summary', '')
            
            # 全章タイトル一覧（番号付き + 各章サマリー）
            chapter_titles = []
            for i, ch in enumerate(chapters):
                # タイトルはそのまま使用（情報を落とさない）
                title = ch.get('original_title', '')
                # 各章サマリーがあれば追加（ディレクトリ構成の精度向上）
                chapter_summary = ch.get('chapter_summary', [])
                if chapter_summary:
                    summary_str = ' / '.join(chapter_summary[:2])  # 最大2項目
                    chapter_titles.append(f"  {i+1:03d}. {title}\n       → {summary_str}")
                else:
                    chapter_titles.append(f"  {i+1:03d}. {title}")
            
            # 全章を表示（大容量でも漏れなく）
            chapters_preview = '\n'.join(chapter_titles)
            
            # サマリーを含める（ディレクトリ構成の判断材料として重要）
            summaries_text += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【ファイル名】{file_name}
【文字数】{char_count:,}文字
【章の数】{len(chapters)}章
【サマリー】
{summary_text if summary_text else "（サマリーなし）"}
【全章タイトル一覧（→ は各章の内容）】
{chapters_preview}
"""
        
        prompt = f"""以下の{len(summaries)}個のドキュメントから、ナレッジベースの構成を設計してください。

{summaries_text}

【あなたの役割】
- カテゴリを決め、各カテゴリにどの元ファイルを配置するか決める
- 各ファイルをどのように分割するか、章番号の範囲で指定する
- ★★ 全ての章を漏れなく割り当てる ★★

【設計のゴール】
1. 人間がフォルダを見たときに、どこに何があるか直感的にわかる
2. 関連する章は同じmdファイルにまとめる
3. 1つのmdファイルは5〜20章程度が理想（多くても25章まで）

【ファイル・グループ名の命名ルール】★重要★
- ★ "file" フィールドは入力の元ファイル名をそのまま使う（変更しない）★
- ★ "name"（グループ名）は内容を反映した名前にする（日付・記号は削除）★
- 例: file="★250708_チャットのやり取りの王道パターン（13章）" → groups: [{{"name": "見積依頼と提示"}}, ...]
- 例: file="【25年度版】業務設計資料（43章）" → groups: [{{"name": "業務概要と体制"}}, ...]
- カテゴリ名（name）も内容がわかる名前にする

【カテゴリの考え方】
- 「業務マニュアル」「定型文・テンプレート」「工数調査」など、わかりやすい名前
- 3〜6カテゴリ程度

【出力順の考え方】★重要★
カテゴリ（フォルダ）の順番：
- 01から順に「上から読んでいくと理解しやすい順」に並べる
- 業務の基礎・全体像 → 個別業務 → 応用・例外 → 参考資料 の流れ
- 例: 01_業務マニュアル → 02_見積作成 → 03_施工管理 → 04_定型文 → 05_参考資料

各カテゴリ内のmdファイルの順番：
- 01から順に「理解しやすい順」に並べる
- 業務フローなら: 問合せ受付 → 見積作成 → 見積提示 → 受注 → 施工依頼 → 施工完了 → 請求
- 概念的なものなら: 概要・基礎 → 詳細・応用 → 例外・補足
- 章番号の順番ではなく、内容の論理的な順番で配置すること

【ファイル分割の考え方】★3万文字制限★
- 各ナレッジファイル（.md）は3万文字以下になるように分割する
- 30章以上のファイルは必ず分割する（1グループ10〜20章が理想）
- 5章以上あり内容が多様な場合も分割を検討する
- 1章あたり平均1,000〜2,000文字として、1グループ10〜20章程度が目安
- 例: 286章 → 15〜25グループに分割
- 例: 136章 → 8〜12グループに分割
- 例: 98章 → 6〜9グループに分割
- 例: 13章 → 内容の区切りに応じて2〜3グループに分割
- 全ての章が必ずいずれかのグループに含まれること（漏れなし・重複なし）

【ファイルマージの考え方】
- 章数が少ないファイル（10章以下）同士は、関連性があれば1つのmdにまとめる
- ただし関連性がなければ別々のファイルにする
- 例: 「AI活用案（8章）」と「AI活用結果（5章）」→ 関連性あり → 1つのmd「AI活用まとめ」にマージ
- 例: 「給湯器v1（6章）」と「給湯器v2（8章）」→ 関連性あり → 1つのmd「給湯器対応」にマージ

【出力形式（JSON）】
{{
    "title": "○○業務ナレッジベース",
    "description": "説明",
    "categories": [
        {{
            "order": 1,
            "id": "01_業務マニュアル",
            "name": "業務マニュアル",
            "description": "説明",
            "source_files": [
                {{
                    "file": "【25年度版】業務設計資料（286章）",
                    "split": true,
                    "groups": [
                        {{"name": "業務概要と体制", "chapter_range": "001-015"}},
                        {{"name": "基準と判定ルール", "chapter_range": "016-030"}},
                        {{"name": "サービス提供可否判定", "chapter_range": "031-050"}},
                        {{"name": "見積作成の基本", "chapter_range": "051-070"}},
                        {{"name": "見積オプション設定", "chapter_range": "071-090"}},
                        {{"name": "施工依頼フロー", "chapter_range": "091-110"}},
                        {{"name": "施工完了と検収", "chapter_range": "111-130"}},
                        {{"name": "DC業務の基本", "chapter_range": "131-160"}},
                        {{"name": "DC業務の応用", "chapter_range": "161-190"}},
                        {{"name": "Back業務全般", "chapter_range": "191-230"}},
                        {{"name": "例外対応", "chapter_range": "231-260"}},
                        {{"name": "その他・補足", "chapter_range": "261-286"}}
                    ]
                }}
            ]
        }},
        {{
            "order": 2,
            "id": "02_チャット対応",
            "name": "チャット対応マニュアル",
            "description": "説明",
            "source_files": [
                {{
                    "file": "★250708_チャットのやり取りの王道パターン（13章）",
                    "split": true,
                    "groups": [
                        {{"name": "見積依頼と提示", "chapter_range": "001-005"}},
                        {{"name": "追加見積と商品説明", "chapter_range": "006-009"}},
                        {{"name": "注文後の対応", "chapter_range": "010-013"}}
                    ]
                }}
            ]
        }},
        {{
            "order": 3,
            "id": "03_定型文",
            "name": "定型文・テンプレート",
            "description": "説明",
            "source_files": [
                {{
                    "file": "型と定型文例（79章）",
                    "split": true,
                    "groups": [
                        {{"name": "提案トークの基本", "chapter_range": "001-020"}},
                        {{"name": "PREP法活用例", "chapter_range": "021-040"}},
                        {{"name": "リマインド文例", "chapter_range": "041-060"}},
                        {{"name": "お詫び・謝罪文例", "chapter_range": "061-079"}}
                    ]
                }}
            ]
        }}
    ]
}}

【注意事項】★重要★
- カテゴリ・ファイルの順番は「上から読んで理解しやすい順」にすること（章番号順ではない）
- 業務フローに関するものは業務の流れに沿った順番にすること
- 1グループあたり10〜20章が理想（多くても25章まで）
- 30章以上のファイルは必ず分割する
- 5章以上でも内容が多様なら分割を検討する（1ファイルに全部入れない）
- 10章以下の小さなファイルは、関連性があればマージを検討
- chapter_range: "開始-終了" 形式（例: "001-015"）、章番号は3桁ゼロ埋め
- 最終章まで必ずカバーすること（例: 286章なら最後のグループは"XXX-286"で終わる）
- ファイル名は内容を反映した具体的な名前（日付・記号は使わない）

JSONのみを出力してください。
"""
        
        # ユーザーフィードバックがある場合はプロンプトに追加
        if user_feedback:
            prompt += f"""

═══════════════════════════════════════════════════════════════════════════════
【ユーザーからの再構成リクエスト】★最優先で考慮すること★
═══════════════════════════════════════════════════════════════════════════════
{user_feedback}
═══════════════════════════════════════════════════════════════════════════════

上記のフィードバックを最優先で考慮し、構成を改善してください。
ユーザーの要望に沿った構成を提案することが最重要です。
"""
        
        try:
            system_prompt = "あなたはナレッジベースの構成設計の専門家です。必ずJSON形式のみで回答してください。"
            
            if "gpt-5" in model or "o1" in model or "o3" in model or self._is_azure:
                params = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    "max_completion_tokens": 32000,  # 大量のグループ生成に対応
                }
                if not self._is_azure:
                    params["response_format"] = {"type": "json_object"}
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
                    max_completion_tokens=16000,
                    response_format={"type": "json_object"},
                )
            
            result_text = response.choices[0].message.content
            if not result_text:
                raise ValueError("AI returned empty response")
            
            result_text = result_text.strip()
            self.logger.debug(f"AI unified structure (first 500): {result_text[:500]}")
            
            # JSONを抽出
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()
            
            structure = json.loads(result_text)
            
            # バリデーション: 25章超のグループがないか確認
            self._validate_and_fix_structure(structure, summaries)
            
            return structure
            
        except Exception as e:
            self.logger.error(f"Failed to get unified structure: {e}")
            return self._create_fallback_grouping_policy(summaries)
    
    def _validate_and_fix_structure(self, structure: Dict, summaries: List[Dict]) -> None:
        """AIの出力を検証し、全章がカバーされているか確認。漏れがあれば自動補完する."""
        # ファイル名→章数のマップを作成
        chapter_counts = {s['file_name']: len(s.get('chapters', [])) for s in summaries}
        
        for cat in structure.get("categories", []):
            for src_file in cat.get("source_files", []):
                file_name = src_file.get("file", "")
                groups = src_file.get("groups")
                
                # ファイル名を正規化して章数を取得
                total_chapters = 0
                matched_file = None
                for fn, count in chapter_counts.items():
                    if file_name in fn or fn in file_name:
                        total_chapters = count
                        matched_file = fn
                        break
                
                if total_chapters == 0:
                    continue
                
                if groups:
                    # 全章がカバーされているかチェック
                    covered = set()
                    for g in groups:
                        chapter_range = g.get("chapter_range", "")
                        start, end = self._parse_chapter_range(chapter_range, total_chapters)
                        for idx in range(start, end + 1):
                            covered.add(idx)
                    
                    # 漏れている章を特定
                    all_chapters = set(range(total_chapters))
                    missing = all_chapters - covered
                    
                    if missing:
                        self.logger.warning(f"  ⚠️ 「{file_name}」で{len(missing)}章が未割り当て → 自動補完")
                        # 未割り当て章を追加グループとして追加
                        missing_sorted = sorted(missing)
                        # 連続する章をグループ化
                        current_start = missing_sorted[0]
                        current_end = missing_sorted[0]
                        extra_groups = []
                        
                        for idx in missing_sorted[1:] + [-1]:  # 番兵追加
                            if idx == current_end + 1:
                                current_end = idx
                            else:
                                extra_groups.append({
                                    "name": f"補完_{current_start + 1:03d}-{current_end + 1:03d}",
                                    "chapter_range": f"{current_start + 1:03d}-{current_end + 1:03d}"
                                })
                                if idx != -1:
                                    current_start = idx
                                    current_end = idx
                        
                        groups.extend(extra_groups)
                        self.logger.info(f"    → {len(extra_groups)}グループを追加")
                
                elif total_chapters > 0:
                    # groupsがnullだが章がある場合、全章を1グループとして扱う
                    # （split=falseの正常ケース）
                    pass
    
    def _create_fallback_grouping_policy(self, summaries: List[Dict]) -> Dict:
        """AIが失敗した場合のフォールバック方針."""
        SPLIT_THRESHOLD = 50  # 50章以上で分割
        CHAPTERS_PER_GROUP = 30  # 30章ずつ（20〜40章の中間）
        categories = []
        
        for i, s in enumerate(summaries):
            file_name = s['file_name']
            chapters = s.get('chapters', [])
            
            # 50章を超えるファイルは分割
            if len(chapters) > SPLIT_THRESHOLD:
                # 30章ずつにグループ化
                groups = []
                for j in range(0, len(chapters), CHAPTERS_PER_GROUP):
                    end = min(j + CHAPTERS_PER_GROUP, len(chapters))
                    groups.append({
                        "name": f"セクション{j // CHAPTERS_PER_GROUP + 1}",
                        "chapter_range": f"{j + 1:03d}-{end:03d}"
                    })
                
                categories.append({
                    "order": i + 1,
                    "id": f"{i+1:02d}_ドキュメント",
                    "name": file_name[:20],
                    "description": "",
                    "source_files": [{
                        "file": file_name,
                        "split": True,
                        "groups": groups
                    }]
                })
            else:
                categories.append({
                    "order": i + 1,
                    "id": f"{i+1:02d}_ドキュメント",
                    "name": file_name[:20],
                    "description": "",
                    "source_files": [{
                        "file": file_name,
                        "split": False,
                        "groups": None
                    }]
                })
        
        return {
            "title": "ナレッジベース",
            "description": "自動生成",
            "categories": categories
        }
    
    def _assign_chapters_with_ai(
        self,
        structure: Dict,
        summaries: List[Dict]
    ) -> Optional[Dict]:
        """AIに各mdファイルへの章割り当てを決定させる.
        
        Args:
            structure: フォルダ構成案（カテゴリ・ファイル名の骨格）
            summaries: 各ファイルのサマリー（全章タイトル含む）
            
        Returns:
            章割り当てのDict、失敗時はNone
        """
        client = self._get_openai_client()
        model = self._get_model_name()
        
        # 全章のタイトル一覧を構築
        all_chapters = []
        for s in summaries:
            file_name = s['file_name']
            for i, ch in enumerate(s.get('chapters', [])):
                ch_id = self._make_chapter_id(file_name, i)
                all_chapters.append({
                    "id": ch_id,
                    "title": ch.get('original_title', ''),
                    "source_file": file_name
                })
        
        if not all_chapters:
            self.logger.warning("  章が見つかりません")
            return None
        
        # フォルダ構成案からファイル一覧を抽出
        files_info = []
        for cat in structure.get("categories", []):
            cat_id = cat.get("id", "")
            cat_name = cat.get("name", "")
            for src_file in cat.get("source_files", []):
                file_name = src_file.get("file", "")
                groups = src_file.get("groups") or []
                
                if groups:
                    for g in groups:
                        files_info.append({
                            "category": cat_id,
                            "category_name": cat_name,
                            "file_name": g.get("name", ""),
                            "source": file_name
                        })
                else:
                    files_info.append({
                        "category": cat_id,
                        "category_name": cat_name,
                        "file_name": file_name[:30],
                        "source": file_name
                    })
        
        # ファイル一覧をテキスト化
        files_text = "\n".join([
            f"  - カテゴリ: {f['category']}, ファイル名: {f['file_name']}"
            for f in files_info
        ])
        
        # 章一覧をテキスト化（全章を表示）
        chapters_text = "\n".join([
            f"  {c['id']}: {c['title']}"
            for c in all_chapters
        ])
        
        prompt = f"""以下のフォルダ構成案と全章のタイトル一覧を見て、
各mdファイルにどの章を入れるべきかを決定してください。

【フォルダ構成案のファイル一覧】
{files_text}

【全章のタイトル一覧】（合計{len(all_chapters)}章）
{chapters_text}

【重要なルール】
1. 各章は必ず1つのファイルに割り当てる（漏れなし、重複なし）
2. ファイル名と内容が一致するように割り当てる
3. 関連する章は同じファイルにまとめる
4. 全ての章を必ず割り当てる（unassigned_chaptersは空にする）

【出力形式（JSON）】
{{
    "assignments": [
        {{
            "category_id": "01_業務マニュアル",
            "file_name": "概要・体制",
            "chapter_ids": ["25年度版_業務設計資料_001", "25年度版_業務設計資料_002"]
        }}
    ],
    "unassigned_chapters": []
}}

注意: chapter_idsには上記の「全章のタイトル一覧」に記載されたIDをそのまま使用してください。
JSONのみを出力してください。
"""
        
        try:
            system_prompt = "あなたはナレッジベースの構成設計の専門家です。必ずJSON形式のみで回答してください。"
            
            if "gpt-5" in model or "o1" in model or "o3" in model or self._is_azure:
                params = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    "max_completion_tokens": 32000,  # 大量の章を扱うため増量
                }
                if not self._is_azure:
                    params["response_format"] = {"type": "json_object"}
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
                    max_completion_tokens=16000,
                    response_format={"type": "json_object"},
                )
            
            result_text = response.choices[0].message.content
            if not result_text:
                raise ValueError("AI returned empty response")
            
            result_text = result_text.strip()
            self.logger.debug(f"章割り当てAI応答 (first 500): {result_text[:500]}")
            
            # JSONを抽出
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()
            
            assignment = json.loads(result_text)
            
            # 漏れチェック
            assigned_ids = set()
            for a in assignment.get("assignments", []):
                assigned_ids.update(a.get("chapter_ids", []))
            
            all_chapter_ids = {c["id"] for c in all_chapters}
            missing = all_chapter_ids - assigned_ids
            
            if missing:
                self.logger.warning(f"  ⚠️ {len(missing)}章が未割り当て、自動補完します")
                # 未割り当て章を新しいファイルとして追加（25章ずつ分割）
                MAX_CHAPTERS_PER_FILE = 25
                missing_list = sorted(list(missing))
                
                # 最後のカテゴリIDを取得
                last_cat_id = "99_その他"
                if assignment.get("assignments"):
                    last_cat_id = assignment["assignments"][-1].get("category_id", "99_その他")
                
                for i in range(0, len(missing_list), MAX_CHAPTERS_PER_FILE):
                    batch = missing_list[i:i + MAX_CHAPTERS_PER_FILE]
                    batch_num = i // MAX_CHAPTERS_PER_FILE + 1
                    assignment["assignments"].append({
                        "category_id": last_cat_id,
                        "file_name": f"その他_{batch_num}",
                        "chapter_ids": batch
                    })
                    self.logger.info(f"    → 新規ファイル「その他_{batch_num}」に{len(batch)}章を追加")
                
                assignment["unassigned_chapters"] = []
            
            self.logger.info(f"  章割り当て完了: {len(assignment.get('assignments', []))}ファイル")
            return assignment
            
        except Exception as e:
            self.logger.error(f"章割り当てAI失敗: {e}")
            return None
    
    def _apply_chapter_assignment(
        self,
        structure: Dict,
        assignment: Dict,
        summaries: List[Dict]
    ) -> Dict:
        """AIの章割り当て結果をフォルダ構成に反映.
        
        Args:
            structure: フォルダ構成案（骨格）
            assignment: 章割り当て結果
            summaries: 各ファイルのサマリー
            
        Returns:
            完成したフォルダ構成提案
        """
        # ファイル名→サマリーのマップ
        summary_map = {s['file_name']: s for s in summaries}
        
        # 結果の構造
        result = {
            "title": structure.get("title", "ナレッジベース"),
            "description": structure.get("description", ""),
            "categories": []
        }
        
        # カテゴリごとにファイルをグループ化
        category_files = {}
        for a in assignment.get("assignments", []):
            cat_id = a.get("category_id", "01_その他")
            if cat_id not in category_files:
                category_files[cat_id] = []
            category_files[cat_id].append(a)
        
        # 元のカテゴリ情報を保持
        cat_info_map = {}
        for cat in structure.get("categories", []):
            cat_info_map[cat.get("id", "")] = cat
        
        # カテゴリごとに処理
        for cat_id, files in sorted(category_files.items()):
            cat_info = cat_info_map.get(cat_id, {})
            
            new_category = {
                "order": cat_info.get("order", 99),
                "id": cat_id,
                "name": cat_info.get("name", cat_id),
                "description": cat_info.get("description", ""),
                "files": []
            }
            
            for i, f in enumerate(files):
                file_name = f.get("file_name", "")
                chapter_ids = f.get("chapter_ids", [])
                
                if not chapter_ids:
                    continue
                
                # 章IDから元ファイル名を特定
                first_ch_id = chapter_ids[0] if chapter_ids else ""
                ch_info = self._chapter_id_map.get(first_ch_id, {})
                source_file = ch_info.get("file_name", "")
                
                # 章数の制限なし（AIの判断を尊重）
                file_order = len(new_category["files"]) + 1
                new_category["files"].append({
                    "order": file_order,
                    "output_name": f"{file_order:02d}_{file_name}.md",
                    "title": file_name,
                    "sources": [{
                        "file": source_file,
                        "chapter_ids": chapter_ids,
                        "is_split": True
                    }],
                    "is_merged": False,
                    "summary": f"{len(chapter_ids)}章を含む"
                })
            
            if new_category["files"]:
                result["categories"].append(new_category)
        
        # カテゴリをorderでソート
        result["categories"].sort(key=lambda x: x.get("order", 999))
        
        return result
    
    def _assign_all_chapters(
        self, 
        grouping_policy: Dict, 
        summaries: List[Dict]
    ) -> Dict:
        """AIの方針に基づき、Pythonで全章を割り当てる（漏れなし保証）."""
        # ファイル名→サマリーのマップ
        summary_map = {s['file_name']: s for s in summaries}
        
        # 結果の構造
        result = {
            "title": grouping_policy.get("title", "ナレッジベース"),
            "description": grouping_policy.get("description", ""),
            "categories": []
        }
        
        for cat in grouping_policy.get("categories", []):
            new_category = {
                "order": cat.get("order", 1),
                "id": cat.get("id", ""),
                "name": cat.get("name", ""),
                "description": cat.get("description", ""),
                "files": []
            }
            
            file_order = 0
            
            for src_file in cat.get("source_files", []):
                file_name = src_file.get("file", "")
                split = src_file.get("split", False)
                groups = src_file.get("groups")
                
                # ファイル名を正規化
                normalized_name = self._normalize_file_name(
                    file_name, 
                    set(summary_map.keys())
                )
                if not normalized_name:
                    self.logger.warning(f"  ⚠️ ファイルが見つかりません: {file_name}")
                    continue
                
                summary = summary_map.get(normalized_name, {})
                chapters = summary.get('chapters', [])
                
                if split and groups and len(chapters) > 0:
                    # 分割モード: 各グループに章を割り当て
                    assigned_indices = set()
                    
                    for group in groups:
                        file_order += 1
                        group_name = group.get("name", "セクション")
                        chapter_range = group.get("chapter_range", "")
                        
                        # 範囲をパース（例: "001-015" → 1〜15）
                        start_idx, end_idx = self._parse_chapter_range(
                            chapter_range, len(chapters)
                        )
                        
                        # 章IDを収集
                        chapter_ids = []
                        for idx in range(start_idx, end_idx + 1):
                            if idx < len(chapters):
                                ch_id = self._make_chapter_id(normalized_name, idx)
                                chapter_ids.append(ch_id)
                                assigned_indices.add(idx)
                        
                        if chapter_ids:
                            new_category["files"].append({
                                "order": file_order,
                                "output_name": f"{file_order:02d}_{group_name}.md",
                                "title": group_name,
                                "sources": [{
                                    "file": normalized_name,
                                    "chapter_ids": chapter_ids,
                                    "is_split": True
                                }],
                                "is_merged": False,
                                "summary": f"{len(chapter_ids)}章を含む"
                            })
                    
                    # 未割り当ての章を追加（漏れなし保証）
                    missing_indices = set(range(len(chapters))) - assigned_indices
                    if missing_indices:
                        self.logger.info(f"  📌 {len(missing_indices)}章を自動追加: {normalized_name}")
                        
                        # 未割り当て章をソートしてグループ化
                        sorted_missing = sorted(missing_indices)
                        for i in range(0, len(sorted_missing), 15):
                            file_order += 1
                            batch = sorted_missing[i:i+15]
                            
                            chapter_ids = []
                            for idx in batch:
                                ch_id = self._make_chapter_id(normalized_name, idx)
                                chapter_ids.append(ch_id)
                            
                            # 最初の章のタイトルから名前を生成
                            first_ch = chapters[batch[0]] if batch else {}
                            first_title = first_ch.get('clean_title', 'その他')[:15]
                            
                            new_category["files"].append({
                                "order": file_order,
                                "output_name": f"{file_order:02d}_{first_title}_他.md",
                                "title": f"{first_title} 他",
                                "sources": [{
                                    "file": normalized_name,
                                    "chapter_ids": chapter_ids,
                                    "is_split": True
                                }],
                                "is_merged": False,
                                "summary": f"自動追加: {len(chapter_ids)}章"
                            })
                else:
                    # 非分割モード: ファイル全体を1つのmdに
                    file_order += 1
                    # ファイル名を整形（日付・記号を削除）
                    clean_name = self._clean_output_name(normalized_name)
                    new_category["files"].append({
                        "order": file_order,
                        "output_name": f"{file_order:02d}_{clean_name[:30]}.md",
                        "title": clean_name[:40],
                        "sources": [{
                            "file": normalized_name,
                            "chapter_ids": None,
                            "is_split": False
                        }],
                        "is_merged": False,
                        "summary": ""
                    })
            
            if new_category["files"]:
                result["categories"].append(new_category)
        
        return result
    
    def _clean_output_name(self, name: str) -> str:
        """ファイル名から日付・記号を削除して整形.
        
        例: "250929_最終ミーティング (2)" → "最終ミーティング"
        例: "★251112_給湯器横展開用" → "給湯器横展開用"
        例: "【25年度版】業務設計資料" → "業務設計資料"
        """
        result = name
        # 【】を削除
        result = re.sub(r'【.*?】', '', result)
        # ★を削除
        result = result.replace('★', '')
        # 日付パターンを削除（6桁数字_）
        result = re.sub(r'^\d{6}_', '', result)
        # (数字) を削除
        result = re.sub(r'\s*\(\d+\)\s*$', '', result)
        # 先頭・末尾の空白を削除
        result = result.strip()
        # 空になった場合は元の名前を使う
        if not result:
            result = name
        return result
    
    def _parse_chapter_range(self, range_str: str, total_chapters: int) -> Tuple[int, int]:
        """章範囲文字列をパース.
        
        Args:
            range_str: "001-015" 形式の文字列
            total_chapters: 全章数
            
        Returns:
            (start_idx, end_idx) 0-indexed
        """
        try:
            if '-' in range_str:
                parts = range_str.split('-')
                start = int(parts[0].strip()) - 1  # 0-indexed
                end = int(parts[1].strip()) - 1
                return max(0, start), min(end, total_chapters - 1)
            else:
                # 単一章
                idx = int(range_str.strip()) - 1
                return max(0, idx), min(idx, total_chapters - 1)
        except:
            return 0, total_chapters - 1
    
    def _old_propose_structure_prompt(self) -> str:
        """旧プロンプト（参考用、使用しない）."""
        return ""
        
        try:
            # システムプロンプトを追加してJSON出力を強制
            system_prompt = "あなたはナレッジベースの構成設計の専門家です。必ずJSON形式のみで回答してください。説明文や前置きは一切不要です。"
            
            if "gpt-5" in model or "o1" in model or "o3" in model or self._is_azure:
                params = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    "max_completion_tokens": 16000,  # 増量
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
                    max_completion_tokens=16000,
                )
            
            result_text = response.choices[0].message.content
            
            # デバッグログ
            if not result_text:
                self.logger.error("AI returned empty response")
                raise ValueError("AI returned empty response")
            
            result_text = result_text.strip()
            self.logger.debug(f"AI response (first 500 chars): {result_text[:500]}")
            
            # Remove markdown code block if present
            if result_text.startswith("```"):
                result_text = re.sub(r"^```(?:json)?\n?", "", result_text)
                result_text = re.sub(r"\n?```$", "", result_text)
            
            # JSONの開始位置を探す（説明文が前にある場合への対応）
            json_start = result_text.find('{')
            if json_start > 0:
                self.logger.warning(f"Found text before JSON, skipping {json_start} chars")
                result_text = result_text[json_start:]
            
            # JSONの終了位置を探す
            json_end = result_text.rfind('}')
            if json_end > 0 and json_end < len(result_text) - 1:
                self.logger.warning(f"Found text after JSON, truncating")
                result_text = result_text[:json_end + 1]
            
            proposal = json.loads(result_text)
            
            # バリデーション
            proposal = self._validate_proposal(proposal, summaries, file_contents)
            
            return proposal
            
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON parse error: {e}")
            self.logger.error(f"Response text (first 1000 chars): {result_text[:1000] if result_text else 'EMPTY'}")
            self.logger.warning("Using fallback proposal - please check the AI response")
            return self._create_fallback_proposal(summaries)
        except Exception as e:
            self.logger.error(f"Failed to propose structure: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            self.logger.warning("Using fallback proposal due to error")
            return self._create_fallback_proposal(summaries)
    
    def _validate_proposal(
        self, 
        proposal: Dict, 
        summaries: List[Dict],
        file_contents: Dict[str, str]
    ) -> Dict:
        """AIの提案をバリデーション・修正.
        
        Args:
            proposal: AI提案
            summaries: サマリー情報
            file_contents: ファイル内容
            
        Returns:
            検証・修正済みの提案
        """
        available_files = set(s['file_name'] for s in summaries)
        
        # 使用された章IDを追跡
        used_chapter_ids: Dict[str, set] = {fn: set() for fn in available_files}
        
        for category in proposal.get("categories", []):
            validated_files = []
            for f in category.get("files", []):
                # sourcesの検証
                valid_sources = []
                for src in f.get("sources", []):
                    src_file = src.get("file", "")
                    # ファイル名の正規化
                    normalized = self._normalize_file_name(src_file, available_files)
                    if normalized:
                        src["file"] = normalized
                        valid_sources.append(src)
                        
                        # 使用された章IDを記録
                        chapter_ids = src.get("chapter_ids")
                        if chapter_ids:
                            for ch_id in chapter_ids:
                                used_chapter_ids[normalized].add(ch_id)
                        elif not src.get("is_split", False):
                            # ファイル全体を使用 → すべての章IDを記録
                            for ch_id, info in self._chapter_id_map.items():
                                if info['file_name'] == normalized:
                                    used_chapter_ids[normalized].add(ch_id)
                    else:
                        self.logger.warning(f"  ⚠️ Unknown source file: {src_file}")
                
                if valid_sources:
                    f["sources"] = valid_sources
                    validated_files.append(f)
            
            category["files"] = validated_files
        
        # 章の網羅性チェック（大容量ファイルのみ）
        for s in summaries:
            file_name = s['file_name']
            char_count = s.get('char_count', 0)
            
            # 50,000文字以上の大容量ファイルをチェック
            if char_count >= 50000:
                all_chapter_ids = set(
                    ch_id for ch_id, info in self._chapter_id_map.items()
                    if info['file_name'] == file_name
                )
                used = used_chapter_ids.get(file_name, set())
                missing = all_chapter_ids - used
                
                if missing:
                    self.logger.warning(
                        f"  ⚠️ 大容量ファイル [{file_name}] で {len(missing)}/{len(all_chapter_ids)} 章が未使用!"
                    )
                    self.logger.warning(f"     未使用章ID (最初の10個): {list(missing)[:10]}")
                    
                    # 自動補完: 未使用章を追加
                    self._add_missing_chapters(proposal, file_name, missing)
        
        return proposal
    
    def _add_missing_chapters(
        self, 
        proposal: Dict, 
        file_name: str, 
        missing_chapter_ids: set
    ) -> None:
        """未使用の章を提案に追加.
        
        Args:
            proposal: 提案
            file_name: ファイル名
            missing_chapter_ids: 未使用の章IDセット
        """
        if not missing_chapter_ids:
            return
        
        # 既存のカテゴリを探す（最初のカテゴリに追加）
        categories = proposal.get("categories", [])
        if not categories:
            return
        
        # 未使用章をソート
        sorted_missing = sorted(missing_chapter_ids)
        
        # 15章ずつにグループ化
        groups = []
        for i in range(0, len(sorted_missing), 15):
            groups.append(sorted_missing[i:i+15])
        
        # 各グループを新しいファイルとして追加
        target_category = categories[0]  # 最初のカテゴリに追加
        existing_files = target_category.get("files", [])
        max_order = max((f.get("order", 0) for f in existing_files), default=0)
        
        for idx, group in enumerate(groups):
            max_order += 1
            # 最初と最後の章IDからタイトルを生成
            first_ch = self._chapter_id_map.get(group[0], {})
            first_title = first_ch.get('chapter', {}).get('clean_title', '')[:20]
            
            new_file = {
                "order": max_order,
                "output_name": f"{max_order:02d}_{first_title}_他.md",
                "title": f"{first_title} 他",
                "sources": [{
                    "file": file_name,
                    "chapter_ids": group,
                    "is_split": True
                }],
                "is_merged": False,
                "summary": f"自動追加: {len(group)}章を含む"
            }
            existing_files.append(new_file)
            self.logger.info(f"     → 自動追加: {new_file['output_name']} ({len(group)}章)")
        
        target_category["files"] = existing_files
    
    def _normalize_file_name(self, name: str, available: set) -> Optional[str]:
        """ファイル名を正規化.
        
        AIが出力した名前（章数付きの可能性あり）と、実際のファイル名をマッチング。
        例: "【25年度版】業務設計資料（43章）" → "【25年度版】業務設計資料"
        """
        if name in available:
            return name
        
        # 章数表記を除去して正規化
        # 例: "ファイル名（43章）" → "ファイル名"
        cleaned_name = re.sub(r'[（\(]\d+章[）\)]$', '', name).strip()
        
        if cleaned_name in available:
            return cleaned_name
        
        # 部分一致を試みる（章数除去版）
        for avail in available:
            # 双方向の部分一致
            if cleaned_name in avail or avail in cleaned_name:
                return avail
            # 元の名前でも試す
            if name in avail or avail in name:
                return avail
        
        # さらに柔軟なマッチング: 記号を除去して比較
        import unicodedata
        def simplify(s: str) -> str:
            # 【】★などの記号、日付、章数を除去
            s = re.sub(r'[【】★\[\]]', '', s)
            s = re.sub(r'\d{6}_', '', s)  # 日付パターン 250708_
            s = re.sub(r'[（\(]\d+章[）\)]', '', s)
            return s.strip()
        
        simplified_name = simplify(name)
        for avail in available:
            if simplify(avail) == simplified_name:
                return avail
            if simplified_name in simplify(avail) or simplify(avail) in simplified_name:
                return avail
        
        return None
    
    def _create_fallback_proposal(self, summaries: List[Dict]) -> Dict:
        """フォールバック提案を作成（AI失敗時）.
        
        ファイル名からカテゴリを推測して分類します。
        """
        # カテゴリ分類ルール
        category_rules = [
            ("業務マニュアル", ["業務設計", "マニュアル", "見積作成", "ナレッジ資料"]),
            ("定型文・テンプレート", ["定型文", "テンプレート", "型と", "横展開"]),
            ("チャット事例", ["チャット", "やり取り", "王道パターン"]),
            ("工数調査", ["工数", "業務量", "調査"]),
            ("プロジェクト報告", ["ミーティング", "AI活用案", "PJ", "報告"]),
        ]
        
        # ファイルをカテゴリに振り分け
        categorized = {name: [] for name, _ in category_rules}
        categorized["その他"] = []
        
        for s in summaries:
            file_name = s['file_name']
            assigned = False
            
            for cat_name, keywords in category_rules:
                if any(kw in file_name for kw in keywords):
                    categorized[cat_name].append(s)
                    assigned = True
                    break
            
            if not assigned:
                categorized["その他"].append(s)
        
        # カテゴリを構築
        categories = []
        order = 0
        
        for cat_name, cat_files in categorized.items():
            if not cat_files:
                continue
            
            order += 1
            cat_id = f"{order:02d}_{cat_name.replace('・', '_')}"
            
            files = []
            for i, s in enumerate(cat_files):
                # ファイル名を短くして見やすくする
                short_name = s['file_name']
                # 先頭の記号や日付を除去
                short_name = re.sub(r'^[★【]?[\d_]+_?', '', short_name)
                short_name = re.sub(r'^【[^】]+】', '', short_name)
                if len(short_name) > 30:
                    short_name = short_name[:30]
                
                files.append({
                    "order": i + 1,
                    "output_name": f"{i+1:02d}_{short_name}.md",
                    "title": short_name,
                    "sources": [{
                        "file": s['file_name'],
                        "chapters": None,
                        "is_split": False
                    }],
                    "is_merged": False,
                    "summary": s.get('summary', '')[:100]
                })
            
            categories.append({
                "order": order,
                "id": cat_id,
                "name": cat_name,
                "description": f"{cat_name}関連のナレッジ（{len(cat_files)}件）",
                "files": files
            })
        
        return {
            "title": "東京ガス機器交換ナレッジベース",
            "description": "自動生成（AI提案失敗のためルールベースで分類）",
            "categories": categories
        }
    
    # =========================================================================
    # Step 3: ユーザー確認
    # =========================================================================
    
    def _display_proposal(self, proposal: Dict, summaries: List[Dict]) -> Tuple[bool, Optional[str]]:
        """構成提案を表示してユーザー確認を得る.
        
        Returns:
            Tuple[bool, Optional[str]]:
                - (True, None): 実行を承認
                - (False, None): キャンセル
                - (False, str): 再構成リクエスト（strはユーザーのフィードバック）
        """
        categories = proposal.get("categories", [])
        title = proposal.get("title", "ナレッジベース")
        
        # サマリーから文字数マップを作成
        char_counts = {s['file_name']: s.get('char_count', 0) for s in summaries}
        
        # 統計
        total_files = sum(len(cat.get("files", [])) for cat in categories)
        
        print("\n" + "═" * 80)
        print(f"                        📂 {title}")
        print("═" * 80)
        print()
        print(f"  {len(summaries)} 個の入力ファイルを {len(categories)} カテゴリ、{total_files} ナレッジに整理します。")
        print()
        print("─" * 80)
        
        for category in sorted(categories, key=lambda x: x.get("order", 999)):
            cat_id = category.get("id", "")
            cat_name = category.get("name", "")
            cat_desc = category.get("description", "")
            files = category.get("files", [])
            
            print()
            print(f"  📁 {cat_id}/  ({len(files)} ファイル)")
            if cat_desc:
                print(f"     {cat_desc}")
            print()
            
            for f in files:
                output_name = f.get("output_name", "")
                sources = f.get("sources", [])
                is_merged = f.get("is_merged", False)
                
                source_info = []
                for src in sources:
                    src_file = src.get("file", "")[:30]
                    if src.get("is_split"):
                        chapter_ids = src.get("chapter_ids", [])
                        ch_str = f"({len(chapter_ids)}章)" if chapter_ids else ""
                        source_info.append(f"{src_file} {ch_str}")
                    else:
                        source_info.append(src_file)
                
                if is_merged:
                    print(f"     ├─ 📄 {output_name} [マージ]")
                    for si in source_info:
                        print(f"     │     ◀── {si}")
                else:
                    si = source_info[0] if source_info else "?"
                    print(f"     ├─ 📄 {output_name}")
                    print(f"     │     ◀── {si}")
        
        print()
        print("─" * 80)
        print()
        
        # 統計サマリー
        split_count = sum(
            1 for cat in categories 
            for f in cat.get("files", [])
            for src in f.get("sources", [])
            if src.get("is_split")
        )
        merge_count = sum(
            1 for cat in categories 
            for f in cat.get("files", [])
            if f.get("is_merged")
        )
        
        print(f"  【統計】 カテゴリ: {len(categories)} / ナレッジ: {total_files}")
        print(f"          分割: {split_count} / マージ: {merge_count}")
        print()
        print("═" * 80)
        print()
        
        if self.skip_confirmation:
            print("  [自動実行モード] 確認をスキップして実行します")
            return (True, None)
        
        while True:
            response = input("  [Y] この構成で実行  [N] キャンセル  [R] 再構成: ").strip().upper()
            if response == 'Y':
                return (True, None)
            elif response == 'N':
                return (False, None)
            elif response == 'R':
                print()
                print("  ─" * 40)
                print("  📝 構成の改善ポイントを入力してください（複数行可、空行で終了）:")
                print("  ─" * 40)
                print("  例: カテゴリを4つに減らしてほしい")
                print("  例: 「業務マニュアル」と「定型文」は分けてほしい")
                print("  例: 施工関連は1つのカテゴリにまとめてほしい")
                print()
                
                feedback_lines = []
                while True:
                    line = input("  > ").strip()
                    if line == "":
                        if feedback_lines:
                            break
                        else:
                            print("  （少なくとも1行入力してください）")
                    else:
                        feedback_lines.append(line)
                
                user_feedback = "\n".join(feedback_lines)
                print()
                print(f"  ✅ フィードバックを受け付けました。再構成します...")
                print()
                return (False, user_feedback)
            else:
                print("  Y, N, または R を入力してください")
    
    # =========================================================================
    # Step 4: 章分割
    # =========================================================================
    
    def _extract_by_chapter_ids(
        self,
        content: str,
        chapter_ids: List[str],
        file_name: str
    ) -> List[Tuple[str, str, Tuple[int, int]]]:
        """章IDを使って章を抽出.
        
        Args:
            content: formatted.mdの内容
            chapter_ids: 抽出する章のIDリスト
            file_name: 元ファイル名
            
        Returns:
            [(章タイトル, 章の内容, (line_start, line_end)), ...]
        """
        lines = content.split('\n')
        results = []
        
        for ch_id in chapter_ids:
            # 章IDマップから章情報を取得
            ch_info = self._chapter_id_map.get(ch_id)
            
            if ch_info and ch_info['file_name'] == file_name:
                chapter = ch_info['chapter']
                start = chapter['line_start'] - 1  # 0-indexed
                end = chapter['line_end']
                chapter_content = '\n'.join(lines[start:end])
                results.append((
                    chapter.get('clean_title', chapter.get('original_title', '')),
                    chapter_content,
                    (chapter['line_start'], chapter['line_end'])
                ))
                self.logger.debug(f"    抽出: {ch_id} → 行{start+1}-{end}")
            else:
                self.logger.warning(f"  章ID不明: {ch_id} (file: {file_name})")
        
        return results
    
    def _split_by_chapters(
        self,
        content: str,
        chapter_titles: List[str],
        summaries: List[Dict],
        file_name: str
    ) -> List[Tuple[str, str, Tuple[int, int]]]:
        """formatted.mdを指定された章タイトルで分割（後方互換性用）.
        
        Args:
            content: formatted.mdの内容
            chapter_titles: 抽出する章のタイトルリスト
            summaries: サマリー情報（章のline_rangeを含む）
            file_name: 元ファイル名
            
        Returns:
            [(章タイトル, 章の内容, (line_start, line_end)), ...]
        """
        # ファイルのサマリー情報を取得
        file_summary = next(
            (s for s in summaries if s['file_name'] == file_name), 
            None
        )
        if not file_summary:
            self.logger.warning(f"  Summary not found for {file_name}")
            return [(chapter_titles[0] if chapter_titles else "content", content, (1, len(content.split('\n'))))]
        
        chapters_info = file_summary.get('chapters', [])
        lines = content.split('\n')
        
        results = []
        for title in chapter_titles:
            # 章情報を探す（複数のマッチング戦略を使用）
            chapter = self._find_matching_chapter(title, chapters_info)
            
            if chapter:
                start = chapter['line_start'] - 1  # 0-indexed
                end = chapter['line_end']
                chapter_content = '\n'.join(lines[start:end])
                results.append((
                    chapter.get('clean_title', title),
                    chapter_content,
                    (chapter['line_start'], chapter['line_end'])
                ))
            else:
                self.logger.warning(f"  Chapter not found: {title} in {file_name}")
        
        return results
    
    def _find_matching_chapter(
        self,
        search_title: str,
        chapters_info: List[Dict]
    ) -> Optional[Dict]:
        """章タイトルでマッチする章を探す（複数のマッチング戦略）.
        
        Args:
            search_title: 検索するタイトル
            chapters_info: 章情報のリスト
            
        Returns:
            マッチした章情報、見つからない場合はNone
        """
        # 戦略1: 完全一致
        for c in chapters_info:
            if search_title == c.get('original_title', '') or search_title == c.get('clean_title', ''):
                return c
        
        # 戦略2: 部分一致（検索タイトルが章タイトルに含まれる）
        for c in chapters_info:
            orig = c.get('original_title', '')
            clean = c.get('clean_title', '')
            if search_title in orig or search_title in clean:
                return c
            if orig in search_title or clean in search_title:
                return c
        
        # 戦略3: キーワードベースマッチング（共通単語数）
        search_words = set(re.findall(r'[\w\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]+', search_title.lower()))
        if len(search_words) < 2:
            return None
        
        best_match = None
        best_score = 0
        
        for c in chapters_info:
            orig = c.get('original_title', '')
            clean = c.get('clean_title', '')
            combined = f"{orig} {clean}".lower()
            chapter_words = set(re.findall(r'[\w\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]+', combined))
            
            # 共通単語数をスコアとする
            common = search_words & chapter_words
            score = len(common) / max(len(search_words), 1)
            
            if score > best_score and score >= 0.5:  # 50%以上の単語が一致
                best_score = score
                best_match = c
        
        return best_match
    
    def _strip_old_toc_and_summary(self, content: str) -> str:
        """元のコンテンツからサマリー・目次セクションを削除.
        
        Args:
            content: 元のコンテンツ
            
        Returns:
            サマリー・目次を除去したコンテンツ
        """
        lines = content.split('\n')
        result_lines = []
        skip_section = False
        
        for line in lines:
            stripped = line.strip()
            
            # サマリー・目次セクションの開始を検出
            if stripped.startswith('## サマリー') or stripped.startswith('## 目次'):
                skip_section = True
                continue
            
            # 次のセクション（##）で終了
            if skip_section and stripped.startswith('## '):
                skip_section = False
            
            # 区切り線でも終了
            if skip_section and stripped == '---':
                skip_section = False
                continue
            
            if not skip_section:
                result_lines.append(line)
        
        return '\n'.join(result_lines)
    
    def _build_toc_from_content(self, content: str) -> str:
        """実際のコンテンツから目次を自動生成（AIなし）.
        
        Args:
            content: Markdownコンテンツ
            
        Returns:
            目次のMarkdown文字列
        """
        toc_entries = []
        lines = content.split('\n')
        
        for line in lines:
            stripped = line.strip()
            
            # ### で始まる見出しを抽出
            if stripped.startswith('### '):
                title = stripped[4:].strip()
                anchor = self._generate_anchor(title)
                toc_entries.append(f"- [{title}](#{anchor})")
            
            # #### で始まる見出しはインデント
            elif stripped.startswith('#### '):
                title = stripped[5:].strip()
                anchor = self._generate_anchor(title)
                toc_entries.append(f"  - [{title}](#{anchor})")
        
        if not toc_entries:
            return ""
        
        return "## 目次\n\n" + '\n'.join(toc_entries[:20])  # 最大20項目
    
    def _generate_anchor(self, title: str) -> str:
        """見出しからMarkdown標準のアンカーIDを生成.
        
        GitHub/VSCode等の標準レンダラーと互換性のあるアンカーを生成:
        - 小文字に変換
        - スペースをハイフンに変換
        - 特殊文字（ピリオド、括弧等）を削除
        - 日本語はそのまま保持
        
        Args:
            title: 見出しタイトル（例: "1.1 資料タイトルと位置づけ"）
            
        Returns:
            アンカーID（例: "11-資料タイトルと位置づけ"）
        """
        anchor = title.lower()
        # スペースをハイフンに変換
        anchor = re.sub(r'\s+', '-', anchor)
        # 日本語・英数字・ハイフン以外を削除
        anchor = re.sub(r'[^\w\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\-]', '', anchor)
        # 連続するハイフンを1つに
        anchor = re.sub(r'-+', '-', anchor)
        # 先頭・末尾のハイフンを削除
        anchor = anchor.strip('-')
        return anchor
    
    def _rebuild_toc_in_content(self, content: str, title: str) -> str:
        """既存コンテンツの目次を実際の見出しから再構築.
        
        Args:
            content: Markdownコンテンツ（目次セクションを含む可能性あり）
            title: タイトル
            
        Returns:
            目次が再構築されたコンテンツ
        """
        # 古い目次を削除
        clean_content = self._strip_old_toc_and_summary(content)
        
        # サマリーを保持（もしあれば）
        summary_section = ""
        if '## サマリー' in content:
            lines = content.split('\n')
            in_summary = False
            summary_lines = []
            for line in lines:
                if line.strip().startswith('## サマリー'):
                    in_summary = True
                    summary_lines.append(line)
                elif in_summary:
                    if line.strip().startswith('## ') or line.strip() == '---':
                        break
                    summary_lines.append(line)
            if summary_lines:
                summary_section = '\n'.join(summary_lines) + '\n\n'
        
        # 新しい目次を生成
        new_toc = self._build_toc_from_content(clean_content)
        
        # タイトル、サマリー、目次、本文を構築
        if new_toc:
            return f"# {title}\n\n{summary_section}{new_toc}\n\n---\n\n{clean_content}"
        elif summary_section:
            return f"# {title}\n\n{summary_section}---\n\n{clean_content}"
        return clean_content
    
    def _generate_chapter_summary(
        self,
        content: str,
        chapter_title: str,
        output_title: str
    ) -> str:
        """分割された章に対してサマリーと目次を生成.
        
        Args:
            content: 章の内容
            chapter_title: 章のタイトル
            output_title: 出力ファイルのタイトル
            
        Returns:
            サマリー・目次付きのコンテンツ
        """
        # 元のサマリー・目次を削除
        clean_content = self._strip_old_toc_and_summary(content)
        
        # コンテンツが短い場合は目次のみ生成（サマリーなし）
        if len(clean_content) < 1000:
            toc = self._build_toc_from_content(clean_content)
            if toc:
                result = f"# {output_title}\n\n{toc}\n\n---\n\n{clean_content}"
                # 見出し番号を整形し、目次を再生成
                result = self._restructure_headings(result, output_title)
                return result
            return clean_content
        
        # 目次を自動生成（AIなし）
        toc = self._build_toc_from_content(clean_content)
        
        # サマリーをAIで生成
        client = self._get_openai_client()
        model = self._get_model_name()
        
        # コンテンツを短縮（プロンプトサイズ制限）
        content_preview = clean_content[:8000] if len(clean_content) > 8000 else clean_content
        
        prompt = f"""以下のMarkdownコンテンツのサマリーを生成してください。

{content_preview}
{'... (省略) ...' if len(clean_content) > 8000 else ''}

【出力形式】
## サマリー
- 要点1（50文字以内）
- 要点2（50文字以内）
- 要点3（50文字以内）
- 要点4（50文字以内、必要なら）
- 要点5（50文字以内、必要なら）

【注意】
- サマリーは3-5個の要点に絞る
- このコンテンツ固有の内容を要約する（一般的な説明は不要）
- 具体的な数値や用語があれば含める

サマリーのみを出力してください。
"""
        
        try:
            if "gpt-5" in model or "o1" in model or "o3" in model or self._is_azure:
                params = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_completion_tokens": 1000,
                }
                if "5.1" in model or "5-1" in model:
                    params["temperature"] = 0
                response = client.chat.completions.create(**params)
            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_completion_tokens=1000,
                )
            
            summary = response.choices[0].message.content
            if summary:
                summary = summary.strip()
                # "## サマリー"が含まれていない場合は追加
                if not summary.startswith('## サマリー'):
                    summary = f"## サマリー\n{summary}"
                
                # 最終的なコンテンツを構築
                result = f"# {output_title}\n\n{summary}\n\n{toc}\n\n---\n\n{clean_content}"
                
                # 見出し番号を整形し、目次を再生成
                result = self._restructure_headings(result, output_title)
                return result
            
        except Exception as e:
            self.logger.warning(f"  サマリー生成失敗: {e}")
        
        # サマリー生成失敗時も目次は付与
        if toc:
            result = f"# {output_title}\n\n{toc}\n\n---\n\n{clean_content}"
            # 見出し番号を整形し、目次を再生成
            result = self._restructure_headings(result, output_title)
            return result
        return clean_content
    
    def _restructure_headings(self, content: str, output_title: str) -> str:
        """見出し番号を連番に振り直し、目次を再生成する（GPT-5.1使用）.
        
        Args:
            content: Markdownコンテンツ（サマリー・目次・本文を含む）
            output_title: 出力ファイルのタイトル
            
        Returns:
            見出し番号が整形され、目次が再生成されたコンテンツ
        """
        # ### 見出しを抽出
        lines = content.split('\n')
        headings = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('### '):
                headings.append({
                    'line_idx': i,
                    'original': stripped,
                    'title': stripped[4:].strip()
                })
            elif stripped.startswith('#### '):
                headings.append({
                    'line_idx': i,
                    'original': stripped,
                    'title': stripped[5:].strip(),
                    'is_sub': True
                })
        
        if not headings:
            return content
        
        # GPT-5.1で見出しを整形
        client = self._get_openai_client()
        model = self._get_model_name()
        
        heading_list = '\n'.join([h['title'] for h in headings if not h.get('is_sub')])
        
        prompt = f"""以下のMarkdown見出しリストを、連番で整形してください。

【現在の見出し】
{heading_list}

【ルール】
1. 1.1, 1.2, 1.3... のような連番形式に統一する
2. 元の見出しの意味・内容は変えない
3. 番号の後にスペースを入れる（例: "1.1 タイトル"）
4. 大きなトピックの区切りで番号をリセットしない（1から最後まで通し番号）
5. 元々番号がついている場合は削除して振り直す

【出力形式】
各行に「元の見出し → 新しい見出し」の形式で出力してください。
例:
7.1 対応開始・完了時の操作セクション概要 → 1.1 対応開始・完了時の操作セクション概要
7.2 手上げ（タスク担当登録）の重要性 → 1.2 手上げ（タスク担当登録）の重要性
"""
        
        try:
            if "gpt-5" in model or "o1" in model or "o3" in model or self._is_azure:
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
            
            result_text = response.choices[0].message.content
            if not result_text:
                return content
            
            # マッピングを解析
            mapping = {}
            for line in result_text.strip().split('\n'):
                if ' → ' in line:
                    parts = line.split(' → ')
                    if len(parts) == 2:
                        old_title = parts[0].strip()
                        new_title = parts[1].strip()
                        mapping[old_title] = new_title
            
            if not mapping:
                return content
            
            # 本文の見出しを置換
            new_lines = lines.copy()
            for h in headings:
                if h.get('is_sub'):
                    continue
                title = h['title']
                # 番号を除去した形でもマッチング
                clean_title = re.sub(r'^\d+\.?\d*\.?\s*', '', title).strip()
                
                # マッピングから新しいタイトルを探す
                new_title = None
                for old, new in mapping.items():
                    old_clean = re.sub(r'^\d+\.?\d*\.?\s*', '', old).strip()
                    if old_clean == clean_title or old == title:
                        new_title = new
                        break
                
                if new_title:
                    new_lines[h['line_idx']] = f"### {new_title}"
            
            # 新しいコンテンツを構築
            new_content = '\n'.join(new_lines)
            
            # サマリーと本文を分離
            parts = new_content.split('\n---\n', 1)
            if len(parts) == 2:
                header_part = parts[0]
                body_part = parts[1]
                
                # 新しい目次を生成
                new_toc = self._build_toc_from_content(body_part)
                
                # サマリー部分を抽出
                summary_section = ""
                if '## サマリー' in header_part:
                    summary_match = re.search(r'(## サマリー.*?)(?=## 目次|$)', header_part, re.DOTALL)
                    if summary_match:
                        summary_section = summary_match.group(1).strip()
                
                # タイトルを抽出
                title_match = re.match(r'^# (.+?)$', header_part, re.MULTILINE)
                title = title_match.group(1) if title_match else output_title
                
                # 再構築
                if new_toc and summary_section:
                    return f"# {title}\n\n{summary_section}\n\n{new_toc}\n\n---\n\n{body_part}"
                elif new_toc:
                    return f"# {title}\n\n{new_toc}\n\n---\n\n{body_part}"
                else:
                    return new_content
            
            return new_content
            
        except Exception as e:
            self.logger.warning(f"  見出し整形失敗: {e}")
            return content
    
    # =========================================================================
    # Step 5: マージ処理（AIリライト）
    # =========================================================================
    
    def _merge_contents(
        self,
        sources: List[Dict],
        file_contents: Dict[str, str],
        summaries: List[Dict],
        output_title: str
    ) -> Tuple[str, List[Tuple[str, str, str]]]:
        """複数のソースをマージ.
        
        情報保持ルールを厳守:
        - 情報の省略・削除は絶対禁止
        - 重複のみ排除
        - サマリー・目次を再生成
        
        Args:
            sources: ソース情報のリスト
            file_contents: ファイル内容のマップ
            summaries: サマリー情報
            output_title: 出力ファイルのタイトル
            
        Returns:
            (マージされた内容, [(元ファイル, 章タイトル, 画像プレフィックス), ...])
        """
        # 各ソースの内容を収集
        contents_to_merge = []
        source_info = []  # (file_name, chapter_title, image_prefix)
        
        for i, src in enumerate(sources):
            file_name = src.get("file", "")
            chapter_ids = src.get("chapter_ids")  # 新形式
            chapters = src.get("chapters")  # 旧形式（後方互換性）
            is_split = src.get("is_split", False)
            prefix = f"src{i+1:02d}"
            
            content = file_contents.get(file_name, "")
            if not content:
                continue
            
            if is_split and (chapter_ids or chapters):
                # 章分割
                if chapter_ids:
                    split_results = self._extract_by_chapter_ids(
                        content, chapter_ids, file_name
                    )
                else:
                    split_results = self._split_by_chapters(
                        content, chapters, summaries, file_name
                    )
                for ch_title, ch_content, _ in split_results:
                    contents_to_merge.append(ch_content)
                    source_info.append((file_name, ch_title, prefix))
            else:
                # ファイル全体
                contents_to_merge.append(content)
                source_info.append((file_name, None, prefix))
        
        if len(contents_to_merge) == 1:
            # 1つだけの場合はそのまま返す
            return contents_to_merge[0], source_info
        
        # 合計文字数チェック
        total_chars = sum(len(c) for c in contents_to_merge)
        
        if total_chars > MAX_INTEGRATION_CHARS:
            # 制限超過: 単純連結（元の構造を保持）
            self.logger.info(f"    📋 文字数制限超過 ({total_chars:,}文字) → 単純連結")
            return self._concatenate_simple(contents_to_merge, source_info), source_info
        
        # AIリライト（情報保持必須）
        self.logger.info(f"    🤖 AIリライト統合 ({total_chars:,}文字)")
        return self._merge_with_ai(contents_to_merge, source_info, output_title), source_info
    
    def _concatenate_simple(
        self, 
        contents: List[str], 
        source_info: List[Tuple[str, str, str]]
    ) -> str:
        """単純連結（AIなし）."""
        result_parts = []
        
        for i, (content, info) in enumerate(zip(contents, source_info)):
            file_name, chapter_title, _ = info
            
            if i == 0:
                result_parts.append(content)
            else:
                separator = f"\n\n---\n\n## 【{file_name}】"
                if chapter_title:
                    separator += f" - {chapter_title}"
                separator += "\n\n"
                
                # 最初の # タイトル行をスキップ
                lines = content.split('\n')
                skip_title = True
                filtered_lines = []
                for line in lines:
                    if skip_title and line.strip().startswith('# '):
                        skip_title = False
                        continue
                    filtered_lines.append(line)
                
                result_parts.append(separator + '\n'.join(filtered_lines))
        
        return '\n'.join(result_parts)
    
    def _merge_with_ai(
        self,
        contents: List[str],
        source_info: List[Tuple[str, str, str]],
        output_title: str
    ) -> str:
        """AIでマージ（情報保持必須）."""
        client = self._get_openai_client()
        model = self._get_model_name()
        
        # コンテンツを整形
        contents_text = ""
        for content, info in zip(contents, source_info):
            file_name, chapter_title, _ = info
            header = f"【{file_name}】"
            if chapter_title:
                header += f" - {chapter_title}"
            contents_text += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{header}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{content}
"""
        
        prompt = f"""以下の複数ドキュメントを1つのMarkdownに統合してください。

{contents_text}

【絶対禁止事項】
- **情報の省略・削除（絶対禁止）**
- **表の行の省略（絶対禁止）**
- **数値・固有名詞の変更（絶対禁止）**

【必須事項】
1. すべての情報を維持してください
2. 重複する内容のみ1回だけ記載（ただし情報は落とさない）
3. サマリー（3-5個の要点）を冒頭に追加
4. 目次を再生成（ページ内リンク形式）
5. 見出しレベルを統一
6. 引用記法 `>` は使用しない

【出力構成】
# {output_title}

## サマリー
- 要点1
- 要点2
- ...

## 目次
- [セクション1](#セクション1)
- [セクション2](#セクション2)
- ...

---

## 本文
（すべての情報を含む）

Markdownのみを出力してください。
"""
        
        try:
            if "gpt-5" in model or "o1" in model or "o3" in model or self._is_azure:
                params = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "あなたは業務マニュアルを統合する専門家です。情報を省略せずにすべて維持してください。"},
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
                        {"role": "system", "content": "あなたは業務マニュアルを統合する専門家です。情報を省略せずにすべて維持してください。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0,
                    max_completion_tokens=16384,
                )
            
            result = response.choices[0].message.content.strip()
            
            # マークダウンコードブロックを除去
            if result.startswith("```markdown"):
                result = result[len("```markdown"):].strip()
            if result.startswith("```"):
                result = result[3:].strip()
            if result.endswith("```"):
                result = result[:-3].strip()
            
            # AIが生成した目次を実際の見出しから再構築
            result = self._rebuild_toc_in_content(result, output_title)
            
            return result
            
        except Exception as e:
            self.logger.error(f"AI merge failed: {e}")
            # フォールバック: 単純連結（元の構造を保持）
            return self._concatenate_simple(contents, source_info)
    
    # =========================================================================
    # Step 6: ファイル生成
    # =========================================================================
    
    def _generate_frontmatter(
        self,
        sources: List[Dict],
        mapping_id: str,
        generated_at: str
    ) -> str:
        """YAMLフロントマターを生成."""
        source_list = []
        for src in sources:
            source_entry = {"file": src.get("file", "")}
            # chapter_ids から章タイトルを取得
            chapter_ids = src.get("chapter_ids")
            chapters = src.get("chapters")
            
            if chapter_ids:
                # 章IDから章タイトルを取得
                chapter_titles = []
                for ch_id in chapter_ids:
                    ch_info = self._chapter_id_map.get(ch_id)
                    if ch_info:
                        chapter = ch_info['chapter']
                        chapter_titles.append(chapter.get('original_title', ch_id))
                    else:
                        chapter_titles.append(ch_id)
                source_entry["chapters"] = chapter_titles
            elif chapters:
                source_entry["chapters"] = chapters
            
            source_list.append(source_entry)
        
        yaml_content = f"""---
source:
"""
        for src in source_list:
            yaml_content += f"  - file: \"{src['file']}\"\n"
            if src.get("chapters"):
                yaml_content += f"    chapters:\n"
                for ch in src["chapters"]:
                    yaml_content += f"      - \"{ch}\"\n"
        
        yaml_content += f"""generated_at: \"{generated_at}\"
mapping_id: \"{mapping_id}\"
---

"""
        return yaml_content
    
    def _process_images(
        self,
        content: str,
        source_info: List[Tuple[str, str, str]],
        file_images: Dict[str, List[Path]],
        output_images_dir: Path
    ) -> Tuple[str, List[Tuple[Path, str]]]:
        """画像パスを処理し、コピー対象をリストアップ.
        
        Args:
            content: Markdown内容
            source_info: ソース情報 [(file_name, chapter_title, prefix), ...]
            file_images: ファイル名→画像パスリスト
            output_images_dir: 出力先imagesディレクトリ
            
        Returns:
            (更新されたcontent, [(src_path, dst_name), ...])
        """
        image_mapping = []  # [(src_path, dst_name), ...]
        path_replacements = {}  # {old_path: new_path}
        
        for file_name, _, prefix in source_info:
            images = file_images.get(file_name, [])
            for img_path in images:
                new_name = f"{prefix}_{img_path.name}"
                image_mapping.append((img_path, new_name))
                
                # 様々な旧パスパターンに対応
                old_patterns = [
                    f"../04_images/{img_path.name}",
                    f"../03_images/{img_path.name}",
                    f"./images/{img_path.name}",
                    f"images/{img_path.name}",
                ]
                for old_path in old_patterns:
                    path_replacements[old_path] = f"./images/{new_name}"
        
        # パス置換
        updated_content = content
        for old_path, new_path in path_replacements.items():
            updated_content = updated_content.replace(old_path, new_path)
        
        return updated_content, image_mapping
    
    def _generate_glossary(self) -> str:
        """用語集を生成（terms.jsonをマージ）."""
        all_terms = []
        
        for item in sorted(self.target_dir.iterdir()):
            if item.is_dir() and not item.name.startswith("_"):
                terms_path = item / FOLDER_FORMATTED_MARKDOWN / "terms.json"
                if terms_path.exists():
                    try:
                        with open(terms_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            terms = data.get("terms", [])
                            all_terms.extend(terms)
                    except (json.JSONDecodeError, OSError) as e:
                        self.logger.warning(f"  ⚠️ terms.json読み込み失敗: {item.name} - {e}")
        
        if not all_terms:
            return "# 用語集\n\n用語が見つかりませんでした。\n"
        
        # 重複排除・マージ
        merged = {}
        for t in all_terms:
            term = t.get("term", "").strip()
            if not term:
                continue
            
            if term not in merged:
                merged[term] = {
                    "term": term,
                    "description": t.get("description", ""),
                    "flag": t.get("flag", 1)
                }
            else:
                existing_desc = merged[term]["description"]
                new_desc = t.get("description", "")
                if len(new_desc) > len(existing_desc):
                    merged[term]["description"] = new_desc
                if t.get("flag", 1) == 0:
                    merged[term]["flag"] = 0
        
        lines = [
            "# 用語集",
            "",
            f"※ 全{len(merged)}件の専門用語・略語を収録",
            "",
            "| 用語 | 説明 | 要確認 |",
            "|------|------|--------|"
        ]
        
        for term in sorted(merged.keys()):
            data = merged[term]
            description = data["description"].replace("|", "\\|")
            flag = "⚠️" if data["flag"] == 1 else ""
            lines.append(f"| {term} | {description} | {flag} |")
        
        self.logger.info(f"  📚 用語集生成: {len(merged)}件")
        return "\n".join(lines)
    
    def _generate_mapping_json(self, proposal: Dict, summaries: List[Dict]) -> Dict:
        """mapping.jsonを生成."""
        now = datetime.now().isoformat()
        
        # サマリー情報をマップ化
        summary_map = {s['file_name']: s for s in summaries}
        
        mappings = []
        mapping_id = 0
        
        for category in proposal.get("categories", []):
            cat_id = category.get("id", "")
            
            for f in category.get("files", []):
                mapping_id += 1
                m_id = f"m{mapping_id:03d}"
                
                sources_detail = []
                for src in f.get("sources", []):
                    file_name = src.get("file", "")
                    file_summary = summary_map.get(file_name, {})
                    
                    # 章のline_rangeを計算
                    line_ranges = []
                    if src.get("chapters"):
                        for ch_title in src.get("chapters", []):
                            for ch in file_summary.get("chapters", []):
                                if ch_title in ch.get("original_title", "") or ch_title in ch.get("clean_title", ""):
                                    line_ranges.append([ch["line_start"], ch["line_end"]])
                    
                    sources_detail.append({
                        "file": file_name,
                        "original_path": f"input/{file_name}.*",
                        "formatted_path": f"pre-knowledge/{file_name}/{FOLDER_FORMATTED_MARKDOWN}/{FILE_ENHANCED_MD}",
                        "hash": file_summary.get("hash", ""),
                        "chapters": src.get("chapters"),
                        "line_ranges": line_ranges if line_ranges else None,
                        "is_split": src.get("is_split", False)
                    })
                
                mappings.append({
                    "id": m_id,
                    "input": sources_detail,
                    "output": {
                        "category": cat_id,
                        "file": f.get("output_name", ""),
                        "path": f"knowledge/{cat_id}/{f.get('output_name', '')}",
                        "title": f.get("title", ""),
                        "is_merged": f.get("is_merged", False)
                    }
                })
        
        return {
            "version": "2.0",
            "created_at": now,
            "updated_at": now,
            "model": self._get_model_name(),
            "mappings": mappings,
            "categories": [
                {
                    "id": cat.get("id", ""),
                    "name": cat.get("name", ""),
                    "description": cat.get("description", "")
                }
                for cat in proposal.get("categories", [])
            ],
            "history": [
                {
                    "date": now,
                    "action": "create",
                    "description": "初回生成"
                }
            ]
        }
    
    def _generate_readme(self, proposal: Dict, mapping_data: Dict) -> str:
        """readme.mdを生成."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        title = proposal.get("title", "ナレッジベース")
        description = proposal.get("description", "")
        
        lines = [
            f"# {title}",
            "",
        ]
        
        if description:
            lines.extend([description, ""])
        
        # フォルダ構成
        lines.extend([
            "## フォルダ構成",
            "",
            "| カテゴリ | 説明 |",
            "|---------|------|"
        ])
        
        for cat in mapping_data.get("categories", []):
            lines.append(f"| {cat['id']}/ | {cat.get('description', cat.get('name', ''))} |")
        
        lines.append("")
        
        # 入力→ナレッジ対応表
        lines.extend([
            "## 入力 → ナレッジ 対応表",
            "",
            "| 入力ファイル | 章 | ナレッジ |",
            "|-------------|-----|---------|"
        ])
        
        for m in mapping_data.get("mappings", []):
            for inp in m.get("input", []):
                file_name = inp.get("file", "")[:40]
                chapters = inp.get("chapters")
                ch_str = ", ".join(chapters)[:30] if chapters else "-"
                output_path = m.get("output", {}).get("path", "").replace("knowledge/", "")
                lines.append(f"| {file_name} | {ch_str} | {output_path} |")
        
        lines.append("")
        
        # 更新履歴
        lines.extend([
            "## 更新履歴",
            "",
            "| 日時 | 更新内容 |",
            "|-----|---------|"
        ])
        
        for h in mapping_data.get("history", []):
            date_str = h.get("date", "")[:16].replace("T", " ")
            lines.append(f"| {date_str} | {h.get('description', '')} |")
        
        lines.extend([
            "",
            "---",
            "",
            f"*生成日時: {now}*",
            f"*使用モデル: {mapping_data.get('model', '')}*"
        ])
        
        return "\n".join(lines)
    
    # =========================================================================
    # メイン処理
    # =========================================================================
    
    def run(self) -> int:
        """デプロイメント処理を実行.
        
        Returns:
            Exit code (0 for success)
        """
        self.logger.info("=" * 60)
        self.logger.info("KNOWLEDGE DEPLOYER - Stage 3 (v2)")
        self.logger.info("=" * 60)
        
        # Step 1: ファイル収集
        self.logger.info("Step 1: ファイル一覧取得中...")
        files = self._list_formatted_files()
        
        if not files:
            self.logger.error("処理対象のファイルが見つかりません")
            return 1
        
        self.results["statistics"]["total_files"] = len(files)
        self.logger.info(f"  {len(files)} ファイルを検出")
        
        # Step 2: ファイル分析
        self.logger.info("Step 2: ファイル内容を分析中...")
        summaries = []
        file_contents = {}
        file_images = {}
        
        for file_name, formatted_path in files:
            self.logger.info(f"  分析中: {file_name}")
            
            content = formatted_path.read_text(encoding="utf-8")
            file_contents[file_name] = content
            
            # 画像取得
            images_dir = formatted_path.parent.parent / FOLDER_IMAGES
            if not images_dir.exists():
                images_dir = formatted_path.parent.parent / "03_images"
            
            images = []
            if images_dir.exists():
                images = [
                    p for p in sorted(images_dir.iterdir())
                    if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
                ]
            file_images[file_name] = images
            
            # サマリー抽出
            summary = self._extract_summary_from_formatted(content, file_name)
            
            # chapter_summaries.jsonを読み込み（あれば）
            chapter_summaries_path = formatted_path.parent / "chapter_summaries.json"
            if chapter_summaries_path.exists():
                try:
                    with open(chapter_summaries_path, "r", encoding="utf-8") as f:
                        chapter_summaries_data = json.load(f)
                    # 各章にサマリーを追加
                    chapter_summaries = chapter_summaries_data.get("chapters", [])
                    for ch in summary["chapters"]:
                        # タイトルでマッチング
                        for cs in chapter_summaries:
                            if cs.get("title") == ch.get("original_title") or cs.get("title") == ch.get("clean_title"):
                                ch["chapter_summary"] = cs.get("summary", [])
                                break
                    self.logger.debug(f"    chapter_summaries.json を読み込み: {len(chapter_summaries)}章")
                except Exception as e:
                    self.logger.debug(f"    chapter_summaries.json 読み込み失敗: {e}")
            
            summaries.append(summary)
            
            self.logger.info(f"    → {summary['char_count']:,}文字, {len(summary['chapters'])}章")
        
        # Step 3: 構成提案（再構成ループ対応）
        user_feedback = None
        while True:
            if user_feedback:
                self.logger.info("Step 3: フォルダ構成を再提案中...")
            else:
                self.logger.info("Step 3: フォルダ構成を提案中...")
            
            proposal = self._propose_structure(summaries, file_contents, user_feedback)
            
            # Step 4: ユーザー確認
            approved, feedback = self._display_proposal(proposal, summaries)
            
            if approved:
                # ユーザーが承認した場合、ループを抜けて実行
                break
            elif feedback is not None:
                # 再構成リクエストの場合、フィードバックを使って再提案
                user_feedback = feedback
                continue
            else:
                # キャンセルの場合
                self.logger.info("処理がキャンセルされました")
                return 0
        
        # Step 5: フォルダ・ファイル生成
        self.logger.info("\nStep 5: ナレッジを生成中...")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        generated_at = datetime.now().isoformat()
        mapping_id_counter = 0
        
        for category in sorted(proposal.get("categories", []), key=lambda x: x.get("order", 999)):
            cat_id = category.get("id", "")
            self.logger.info(f"  📁 {cat_id}/")
            
            # カテゴリフォルダ作成
            cat_dir = self.output_dir / cat_id
            cat_dir.mkdir(parents=True, exist_ok=True)
            images_dir = cat_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            
            for f in category.get("files", []):
                mapping_id_counter += 1
                m_id = f"m{mapping_id_counter:03d}"
                output_name = f.get("output_name", "")
                # ファイル名として使えない文字をサニタイズ
                output_name = re.sub(r'[/\\:*?"<>|]', '・', output_name)
                sources = f.get("sources", [])
                is_merged = f.get("is_merged", False)
                
                self.logger.info(f"     ├─ 📄 {output_name}")
                
                # コンテンツ取得（分割 or マージ）
                if is_merged:
                    self.results["statistics"]["merged_files"] += 1
                    content, source_info = self._merge_contents(
                        sources, file_contents, summaries, f.get("title", "")
                    )
                else:
                    # 単一ソース
                    src = sources[0] if sources else {}
                    file_name = src.get("file", "")
                    chapter_ids = src.get("chapter_ids")  # 新形式
                    chapters = src.get("chapters")  # 旧形式（後方互換性）
                    is_split = src.get("is_split", False)
                    
                    if is_split and (chapter_ids or chapters):
                        self.results["statistics"]["split_files"] += 1
                        
                        # 新形式（chapter_ids）を優先
                        if chapter_ids:
                            split_results = self._extract_by_chapter_ids(
                                file_contents.get(file_name, ""),
                                chapter_ids,
                                file_name
                            )
                        else:
                            # 旧形式（後方互換性）
                            split_results = self._split_by_chapters(
                                file_contents.get(file_name, ""),
                                chapters,
                                summaries,
                                file_name
                            )
                        
                        if split_results:
                            # 複数の章がある場合はすべて結合
                            if len(split_results) == 1:
                                ch_title, content, _ = split_results[0]
                            else:
                                # 複数章を結合
                                content_parts = []
                                ch_title = None
                                for ch_t, ch_content, _ in split_results:
                                    content_parts.append(ch_content)
                                    if ch_title is None:
                                        ch_title = ch_t
                                content = "\n\n---\n\n".join(content_parts)
                            
                            # 分割されたコンテンツにサマリーを追加
                            if self.generate_split_summary:
                                self.logger.debug(f"    サマリー生成中...")
                                content = self._generate_chapter_summary(
                                    content, ch_title or "", f.get("title", "")
                                )
                            
                            source_info = [(file_name, ch[0], "src01") for ch in split_results]
                        else:
                            self.logger.warning(f"    章が見つからないため全体をコピー: {file_name}")
                            content = file_contents.get(file_name, "")
                            source_info = [(file_name, None, "src01")]
                    else:
                        content = file_contents.get(file_name, "")
                        source_info = [(file_name, None, "src01")]
                
                # 画像処理
                content, image_mapping = self._process_images(
                    content, source_info, file_images, images_dir
                )
                
                # 画像コピー
                for src_path, dst_name in image_mapping:
                    dst_path = images_dir / dst_name
                    try:
                        if not dst_path.exists():
                            shutil.copy2(src_path, dst_path)
                    except Exception as e:
                        self.logger.warning(f"    画像コピー失敗: {src_path} -> {e}")
                
                # フロントマター追加
                frontmatter = self._generate_frontmatter(sources, m_id, generated_at)
                final_content = frontmatter + content
                
                # ファイル保存
                output_path = cat_dir / output_name
                output_path.write_text(final_content, encoding="utf-8")
                
                self.results["statistics"]["total_knowledge"] += 1
        
        self.results["statistics"]["total_categories"] = len(proposal.get("categories", []))
        
        # Step 6: 用語集生成
        if self.generate_glossary:
            self.logger.info("Step 6: 用語集を生成中...")
            glossary_content = self._generate_glossary()
            glossary_path = self.output_dir / FILE_GLOSSARY
            glossary_path.write_text(glossary_content, encoding="utf-8")
        
        # Step 7: mapping.json, readme.md生成
        self.logger.info("Step 7: メタデータファイルを生成中...")
        
        mapping_data = self._generate_mapping_json(proposal, summaries)
        mapping_path = self.output_dir / FILE_MAPPING
        with open(mapping_path, "w", encoding="utf-8") as f:
            json.dump(mapping_data, f, ensure_ascii=False, indent=2)
        self.logger.info("  ✅ mapping.json 生成完了")
        
        readme_content = self._generate_readme(proposal, mapping_data)
        readme_path = self.output_dir / FILE_README
        readme_path.write_text(readme_content, encoding="utf-8")
        self.logger.info("  ✅ readme.md 生成完了")
        
        # 完了サマリー
        self._print_summary()
        
        return 0
    
    def _print_summary(self) -> None:
        """完了サマリーを表示."""
        stats = self.results["statistics"]
        
        print("\n" + "=" * 60)
        print("DEPLOYMENT COMPLETE")
        print("=" * 60)
        
        print(f"\n📊 Statistics:")
        print(f"  入力ファイル数: {stats['total_files']}")
        print(f"  カテゴリ数: {stats['total_categories']}")
        print(f"  生成ナレッジ数: {stats['total_knowledge']}")
        print(f"    ├── 分割: {stats['split_files']}")
        print(f"    └── マージ: {stats['merged_files']}")
        
        print(f"\n📁 Output: {self.output_dir}")
        print(f"   ├── [カテゴリ]/[ナレッジ].md")
        print(f"   ├── mapping.json")
        print(f"   └── readme.md")
        
        if self.results.get("errors"):
            print(f"\n⚠️ Errors:")
            for error in self.results["errors"][:5]:
                print(f"  - {error}")
        
        print("\n" + "=" * 60)
