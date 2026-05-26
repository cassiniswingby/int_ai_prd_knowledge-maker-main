"""Quality checker for enhanced documents.

Validates that formatted.md maintains content integrity from transcribed.md.

改定版（2025-12-19）:
- チェック項目を2つに絞る（文字数、表の行数）
- 文字数チェックに健全な減少理由分析を追加
- 85%以下で詳細表示、60%未満でエラー
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Result of a single quality check."""
    
    name: str
    passed: bool
    severity: str  # "error", "warning", "info"
    message: str
    details: Optional[Dict] = None
    sub_messages: List[str] = field(default_factory=list)  # 詳細行


@dataclass
class QualityReport:
    """Complete quality report for a document."""
    
    document_name: str
    passed: bool = True
    checks: List[CheckResult] = field(default_factory=list)
    input_stats: Dict = field(default_factory=dict)
    output_stats: Dict = field(default_factory=dict)
    
    def add_check(self, check: CheckResult) -> None:
        self.checks.append(check)
        if check.severity == "error" and not check.passed:
            self.passed = False
    
    @property
    def error_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed and c.severity == "error")
    
    @property
    def warning_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed and c.severity == "warning")
    
    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)
    
    def get_failure_summary(self) -> str:
        """Get summary of failures for retry prompt."""
        failures = [c for c in self.checks if not c.passed]
        if not failures:
            return ""
        
        lines = ["以下の品質チェックに失敗しました:"]
        for c in failures:
            lines.append(f"- {c.name}: {c.message}")
            if c.details:
                if "missing" in c.details and c.details["missing"]:
                    missing_items = c.details["missing"][:5]
                    lines.append(f"  欠落項目: {', '.join(str(x) for x in missing_items)}")
        
        return "\n".join(lines)
    
    def to_dict(self) -> Dict:
        def serialize(obj):
            """Convert set to list for JSON serialization."""
            if isinstance(obj, set):
                return list(obj)
            elif isinstance(obj, dict):
                return {k: serialize(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [serialize(v) for v in obj]
            return obj
        
        return {
            "document_name": self.document_name,
            "passed": self.passed,
            "summary": {
                "total_checks": len(self.checks),
                "passed": self.pass_count,
                "warnings": self.warning_count,
                "errors": self.error_count,
            },
            "input_stats": serialize(self.input_stats),
            "output_stats": serialize(self.output_stats),
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "severity": c.severity,
                    "message": c.message,
                    "sub_messages": c.sub_messages,
                    "details": serialize(c.details),
                }
                for c in self.checks
            ],
        }


class QualityChecker:
    """Validates content integrity between transcribed.md and formatted.md.
    
    チェック項目（2つに絞り込み）:
    1. 文字数チェック - 85%以下で詳細表示、60%未満でエラー
    2. 表の行数チェック - 80%未満で警告
    """
    
    # Thresholds
    CHAR_RATIO_DETAIL_THRESHOLD = 0.85  # 85%以下で詳細表示
    CHAR_RATIO_MIN_ERROR = 0.60         # 60%未満でエラー
    TABLE_ROW_RATIO = 0.80              # 80%未満で警告
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
    
    def check(
        self,
        input_content: str,
        output_content: str,
        document_name: str,
    ) -> QualityReport:
        """Run all quality checks.
        
        Args:
            input_content: Content from transcribed.md (02_)
            output_content: Content from formatted.md (03_)
            document_name: Name of the document
            
        Returns:
            QualityReport with all check results
        """
        report = QualityReport(document_name=document_name)
        
        # Collect stats
        input_stats = self._analyze_content(input_content)
        output_stats = self._analyze_content(output_content)
        report.input_stats = input_stats
        report.output_stats = output_stats
        
        # Run 2 checks
        report.add_check(self._check_char_count(input_content, output_content, input_stats, output_stats))
        report.add_check(self._check_table_rows(input_stats, output_stats))
        
        return report
    
    def _analyze_content(self, content: str) -> Dict:
        """Analyze content and extract statistics."""
        return {
            "char_count": len(content),
            "char_count_no_space": len(re.sub(r'\s', '', content)),
            "line_count": content.count('\n') + 1,
            "table_rows": self._count_table_rows(content),
        }
    
    def _count_table_rows(self, content: str) -> int:
        """Count rows in Markdown tables."""
        # Match lines that look like table rows (start with |)
        pattern = r'^\|.+\|$'
        rows = re.findall(pattern, content, re.MULTILINE)
        # Exclude separator rows (|---|---|)
        rows = [r for r in rows if not re.match(r'^\|[\s\-:]+\|$', r)]
        return len(rows)
    
    # === 健全な減少理由の検出 ===
    
    def _detect_decorative_image_reduction(self, input_content: str, output_content: str) -> Tuple[float, str]:
        """装飾画像の説明省略による減少を検出."""
        decorative_patterns = [
            r'slide_001', r'cover', r'logo', r'背景', r'タイトルスライド',
            r'表紙', r'img_001_001', r'装飾',
        ]
        
        input_decorative_chars = 0
        for pattern in decorative_patterns:
            # パターンを含む行とその周辺を探す
            matches = re.findall(rf'.*{pattern}.*', input_content, re.IGNORECASE)
            for match in matches:
                if match not in output_content:
                    input_decorative_chars += len(match)
        
        if len(input_content) == 0:
            return 0.0, ""
        
        ratio = input_decorative_chars / len(input_content)
        if ratio > 0.01:  # 1%以上なら報告
            return ratio, f"装飾画像省略(-{ratio:.0%})"
        return 0.0, ""
    
    def _detect_doc_info_reduction(self, input_content: str, output_content: str) -> Tuple[float, str]:
        """ドキュメント情報・改訂履歴削除による減少を検出."""
        doc_info_patterns = [
            r'抽出日時[：:].*',
            r'ファイル形式[：:].*',
            r'元ファイル名[：:].*',
            r'更新日[：:].*',
            r'作成日[：:].*',
            r'バージョン[：:].*',
            r'改訂履歴',
            r'更新履歴',
            r'変更履歴',
        ]
        
        removed_chars = 0
        for pattern in doc_info_patterns:
            input_matches = re.findall(pattern, input_content, re.IGNORECASE)
            output_matches = re.findall(pattern, output_content, re.IGNORECASE)
            if len(input_matches) > len(output_matches):
                for match in input_matches:
                    if match not in output_content:
                        removed_chars += len(match)
        
        # 改訂履歴セクション全体をチェック
        revision_section = re.search(r'(#{1,3}\s*(?:改訂|更新|変更)履歴.*?)(?=^#{1,3}\s|\Z)', 
                                     input_content, re.MULTILINE | re.DOTALL)
        if revision_section:
            section_text = revision_section.group(1)
            if section_text not in output_content:
                removed_chars += len(section_text)
        
        if len(input_content) == 0:
            return 0.0, ""
        
        ratio = removed_chars / len(input_content)
        if ratio > 0.01:
            return ratio, f"文書情報/改訂履歴削除(-{ratio:.0%})"
        return 0.0, ""
    
    def _detect_quote_reduction(self, input_content: str, output_content: str) -> Tuple[float, str]:
        """引用記法削除による減少を検出."""
        input_quotes = re.findall(r'^>\s*.+$', input_content, re.MULTILINE)
        output_quotes = re.findall(r'^>\s*.+$', output_content, re.MULTILINE)
        
        removed_chars = 0
        for quote in input_quotes:
            if quote not in output_content:
                removed_chars += len(quote)
        
        if len(input_content) == 0:
            return 0.0, ""
        
        ratio = removed_chars / len(input_content)
        if ratio > 0.005:  # 0.5%以上なら報告
            return ratio, f"引用記法削除(-{ratio:.0%})"
        return 0.0, ""
    
    def _detect_label_reduction(self, input_content: str, output_content: str) -> Tuple[float, str]:
        """「図の説明:」等のラベル削除による減少を検出."""
        label_patterns = [
            r'図の説明[：:]',
            r'表の説明[：:]',
            r'>\s*\*\*図の説明',
            r'>\s*\*\*表の説明',
        ]
        
        removed_chars = 0
        for pattern in label_patterns:
            input_matches = re.findall(pattern, input_content)
            output_matches = re.findall(pattern, output_content)
            diff = len(input_matches) - len(output_matches)
            if diff > 0:
                removed_chars += diff * 10  # 1ラベルあたり約10文字
        
        if len(input_content) == 0:
            return 0.0, ""
        
        ratio = removed_chars / len(input_content)
        if ratio > 0.005:
            return ratio, f"ラベル削除(-{ratio:.0%})"
        return 0.0, ""
    
    def _detect_heading_consolidation(self, input_content: str, output_content: str) -> Tuple[float, str]:
        """見出し統合による減少を検出."""
        input_headings = len(re.findall(r'^#{1,4}\s+.+$', input_content, re.MULTILINE))
        output_headings = len(re.findall(r'^#{1,4}\s+.+$', output_content, re.MULTILINE))
        
        if input_headings > output_headings:
            diff = input_headings - output_headings
            # 1見出しあたり平均20文字と仮定
            removed_chars = diff * 20
            if len(input_content) == 0:
                return 0.0, ""
            ratio = removed_chars / len(input_content)
            if ratio > 0.005:
                return ratio, f"見出し統合(-{ratio:.0%})"
        return 0.0, ""
    
    # === Check Methods ===
    
    def _check_char_count(
        self, 
        input_content: str, 
        output_content: str,
        input_stats: Dict, 
        output_stats: Dict
    ) -> CheckResult:
        """Check if character count is maintained with reduction reason analysis.
        
        - 85%以上: OK（詳細表示なし）
        - 60%〜85%: 警告（健全な減少理由を分析して表示）
        - 60%未満: エラー（情報欠落の可能性大）
        """
        input_chars = input_stats["char_count_no_space"]
        output_chars = output_stats["char_count_no_space"]
        
        if input_chars == 0:
            return CheckResult(
                name="文字数チェック",
                passed=True,
                severity="info",
                message="入力が空です",
            )
        
        ratio = output_chars / input_chars
        
        # 85%以上ならOK（詳細なし）
        if ratio >= self.CHAR_RATIO_DETAIL_THRESHOLD:
            return CheckResult(
                name="文字数チェック",
                passed=True,
                severity="info",
                message=f"{ratio:.0%} ({input_chars:,}文字 → {output_chars:,}文字)",
                details={"input": input_chars, "output": output_chars, "ratio": ratio},
            )
        
        # 85%未満の場合、健全な減少理由を分析
        reduction_reasons = []
        total_explained_ratio = 0.0
        
        # 各減少理由を検出
        detectors = [
            self._detect_decorative_image_reduction,
            self._detect_doc_info_reduction,
            self._detect_quote_reduction,
            self._detect_label_reduction,
            self._detect_heading_consolidation,
        ]
        
        for detector in detectors:
            detected_ratio, reason = detector(input_content, output_content)
            if reason:
                reduction_reasons.append(reason)
                total_explained_ratio += detected_ratio
        
        # 判定
        actual_reduction = 1.0 - ratio  # 例: 78% → 22%減少
        unexplained_reduction = actual_reduction - total_explained_ratio
        
        sub_messages = []
        if reduction_reasons:
            sub_messages.append(f"減少理由: {', '.join(reduction_reasons)}")
            sub_messages.append(f"説明可能な減少: 約{total_explained_ratio:.0%}")
        
        if ratio < self.CHAR_RATIO_MIN_ERROR:
            # 60%未満: エラー
            passed = False
            severity = "error"
            if unexplained_reduction > 0.1:
                sub_messages.append(f"残り{unexplained_reduction:.0%}は情報欠落の可能性大")
        elif unexplained_reduction <= 0.05:
            # 説明がつく減少のみ: OK扱い
            passed = True
            severity = "info"
            sub_messages.append("→ 情報欠落なしと判断")
        else:
            # 説明できない減少がある: 警告
            passed = True  # 警告だがpassedはTrue
            severity = "warning"
            sub_messages.append(f"残り{unexplained_reduction:.0%}は要確認")
        
        return CheckResult(
            name="文字数チェック",
            passed=passed,
            severity=severity,
            message=f"{ratio:.0%} ({input_chars:,}文字 → {output_chars:,}文字)",
            sub_messages=sub_messages,
            details={
                "input": input_chars, 
                "output": output_chars, 
                "ratio": ratio,
                "reduction_reasons": reduction_reasons,
                "explained_ratio": total_explained_ratio,
                "unexplained_ratio": unexplained_reduction,
            },
        )
    
    def _check_table_rows(self, input_stats: Dict, output_stats: Dict) -> CheckResult:
        """Check if table rows are maintained."""
        input_rows = input_stats["table_rows"]
        output_rows = output_stats["table_rows"]
        
        if input_rows == 0:
            return CheckResult(
                name="表の行数チェック",
                passed=True,
                severity="info",
                message="入力に表なし",
            )
        
        ratio = output_rows / input_rows
        passed = ratio >= self.TABLE_ROW_RATIO
        
        sub_messages = []
        if not passed:
            sub_messages.append("表のデータが減っています")
        
        return CheckResult(
            name="表の行数チェック",
            passed=passed,
            severity="warning" if not passed else "info",
            message=f"{ratio:.0%} ({input_rows}行 → {output_rows}行)",
            sub_messages=sub_messages,
            details={"input": input_rows, "output": output_rows, "ratio": ratio},
        )

    def print_report(self, report: QualityReport) -> None:
        """Print formatted quality report to console."""
        print()
        print("  ┌" + "─" * 70 + "┐")
        print("  │ 📋 品質チェック結果" + " " * 49 + "│")
        print("  ├" + "─" * 70 + "┤")
        
        for check in report.checks:
            if check.passed:
                icon = "✅"
            elif check.severity == "error":
                icon = "🔴"
            else:
                icon = "⚠️"
            
            # メイン行: アイコン + 名前 + メッセージ
            name_padded = f"{check.name}".ljust(18)
            message = check.message[:45]
            line = f"  │ {icon} {name_padded}: {message}"
            line = line[:71].ljust(71) + "│"
            print(line)
            
            # サブメッセージ（詳細行）
            for sub_msg in check.sub_messages:
                sub_line = f"  │    └ {sub_msg}"
                sub_line = sub_line[:71].ljust(71) + "│"
                print(sub_line)
        
        print("  ├" + "─" * 70 + "┤")
        
        if report.passed:
            result_line = f"  │ 結果: ✅ PASS ({report.pass_count}/{len(report.checks)}項目)"
        else:
            result_line = f"  │ 結果: ❌ FAIL (エラー: {report.error_count}件, 警告: {report.warning_count}件)"
        print(result_line.ljust(71) + "│")
        
        if not report.passed:
            print("  │" + " " * 70 + "│")
            hint = "  │ 💡 --retry-failed オプションで再処理できます"
            print(hint.ljust(71) + "│")
        
        print("  └" + "─" * 70 + "┘")
