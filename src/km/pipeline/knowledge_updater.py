"""Knowledge Updater - ナレッジ更新を実行.

ProposalGeneratorで生成された提案に基づいて、
実際にナレッジファイルの作成・更新を実行する。
AI（gpt-5.1）を使用してコンテンツのスマートマージを行う。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .proposal_generator import (
    ActionType,
    ChangeDetail,
    MultiTargetUpdate,
    PartialUpdateInfo,
    Proposal,
    ProposedAction,
    TargetFileInfo,
    UpdateMode,
)
from .knowledge_config import (
    FOLDER_FORMATTED_MARKDOWN,
    FOLDER_IMAGES,
    FILE_ENHANCED_MD,
)


logger = logging.getLogger(__name__)


class KnowledgeUpdater:
    """ナレッジ更新を実行するクラス（AI使用）"""
    
    def __init__(
        self,
        pre_knowledge_dir: Path,
        output_dir: Path,
        proposal: Proposal,
        use_ai: bool = True,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Args:
            pre_knowledge_dir: pre-knowledge/フォルダのパス
            output_dir: 出力先（knowledge/）フォルダのパス
            proposal: 更新提案
            use_ai: AIを使用するかどうか（デフォルト: True）
            logger: ロガー
        """
        self.pre_knowledge_dir = Path(pre_knowledge_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.proposal = proposal
        self.use_ai = use_ai
        self.logger = logger or logging.getLogger(__name__)
        
        # OpenAIクライアント
        self._client = None
        self._model_name = None
        self._is_azure = False
        
        # 実行結果
        self.results: Dict[str, List] = {
            "created": [],
            "updated": [],
            "skipped": [],
            "errors": [],
        }
        
        # 変更詳細（レポート用）
        self.change_details: List[Dict] = []
    
    def _get_openai_client(self):
        """Lazy-load OpenAI or Azure OpenAI client."""
        if self._client is None:
            try:
                from ..utils.openai_client import get_openai_client, get_model_name
                
                self._client, self._is_azure = get_openai_client(timeout=300.0, purpose="chat")
                self._model_name = get_model_name(purpose="chat", is_azure=self._is_azure)
                self.logger.info(f"KnowledgeUpdater using model: {self._model_name}")
                
            except Exception as e:
                self.logger.warning(f"Failed to initialize OpenAI client: {e}")
                self._client = None
        
        return self._client
    
    def _get_model_name(self) -> str:
        """Get model/deployment name for chat completions."""
        if self._model_name:
            return self._model_name
        from ..utils.openai_client import get_model_name
        return get_model_name(purpose="chat", is_azure=self._is_azure)
    
    def execute(self) -> Dict:
        """提案に基づいて更新を実行"""
        self.logger.info(f"Starting knowledge update: {len(self.proposal.actions)} actions")
        
        for action in self.proposal.actions:
            try:
                if action.action_type == ActionType.CREATE:
                    self._execute_create(action)
                elif action.action_type == ActionType.UPDATE:
                    self._execute_update(action)
                elif action.action_type == ActionType.PARTIAL_UPDATE:
                    self._execute_partial_update(action)
                elif action.action_type == ActionType.SKIP:
                    self._execute_skip(action)
            except Exception as e:
                self.logger.error(f"Failed to execute action for {action.source_document}: {e}")
                self.results["errors"].append({
                    "source": action.source_document,
                    "action": action.action_type.value,
                    "error": str(e),
                })
        
        # mapping.json を更新
        self._update_mapping()
        
        return self.results
    
    def _get_source_content(self, doc_name: str) -> Optional[str]:
        """ソースドキュメントのformatted.mdを取得"""
        source_dir = self.pre_knowledge_dir / doc_name
        formatted_md = source_dir / FOLDER_FORMATTED_MARKDOWN / FILE_ENHANCED_MD
        
        if not formatted_md.exists():
            return None
        
        return formatted_md.read_text(encoding="utf-8")
    
    def _get_source_images(self, doc_name: str) -> List[Path]:
        """ソースドキュメントの画像一覧を取得"""
        source_dir = self.pre_knowledge_dir / doc_name
        images_dir = source_dir / FOLDER_IMAGES
        
        if not images_dir.exists():
            return []
        
        return list(images_dir.glob("*.*"))
    
    def _execute_create(self, action: ProposedAction) -> None:
        """新規ファイルを作成"""
        self.logger.info(f"Creating: {action.target_path} from {action.source_document}")
        
        # ソースコンテンツを取得
        content = self._get_source_content(action.source_document)
        if content is None:
            raise ValueError(f"Source content not found: {action.source_document}")
        
        # 分割が指定されている場合
        if action.split_into:
            self._execute_create_with_split(action, content)
            return
        
        # 出力先パスを決定
        target_path = self.output_dir / action.target_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        # YAMLフロントマターを追加
        frontmatter = self._generate_frontmatter(action)
        final_content = frontmatter + "\n\n" + content
        
        # ファイルを書き込み
        target_path.write_text(final_content, encoding="utf-8")
        
        # 画像をコピー
        self._copy_images(action.source_document, target_path.parent)
        
        self.results["created"].append({
            "source": action.source_document,
            "target": action.target_path,
        })
        
        self.change_details.append({
            "type": "create",
            "source": action.source_document,
            "target": action.target_path,
            "reason": action.reason,
        })
    
    def _execute_create_with_split(self, action: ProposedAction, content: str) -> None:
        """コンテンツを分割して複数ファイルを作成"""
        self.logger.info(f"Creating with split: {len(action.split_into)} files from {action.source_document}")
        
        # AIを使って分割
        if self.use_ai:
            split_contents = self._split_content_with_ai(content, action.split_into)
        else:
            # AIなしの場合は単純な章分割
            split_contents = self._split_content_simple(content, len(action.split_into))
        
        for i, (split_path, split_content) in enumerate(zip(action.split_into, split_contents)):
            target_path = self.output_dir / split_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            
            # YAMLフロントマターを追加
            frontmatter = self._generate_frontmatter(action, split_index=i+1)
            final_content = frontmatter + "\n\n" + split_content
            
            target_path.write_text(final_content, encoding="utf-8")
            
            self.results["created"].append({
                "source": action.source_document,
                "target": split_path,
                "split_index": i + 1,
            })
            
            self.change_details.append({
                "type": "create",
                "source": action.source_document,
                "target": split_path,
                "reason": f"{action.reason}（分割{i+1}/{len(action.split_into)}）",
            })
        
        # 画像をコピー（分割先の親ディレクトリへ）
        if action.split_into:
            first_target = self.output_dir / action.split_into[0]
            self._copy_images(action.source_document, first_target.parent)
    
    def _split_content_with_ai(self, content: str, target_paths: List[str]) -> List[str]:
        """AIを使ってコンテンツを分割"""
        client = self._get_openai_client()
        if client is None:
            return self._split_content_simple(content, len(target_paths))
        
        # 見出し一覧を抽出
        headings = []
        for line in content.split("\n"):
            match = re.match(r"^(#{1,3})\s+(.+)$", line)
            if match:
                headings.append(match.group(2).strip())
        
        prompt = f"""以下のMarkdownコンテンツを{len(target_paths)}個のファイルに分割してください。

【分割先ファイル】
{chr(10).join(f'{i+1}. {p}' for i, p in enumerate(target_paths))}

【コンテンツの見出し一覧】
{chr(10).join(f'- {h}' for h in headings[:30])}

【回答形式】
各ファイルに含める見出しを指定してください。JSON形式で回答：

{{
    "splits": [
        {{
            "file_index": 1,
            "start_heading": "<開始見出し>",
            "end_heading": "<終了見出し>",
            "description": "<このファイルの内容説明>"
        }},
        ...
    ]
}}

【ルール】
- 関連するトピックは同じファイルにまとめる
- 各ファイルは10-20章程度が理想
- 論理的なまとまりで分割する
"""
        
        try:
            response = client.chat.completions.create(
                model=self._get_model_name(),
                messages=[
                    {"role": "system", "content": "あなたはドキュメント構造化の専門家です。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_completion_tokens=1000,
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # JSONを抽出
            json_match = re.search(r'\{[^{}]*"splits"[^{}]*\[[\s\S]*?\]\s*\}', result_text)
            if json_match:
                result = json.loads(json_match.group())
                splits_info = result.get("splits", [])
                
                # 見出しに基づいて分割
                return self._split_by_headings(content, splits_info, len(target_paths))
            
        except Exception as e:
            self.logger.warning(f"AI content split failed: {e}")
        
        return self._split_content_simple(content, len(target_paths))
    
    def _split_by_headings(self, content: str, splits_info: List[Dict], num_files: int) -> List[str]:
        """見出しに基づいてコンテンツを分割"""
        lines = content.split("\n")
        heading_lines = []
        
        # 見出し行を特定
        for i, line in enumerate(lines):
            if re.match(r"^#{1,3}\s+", line):
                heading_lines.append(i)
        
        if len(heading_lines) < num_files:
            return self._split_content_simple(content, num_files)
        
        # 均等に分割
        chunk_size = max(1, len(heading_lines) // num_files)
        split_contents = []
        
        for i in range(num_files):
            start_idx = heading_lines[i * chunk_size] if i * chunk_size < len(heading_lines) else 0
            
            if i == num_files - 1:
                end_idx = len(lines)
            else:
                next_heading_idx = min((i + 1) * chunk_size, len(heading_lines) - 1)
                end_idx = heading_lines[next_heading_idx]
            
            chunk = "\n".join(lines[start_idx:end_idx])
            split_contents.append(chunk)
        
        return split_contents
    
    def _split_content_simple(self, content: str, num_files: int) -> List[str]:
        """簡易分割（見出しベース）"""
        lines = content.split("\n")
        
        # 見出し行を見つける
        heading_indices = []
        for i, line in enumerate(lines):
            if re.match(r"^#{1,2}\s+", line):
                heading_indices.append(i)
        
        if len(heading_indices) < num_files:
            # 見出しが足りない場合は均等に分割
            chunk_size = max(1, len(lines) // num_files)
            return ["\n".join(lines[i * chunk_size:(i + 1) * chunk_size]) for i in range(num_files)]
        
        # 見出しで分割
        chunk_size = max(1, len(heading_indices) // num_files)
        split_contents = []
        
        for i in range(num_files):
            start_idx = heading_indices[i * chunk_size] if i * chunk_size < len(heading_indices) else 0
            
            if i == num_files - 1:
                end_idx = len(lines)
            else:
                next_heading_idx = min((i + 1) * chunk_size, len(heading_indices) - 1)
                end_idx = heading_indices[next_heading_idx]
            
            chunk = "\n".join(lines[start_idx:end_idx])
            split_contents.append(chunk)
        
        return split_contents
    
    # マージ後分割の閾値
    UPDATE_SPLIT_THRESHOLD = 10000  # 1万文字以上で分割
    
    def _execute_update(self, action: ProposedAction) -> None:
        """既存ファイルを更新（マージ後10,000文字超なら分割）"""
        self.logger.info(f"Updating: {action.target_path} from {action.source_document}")
        
        target_path = self.output_dir / action.target_path
        
        if not target_path.exists():
            # ファイルが存在しない場合は新規作成として処理
            self.logger.warning(f"Target file not found, creating: {action.target_path}")
            self._execute_create(action)
            return
        
        # 既存コンテンツをバックアップ
        backup_path = target_path.with_suffix(".md.bak")
        shutil.copy2(target_path, backup_path)
        
        # ソースコンテンツを取得
        new_content = self._get_source_content(action.source_document)
        if new_content is None:
            raise ValueError(f"Source content not found: {action.source_document}")
        
        # 既存コンテンツを読み込み
        existing_content = target_path.read_text(encoding="utf-8")
        
        # コンテンツをマージ（AI使用）
        if self.use_ai:
            merged_content, merge_changes = self._merge_content_with_ai(existing_content, new_content, action)
            # 変更詳細を更新
            if merge_changes:
                action.changes.extend(merge_changes)
        else:
            merged_content = self._merge_content_simple(existing_content, new_content, action)
        
        # YAMLフロントマターを更新
        final_content = self._update_frontmatter(merged_content, action)
        
        # マージ後サイズをチェック - 10,000文字超なら分割
        if len(final_content) > self.UPDATE_SPLIT_THRESHOLD:
            self.logger.info(f"Merged content exceeds {self.UPDATE_SPLIT_THRESHOLD} chars ({len(final_content)}), splitting...")
            self._execute_update_with_split(action, final_content, target_path, backup_path)
            return
        
        # ファイルを書き込み
        target_path.write_text(final_content, encoding="utf-8")
        
        # 画像をコピー
        self._copy_images(action.source_document, target_path.parent)
        
        self.results["updated"].append({
            "source": action.source_document,
            "target": action.target_path,
            "backup": str(backup_path),
            "merged_size": len(final_content),
        })
        
        self.change_details.append({
            "type": "update",
            "source": action.source_document,
            "target": action.target_path,
            "reason": action.reason,
            "merged_size": len(final_content) if 'final_content' in dir() else 0,
            "changes": [
                {
                    "item": c.item,
                    "target_line": c.target_line,
                    "before": c.before_content[:100] + "..." if len(c.before_content) > 100 else c.before_content,
                    "after": c.after_content[:100] + "..." if len(c.after_content) > 100 else c.after_content,
                    "is_primary": c.is_primary,
                }
                for c in action.changes
            ],
        })
    
    def _execute_update_with_split(
        self, 
        action: ProposedAction, 
        merged_content: str, 
        target_path: Path,
        backup_path: Path
    ) -> None:
        """マージ後コンテンツを分割して保存（10,000文字超の場合）"""
        self.logger.info(f"Splitting merged content: {len(merged_content)} chars")
        
        # セクション単位で分割
        sections = self._extract_sections_from_content(merged_content)
        
        if len(sections) < 2:
            # 分割できない場合はそのまま保存
            target_path.write_text(merged_content, encoding="utf-8")
            self.results["updated"].append({
                "source": action.source_document,
                "target": action.target_path,
                "backup": str(backup_path),
                "merged_size": len(merged_content),
                "split": False,
            })
            return
        
        # 分割ポイントを決定（10,000文字を超えない範囲で）
        split_files = []
        current_sections = []
        current_size = 0
        file_index = 0
        
        for section in sections:
            section_size = len(section["content"]) + len(section["heading"]) + 10
            
            if current_size + section_size > self.UPDATE_SPLIT_THRESHOLD and current_sections:
                # 現在のファイルを保存
                split_files.append({
                    "sections": current_sections,
                    "size": current_size,
                })
                current_sections = [section]
                current_size = section_size
                file_index += 1
            else:
                current_sections.append(section)
                current_size += section_size
        
        # 最後のファイルを追加
        if current_sections:
            split_files.append({
                "sections": current_sections,
                "size": current_size,
            })
        
        # 分割が不要な場合（1ファイルに収まる）
        if len(split_files) <= 1:
            target_path.write_text(merged_content, encoding="utf-8")
            self.results["updated"].append({
                "source": action.source_document,
                "target": action.target_path,
                "backup": str(backup_path),
                "merged_size": len(merged_content),
                "split": False,
            })
            return
        
        # 分割ファイルを作成
        base_name = target_path.stem
        category_dir = target_path.parent
        
        for i, split_file in enumerate(split_files):
            # ファイル名を生成
            if i == 0:
                file_path = target_path
                suffix = ""
            else:
                # 新しいファイル名を生成
                suffix = f"_part{i + 1}"
                new_name = f"{base_name}{suffix}.md"
                file_path = category_dir / new_name
            
            # コンテンツを構築
            content_parts = []
            for section in split_file["sections"]:
                if section["heading"] != "（冒頭）":
                    content_parts.append(f"## {section['heading']}")
                content_parts.append(section["content"])
            
            file_content = "\n\n".join(content_parts)
            
            # YAMLフロントマターを追加
            if i == 0:
                file_content = self._update_frontmatter(file_content, action)
            else:
                file_content = self._create_frontmatter(f"{action.source_document} (Part {i + 1})") + "\n\n" + file_content
            
            file_path.write_text(file_content, encoding="utf-8")
            
            if i == 0:
                self.results["updated"].append({
                    "source": action.source_document,
                    "target": str(file_path.relative_to(self.output_dir)),
                    "backup": str(backup_path),
                    "merged_size": len(file_content),
                    "split": True,
                    "split_total": len(split_files),
                })
            else:
                self.results["created"].append({
                    "source": f"{action.source_document} (分割 Part {i + 1})",
                    "target": str(file_path.relative_to(self.output_dir)),
                })
        
        # 画像をコピー
        self._copy_images(action.source_document, category_dir)
        
        self.change_details.append({
            "type": "update_split",
            "source": action.source_document,
            "target": action.target_path,
            "reason": action.reason,
            "original_size": len(merged_content),
            "split_into": len(split_files),
            "split_files": [
                f"{base_name}_part{i + 1}.md" if i > 0 else f"{base_name}.md"
                for i in range(len(split_files))
            ],
        })
        
        self.logger.info(f"Split into {len(split_files)} files")
    
    def _extract_sections_from_content(self, content: str) -> List[Dict[str, Any]]:
        """コンテンツをセクション単位で分割"""
        sections = []
        lines = content.split("\n")
        current_heading = "（冒頭）"
        current_content = []
        
        for line in lines:
            match = re.match(r"^(#{1,4})\s+(.+)$", line)
            if match:
                if current_content:
                    sections.append({
                        "heading": current_heading,
                        "content": "\n".join(current_content),
                    })
                current_heading = match.group(2).strip()
                current_content = []
            else:
                current_content.append(line)
        
        if current_content:
            sections.append({
                "heading": current_heading,
                "content": "\n".join(current_content),
            })
        
        return sections
    
    def _execute_partial_update(self, action: ProposedAction) -> None:
        """部分更新を実行（一部を既存ファイルに更新、残りを新規ファイルに作成）"""
        partial = action.partial_info
        if not partial:
            # partial_infoがない場合は通常の更新として処理
            self.logger.warning(f"No partial_info for {action.source_document}, falling back to update")
            self._execute_update(action)
            return
        
        self.logger.info(f"Partial update: {action.source_document}")
        self.logger.info(f"  Update: {partial.update_char_count} chars -> {partial.update_target}")
        self.logger.info(f"  Create: {partial.create_char_count} chars -> {partial.create_target}")
        
        # ソースコンテンツを取得
        source_content = self._get_source_content(action.source_document)
        if source_content is None:
            raise ValueError(f"Source content not found: {action.source_document}")
        
        # セクションを分割して振り分け
        update_content, create_content = self._split_content_by_sections(
            source_content, 
            partial.update_sections, 
            partial.create_sections
        )
        
        # 1. 既存ファイルに更新部分を追記
        if update_content:
            update_target_path = self.output_dir / partial.update_target
            
            if update_target_path.exists():
                existing_content = update_target_path.read_text(encoding="utf-8")
                
                # バックアップ
                backup_path = update_target_path.with_suffix(".md.bak")
                shutil.copy2(update_target_path, backup_path)
                
                # マージ
                if self.use_ai:
                    merged_content, _ = self._merge_content_with_ai(existing_content, update_content, action)
                else:
                    merged_content = self._merge_content_simple(existing_content, update_content, action)
                
                # 書き込み
                update_target_path.write_text(merged_content, encoding="utf-8")
                
                self.results["updated"].append({
                    "source": f"{action.source_document} (部分更新)",
                    "target": partial.update_target,
                    "backup": str(backup_path),
                })
            else:
                self.logger.warning(f"Update target not found: {partial.update_target}")
        
        # 2. 新規ファイルを作成
        if create_content:
            create_target_path = self.output_dir / partial.create_target
            create_target_path.parent.mkdir(parents=True, exist_ok=True)
            
            # タイトルを追加
            doc_title = action.source_document.replace("_", " ")
            header = f"# {doc_title}（新規セクション）\n\n"
            final_content = header + create_content
            
            # YAMLフロントマターを追加
            final_content = self._create_frontmatter(action.source_document) + "\n\n" + final_content
            
            create_target_path.write_text(final_content, encoding="utf-8")
            
            self.results["created"].append({
                "source": f"{action.source_document} (新規部分)",
                "target": partial.create_target,
            })
        
        # 画像をコピー（両方のディレクトリに）
        if update_content:
            self._copy_images(action.source_document, (self.output_dir / partial.update_target).parent)
        if create_content:
            self._copy_images(action.source_document, (self.output_dir / partial.create_target).parent)
        
        self.change_details.append({
            "type": "partial_update",
            "source": action.source_document,
            "update_target": partial.update_target,
            "create_target": partial.create_target,
            "update_char_count": partial.update_char_count,
            "create_char_count": partial.create_char_count,
            "reason": action.reason,
        })
    
    def _split_content_by_sections(
        self, 
        content: str, 
        update_sections: List[str], 
        create_sections: List[str]
    ) -> Tuple[str, str]:
        """コンテンツをセクション名に基づいて2つに分割"""
        lines = content.split("\n")
        update_lines = []
        create_lines = []
        
        current_target = "update"  # デフォルトは更新側
        current_heading = ""
        
        for line in lines:
            # 見出し行を検出
            match = re.match(r"^(#{1,4})\s+(.+)$", line)
            if match:
                current_heading = match.group(2).strip()
                
                # どちらに振り分けるか判定
                if current_heading in update_sections:
                    current_target = "update"
                elif current_heading in create_sections:
                    current_target = "create"
                else:
                    # 部分一致チェック
                    matched_update = any(s in current_heading or current_heading in s for s in update_sections)
                    matched_create = any(s in current_heading or current_heading in s for s in create_sections)
                    
                    if matched_create and not matched_update:
                        current_target = "create"
                    else:
                        current_target = "update"  # デフォルトは更新側
            
            # 行を追加
            if current_target == "update":
                update_lines.append(line)
            else:
                create_lines.append(line)
        
        return "\n".join(update_lines), "\n".join(create_lines)
    
    def _merge_content_with_ai(
        self,
        existing: str,
        new: str,
        action: ProposedAction,
    ) -> Tuple[str, List[ChangeDetail]]:
        """AIを使ってコンテンツをスマートマージ"""
        client = self._get_openai_client()
        if client is None:
            return (self._merge_content_simple(existing, new, action), [])
        
        # 既存と新規の見出しを抽出
        existing_headings = self._extract_headings(existing)
        new_headings = self._extract_headings(new)
        
        prompt = f"""以下の2つのMarkdownコンテンツをマージしてください。

【knowledge/（更新対象）】見出し一覧:
{chr(10).join(f'- {h}' for h in existing_headings[:20])}

【新しいコンテンツ（追加元）】見出し一覧:
{chr(10).join(f'- {h}' for h in new_headings[:20])}

【重要】
- 必ず「knowledge/」の内容を確認して判断してください。想像で書かないでください。
- 各変更は「knowledge/ のどのセクションに影響するか」を明確にしてください。

【マージ方針】
1. 新しいコンテンツの情報を優先する
2. 既存コンテンツにしかない情報は保持する
3. 同じトピックの場合は新しい内容で更新する
4. 新規セクションは適切な位置に挿入する

【回答形式】
JSON形式で回答してください：

{{
    "merge_strategy": "<選択した戦略: replace/append/integrate>",
    "changes": [
        {{
            "section": "<変更されるセクション名（既存ナレッジから確認）>",
            "action": "<update/add/keep>",
            "reason": "<変更理由（具体的に）>",
            "is_primary": <true/false>
        }}
    ],
    "recommendation": "<推奨事項>"
}}

【is_primary の判定基準】
- true（主要変更）: 新しいフロー・セクションを既存ナレッジに直接追記する箇所
- false（派生変更）: 主要変更に伴い、整合性を保つために更新が必要な箇所
  （例: 目次の更新、リンクの修正、参照先の変更、関連セクションの軽微な修正など）

【merge_strategy の選択基準】
- replace: 新しいコンテンツで置換（全面的な改訂の場合）
- append: 新しい内容を末尾に追加（独立したセクションの追加）
- integrate: 章単位で統合（部分的な更新・追記）
"""
        
        try:
            response = client.chat.completions.create(
                model=self._get_model_name(),
                messages=[
                    {"role": "system", "content": "あなたはドキュメントマージの専門家です。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_completion_tokens=1000,
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # JSONを抽出
            json_match = re.search(r'\{[\s\S]*\}', result_text)
            if json_match:
                result = json.loads(json_match.group())
                
                strategy = result.get("merge_strategy", "replace")
                changes_info = result.get("changes", [])
                
                # 変更詳細を作成
                merge_changes = []
                for change in changes_info:
                    # is_primaryはAIの回答から取得、なければactionで判定
                    is_primary = change.get("is_primary", change.get("action") != "keep")
                    merge_changes.append(ChangeDetail(
                        item=change.get("section", ""),
                        target_file=action.target_path,
                        target_line="（AIによる判定）",
                        before_content=f"アクション: {change.get('action', '')}",
                        after_content=change.get("reason", ""),
                        is_primary=is_primary,
                    ))
                
                # マージ実行
                if strategy == "append":
                    merged = existing + "\n\n---\n\n## 追加コンテンツ\n\n" + new
                elif strategy == "integrate":
                    # multi_target情報を活用してセクションを正確にマージ
                    merged = self._integrate_content(existing, new, action.multi_target)
                else:  # replace
                    merged = new
                
                return (merged, merge_changes)
            
        except Exception as e:
            self.logger.warning(f"AI content merge failed: {e}")
        
        return (self._merge_content_simple(existing, new, action), [])
    
    def _integrate_content(
        self, 
        existing: str, 
        new: str,
        multi_target: Optional[MultiTargetUpdate] = None
    ) -> str:
        """章単位でコンテンツを統合
        
        【重要】新規ファイルの内容は全て反映されることを保証する：
        1. 更新セクション: 既存セクションの位置に新しい内容を挿入
        2. 新規セクション: 末尾に追加
        3. 変更なしセクション: 既存の内容を保持
        """
        existing_sections = self._parse_sections(existing)
        new_sections = self._parse_sections(new)
        
        # 新規セクションの辞書を作成
        new_sections_dict = {self._normalize_title(t): (t, c) for t, c in new_sections}
        
        result_sections = []
        used_new_sections = set()
        
        # multi_target情報から更新対象のセクションマッピングを取得
        update_mappings = {}  # normalized_new_heading -> normalized_existing_heading
        if multi_target and multi_target.targets:
            for target in multi_target.targets:
                # 「新規セクション名 → 既存セクション名」形式の解析
                for update_section in target.update_sections:
                    if " → " in update_section:
                        new_heading, existing_heading = update_section.split(" → ", 1)
                        update_mappings[self._normalize_title(new_heading.strip())] = \
                            self._normalize_title(existing_heading.strip())
        
        # 既存セクションを処理
        for title, content in existing_sections:
            normalized_title = self._normalize_title(title)
            new_match = None
            
            # 1. まずmulti_target情報からマッチを探す
            for new_normalized, existing_normalized in update_mappings.items():
                if normalized_title == existing_normalized:
                    # このセクションを更新
                    if new_normalized in new_sections_dict:
                        new_match = new_sections_dict[new_normalized]
                        used_new_sections.add(new_normalized)
                        self.logger.debug(f"Updating section via mapping: '{title}' <- '{new_match[0]}'")
                        break
            
            # 2. マッピングでマッチしなければ、タイトル完全一致を試す
            if not new_match and normalized_title in new_sections_dict:
                new_match = new_sections_dict[normalized_title]
                used_new_sections.add(normalized_title)
                self.logger.debug(f"Updating section via title match: '{title}'")
            
            if new_match:
                result_sections.append(new_match)
            else:
                result_sections.append((title, content))
        
        # 新規セクションを追加（使用済み以外）
        added_new = 0
        for new_title, new_content in new_sections:
            normalized_new = self._normalize_title(new_title)
            if normalized_new not in used_new_sections:
                result_sections.append((new_title, new_content))
                added_new += 1
                self.logger.debug(f"Adding new section: '{new_title}'")
        
        if added_new > 0:
            self.logger.info(f"Added {added_new} new sections from source document")
        
        # 再構成
        result_lines = []
        for title, content in result_sections:
            result_lines.append(title)
            result_lines.append(content)
            result_lines.append("")
        
        return "\n".join(result_lines)
    
    def _parse_sections(self, content: str) -> List[Tuple[str, str]]:
        """コンテンツをセクションに分割"""
        sections = []
        lines = content.split("\n")
        current_title = ""
        current_content = []
        
        for line in lines:
            if re.match(r"^#{1,2}\s+", line):
                if current_title:
                    sections.append((current_title, "\n".join(current_content)))
                current_title = line
                current_content = []
            else:
                current_content.append(line)
        
        if current_title:
            sections.append((current_title, "\n".join(current_content)))
        
        return sections
    
    def _normalize_title(self, title: str) -> str:
        """タイトルを正規化"""
        # マークダウン記号と空白を除去
        title = re.sub(r"^#+\s*", "", title)
        title = re.sub(r"\s+", "", title)
        return title.lower()
    
    def _extract_headings(self, content: str) -> List[str]:
        """見出しを抽出"""
        headings = []
        for line in content.split("\n"):
            match = re.match(r"^(#{1,4})\s+(.+)$", line)
            if match:
                headings.append(match.group(2).strip())
        return headings
    
    def _merge_content_simple(
        self,
        existing: str,
        new: str,
        action: ProposedAction,
    ) -> str:
        """簡易マージ（新しい内容で置換）"""
        return new
    
    def _execute_skip(self, action: ProposedAction) -> None:
        """スキップ"""
        self.logger.info(f"Skipping: {action.source_document} - {action.reason}")
        
        self.results["skipped"].append({
            "source": action.source_document,
            "reason": action.reason,
        })
    
    def _generate_frontmatter(self, action: ProposedAction, split_index: Optional[int] = None) -> str:
        """YAMLフロントマターを生成"""
        now = datetime.now().isoformat()
        
        frontmatter = [
            "---",
            "source:",
            f'  - file: "{action.source_document}"',
        ]
        
        if split_index:
            frontmatter.append(f'  - split_index: {split_index}')
        
        frontmatter.extend([
            f'generated_at: "{now}"',
            "---",
        ])
        
        return "\n".join(frontmatter)
    
    def _update_frontmatter(self, content: str, action: ProposedAction) -> str:
        """YAMLフロントマターを更新"""
        now = datetime.now().isoformat()
        
        # 既存のフロントマターを解析
        if content.startswith("---"):
            end_idx = content.find("---", 3)
            if end_idx > 0:
                # 既存のフロントマターを削除
                content = content[end_idx + 3:].lstrip()
        
        # 新しいフロントマターを追加
        frontmatter = [
            "---",
            "source:",
            f'  - file: "{action.source_document}"',
            f'updated_at: "{now}"',
            "---",
            "",
        ]
        
        return "\n".join(frontmatter) + content
    
    def _copy_images(self, doc_name: str, target_dir: Path) -> None:
        """画像をコピー"""
        source_images = self._get_source_images(doc_name)
        
        if not source_images:
            return
        
        # images/フォルダを作成
        images_dir = target_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        
        for img_path in source_images:
            target_img = images_dir / img_path.name
            
            if not target_img.exists():
                shutil.copy2(img_path, target_img)
                self.logger.debug(f"Copied image: {img_path.name}")
    
    def _update_mapping(self) -> None:
        """mapping.jsonを更新"""
        mapping_path = self.output_dir / "mapping.json"
        
        # 既存のmappingを読み込み
        existing = {}
        if mapping_path.exists():
            try:
                with open(mapping_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception as e:
                self.logger.warning(f"Failed to load existing mapping.json: {e}")
        
        # 新しいエントリを追加
        now = datetime.now().isoformat()
        
        for created in self.results["created"]:
            existing[created["target"]] = {
                "inputs": [created["source"]],
                "created_at": now,
            }
        
        for updated in self.results["updated"]:
            target = updated["target"]
            if target in existing:
                # 既存エントリを更新
                inputs = existing[target].get("inputs", [])
                if updated["source"] not in inputs:
                    inputs.append(updated["source"])
                existing[target]["inputs"] = inputs
                existing[target]["updated_at"] = now
            else:
                existing[target] = {
                    "inputs": [updated["source"]],
                    "created_at": now,
                }
        
        # 保存
        with open(mapping_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        
        self.logger.info(f"Updated mapping.json with {len(self.results['created'])} new and {len(self.results['updated'])} updated entries")
    
    def generate_update_report(self) -> str:
        """UPDATE_REPORT.md を生成"""
        lines = [
            "# ナレッジ更新レポート",
            "",
            f"**更新日時**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            "",
            "## 📊 更新サマリー",
            "",
            "| 種別 | 件数 |",
            "|------|------|",
            f"| ✨ 新規追加 | {len(self.results['created'])}件 |",
            f"| 📝 更新 | {len(self.results['updated'])}件 |",
            f"| ⏭️ スキップ | {len(self.results['skipped'])}件 |",
            "",
        ]
        
        if self.results["errors"]:
            lines.append(f"| ❌ エラー | {len(self.results['errors'])}件 |")
            lines.append("")
        
        lines.append("---")
        lines.append("")
        
        # 新規追加
        if self.results["created"]:
            lines.append("## 1. 新規追加")
            lines.append("")
            lines.append("| ファイル | ソース | 判断理由 |")
            lines.append("|---------|-------|---------|")
            
            for item in self.results["created"]:
                detail = next((d for d in self.change_details if d["target"] == item["target"]), {})
                reason = detail.get("reason", "")
                lines.append(f"| `{item['target']}` | {item['source']} | {reason} |")
            
            lines.append("")
        
        # 更新
        if self.results["updated"]:
            lines.append("## 2. 更新")
            lines.append("")
            
            for idx, item in enumerate(self.results["updated"], 1):
                detail = next((d for d in self.change_details if d["target"] == item["target"]), {})
                
                lines.append(f"### 2.{idx} {item['source']} → {item['target']}")
                lines.append("")
                lines.append(f"**ソース**: pre-knowledge/{item['source']}/")
                lines.append(f"**判断理由**: {detail.get('reason', '')}")
                
                # マージ後サイズと分割情報
                merged_size = item.get("merged_size", 0)
                if merged_size > 0:
                    lines.append(f"**マージ後サイズ**: {merged_size:,}文字")
                
                is_split = item.get("split", False)
                if is_split:
                    split_total = item.get("split_total", 1)
                    lines.append(f"**分割**: ⚠️ {split_total}ファイルに分割されました（10,000文字超過のため）")
                
                lines.append("")
                
                changes = detail.get("changes", [])
                
                # 主要変更
                primary_changes = [c for c in changes if c.get("is_primary", True)]
                if primary_changes:
                    lines.append("#### 主要変更")
                    lines.append("")
                    lines.append("| 更新項目 | 更新先ファイル | 更新行 | 更新前内容（抜粋） | 更新後内容 |")
                    lines.append("|---------|--------------|--------|------------------|-----------|")
                    
                    for change in primary_changes:
                        lines.append(
                            f"| {change['item']} | {item['target']} | {change['target_line']} | "
                            f"{change['before']} | {change['after']} |"
                        )
                    
                    lines.append("")
                
                # 派生変更
                secondary_changes = [c for c in changes if not c.get("is_primary", True)]
                if secondary_changes:
                    lines.append("#### 派生変更")
                    lines.append("")
                    lines.append("| 更新項目 | 更新先ファイル | 更新行 | 更新前内容（抜粋） | 更新後内容 |")
                    lines.append("|---------|--------------|--------|------------------|-----------|")
                    
                    for change in secondary_changes:
                        lines.append(
                            f"| {change['item']} | {item['target']} | {change['target_line']} | "
                            f"{change['before']} | {change['after']} |"
                        )
                    
                    lines.append("")
        
        # スキップ
        if self.results["skipped"]:
            lines.append("## 3. スキップ")
            lines.append("")
            lines.append("| ドキュメント | 理由 |")
            lines.append("|-------------|------|")
            
            for item in self.results["skipped"]:
                lines.append(f"| {item['source']} | {item['reason']} |")
            
            lines.append("")
        
        # エラー
        if self.results["errors"]:
            lines.append("## ❌ エラー")
            lines.append("")
            lines.append("| ドキュメント | アクション | エラー |")
            lines.append("|-------------|----------|--------|")
            
            for item in self.results["errors"]:
                lines.append(f"| {item['source']} | {item['action']} | {item['error']} |")
            
            lines.append("")
        
        # レビューチェックポイント
        lines.extend([
            "---",
            "",
            "## 📝 レビュー時のチェックポイント",
            "",
        ])
        
        if self.results["created"]:
            lines.append("### 新規追加")
            lines.append("")
            for item in self.results["created"]:
                lines.append(f"- [ ] `{item['target']}` の内容が正しいか")
            lines.append("")
        
        if self.results["updated"]:
            lines.append("### 更新")
            lines.append("")
            for item in self.results["updated"]:
                lines.append(f"- [ ] `{item['target']}` の更新内容が適切か")
            lines.append("")
        
        lines.extend([
            "---",
            "",
            "**このレポートに問題がなければ、PRをマージしてください。**",
        ])
        
        return "\n".join(lines)
    
    def save_update_report(self) -> Path:
        """UPDATE_REPORT.md を PR フォルダに日付時間で保存"""
        report_content = self.generate_update_report()
        
        # PRフォルダを作成
        pr_dir = self.output_dir / "PR"
        pr_dir.mkdir(parents=True, exist_ok=True)
        
        # 日付時間をファイル名に含める
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = pr_dir / f"UPDATE_REPORT_{timestamp}.md"
        
        report_path.write_text(report_content, encoding="utf-8")
        self.logger.info(f"Saved update report: {report_path}")
        
        return report_path
