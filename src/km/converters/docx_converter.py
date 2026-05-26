#!/usr/bin/env python3
"""
DOCX to text converter via PDF conversion for page-based output.
Uses LibreOffice to convert DOCX to PDF, then processes as PDF for consistent page structure.
Also extracts images directly from DOCX to ensure no image loss during PDF conversion.
"""

import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional, Set

from ..core import BaseConverter, ConversionResult, ExtractedImage
from ..core.libreoffice_bridge import LibreOfficeBridge

logger = logging.getLogger(__name__)


def _is_image_extraction_enabled() -> bool:
    """Check if image extraction is enabled via environment variable."""
    return os.getenv("ENABLE_IMAGE_EXTRACTION", "true").lower() in ("true", "1", "yes")


def _is_image_ai_description_enabled() -> bool:
    """Check if AI image description is enabled via environment variable."""
    return os.getenv("ENABLE_IMAGE_AI_DESCRIPTION", "true").lower() in ("true", "1", "yes")


def _compute_image_hash(image_data: bytes) -> str:
    """Compute MD5 hash of image data for deduplication."""
    return hashlib.md5(image_data).hexdigest()


def _deduplicate_images(images: List[ExtractedImage]) -> List[ExtractedImage]:
    """Remove duplicate images based on content hash.
    
    Args:
        images: List of extracted images (may contain duplicates)
        
    Returns:
        List of unique images (first occurrence kept)
    """
    seen_hashes: Set[str] = set()
    unique_images: List[ExtractedImage] = []
    
    for img in images:
        if img.data:
            img_hash = _compute_image_hash(img.data)
            if img_hash not in seen_hashes:
                seen_hashes.add(img_hash)
                unique_images.append(img)
            else:
                logger.debug(f"Removed duplicate image: {img.filename}")
    
    if len(images) != len(unique_images):
        logger.info(f"Deduplication: {len(images)} → {len(unique_images)} images ({len(images) - len(unique_images)} duplicates removed)")
    
    return unique_images


class DOCXConverter(BaseConverter):
    """Convert DOCX files to text via PDF conversion for page-based output.
    
    This converter uses LibreOffice to convert DOCX to PDF first, then processes
    the PDF to extract text and images with proper page-based structure.
    
    This approach ensures:
    - Consistent page numbering (Page 1, Page 2, ...)
    - Images embedded within their respective pages
    - Same output format as PDF and PPTX converters
    
    Environment Variables:
        ENABLE_IMAGE_EXTRACTION: Enable/disable image extraction (default: true)
        ENABLE_IMAGE_AI_DESCRIPTION: Enable/disable Vision API descriptions (default: true)
        IMAGE_MAX_WIDTH: Maximum image width in pixels (default: 1024)
    """

    def __init__(self):
        """Initialize DOCX converter with LibreOffice bridge."""
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.bridge = LibreOfficeBridge()
        self._pdf_converter = None
        # Set Japanese locale
        os.environ.update({
            'LC_ALL': 'ja_JP.UTF-8',
            'LC_CTYPE': 'ja_JP.UTF-8',
            'LANG': 'ja_JP.UTF-8'
        })

    def _get_pdf_converter(self):
        """Lazy-load PDF converter."""
        if self._pdf_converter is None:
            from .pdf_converter import PDFConverter
            self._pdf_converter = PDFConverter()
        return self._pdf_converter

    def _is_supported_extension(self, file_path: Path) -> bool:
        """Check if file is a DOCX."""
        return file_path.suffix.lower() in ['.docx', '.doc']

    def convert(self, file_path: Path) -> ConversionResult:
        """
        Convert DOCX to text via PDF conversion for page-based output.

        Args:
            file_path: Path to DOCX file

        Returns:
            ConversionResult with text content and extracted images, page by page
        """
        file_path = Path(file_path).resolve()
        pdf_path: Optional[Path] = None
        log_path: Optional[Path] = None
        
        try:
            # Validate file
            if not file_path.exists():
                return ConversionResult(
                    success=False,
                    message=f"File not found: {file_path}",
                    original_path=file_path,
                    file_format="docx",
                )
            
            if not self._is_supported_extension(file_path):
                return ConversionResult(
                    success=False,
                    message=f"Unsupported file extension: {file_path.suffix}",
                    original_path=file_path,
                    file_format="docx",
                )

            # Step 1: Convert DOCX to PDF using LibreOffice
            self.logger.info(f"Converting DOCX to PDF: {file_path.name}")
            pdf_path, success, log_path = self.bridge.convert_to_pdf(file_path)
            
            if not success or pdf_path is None or not pdf_path.exists():
                error_msg = f"LibreOffice conversion failed for {file_path.name}"
                if log_path and log_path.exists():
                    self._log_tail(log_path)
                    error_msg += f". See log: {log_path}"
                return ConversionResult(
                    success=False,
                    message=error_msg,
                    original_path=file_path,
                    file_format="docx",
                )
            
            self.logger.info(f"PDF created: {pdf_path}")
            
            # Step 2: Process PDF using PDF converter
            pdf_converter = self._get_pdf_converter()
            result = pdf_converter.convert(pdf_path)
            
            if not result.success:
                return ConversionResult(
                    success=False,
                    message=f"PDF processing failed: {result.message}",
                    original_path=file_path,
                    file_format="docx",
                )
            
            # Step 3: Deduplicate images (PDF conversion may create duplicates)
            deduplicated_images = _deduplicate_images(result.images) if result.images else []
            
            # Update result metadata to reflect original DOCX file
            self.logger.info(
                f"Successfully converted DOCX via PDF: {len(result.text or '')} chars, "
                f"{len(deduplicated_images)} unique images from {file_path.name}"
            )
            
            return ConversionResult(
                success=True,
                text=result.text,
                message=f"Converted via PDF: {result.message}",
                images=deduplicated_images,
                original_path=file_path,
                file_format="docx",
            )

        except Exception as e:
            error_msg = f"DOCX conversion error: {str(e)}"
            self.logger.error(error_msg)
            return ConversionResult(
                success=False,
                message=error_msg,
                original_path=file_path,
                file_format="docx",
            )
        
        finally:
            # Cleanup temporary PDF
            if pdf_path and pdf_path.exists():
                try:
                    pdf_path.unlink()
                    # Also clean up parent temp directory if empty
                    parent = pdf_path.parent
                    if parent.exists() and not any(parent.iterdir()):
                        shutil.rmtree(parent, ignore_errors=True)
                except Exception as e:
                    self.logger.warning(f"Failed to cleanup temp PDF: {e}")

    def _log_tail(self, log_path: Path, lines: int = 20) -> None:
        """Log the last N lines of a file for debugging."""
        try:
            if not log_path.exists():
                return
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.readlines()
                tail = content[-lines:] if len(content) > lines else content
                self.logger.error(f"LibreOffice log tail ({log_path}):\n{''.join(tail)}")
        except Exception:
            pass
