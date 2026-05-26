#!/usr/bin/env python3
"""
DOC to text converter using LibreOffice Bridge.
Handles legacy Word documents through unified LibreOffice interface.
"""

import logging
import os
from pathlib import Path

from ..core import BaseConverter, ConversionResult
from ..core.libreoffice_bridge import LibreOfficeBridge


class DOCConverter(BaseConverter):
    """Convert DOC files to text using LibreOffice Bridge.
    
    This converter first converts DOC to DOCX using LibreOffice,
    then uses DOCXConverter to extract text and images.
    """

    def __init__(self):
        """Initialize DOC converter with LibreOffice Bridge."""
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
        """Check if file is a DOC."""
        return file_path.suffix.lower() == '.doc'

    def convert(self, file_path: Path) -> ConversionResult:
        """
        Convert DOC to text using LibreOffice Bridge.

        Args:
            file_path: Path to DOC file

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
                    file_format="doc",
                )
            
            if not self._is_supported_extension(file_path):
                return ConversionResult(
                    success=False,
                    message=f"Unsupported file extension: {file_path.suffix}",
                    original_path=file_path,
                    file_format="doc",
                )

            # Convert to DOCX using LibreOffice
            self.logger.info(f"Converting DOC to DOCX: {file_path.name}")
            docx_path, success = self.bridge.convert_to_docx(file_path)
            
            if not success or not docx_path:
                return ConversionResult(
                    success=False,
                    message="Failed to convert DOC to DOCX using LibreOffice",
                    original_path=file_path,
                    file_format="doc",
                )
            
            try:
                # Import DOCX converter for text extraction
                from .docx_converter import DOCXConverter
                docx_converter = DOCXConverter()
                
                # Extract text from converted DOCX
                result = docx_converter.convert(docx_path)
                
                # Update original path and format
                result.original_path = file_path
                result.file_format = "doc"
                
                if result.success:
                    self.logger.info(
                        f"Successfully extracted {len(result.text or '')} characters, "
                        f"{len(result.images)} images from {file_path.name}"
                    )
                
                return result
                    
            finally:
                # Clean up temporary DOCX file
                if docx_path.exists():
                    try:
                        docx_path.unlink()
                        self.logger.debug(f"Cleaned up temporary file: {docx_path}")
                    except Exception as e:
                        self.logger.warning(f"Failed to clean up temporary file: {e}")

        except FileNotFoundError:
            error_msg = f"File not found: {file_path}"
            self.logger.error(error_msg)
            return ConversionResult(
                success=False,
                message=error_msg,
                original_path=file_path,
                file_format="doc",
            )

        except Exception as e:
            error_msg = f"DOC conversion error: {str(e)}"
            self.logger.error(error_msg)
            return ConversionResult(
                success=False,
                message=error_msg,
                original_path=file_path,
                file_format="doc",
            )
