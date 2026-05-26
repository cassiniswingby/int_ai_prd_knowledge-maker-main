#!/usr/bin/env python3
"""Slide-to-image utilities for OCR fallbacks."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from ..core.libreoffice_bridge import LibreOfficeBridge

logger = logging.getLogger(__name__)


class ImageConverter:
    """Convert presentation documents to per-page images via PDF."""

    def __init__(self, bridge: Optional[LibreOfficeBridge] = None, dpi: int = 200, image_format: str = "png"):
        """
        Args:
            bridge: Optional LibreOfficeBridge instance to reuse.
            dpi: Rendering resolution for PyMuPDF.
            image_format: Image format extension (png/jpg).
        """
        self.bridge = bridge or LibreOfficeBridge()
        self.dpi = dpi
        self.image_format = image_format.lower()

    def pptx_to_images(
        self,
        pptx_path: Path,
        output_dir: Optional[Path] = None,
    ) -> Tuple[List[Path], Optional[Path], str]:
        """Convert PPT/PPTX/ODP to images.

        Returns:
            (image_paths, work_dir, message)
            work_dir should be cleaned up by the caller if provided by this method.
        """
        pdf_path, success, log_path = self.bridge.convert_to_pdf(pptx_path)
        if not success:
            return [], None, f"LibreOffice conversion failed. Log: {log_path}"

        return self.pdf_to_images(pdf_path, output_dir)

    def pdf_to_images(
        self,
        pdf_path: Path,
        output_dir: Optional[Path] = None,
    ) -> Tuple[List[Path], Optional[Path], str]:
        """Render a PDF to page images using PyMuPDF."""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            return [], None, "PyMuPDF (fitz) is required for image conversion"

        if not pdf_path.exists():
            return [], None, f"PDF not found: {pdf_path}"

        # Prepare working directory
        temp_dir_created = False
        work_dir: Path
        if output_dir:
            work_dir = Path(output_dir)
            work_dir.mkdir(parents=True, exist_ok=True)
        else:
            work_dir = Path(tempfile.mkdtemp(prefix=f"{pdf_path.stem}_images_"))
            temp_dir_created = True

        image_paths: List[Path] = []

        try:
            doc = fitz.open(pdf_path)
            for page_index in range(doc.page_count):
                page = doc.load_page(page_index)
                pix = page.get_pixmap(dpi=self.dpi)
                img_path = work_dir / f"{pdf_path.stem}_page_{page_index + 1}.{self.image_format}"
                pix.save(img_path)
                image_paths.append(img_path)
            doc.close()
            if not image_paths:
                return [], work_dir if temp_dir_created else None, "No images rendered from PDF"
            return image_paths, work_dir if temp_dir_created else None, ""
        except Exception as e:
            logger.error(f"PDF to images failed: {e}")
            return [], work_dir if temp_dir_created else None, f"PDF to images failed: {e}"
