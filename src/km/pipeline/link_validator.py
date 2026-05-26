"""Link Validator - ナレッジ内のリンクを検証.

Markdownリンク、画像リンク、アンカーリンクを検証し、
リンク切れがあれば報告する。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


logger = logging.getLogger(__name__)


@dataclass
class LinkIssue:
    """リンク問題"""
    file_path: str          # 問題があるファイル
    line_number: int        # 行番号
    link_type: str          # md / image / anchor
    link_text: str          # リンクテキスト
    target: str             # リンク先
    issue: str              # 問題内容


class LinkValidator:
    """リンク検証器"""
    
    def __init__(
        self,
        knowledge_dir: Path,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Args:
            knowledge_dir: ナレッジディレクトリのパス
            logger: ロガー
        """
        self.knowledge_dir = Path(knowledge_dir).resolve()
        self.logger = logger or logging.getLogger(__name__)
        self.issues: List[LinkIssue] = []
        
        # 全ファイルの見出しをキャッシュ
        self.all_headings: Dict[str, List[str]] = {}
        self.all_files: List[str] = []
    
    def _cache_all_headings(self) -> None:
        """全ファイルの見出しをキャッシュ"""
        self.all_headings = {}
        self.all_files = []
        
        for md_file in self.knowledge_dir.rglob("*.md"):
            rel_path = str(md_file.relative_to(self.knowledge_dir))
            self.all_files.append(rel_path)
            
            try:
                content = md_file.read_text(encoding="utf-8")
                self.all_headings[rel_path] = self._extract_anchor_ids(content)
            except Exception as e:
                self.logger.warning(f"Failed to read {md_file}: {e}")
                self.all_headings[rel_path] = []
    
    def _extract_anchor_ids(self, content: str) -> List[str]:
        """見出しからアンカーIDを抽出"""
        anchors = []
        
        for line in content.split("\n"):
            match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if match:
                title = match.group(2).strip()
                anchor = self._title_to_anchor(title)
                anchors.append(anchor)
        
        return anchors
    
    def _title_to_anchor(self, title: str) -> str:
        """タイトルをGitHub形式のアンカーIDに変換"""
        # GitHubのアンカー生成ルールに従う
        anchor = title.lower()
        
        # 特定の記号を削除（日本語は保持）
        anchor = re.sub(r"[^\w\s\-\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]", "", anchor)
        
        # スペースをハイフンに
        anchor = re.sub(r"\s+", "-", anchor)
        
        # 連続するハイフンを1つに
        anchor = re.sub(r"-+", "-", anchor)
        
        # 先頭・末尾のハイフンを削除
        anchor = anchor.strip("-")
        
        return anchor
    
    def _normalize_path(self, base_dir: Path, target: str) -> Optional[Path]:
        """相対パスを正規化"""
        try:
            # パスを解決
            if target.startswith("/"):
                # 絶対パス（ナレッジルートからの相対）
                return (self.knowledge_dir / target.lstrip("/")).resolve()
            else:
                # 相対パス
                return (base_dir / target).resolve()
        except Exception:
            return None
    
    def validate_file(self, file_path: Path) -> List[LinkIssue]:
        """1ファイルのリンクを検証"""
        issues = []
        
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            self.logger.warning(f"Failed to read {file_path}: {e}")
            return issues
        
        rel_path = str(file_path.relative_to(self.knowledge_dir))
        file_dir = file_path.parent
        
        for line_num, line in enumerate(content.split("\n"), 1):
            # Markdownリンク: [text](path)
            for match in re.finditer(r"\[([^\]]*)\]\(([^)]+)\)", line):
                link_text, target = match.groups()
                
                # 外部リンクはスキップ
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                
                # アンカーのみ
                if target.startswith("#"):
                    anchor = target[1:]
                    file_anchors = self.all_headings.get(rel_path, [])
                    
                    if anchor not in file_anchors:
                        issues.append(LinkIssue(
                            file_path=rel_path,
                            line_number=line_num,
                            link_type="anchor",
                            link_text=link_text,
                            target=target,
                            issue=f"アンカー '{anchor}' が見つかりません",
                        ))
                
                # ファイルリンク（アンカー付きも含む）
                elif "#" in target:
                    file_part, anchor = target.split("#", 1)
                    
                    target_path = self._normalize_path(file_dir, file_part)
                    
                    if target_path is None or not target_path.exists():
                        issues.append(LinkIssue(
                            file_path=rel_path,
                            line_number=line_num,
                            link_type="md",
                            link_text=link_text,
                            target=target,
                            issue=f"ファイル '{file_part}' が見つかりません",
                        ))
                    else:
                        # ファイルは存在するがアンカーがあるかチェック
                        try:
                            target_rel = str(target_path.relative_to(self.knowledge_dir))
                            target_anchors = self.all_headings.get(target_rel, [])
                            
                            if anchor not in target_anchors:
                                issues.append(LinkIssue(
                                    file_path=rel_path,
                                    line_number=line_num,
                                    link_type="anchor",
                                    link_text=link_text,
                                    target=target,
                                    issue=f"アンカー '{anchor}' が '{file_part}' 内に見つかりません",
                                ))
                        except ValueError:
                            # ナレッジディレクトリ外のファイル
                            pass
                
                else:
                    # ファイルリンクのみ
                    target_path = self._normalize_path(file_dir, target)
                    
                    if target_path is None or not target_path.exists():
                        issues.append(LinkIssue(
                            file_path=rel_path,
                            line_number=line_num,
                            link_type="md",
                            link_text=link_text,
                            target=target,
                            issue=f"ファイル '{target}' が見つかりません",
                        ))
            
            # 画像: ![alt](path)
            for match in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", line):
                alt_text, target = match.groups()
                
                # 外部画像はスキップ
                if target.startswith(("http://", "https://")):
                    continue
                
                target_path = self._normalize_path(file_dir, target)
                
                if target_path is None or not target_path.exists():
                    issues.append(LinkIssue(
                        file_path=rel_path,
                        line_number=line_num,
                        link_type="image",
                        link_text=alt_text,
                        target=target,
                        issue=f"画像 '{target}' が見つかりません",
                    ))
        
        return issues
    
    def validate_all(self) -> List[LinkIssue]:
        """全ファイルを検証"""
        self.issues = []
        
        # まず全ファイルの見出しをキャッシュ
        self._cache_all_headings()
        
        # 各ファイルを検証
        for md_file in self.knowledge_dir.rglob("*.md"):
            file_issues = self.validate_file(md_file)
            self.issues.extend(file_issues)
        
        return self.issues
    
    def generate_report(self) -> str:
        """link_check_report.md を生成"""
        lines = [
            "# リンク検証レポート",
            "",
            f"**検証日時**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**検証対象**: {self.knowledge_dir}",
            f"**検証ファイル数**: {len(self.all_files)}",
            "",
        ]
        
        if not self.issues:
            lines.append("✅ **リンク問題なし**")
            lines.append("")
            lines.append(f"全{len(self.all_files)}ファイルのリンクを検証しました。問題は見つかりませんでした。")
            return "\n".join(lines)
        
        # 問題あり
        lines.append(f"❌ **{len(self.issues)} 件の問題を検出**")
        lines.append("")
        
        # 種別ごとに集計
        by_type = {}
        for issue in self.issues:
            by_type.setdefault(issue.link_type, []).append(issue)
        
        lines.append("## サマリー")
        lines.append("")
        lines.append("| 種別 | 件数 |")
        lines.append("|------|------|")
        for link_type, issues in by_type.items():
            type_name = {"md": "Markdownリンク", "image": "画像", "anchor": "アンカー"}.get(link_type, link_type)
            lines.append(f"| {type_name} | {len(issues)}件 |")
        lines.append("")
        
        # 詳細
        lines.append("## 詳細")
        lines.append("")
        lines.append("| ファイル | 行 | 種別 | リンク | 問題 |")
        lines.append("|---------|-----|------|-------|------|")
        
        for issue in self.issues:
            type_name = {"md": "md", "image": "画像", "anchor": "アンカー"}.get(issue.link_type, issue.link_type)
            lines.append(
                f"| {issue.file_path} | {issue.line_number} | "
                f"{type_name} | `{issue.target}` | {issue.issue} |"
            )
        
        return "\n".join(lines)
    
    def has_issues(self) -> bool:
        """問題があるかどうか"""
        return len(self.issues) > 0
    
    def get_summary(self) -> Dict:
        """サマリー情報を取得"""
        return {
            "total_files": len(self.all_files),
            "total_issues": len(self.issues),
            "by_type": {
                "md": len([i for i in self.issues if i.link_type == "md"]),
                "image": len([i for i in self.issues if i.link_type == "image"]),
                "anchor": len([i for i in self.issues if i.link_type == "anchor"]),
            }
        }

