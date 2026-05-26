#!/usr/bin/env python3
"""
XLS to text converter using LibreOffice Bridge.
Handles legacy Excel files through unified LibreOffice interface.
"""

import logging
import os
from pathlib import Path

from ..core import BaseConverter, ConversionResult
from ..core.libreoffice_bridge import LibreOfficeBridge


class XLSConverter(BaseConverter):
    """Convert XLS files to text using LibreOffice Bridge.
    
    This converter first converts XLS to XLSX using LibreOffice,
    then uses XLSXConverter to extract text and images.
    """

    def __init__(self):
        """Initialize XLS converter with LibreOffice Bridge."""
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.bridge = LibreOfficeBridge()
        # Set Japanese locale
        os.environ.update({
            'LC_ALL': 'ja_JP.UTF-8',
            'LC_CTYPE': 'ja_JP.UTF-8',
            'LANG': 'ja_JP.UTF-8'
        })

    def _is_supported_extension(self, file_path: Path) -> bool:
        """Check if file is XLS."""
        return file_path.suffix.lower() == '.xls'

    def convert(self, file_path: Path) -> ConversionResult:
        """
        Convert XLS to text using LibreOffice Bridge.

        Args:
            file_path: Path to XLS file

        Returns:
            ConversionResult with text content and extracted images
        """
        file_path = Path(file_path).resolve()
        
        try:
            # Validate file
            if not file_path.exists():
                return ConversionResult(
                    success=False,
                    message=f"File not found: {file_path}",
                    original_path=file_path,
                    file_format="xls",
                )
            
            if not self._is_supported_extension(file_path):
                return ConversionResult(
                    success=False,
                    message=f"Unsupported file extension: {file_path.suffix}",
                    original_path=file_path,
                    file_format="xls",
                )

            # Convert to XLSX using LibreOffice
            self.logger.info(f"Converting XLS to XLSX: {file_path.name}")
            xlsx_path, success = self.bridge.convert_to_xlsx(file_path)
            
            if not success or not xlsx_path:
                return ConversionResult(
                    success=False,
                    message="Failed to convert XLS to XLSX using LibreOffice",
                    original_path=file_path,
                    file_format="xls",
                )
            
            try:
                # Import XLSX converter for text extraction
                from .xlsx_converter import XLSXConverter
                xlsx_converter = XLSXConverter()
                
                # Extract text from converted XLSX
                result = xlsx_converter.convert(xlsx_path)
                
                # Update original path and format
                result.original_path = file_path
                result.file_format = "xls"
                
                if result.success:
                    self.logger.info(
                        f"Successfully extracted {len(result.text or '')} characters, "
                        f"{len(result.images)} images from {file_path.name}"
                    )
                
                return result
                    
            finally:
                # Clean up temporary XLSX file
                if xlsx_path.exists():
                    try:
                        xlsx_path.unlink()
                        self.logger.debug(f"Cleaned up temporary file: {xlsx_path}")
                    except Exception as e:
                        self.logger.warning(f"Failed to clean up temporary file: {e}")

        except FileNotFoundError:
            error_msg = f"File not found: {file_path}"
            self.logger.error(error_msg)
            return ConversionResult(
                success=False,
                message=error_msg,
                original_path=file_path,
                file_format="xls",
            )

        except Exception as e:
            error_msg = f"XLS conversion error: {str(e)}"
            self.logger.error(error_msg)
            return ConversionResult(
                success=False,
                message=error_msg,
                original_path=file_path,
                file_format="xls",
            )
