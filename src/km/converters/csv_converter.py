#!/usr/bin/env python3
"""CSV to Markdown table converter."""

from pathlib import Path
from typing import Optional, Tuple
import logging

try:
    import pandas as pd
except ImportError:  # pragma: no cover - handled at runtime
    pd = None

from ..core.factory import BaseConverter


class CSVConverter(BaseConverter):
    """Convert CSV files to Markdown tables."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def _is_supported_extension(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".csv"

    def _sanitize_cell(self, value) -> str:
        if value is None:
            return ""
        # pandas uses NaN/NaT for missing values
        try:
            import pandas as _pd  # type: ignore
            if _pd.isna(value):
                return ""
        except Exception:
            pass

        text = str(value).strip()
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
        text = text.replace("|", "\\|")
        return text

    def convert(self, file_path: Path) -> Tuple[bool, Optional[str], str]:
        """
        Convert CSV to Markdown table text.

        Returns:
            (success, markdown_text, message)
        """
        if pd is None:
            return False, None, "pandas is required to convert CSV files"

        try:
            if not file_path.exists():
                return False, None, f"File not found: {file_path}"

            if not self._is_supported_extension(file_path):
                return False, None, f"Unsupported file extension: {file_path.suffix}"

            df = pd.read_csv(file_path)
        except Exception as e:
            self.logger.error(f"Failed to read CSV: {e}")
            return False, None, f"Failed to read CSV: {e}"

        if df.empty:
            self.logger.warning(f"CSV file is empty: {file_path}")
            return True, "", "CSV file is empty"

        try:
            df_sanitized = df.applymap(self._sanitize_cell)
            markdown = df_sanitized.to_markdown(index=False)
            return True, markdown, ""
        except Exception as e:
            self.logger.error(f"CSV conversion error: {e}")
            return False, None, f"CSV conversion error: {e}"
