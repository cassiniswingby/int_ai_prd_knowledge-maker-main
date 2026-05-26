"""Stage 3: Knowledge Deployer - Integrate and deploy knowledge.

This module handles the third stage of the knowledge conversion pipeline:
- Analyze formatted.md files from pre-knowledge/ (parse summary/toc, no AI)
- Propose folder structure using AI (with char count limits)
- Integrate multiple files into single knowledge (AI rewrite if <= 80,000 chars)
- Generate glossary from terms.json files (no AI, just merge)
- Generate _sources.md, and _global/ files

改善点（2025年12月）:
- ファイル分析: formatted.mdをパースしてサマリー・目次を抽出（AI不要）
- 構成提案: 文字数情報を考慮し、80,000文字超の統合を禁止
- 統合処理: 80,000文字超はコピーのみ、以下はAIリライト
- 用語集: terms.jsonをマージ（AI不要）
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .knowledge_config import (
    DocumentFolder,
    DocumentFolderManager,
    FOLDER_FORMATTED_MARKDOWN,
    FOLDER_IMAGES,
    FILE_ENHANCED_MD,
)


logger = logging.getLogger(__name__)


# Output folder constants
FOLDER_GLOBAL = "_global"
FILE_SOURCE_MAPPING = "source_mapping.md"
FILE_STRUCTURE_PROPOSAL = "structure_proposal.json"
FILE_GLOSSARY = "00_用語集.md"

# 統合処理の文字数閾値
MAX_INTEGRATION_CHARS = 80000  # 80,000文字を超える場合は統合しない


class KnowledgeDeployer:
    """Stage 3: Integrate and deploy knowledge from pre-knowledge.
    
    This class handles:
    - Analyzing formatted.md files
    - Proposing folder structure using AI
    - Integrating multiple files into single knowledge
    - Generating glossary and metadata files
    """
    
    def __init__(
        self,
        target_dir: Path,
        output_dir: Path,
        *,
        generate_glossary: bool = True,
        skip_confirmation: bool = False,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Initialize the Knowledge Deployer.
        
        Args:
            target_dir: Path to the pre-knowledge directory
            output_dir: Path to the output knowledge directory
            generate_glossary: Whether to generate glossary
            skip_confirmation: Whether to skip user confirmation (--force mode)
            logger: Optional logger instance
        """
        self.target_dir = Path(target_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.generate_glossary = generate_glossary
        self.skip_confirmation = skip_confirmation
        self.logger = logger or self._build_logger()
        
        self._client = None
        self.results = self._fresh_results()
        
        self.logger.info(
            f"KnowledgeDeployer initialized: target={self.target_dir}, output={self.output_dir}"
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
        if hasattr(self, '_model_name'):
            return self._model_name
        # フォールバック: クライアント初期化前に呼ばれた場合
        from ..utils.openai_client import get_model_name
        return get_model_name(purpose="chat", is_azure=getattr(self, '_is_azure', False))
    
    def _fresh_results(self) -> Dict:
        """Create fresh results structure."""
        return {
            "metadata": {
                "start_time": datetime.now().isoformat(),
            },
            "statistics": {
                "total_files": 0,
                "total_knowledge": 0,
                "integrated": 0,
                "single": 0,
            },
            "knowledge_list": [],
            "errors": [],
        }
    
    def _save_results(self) -> None:
        """Save results to _global folder."""
        self.results["metadata"]["end_time"] = datetime.now().isoformat()
        global_dir = self.output_dir / FOLDER_GLOBAL
        global_dir.mkdir(parents=True, exist_ok=True)
        
        results_path = global_dir / "deploy_results.json"
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
    
    def _list_formatted_files(self) -> List[Tuple[str, Path]]:
        """List all formatted.md files in pre-knowledge directory.
        
        Returns:
            List of (document_name, formatted_md_path) tuples
        """
        files = []
        for item in sorted(self.target_dir.iterdir()):
            if item.is_dir() and not item.name.startswith("_"):
                formatted_dir = item / FOLDER_FORMATTED_MARKDOWN
                formatted_md = formatted_dir / FILE_ENHANCED_MD
                if formatted_md.exists():
                    files.append((item.name, formatted_md))
        return files
    
    def _extract_summary_from_formatted(self, content: str, file_name: str) -> Dict:
        """formatted.mdからサマリーと目次をパースして抽出（AI不要）.
        
        Step2で生成されたformatted.mdには既にサマリーと目次が含まれているため、
        それをパースするだけで必要な情報を取得できます。
        
        Args:
            content: Content of the formatted.md file
            file_name: Name of the file
            
        Returns:
            Dictionary with summary, topics, keywords, target_business, char_count
        """
        result = {
            "file_name": file_name,
            "summary": "",
            "topics": [],
            "keywords": [],
            "target_business": "不明",
            "char_count": len(content)
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
        
        # 目次の大見出し（### ）をトピックとして抽出
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('### ') and '目次' not in stripped.lower():
                # "### 1. 業務設計資料の概要" → "業務設計資料の概要"
                topic = re.sub(r'^###\s*\d+\.\s*', '', stripped).strip()
                if topic and topic not in result["topics"]:
                    result["topics"].append(topic)
        
        # キーワードはサマリーから主要な名詞を抽出（簡易版）
        # システム名、略語、カタカナ語を抽出
        keywords_pattern = r'[A-Z]{2,}|[A-Za-z]+(?:システム|ツール)|[ァ-ヴー]{3,}'
        found_keywords = re.findall(keywords_pattern, result["summary"])
        result["keywords"] = list(set(found_keywords))[:10]
        
        # 対象業務はサマリーの最初の文から推測
        if summary_lines:
            first_line = summary_lines[0]
            # 「〜業務」「〜処理」「〜受付」などのパターンを探す
            business_match = re.search(r'([^、。]+(?:業務|処理|受付|管理|設計|運用))', first_line)
            if business_match:
                result["target_business"] = business_match.group(1)
        
        self.logger.debug(f"  Extracted from {file_name}: {len(result['summary'])} chars summary, {len(result['topics'])} topics")
        
        return result
    
    def _propose_structure(self, summaries: List[Dict], file_contents: Dict[str, str]) -> Dict:
        """Propose folder structure using AI.
        
        文字数情報を考慮し、80,000文字を超える統合は禁止します。
        
        Args:
            summaries: List of file summaries (with char_count)
            file_contents: Dictionary mapping file_name to content (for char count validation)
            
        Returns:
            Structure proposal dictionary
        """
        client = self._get_openai_client()
        model = self._get_model_name()
        
        # Format summaries for prompt (with char count)
        summaries_text = ""
        for s in summaries:
            char_count = s.get('char_count', 0)
            summaries_text += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【ファイル】{s['file_name']}
【文字数】{char_count:,}文字
【対象業務】{s.get('target_business', '不明')}
【トピック】{', '.join(s.get('topics', []))}
【キーワード】{', '.join(s.get('keywords', []))}
【要約】
{s.get('summary', '')}
"""
        
        prompt = f"""以下の{len(summaries)}個のドキュメントから、チャットボット用ナレッジベースの全体構成を設計してください。

{summaries_text}

【設計のポイント】
1. **章（chapters）で大分類** - 業務フローに沿った論理的なグループ分け
2. **章の中にナレッジを配置** - 各章に関連するナレッジをまとめる
3. **統一感のある分かりやすい命名** - 全体を見たときに構成が把握できる名前
4. **適切なグルーピング** - 関連する内容は統合、独立した内容は単体

【統合ルール】
- 関連性が明確なもののみ統合（同じ業務フロー、補足関係など）
- **⚠️ 統合後の合計文字数が{MAX_INTEGRATION_CHARS:,}文字を超える場合は統合しない**
- 迷う場合は統合しない（1ファイル = 1ナレッジ）

【出力形式（JSON）- 階層構造】
{{
    "overall_structure": {{
        "title": "○○業務マニュアル",
        "description": "このナレッジベースは○○についての情報を集約したものです。"
    }},
    "chapters": [
        {{
            "order": 1,
            "id": "01_契約関連",
            "name": "契約関連",
            "description": "契約開始から審査までの流れを説明",
            "knowledge": [
                {{
                    "order": 1,
                    "id": "01_申込受付",
                    "name": "申込受付",
                    "sources": ["【○○PJ】業務フロー_受付_ver1.36", "【○○PJ】業務フロー_異動_ver1.08"],
                    "summary": "申込受付から審査開始までのフロー",
                    "reason": "両ファイルとも申込に関する内容。合計50,000文字で制限内。",
                    "is_integrated": true
                }},
                {{
                    "order": 2,
                    "id": "02_審査契約",
                    "name": "審査契約",
                    "sources": ["【○○】第2章_契約開始申込・審査_ver.4.00"],
                    "summary": "審査基準と承認プロセス",
                    "reason": "単体で十分な内容量",
                    "is_integrated": false
                }}
            ]
        }},
        {{
            "order": 2,
            "id": "02_顧客管理",
            "name": "顧客管理",
            "description": "顧客情報の変更・解約処理",
            "knowledge": [...]
        }}
    ]
}}

【注意】
- chaptersは章（大分類）、その中にknowledge（ナレッジ）を配置
- 章のorderは1から開始、ナレッジのorderも各章内で1から開始
- idは「XX_名前」形式（orderと連動）
- 全ファイルをいずれかの章に必ず配置すること
- **⚠️ sourcesには【ファイル】で示した正確なファイル名をそのまま使用すること（短縮・省略禁止）**

JSONのみを出力してください。
"""
        
        try:
            if "gpt-5" in model or "o1" in model or "o3" in model or getattr(self, '_is_azure', False):
                params = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_completion_tokens": 8000,
                }
                if "5.1" in model or "5-1" in model:
                    params["temperature"] = 0
                response = client.chat.completions.create(**params)
            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=8000,
                )
            
            result_text = response.choices[0].message.content.strip()
            
            # Remove markdown code block if present
            if result_text.startswith("```"):
                result_text = re.sub(r"^```(?:json)?\n?", "", result_text)
                result_text = re.sub(r"\n?```$", "", result_text)
            
            proposal = json.loads(result_text)
            
            # Post-process: validate char count limits
            proposal = self._validate_char_limits(proposal, file_contents)
            
            return proposal
            
        except Exception as e:
            self.logger.error(f"Failed to propose structure: {e}")
            # Fallback: each file becomes a single knowledge in one chapter
            return {
                "overall_structure": {
                    "title": "ナレッジベース",
                    "description": "自動生成（AI提案失敗のためデフォルト構成）"
                },
                "chapters": [
                    {
                        "order": 1,
                        "id": "01_全体",
                        "name": "全体",
                        "description": "自動生成（AI提案失敗のためデフォルト構成）",
                        "knowledge": [
                            {
                                "order": i + 1,
                                "id": f"{i + 1:02d}_{s['file_name']}",
                                "name": s['file_name'],
                                "sources": [s['file_name']],
                                "summary": s.get("summary", "")[:50] if s.get("summary") else "",
                                "reason": "自動生成（AI提案失敗のため1ファイル=1ナレッジ）",
                                "is_integrated": False,
                                "total_chars": len(file_contents.get(s['file_name'], ""))
                            }
                            for i, s in enumerate(summaries)
                        ]
                    }
                ]
            }
    
    def _normalize_source_name(self, source: str, available_names: set) -> str:
        """AIが返したsourcesのファイル名をfile_contentsのキーと照合して正規化.
        
        AIが「【ファイル】」プレフィックスを付けたり、ファイル名を微妙に変えたりする
        ことがあるため、実際のファイル名にマッチングする。
        
        Args:
            source: AIが返したソースファイル名
            available_names: file_contentsで利用可能なファイル名のセット
            
        Returns:
            正規化されたファイル名（見つからない場合は元のsourceをそのまま返す）
        """
        # 完全一致があればそのまま返す
        if source in available_names:
            return source
        
        # 「【ファイル】」プレフィックスを除去して再チェック
        source_clean = source.replace("【ファイル】", "").strip()
        if source_clean in available_names:
            return source_clean
        
        # 部分一致を試みる（AIがファイル名を短縮した場合への対応）
        for name in available_names:
            # source_cleanがnameに含まれる、またはnameがsource_cleanに含まれる
            if source_clean in name or name in source_clean:
                return name
        
        # 最後の手段：前方一致
        for name in available_names:
            if name.startswith(source_clean[:10]) or source_clean.startswith(name[:10]):
                return name
        
        self.logger.warning(f"  ⚠️ ファイル名マッチング失敗: {source}")
        return source  # フォールバック
    
    def _validate_char_limits(self, proposal: Dict, file_contents: Dict[str, str]) -> Dict:
        """AIの提案を検証し、文字数制限を超える統合を分割.
        
        階層構造（chapters > knowledge）に対応。
        
        Args:
            proposal: AI提案の構成（chapters配列を含む）
            file_contents: ファイル名→コンテンツのマップ
            
        Returns:
            検証・修正済みの提案
        """
        validated_chapters = []
        available_names = set(file_contents.keys())
        
        # chaptersをorderでソートしてから処理
        chapters = sorted(proposal.get("chapters", []), key=lambda x: x.get("order", 999))
        
        for chapter_idx, chapter in enumerate(chapters):
            validated_knowledge = []
            
            # 章内のknowledgeをorderでソート
            knowledge_list = sorted(chapter.get("knowledge", []), key=lambda x: x.get("order", 999))
            
            for k in knowledge_list:
                # sourcesの正規化
                raw_sources = k.get("sources", [])
                sources = [self._normalize_source_name(s, available_names) for s in raw_sources]
                k["sources"] = sources  # 正規化されたソースで更新
                
                # 合計文字数を計算
                total_chars = sum(len(file_contents.get(s, "")) for s in sources)
                
                if total_chars > MAX_INTEGRATION_CHARS and len(sources) > 1:
                    # 制限超過の場合、各ファイルを単体ナレッジに分割
                    self.logger.warning(f"  ⚠️ {k.get('name', 'Unknown')} ({total_chars:,}文字) は制限超過のため分割")
                    
                    for source in sources:
                        char_count = len(file_contents.get(source, ""))
                        validated_knowledge.append({
                            "order": len(validated_knowledge) + 1,
                            "id": f"{len(validated_knowledge) + 1:02d}_{source}",
                            "name": source,
                            "sources": [source],
                            "summary": k.get("summary", ""),
                            "reason": f"文字数制限（{MAX_INTEGRATION_CHARS:,}文字）超過のため単体ナレッジ化（{char_count:,}文字）",
                            "is_integrated": False,
                            "total_chars": char_count
                        })
                else:
                    # 制限内、またはもともと単体
                    k["order"] = len(validated_knowledge) + 1
                    k["total_chars"] = total_chars
                    k["id"] = f"{len(validated_knowledge) + 1:02d}_{k.get('name', 'unknown')}"
                    validated_knowledge.append(k)
            
            # 章を追加
            validated_chapter = {
                "order": chapter_idx + 1,
                "id": f"{chapter_idx + 1:02d}_{chapter.get('name', 'unknown')}",
                "name": chapter.get("name", ""),
                "description": chapter.get("description", ""),
                "knowledge": validated_knowledge
            }
            validated_chapters.append(validated_chapter)
        
        # overall_structureを保持
        result = {"chapters": validated_chapters}
        if "overall_structure" in proposal:
            result["overall_structure"] = proposal["overall_structure"]
        
        return result
    
    def _display_proposal(self, proposal: Dict, summaries: List[Dict]) -> bool:
        """Display the structure proposal and get user confirmation.
        
        階層構造（chapters > knowledge）に対応。
        
        Args:
            proposal: Structure proposal from AI（chapters配列を含む）
            summaries: File summaries
            
        Returns:
            True if user confirms, False otherwise
        """
        chapters = proposal.get("chapters", [])
        overall = proposal.get("overall_structure", {})
        
        # サマリーから文字数マップを作成
        char_counts = {s['file_name']: s.get('char_count', 0) for s in summaries}
        
        # ナレッジ総数を計算
        total_knowledge = sum(len(ch.get("knowledge", [])) for ch in chapters)
        
        print("\n" + "═" * 80)
        print("                        📂 ナレッジベース構成提案")
        print("═" * 80)
        
        # 全体構成を表示
        if overall:
            print()
            title = overall.get("title", "ナレッジベース")
            description = overall.get("description", "")
            print(f"  📚 {title}")
            if description:
                print(f"     {description}")
        
        print()
        print("─" * 80)
        print()
        print(f"  pre-knowledge/ 内の {len(summaries)} ファイルを分析しました。")
        print(f"  {len(chapters)} つの章、{total_knowledge} つのナレッジに整理します。")
        print(f"  ※ 統合文字数制限: {MAX_INTEGRATION_CHARS:,}文字")
        print()
        print("─" * 80)
        
        # 階層構造で表示
        for chapter in chapters:
            chapter_id = chapter.get("id", "")
            chapter_name = chapter.get("name", "")
            chapter_desc = chapter.get("description", "")
            knowledge_list = chapter.get("knowledge", [])
            
            print()
            print(f"  📁 {chapter_id}/ - {chapter_name}")
            if chapter_desc:
                print(f"     {chapter_desc}")
            print()
            
            for k in knowledge_list:
                sources = k.get("sources", [])
                total_chars = k.get("total_chars", sum(char_counts.get(s, 0) for s in sources))
                is_integrated = k.get("is_integrated", False)
                k_id = k.get("id", "")
                
                if is_integrated:
                    # 統合グループ
                    print(f"     ├─ 📁 {k_id}/ (統合・AIリライト)")
                    for src in sources:
                        src_chars = char_counts.get(src, 0)
                        print(f"     │     ├─ 📄 {src} ({src_chars:,}文字)")
                    print(f"     │     └─ 合計: {total_chars:,}文字")
                else:
                    # 単体
                    src = sources[0] if sources else 'Unknown'
                    warn = " ⚠️大容量" if total_chars > MAX_INTEGRATION_CHARS else ""
                    print(f"     ├─ 📁 {k_id}/ ← 📄 {src} ({total_chars:,}文字){warn}")
        
        print()
        print("─" * 80)
        print()
        print("  【サマリー】")
        print("  ┌─────────────────────┬──────────┬────────────┬──────────────────────────┐")
        print("  │ 章/ナレッジ          │ ファイル数 │ 合計文字数  │ 処理                      │")
        print("  ├─────────────────────┼──────────┼────────────┼──────────────────────────┤")
        for chapter in chapters:
            chapter_id = chapter.get("id", "")[:19]
            ch_knowledge = chapter.get("knowledge", [])
            ch_files = sum(len(k.get("sources", [])) for k in ch_knowledge)
            ch_chars = sum(k.get("total_chars", 0) for k in ch_knowledge)
            print(f"  │ {chapter_id:<19} │ {ch_files:>8} │ {ch_chars:>10,} │ {'章':^24} │")
            for k in ch_knowledge:
                sources = k.get("sources", [])
                total_chars = k.get("total_chars", 0)
                name = "  └─" + k.get("id", "")[:15]
                is_integrated = k.get("is_integrated", False)
                process_type = "AIリライト" if is_integrated else "コピー"
                print(f"  │ {name:<19} │ {len(sources):>8} │ {total_chars:>10,} │ {process_type:<24} │")
        print("  └─────────────────────┴──────────┴────────────┴──────────────────────────┘")
        print()
        
        # 統計を計算
        integrated_count = 0
        integrated_files = 0
        single_count = 0
        for chapter in chapters:
            for k in chapter.get("knowledge", []):
                if k.get("is_integrated", False):
                    integrated_count += 1
                    integrated_files += len(k.get("sources", []))
                else:
                    single_count += 1
        
        print(f"  統合: {integrated_count}グループ（{integrated_files}ファイル） / 単体: {single_count}ファイル / 合計: {total_knowledge}ナレッジ")
        print()
        
        # ファイル配置を表示
        print("  【ファイル配置】")
        print("       インプットファイル" + " " * 36 + "ナレッジ")
        print("  " + "─" * 76)
        print()
        
        for chapter in chapters:
            chapter_id = chapter.get("id", "")
            for k in chapter.get("knowledge", []):
                k_id = k.get("id", "")
                sources = k.get("sources", [])
                is_integrated = k.get("is_integrated", False)
                total_chars = k.get("total_chars", sum(char_counts.get(s, 0) for s in sources))
                knowledge_path = f"{chapter_id}/{k_id}"
                
                if is_integrated and len(sources) > 1:
                    # 統合グループ: 罫線で囲む
                    print("  ┌─ 統合 " + "─" * 68 + "┐")
                    for i, source in enumerate(sources):
                        src_chars = char_counts.get(source, 0)
                        src_display = source if len(source) <= 38 else source[:35] + "..."
                        
                        if i == 0:
                            print(f"  │  📄 {src_display} ({src_chars:,}字)".ljust(52) + "─┐" + " " * 22 + "│")
                        elif i == len(sources) - 1:
                            kn_display = knowledge_path if len(knowledge_path) <= 28 else knowledge_path[:25] + "..."
                            print(f"  │  📄 {src_display} ({src_chars:,}字)".ljust(52) + f"─┘→  📁 {kn_display}│")
                        else:
                            print(f"  │  📄 {src_display} ({src_chars:,}字)".ljust(52) + "─┤" + " " * 22 + "│")
                    
                    print(f"  │" + " " * 55 + f"(AIリライト・{total_chars:,}字)".ljust(20) + "│")
                    print("  └" + "─" * 76 + "┘")
                    print()
                else:
                    # 単体ファイル
                    source = sources[0] if sources else "Unknown"
                    src_chars = char_counts.get(source, 0)
                    src_display = source if len(source) <= 38 else source[:35] + "..."
                    kn_display = knowledge_path if len(knowledge_path) <= 28 else knowledge_path[:25] + "..."
                    warn = " ⚠️大容量" if total_chars > MAX_INTEGRATION_CHARS else ""
                    print(f"  📄 {src_display} ({src_chars:,}字)".ljust(54) + f"→  📁 {kn_display}{warn}")
                    print()
        
        print("═" * 80)
        print()
        
        # Skip confirmation if in force mode
        if self.skip_confirmation:
            print("  [自動実行モード] 確認をスキップして実行します")
            return True
        
        # Get user confirmation
        while True:
            response = input("  [Y] この構成で実行  [N] キャンセル: ").strip().upper()
            if response == 'Y':
                return True
            elif response == 'N':
                return False
            else:
                print("  Y または N を入力してください")
    
    def _integrate_knowledge(
        self,
        knowledge: Dict,
        file_contents: Dict[str, str],
        file_images: Dict[str, List[Path]],
    ) -> Tuple[str, List[Tuple[Path, str]]]:
        """Integrate multiple files into single knowledge.
        
        文字数が80,000文字以下の場合はAIでリライト統合、
        超える場合はコピーのみ（AIリライトなし）。
        
        Args:
            knowledge: Knowledge definition from proposal
            file_contents: Dictionary mapping file_name to content
            file_images: Dictionary mapping file_name to list of image paths
            
        Returns:
            Tuple of (integrated_markdown, [(source_image_path, new_name), ...])
        """
        sources = knowledge.get("sources", [])
        knowledge_id = knowledge.get("id", "unknown")
        total_chars = knowledge.get("total_chars", sum(len(file_contents.get(s, "")) for s in sources))
        is_single = len(sources) == 1
        
        # Collect all images with new names
        image_mapping = []  # [(source_path, new_name), ...]
        image_path_replacements = {}  # {old_path: new_path}
        
        for src_idx, source in enumerate(sources):
            images = file_images.get(source, [])
            for img_path in images:
                if is_single:
                    # 単体ナレッジ: 画像名そのまま（パスのみ変更）
                    new_name = img_path.name
                else:
                    # 統合ナレッジ: 英数字プレフィックスを付与（src1_, src2_...）
                    new_name = f"src{src_idx + 1}_{img_path.name}"
                
                image_mapping.append((img_path, new_name))
                # Map various possible old paths to new path
                old_patterns = [
                    f"../04_images/{img_path.name}",
                    f"../03_images/{img_path.name}",
                    f"./images/{img_path.name}",
                ]
                for old_path in old_patterns:
                    image_path_replacements[old_path] = f"./images/{new_name}"
        
        # If single file, just copy content and update image paths
        if is_single:
            content = file_contents.get(sources[0], "")
            for old_path, new_path in image_path_replacements.items():
                content = content.replace(old_path, new_path)
            return content, image_mapping
        
        # Multiple files - check char count limit
        if total_chars > MAX_INTEGRATION_CHARS:
            # 文字数制限超過: コピーのみ（AIリライトなし）
            self.logger.info(f"    📋 文字数制限超過 ({total_chars:,}文字) → コピーのみ")
            return self._concatenate_without_ai(sources, file_contents, image_path_replacements), image_mapping
        
        # 文字数制限内: AIでリライト統合
        self.logger.info(f"    🤖 AIリライト統合 ({total_chars:,}文字)")
        client = self._get_openai_client()
        model = self._get_model_name()
        
        # Build prompt with all contents
        contents_text = ""
        for source in sources:
            content = file_contents.get(source, "")
            contents_text += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【ドキュメント】{source}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{content}
"""
        
        # Build image path instructions
        image_instructions = ""
        for src_idx, source in enumerate(sources):
            images = file_images.get(source, [])
            if images:
                image_instructions += f"\n【{source}の画像】\n"
                for img_path in images:
                    # 統合時は英数字プレフィックス
                    new_name = f"src{src_idx + 1}_{img_path.name}"
                    image_instructions += f"  - {img_path.name} → ./images/{new_name}\n"
        
        prompt = f"""以下の複数ドキュメントを1つのMarkdownに統合してください。

{contents_text}

【画像パスの変換】
{image_instructions}

【絶対禁止事項】
- **情報の省略・削除（絶対禁止）**
- **表の行の省略（絶対禁止）**
- **数値・固有名詞の変更（絶対禁止）**

【必須事項】
1. すべての情報を維持してください
2. 重複する内容は1回だけ記載してください（ただし情報は落とさない）
3. サマリー（3-5個の要点）を冒頭に追加
4. 目次を再生成（ページ内リンク形式）
5. 画像パスを上記の変換ルールに従って変更
6. 引用記法 `>` は使用しない
7. 関連資料は「なし」と記載

【出力構成】
# タイトル

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

## 関連資料
なし

Markdownのみを出力してください。
"""
        
        try:
            if "gpt-5" in model or "o1" in model or "o3" in model or getattr(self, '_is_azure', False):
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
                    max_tokens=16384,
                )
            
            result = response.choices[0].message.content.strip()
            
            # Remove markdown code block wrapper if present
            if result.startswith("```markdown"):
                result = result[len("```markdown"):].strip()
            if result.startswith("```"):
                result = result[3:].strip()
            if result.endswith("```"):
                result = result[:-3].strip()
            
            return result, image_mapping
            
        except Exception as e:
            self.logger.error(f"Failed to integrate knowledge {knowledge_id}: {e}")
            # Fallback: simple concatenation
            return self._concatenate_without_ai(sources, file_contents, image_path_replacements), image_mapping
    
    def _concatenate_without_ai(
        self,
        sources: List[str],
        file_contents: Dict[str, str],
        image_path_replacements: Dict[str, str]
    ) -> str:
        """AIを使わずにファイルを単純連結.
        
        文字数制限超過時、またはAI統合失敗時のフォールバックとして使用。
        
        Args:
            sources: ソースファイル名のリスト
            file_contents: ファイル名→コンテンツのマップ
            image_path_replacements: 画像パス置換マップ
            
        Returns:
            連結されたMarkdown
        """
        if len(sources) == 1:
            content = file_contents.get(sources[0], "")
            for old_path, new_path in image_path_replacements.items():
                content = content.replace(old_path, new_path)
            return content
        
        # 複数ファイルの単純連結
        result_parts = []
        
        # タイトルとサマリーを最初のファイルから取得
        first_content = file_contents.get(sources[0], "")
        
        # 各ファイルの内容を連結
        for i, source in enumerate(sources):
            content = file_contents.get(source, "")
            # 画像パスを更新
            for old_path, new_path in image_path_replacements.items():
                content = content.replace(old_path, new_path)
            
            if i == 0:
                # 最初のファイルはそのまま
                result_parts.append(content)
            else:
                # 2番目以降はタイトル（#）を除いて追加
                lines = content.split('\n')
                # 最初の # で始まる行をスキップ
                skip_title = True
                filtered_lines = []
                for line in lines:
                    if skip_title and line.strip().startswith('# '):
                        skip_title = False
                        continue
                    filtered_lines.append(line)
                
                result_parts.append(f"\n\n---\n\n## 【{source}】\n\n" + '\n'.join(filtered_lines))
        
        return '\n'.join(result_parts)
    
    def _generate_sources_md(self, knowledge: Dict) -> str:
        """Generate _sources.md content.
        
        Args:
            knowledge: Knowledge definition
            
        Returns:
            Markdown content for _sources.md
        """
        knowledge_id = knowledge.get("id", "unknown")
        sources = knowledge.get("sources", [])
        today = datetime.now().strftime("%Y-%m-%d")
        
        content = f"# {knowledge_id} - 元ファイル一覧\n\n"
        content += "| 元ファイル | 統合日 | 備考 |\n"
        content += "|-----------|--------|------|\n"
        
        for i, source in enumerate(sources):
            note = "メインコンテンツ" if i == 0 and len(sources) > 1 else "-"
            if len(sources) > 1 and i > 0:
                note = "補足情報"
            content += f"| {source} | {today} | {note} |\n"
        
        return content
    
    def _generate_chapter_md(self, chapter: Dict) -> str:
        """Generate _chapter.md content for a chapter folder.
        
        Args:
            chapter: Chapter definition (id, name, description, knowledge)
            
        Returns:
            Markdown content for _chapter.md
        """
        chapter_id = chapter.get("id", "")
        chapter_name = chapter.get("name", "")
        chapter_desc = chapter.get("description", "")
        knowledge_list = chapter.get("knowledge", [])
        
        content = f"# {chapter_name}\n\n"
        
        if chapter_desc:
            content += f"{chapter_desc}\n\n"
        
        content += "## この章のナレッジ\n\n"
        
        for k in knowledge_list:
            k_id = k.get("id", "")
            k_name = k.get("name", "")
            k_summary = k.get("summary", "")
            is_integrated = "（統合）" if k.get("is_integrated", False) else ""
            content += f"- [{k_name}](./{k_id}/){is_integrated}\n"
            if k_summary:
                content += f"  - {k_summary}\n"
        
        content += "\n---\n\n"
        content += f"*生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"
        
        return content
    
    def _generate_overview(self, proposal: Dict) -> str:
        """Generate overview.md content from proposal.
        
        階層構造（chapters > knowledge）からナレッジベース全体の説明を生成。
        
        Args:
            proposal: AI提案（overall_structure, chaptersを含む）
            
        Returns:
            Markdown content for overview.md
        """
        overall = proposal.get("overall_structure", {})
        chapters = proposal.get("chapters", [])
        
        title = overall.get("title", "ナレッジベース")
        description = overall.get("description", "")
        
        content = f"# {title}\n\n"
        
        if description:
            content += f"{description}\n\n"
        
        content += "## 目次\n\n"
        
        # 階層構造の目次を生成
        for chapter in sorted(chapters, key=lambda x: x.get("order", 999)):
            ch_id = chapter.get("id", "")
            ch_name = chapter.get("name", "")
            ch_desc = chapter.get("description", "")
            content += f"### {ch_id} - {ch_name}\n\n"
            if ch_desc:
                content += f"{ch_desc}\n\n"
            
            for k in chapter.get("knowledge", []):
                k_id = k.get("id", "")
                k_name = k.get("name", "")
                k_summary = k.get("summary", "")
                is_integrated = " 🔗" if k.get("is_integrated", False) else ""
                content += f"- [{k_name}](../{ch_id}/{k_id}/){is_integrated}\n"
            content += "\n"
        
        content += "---\n\n"
        content += f"*生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"
        
        return content
    
    def _generate_source_mapping(self, proposal: Dict) -> str:
        """Generate source_mapping.md content with text diagram.
        
        階層構造に対応し、入力→ナレッジの対応をテキスト図で表現。
        
        Args:
            proposal: AI提案（chaptersを含む）
            
        Returns:
            Markdown content for source_mapping.md
        """
        chapters = proposal.get("chapters", [])
        
        content = "# ソースマッピング\n\n"
        content += "入力ファイルがどのようにナレッジとしてまとまったかを示します。\n\n"
        
        content += "## 入力 → ナレッジ 対応図\n\n"
        content += "```\n"
        content += "入力ファイル                                      →  ナレッジ\n"
        content += "━" * 80 + "\n\n"
        
        # 統計用
        total_files = 0
        total_knowledge = 0
        integrated_count = 0
        
        for chapter in sorted(chapters, key=lambda x: x.get("order", 999)):
            ch_id = chapter.get("id", "")
            ch_name = chapter.get("name", "")
            content += f"【{ch_id}】{ch_name}\n"
            
            for k in chapter.get("knowledge", []):
                k_id = k.get("id", "")
                sources = k.get("sources", [])
                is_integrated = k.get("is_integrated", False)
                total_chars = k.get("total_chars", 0)
                total_knowledge += 1
                
                if is_integrated:
                    integrated_count += 1
                    # 統合の場合
                    for i, src in enumerate(sources):
                        total_files += 1
                        if i == 0:
                            content += f"  ├─ {src:<40} ─┐\n"
                        elif i == len(sources) - 1:
                            content += f"  │  {src:<40} ─┴─→ {k_id}/（統合）\n"
                        else:
                            content += f"  │  {src:<40} ─┤\n"
                else:
                    # 単体の場合
                    src = sources[0] if sources else "Unknown"
                    total_files += 1
                    content += f"  └─ {src:<40} ────→ {k_id}/\n"
            
            content += "\n"
        
        content += "```\n\n"
        
        # 統計
        content += "## 統計\n\n"
        content += "| 項目 | 件数 |\n"
        content += "|------|------|\n"
        content += f"| 入力ファイル数 | {total_files} |\n"
        content += f"| 生成ナレッジ数 | {total_knowledge} |\n"
        content += f"| 章の数 | {len(chapters)} |\n"
        content += f"| 統合されたナレッジ | {integrated_count} |\n"
        
        content += "\n---\n\n"
        content += f"*生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"
        
        return content
    
    def _generate_glossary(self) -> str:
        """各ナレッジのterms.jsonを読み込んでマージし、用語集を生成（AI不要）.
        
        Step2で生成されたterms.jsonファイルを読み込み、
        重複排除・マージしてMarkdown表を生成します。
        
        Returns:
            Markdown content for glossary
        """
        all_terms = []
        
        # 全terms.jsonを読み込み
        for item in sorted(self.target_dir.iterdir()):
            if item.is_dir() and not item.name.startswith("_"):
                terms_path = item / FOLDER_FORMATTED_MARKDOWN / "terms.json"
                if terms_path.exists():
                    try:
                        with open(terms_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            terms = data.get("terms", [])
                            all_terms.extend(terms)
                            self.logger.debug(f"  Loaded {len(terms)} terms from {item.name}")
                    except (json.JSONDecodeError, OSError) as e:
                        self.logger.warning(f"  ⚠️ terms.json読み込み失敗: {item.name} - {e}")
        
        if not all_terms:
            self.logger.warning("  ⚠️ 用語が見つかりません。空の用語集を生成します。")
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
                # 既存の説明がより短い場合は上書き
                existing_desc = merged[term]["description"]
                new_desc = t.get("description", "")
                if len(new_desc) > len(existing_desc):
                    merged[term]["description"] = new_desc
                
                # flagは0（確定）を優先
                if t.get("flag", 1) == 0:
                    merged[term]["flag"] = 0
        
        # Markdown表を生成
        lines = [
            "# 用語集",
            "",
            f"※ 全{len(merged)}件の専門用語・略語を収録",
            "",
            "| 用語 | 説明 | 要確認Flg |",
            "|------|------|-----------|"
        ]
        
        # 用語をソート
        for term in sorted(merged.keys()):
            data = merged[term]
            description = data["description"].replace("|", "\\|")  # パイプをエスケープ
            flag = data["flag"]
            lines.append(f"| {term} | {description} | {flag} |")
        
        self.logger.info(f"  📚 用語集生成: {len(merged)}件")
        
        return "\n".join(lines)
    
    def run(self) -> int:
        """Run the deployment process.
        
        Returns:
            Exit code (0 for success)
        """
        self.logger.info("=" * 60)
        self.logger.info("KNOWLEDGE DEPLOYER - Stage 3")
        self.logger.info("=" * 60)
        
        # Step 1: List formatted files
        self.logger.info("Step 1: ファイル一覧取得中...")
        files = self._list_formatted_files()
        
        if not files:
            self.logger.error("処理対象のファイルが見つかりません")
            return 1
        
        self.results["statistics"]["total_files"] = len(files)
        self.logger.info(f"  {len(files)} ファイルを検出")
        
        # Step 2: Analyze files (parse summary/toc from formatted.md - no AI)
        self.logger.info("Step 2: ファイル内容を分析中（パース）...")
        summaries = []
        file_contents = {}
        file_images = {}
        
        for file_name, formatted_path in files:
            self.logger.info(f"  分析中: {file_name}")
            
            # Read content
            content = formatted_path.read_text(encoding="utf-8")
            file_contents[file_name] = content
            
            # Get images
            images_dir = formatted_path.parent.parent / FOLDER_IMAGES
            if not images_dir.exists():
                # Try alternate paths (legacy folder name)
                images_dir = formatted_path.parent.parent / "03_images"
            
            images = []
            if images_dir.exists():
                images = [
                    p for p in sorted(images_dir.iterdir())
                    if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
                ]
            file_images[file_name] = images
            
            # Extract summary from formatted.md (no AI - just parsing)
            summary = self._extract_summary_from_formatted(content, file_name)
            summaries.append(summary)
            
            self.logger.info(f"    → {summary.get('char_count', 0):,}文字, {len(summary.get('topics', []))}トピック")
        
        # Step 3: Propose structure (with char count info)
        self.logger.info("Step 3: フォルダ構成を提案中...")
        proposal = self._propose_structure(summaries, file_contents)
        
        # Step 4: Display proposal and get confirmation
        if not self._display_proposal(proposal, summaries):
            self.logger.info("処理がキャンセルされました")
            return 0
        
        # Step 5: Create knowledge folders (hierarchical: chapters > knowledge)
        self.logger.info("\nStep 5: ナレッジフォルダを作成中...")
        chapters = sorted(proposal.get("chapters", []), key=lambda x: x.get("order", 999))
        total_knowledge = sum(len(ch.get("knowledge", [])) for ch in chapters)
        self.results["statistics"]["total_knowledge"] = total_knowledge
        self.results["statistics"]["total_chapters"] = len(chapters)
        
        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # フラット化されたknowledge_list（後方互換用）
        knowledge_list = []
        
        for chapter in chapters:
            chapter_id = chapter.get("id", "unknown")
            chapter_name = chapter.get("name", "")
            chapter_desc = chapter.get("description", "")
            
            self.logger.info(f"  📁 章を作成中: {chapter_id}/")
            
            # Create chapter folder
            chapter_dir = self.output_dir / chapter_id
            chapter_dir.mkdir(parents=True, exist_ok=True)
            
            # Generate _chapter.md
            chapter_content = self._generate_chapter_md(chapter)
            chapter_md_path = chapter_dir / "_chapter.md"
            chapter_md_path.write_text(chapter_content, encoding="utf-8")
            
            # Process knowledge in this chapter
            for knowledge in chapter.get("knowledge", []):
                knowledge_id = knowledge.get("id", "unknown")
                sources = knowledge.get("sources", [])
                is_integrated = knowledge.get("is_integrated", False)
                
                self.logger.info(f"     ├─ {knowledge_id} ({len(sources)}ファイル)")
                
                # Create knowledge folder inside chapter
                knowledge_dir = chapter_dir / knowledge_id
                knowledge_dir.mkdir(parents=True, exist_ok=True)
                images_dir = knowledge_dir / "images"
                images_dir.mkdir(parents=True, exist_ok=True)
                
                # Integrate or copy content
                if is_integrated:
                    self.results["statistics"]["integrated"] += 1
                else:
                    self.results["statistics"]["single"] += 1
                
                content, image_mapping = self._integrate_knowledge(
                    knowledge,
                    file_contents,
                    file_images,
                )
                
                # Save index.md
                index_path = knowledge_dir / "index.md"
                index_path.write_text(content, encoding="utf-8")
                
                # Copy images with new names
                for src_path, new_name in image_mapping:
                    dst_path = images_dir / new_name
                    try:
                        shutil.copy2(src_path, dst_path)
                    except Exception as e:
                        self.logger.warning(f"    画像コピー失敗: {src_path} -> {e}")
                
                # Generate _sources.md
                sources_content = self._generate_sources_md(knowledge)
                sources_path = knowledge_dir / "_sources.md"
                sources_path.write_text(sources_content, encoding="utf-8")
                
                # フラット化されたリストにも追加
                knowledge_list.append({
                    "id": knowledge_id,
                    "chapter_id": chapter_id,
                    "sources": sources,
                    "is_integrated": is_integrated,
                })
                
                self.results["knowledge_list"].append({
                    "id": knowledge_id,
                    "chapter_id": chapter_id,
                    "sources": sources,
                    "is_integrated": is_integrated,
                })
        
        # Step 6: Generate glossary (merge terms.json files - no AI)
        if self.generate_glossary:
            self.logger.info("Step 6: 用語集を生成中（terms.jsonマージ）...")
            glossary_content = self._generate_glossary()
            glossary_path = self.output_dir / FILE_GLOSSARY
            glossary_path.write_text(glossary_content, encoding="utf-8")
        
        # Step 7: Generate _global files
        self.logger.info("Step 7: メタデータファイルを生成中...")
        global_dir = self.output_dir / FOLDER_GLOBAL
        global_dir.mkdir(parents=True, exist_ok=True)
        
        # overview.md - ナレッジベース全体構成
        overview_content = self._generate_overview(proposal)
        overview_path = global_dir / "overview.md"
        overview_path.write_text(overview_content, encoding="utf-8")
        self.logger.info("  ✅ overview.md 生成完了")
        
        # source_mapping.md - テキスト対応図
        mapping_content = self._generate_source_mapping(proposal)
        mapping_path = global_dir / FILE_SOURCE_MAPPING
        mapping_path.write_text(mapping_content, encoding="utf-8")
        self.logger.info("  ✅ source_mapping.md 生成完了")
        
        # structure_proposal.json - Add summary info to each knowledge (階層構造対応)
        summaries_by_name = {s["file_name"]: s for s in summaries}
        for chapter in proposal.get("chapters", []):
            for knowledge in chapter.get("knowledge", []):
                sources = knowledge.get("sources", [])
                if len(sources) == 1:
                    # 単体: そのファイルのサマリーを追加
                    source = sources[0]
                    if source in summaries_by_name:
                        s = summaries_by_name[source]
                        knowledge["summary"] = s.get("summary", "")
                        knowledge["topics"] = s.get("topics", [])
                else:
                    # 統合: 全ソースのサマリーを結合
                    combined_summary = []
                    combined_topics = []
                    for source in sources:
                        if source in summaries_by_name:
                            s = summaries_by_name[source]
                            combined_summary.append(f"【{source}】\n{s.get('summary', '')}")
                            combined_topics.extend(s.get("topics", []))
                    knowledge["summary"] = "\n\n".join(combined_summary)
                    knowledge["topics"] = combined_topics
        
        proposal["created_at"] = datetime.now().isoformat()
        proposal["model"] = self._get_model_name()
        proposal_path = global_dir / FILE_STRUCTURE_PROPOSAL
        with open(proposal_path, "w", encoding="utf-8") as f:
            json.dump(proposal, f, ensure_ascii=False, indent=2)
        
        # Save results
        self._save_results()
        
        # Print summary
        self._print_summary()
        
        return 0
    
    def _print_summary(self) -> None:
        """Print summary of the deployment."""
        stats = self.results["statistics"]
        
        print("\n" + "=" * 60)
        print("DEPLOYMENT COMPLETE")
        print("=" * 60)
        
        print(f"\n📊 Statistics:")
        print(f"  入力ファイル数: {stats['total_files']}")
        print(f"  章の数: {stats.get('total_chapters', 0)}")
        print(f"  生成ナレッジ数: {stats['total_knowledge']}")
        print(f"    ├── 統合: {stats['integrated']}")
        print(f"    └── 単体: {stats['single']}")
        
        print(f"\n📁 Output: {self.output_dir}")
        print(f"   ├── [章]/[ナレッジ]/index.md")
        print(f"   └── _global/overview.md")
        
        if self.results.get("errors"):
            print(f"\n⚠️ Errors:")
            for error in self.results["errors"][:5]:
                print(f"  - {error}")
        
        print("\n" + "=" * 60)

