"""PDF to Markdown converter with image and diagram extraction."""

import gc
import io
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

from ..core import BaseConverter, ConversionResult, ExtractedImage
from ..utils.ocr_client import is_skip_response

logger = logging.getLogger(__name__)


def _get_parallel_workers() -> int:
    """Get number of parallel workers for Vision API calls."""
    try:
        return int(os.getenv("VISION_API_PARALLEL_WORKERS", "200"))
    except ValueError:
        return 50


# Minimum image size to extract (skip small logos/icons)
MIN_IMAGE_WIDTH = 150
MIN_IMAGE_HEIGHT = 150

# Minimum drawing objects to consider as a diagram (緩和: 15→8)
MIN_DRAWING_OBJECTS = 8

# Minimum diagram area (width * height) (緩和: 20000→10000)
MIN_DIAGRAM_AREA = 10000

# 表スクショの有効化
def _is_table_screenshot_enabled() -> bool:
    """Check if table screenshot is enabled via environment variable."""
    return os.getenv("ENABLE_TABLE_SCREENSHOT", "true").lower() in ("true", "1", "yes")


def _is_image_extraction_enabled() -> bool:
    """Check if image extraction is enabled via environment variable."""
    return os.getenv("ENABLE_IMAGE_EXTRACTION", "true").lower() in ("true", "1", "yes")


def _is_image_ai_description_enabled() -> bool:
    """Check if AI image description is enabled via environment variable."""
    return os.getenv("ENABLE_IMAGE_AI_DESCRIPTION", "true").lower() in ("true", "1", "yes")


def _get_image_max_width() -> int:
    """Get maximum image width for resizing."""
    try:
        return int(os.getenv("IMAGE_MAX_WIDTH", "1200"))
    except ValueError:
        return 1200


class PDFConverter(BaseConverter):
    """Convert PDF files to Markdown with images and diagrams.
    
    Extracts:
    - Text content
    - Embedded images (photos, charts)
    - Diagrams made from drawing objects (lines, rectangles, arrows)
    
    All images include AI-generated descriptions when OPENAI_API_KEY is set.
    
    Environment Variables:
        ENABLE_IMAGE_EXTRACTION: Enable/disable image extraction (default: true)
        ENABLE_IMAGE_AI_DESCRIPTION: Enable/disable AI descriptions (default: true)
        IMAGE_MAX_WIDTH: Maximum image width in pixels (default: 1200)
    """

    def __init__(self):
        """Initialize PDF converter."""
        os.environ.update({
            'LC_ALL': 'ja_JP.UTF-8',
            'LC_CTYPE': 'ja_JP.UTF-8',
            'LANG': 'ja_JP.UTF-8'
        })
        self._ocr_client = None

    def _get_ocr_client(self):
        """Lazy-load OCR client for Vision API."""
        if self._ocr_client is None and _is_image_ai_description_enabled():
            try:
                from ..utils import OCRClient
                self._ocr_client = OCRClient()
            except Exception as e:
                logger.warning(f"Failed to initialize OCR client: {e}")
        return self._ocr_client

    def convert(self, file_path: Path) -> ConversionResult:
        """Convert PDF file to Markdown with images and diagrams.
        
        Uses parallel processing for Vision API calls to speed up image descriptions.
        
        Args:
            file_path: Path to PDF file
            
        Returns:
            ConversionResult with Markdown text and extracted images
        """
        file_path = Path(file_path).resolve()
        
        if not file_path.exists():
            return ConversionResult(
                success=False,
                message=f"File not found: {file_path}",
                original_path=file_path,
                file_format="pdf",
            )
        
        if file_path.suffix.lower() != '.pdf':
            return ConversionResult(
                success=False,
                message=f"Not a PDF file: {file_path.suffix}",
                original_path=file_path,
                file_format="pdf",
            )
        
        if fitz is None:
            return ConversionResult(
                success=False,
                message="PyMuPDF (fitz) is not installed",
                original_path=file_path,
                file_format="pdf",
            )
        
        try:
            doc = fitz.open(file_path)
            num_pages = len(doc)
            all_images: List[ExtractedImage] = []
            markdown_parts = []
            image_counter = 0
            diagram_counter = 0
            
            # Phase 1: Extract all pages (without AI descriptions)
            page_contents = []
            for page_num in range(num_pages):
                page = doc[page_num]
                page_content = self._process_page_without_ai(
                    doc, page, page_num, image_counter, diagram_counter
                )
                page_contents.append(page_content)
                image_counter += page_content["image_count"]
                diagram_counter += page_content["diagram_count"]
            
            doc.close()
            
            # Collect all images that need AI descriptions
            images_needing_description = []
            for page_content in page_contents:
                for img in page_content["images"]:
                    if img.data:  # Has image data
                        images_needing_description.append(img)
            
            # Phase 2: Get AI descriptions in parallel
            if images_needing_description and _is_image_ai_description_enabled():
                logger.info(f"Getting AI descriptions for {len(images_needing_description)} images in parallel...")
                self._get_descriptions_parallel(images_needing_description)
            
            # Phase 3: Build markdown with descriptions
            for page_num, page_content in enumerate(page_contents):
                markdown_parts.append(f"## Page {page_num + 1}\n")
                # Rebuild markdown with updated descriptions
                markdown = self._rebuild_markdown_with_descriptions(page_content)
                markdown_parts.append(markdown)
                markdown_parts.append("\n---\n")
                all_images.extend(page_content["images"])
            
            full_text = "\n".join(markdown_parts)
            
            gc.collect()
            
            return ConversionResult(
                success=True,
                text=full_text,
                message=f"Extracted {num_pages} pages, {len(all_images)} images/diagrams",
                images=all_images,
                original_path=file_path,
                file_format="pdf",
            )
                    
        except Exception as e:
            logger.error(f"PDF extraction error for {file_path}: {e}")
            return ConversionResult(
                success=False,
                message=f"PDF extraction failed: {str(e)}",
                original_path=file_path,
                file_format="pdf",
            )

    def _process_page_without_ai(
        self,
        doc: "fitz.Document",
        page: "fitz.Page",
        page_num: int,
        image_start_index: int,
        diagram_start_index: int,
    ) -> Dict:
        """Process a single page: extract text, images, diagrams, and tables WITHOUT AI descriptions.
        
        AI descriptions will be added later in parallel for better performance.
        
        Args:
            doc: PyMuPDF document
            page: PyMuPDF page
            page_num: Page number (0-indexed)
            image_start_index: Starting index for image numbering
            diagram_start_index: Starting index for diagram numbering
            
        Returns:
            Dict with 'content_items', 'images', 'image_count', 'diagram_count'
        """
        page_dict = page.get_text("dict")
        blocks = page_dict.get("blocks", [])
        
        content_items = []  # List of (y_position, type, content, image_ref)
        extracted_images = []
        image_index = image_start_index
        diagram_index = diagram_start_index
        table_index = 0
        
        # 0. Detect tables and screenshot them
        if _is_table_screenshot_enabled():
            try:
                tables = page.find_tables()
                if tables.tables:
                    for table in tables.tables:
                        table_index += 1
                        table_result = self._extract_table_screenshot(
                            page, table, page_num, table_index
                        )
                        if table_result:
                            extracted_images.append(table_result["image"])
                            content_items.append((
                                table_result["y"],
                                "table",
                                table_result["image"].filename,
                                table_result["image"]
                            ))
                            logger.debug(f"Page {page_num + 1}: extracted table screenshot")
            except Exception as e:
                logger.debug(f"Table detection failed on page {page_num + 1}: {e}")
        
        # 1. Process embedded images
        embedded_images = {}
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                img_data = doc.extract_image(xref)
                if img_data:
                    embedded_images[xref] = img_data
            except Exception:
                continue
        
        # 2. Process blocks (text and image blocks)
        for block in blocks:
            bbox = block.get("bbox", [0, 0, 0, 0])
            y_pos = bbox[1]
            block_width = bbox[2] - bbox[0]
            block_height = bbox[3] - bbox[1]
            
            if block.get("type") == 0:  # Text block
                text_lines = []
                for line in block.get("lines", []):
                    line_text = ""
                    for span in line.get("spans", []):
                        line_text += span.get("text", "")
                    if line_text.strip():
                        text_lines.append(line_text.strip())
                
                if text_lines:
                    content_items.append((y_pos, "text", "\n".join(text_lines), None))
            
            elif block.get("type") == 1:  # Image block
                if block_width < MIN_IMAGE_WIDTH or block_height < MIN_IMAGE_HEIGHT:
                    continue
                
                # Find matching embedded image
                image_data = None
                matched_xref = None
                for xref, img_data in embedded_images.items():
                    img_w, img_h = img_data["width"], img_data["height"]
                    if img_w >= MIN_IMAGE_WIDTH and img_h >= MIN_IMAGE_HEIGHT:
                        image_data = img_data
                        matched_xref = xref
                        break
                
                if image_data and matched_xref:
                    del embedded_images[matched_xref]
                    image_index += 1
                    filename = f"img_{page_num + 1:03d}_{image_index:03d}.{image_data['ext']}"
                    
                    # Create image without AI description (will be added later)
                    extracted_image = ExtractedImage(
                        data=image_data["image"],
                        filename=filename,
                        page_or_sheet=f"Page {page_num + 1}",
                        position=image_index,
                        ai_description=None,  # Will be filled in parallel
                        width=image_data["width"],
                        height=image_data["height"],
                        format=image_data["ext"],
                    )
                    extracted_images.append(extracted_image)
                    
                    # Store reference to image for later markdown generation
                    content_items.append((y_pos, "image", filename, extracted_image))
        
        # 3. Detect and extract diagrams from drawing objects (tables are now handled separately)
        drawings = page.get_drawings()
        if len(drawings) >= MIN_DRAWING_OBJECTS:
            diagram_regions = self._find_diagram_regions(drawings, page)
            
            for region in diagram_regions:
                diagram_index += 1
                diagram_result = self._extract_diagram_without_ai(
                    page, region, page_num, diagram_index
                )
                
                if diagram_result:
                    extracted_images.append(diagram_result["image"])
                    content_items.append((
                        region["y"],
                        "diagram",
                        diagram_result["image"].filename,
                        diagram_result["image"]
                    ))
        
        # Sort by Y position
        content_items.sort(key=lambda x: x[0])
        
        return {
            "content_items": content_items,
            "images": extracted_images,
            "image_count": image_index - image_start_index,
            "diagram_count": diagram_index - diagram_start_index,
        }
    
    def _get_descriptions_parallel(self, images: List[ExtractedImage]) -> None:
        """Get AI descriptions for multiple images in parallel.
        
        Updates the ai_description field of each ExtractedImage in place.
        Skips images that are identified as logos/icons/decorations.
        
        Args:
            images: List of ExtractedImage objects to process
        """
        ocr_client = self._get_ocr_client()
        if ocr_client is None:
            return
        
        max_workers = _get_parallel_workers()
        logger.info(f"Processing {len(images)} images with {max_workers} parallel workers")
        
        def get_description_for_image(img: ExtractedImage) -> Tuple[ExtractedImage, Optional[str]]:
            """Worker function to get description for a single image."""
            try:
                description = self._get_image_description(img.data, img.format or "png")
                # SKIPレスポンスのチェック
                if description and is_skip_response(description):
                    logger.debug(f"Skipped image {img.filename} (logo/icon/decoration)")
                    return (img, None)
                return (img, description)
            except Exception as e:
                logger.warning(f"Failed to get description for {img.filename}: {e}")
                return (img, None)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(get_description_for_image, img): img for img in images}
            
            completed = 0
            skipped = 0
            for future in as_completed(futures):
                completed += 1
                try:
                    img, description = future.result()
                    if description is None:
                        skipped += 1
                    # Update the image object in place
                    object.__setattr__(img, 'ai_description', description)
                    if completed % 5 == 0:
                        logger.info(f"AI descriptions: {completed}/{len(images)} completed")
                except Exception as e:
                    logger.warning(f"Error processing image: {e}")
        
        logger.info(f"AI descriptions: {len(images)}/{len(images)} completed ({skipped} skipped)")
    
    def _extract_table_screenshot(
        self,
        page: "fitz.Page",
        table,
        page_num: int,
        table_index: int,
    ) -> Optional[Dict]:
        """Extract a table as a screenshot image.
        
        Args:
            page: PyMuPDF page
            table: PyMuPDF table object
            page_num: Page number (0-indexed)
            table_index: Table index for filename
            
        Returns:
            Dict with 'image' (ExtractedImage) and 'y', or None
        """
        try:
            # Get table bounding box
            bbox = table.bbox
            rect = fitz.Rect(bbox)
            
            # Check if table is large enough
            if rect.width < 100 or rect.height < 50:
                return None
            
            # Add padding
            padding = 5
            page_rect = page.rect
            rect.x0 = max(0, rect.x0 - padding)
            rect.y0 = max(0, rect.y0 - padding)
            rect.x1 = min(page_rect.width, rect.x1 + padding)
            rect.y1 = min(page_rect.height, rect.y1 + padding)
            
            # Calculate zoom for good quality
            max_width = _get_image_max_width()
            zoom = max_width / rect.width if rect.width > 0 else 1.0
            zoom = min(zoom, 3.0)  # Cap at 3x
            zoom = max(zoom, 1.5)  # Minimum 1.5x for readability
            
            # Render the region as image
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, clip=rect)
            
            image_bytes = pix.tobytes("png")
            filename = f"table_{page_num + 1:03d}_{table_index:03d}.png"
            
            extracted_image = ExtractedImage(
                data=image_bytes,
                filename=filename,
                page_or_sheet=f"Page {page_num + 1}",
                position=table_index,
                ai_description=None,  # Will be filled in parallel
                width=pix.width,
                height=pix.height,
                format="png",
            )
            
            return {
                "image": extracted_image,
                "y": rect.y0,
            }
            
        except Exception as e:
            logger.warning(f"Failed to extract table from page {page_num + 1}: {e}")
            return None

    def _rebuild_markdown_with_descriptions(self, page_content: Dict) -> str:
        """Rebuild markdown for a page with AI descriptions included.
        
        Args:
            page_content: Page content dict from _process_page_without_ai
            
        Returns:
            Markdown string with image descriptions
        """
        markdown_lines = []
        
        for _, content_type, content, image_ref in page_content["content_items"]:
            if content_type == "text":
                markdown_lines.append(content)
            elif content_type in ("image", "diagram", "table"):
                filename = content
                img_markdown = f"\n![{filename}](../04_images/{filename})\n"
                if image_ref and image_ref.ai_description:
                    # 全行を > で囲む（複数行対応）
                    desc_lines = image_ref.ai_description.split('\n')
                    quoted_desc = '\n'.join(f"> {line}" for line in desc_lines)
                    if content_type == "table":
                        img_markdown += f"\n> **表の説明:**\n>\n{quoted_desc}\n"
                    else:
                        img_markdown += f"\n> **図の説明:**\n>\n{quoted_desc}\n"
                markdown_lines.append(img_markdown)
        
        return "\n\n".join(markdown_lines)
    
    def _extract_diagram_without_ai(
        self,
        page: "fitz.Page",
        region: Dict,
        page_num: int,
        diagram_index: int,
    ) -> Optional[Dict]:
        """Extract a diagram region as an image WITHOUT AI description.
        
        Args:
            page: PyMuPDF page
            region: Diagram region info with 'rect'
            page_num: Page number (0-indexed)
            diagram_index: Diagram index for filename
            
        Returns:
            Dict with 'image' (ExtractedImage), or None
        """
        rect = region["rect"]
        
        try:
            # Calculate zoom for good quality
            max_width = _get_image_max_width()
            zoom = max_width / rect.width if rect.width > 0 else 1.0
            zoom = min(zoom, 3.0)  # Cap at 3x
            zoom = max(zoom, 1.5)  # Minimum 1.5x for readability
            
            # Render the region as image
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, clip=rect)
            
            image_bytes = pix.tobytes("png")
            filename = f"diagram_{page_num + 1:03d}_{diagram_index:03d}.png"
            
            extracted_image = ExtractedImage(
                data=image_bytes,
                filename=filename,
                page_or_sheet=f"Page {page_num + 1}",
                position=diagram_index,
                ai_description=None,  # Will be filled in parallel
                width=pix.width,
                height=pix.height,
                format="png",
            )
            
            return {
                "image": extracted_image,
            }
            
        except Exception as e:
            logger.warning(f"Failed to extract diagram from page {page_num + 1}: {e}")
            return None
    
    def _process_page(
        self,
        doc: "fitz.Document",
        page: "fitz.Page",
        page_num: int,
        image_start_index: int,
        diagram_start_index: int,
    ) -> Dict:
        """Process a single page: extract text, images, and diagrams.
        
        DEPRECATED: Use _process_page_without_ai + _get_descriptions_parallel instead.
        Kept for backward compatibility.
        
        Args:
            doc: PyMuPDF document
            page: PyMuPDF page
            page_num: Page number (0-indexed)
            image_start_index: Starting index for image numbering
            diagram_start_index: Starting index for diagram numbering
            
        Returns:
            Dict with 'markdown', 'images', 'image_count', 'diagram_count'
        """
        page_dict = page.get_text("dict")
        blocks = page_dict.get("blocks", [])
        
        content_items = []  # List of (y_position, type, content)
        extracted_images = []
        image_index = image_start_index
        diagram_index = diagram_start_index
        
        # 1. Process embedded images
        embedded_images = {}
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                img_data = doc.extract_image(xref)
                if img_data:
                    embedded_images[xref] = img_data
            except Exception:
                continue
        
        # 2. Process blocks (text and image blocks)
        for block in blocks:
            bbox = block.get("bbox", [0, 0, 0, 0])
            y_pos = bbox[1]
            block_width = bbox[2] - bbox[0]
            block_height = bbox[3] - bbox[1]
            
            if block.get("type") == 0:  # Text block
                text_lines = []
                for line in block.get("lines", []):
                    line_text = ""
                    for span in line.get("spans", []):
                        line_text += span.get("text", "")
                    if line_text.strip():
                        text_lines.append(line_text.strip())
                
                if text_lines:
                    content_items.append((y_pos, "text", "\n".join(text_lines)))
            
            elif block.get("type") == 1:  # Image block
                if block_width < MIN_IMAGE_WIDTH or block_height < MIN_IMAGE_HEIGHT:
                    continue
                
                # Find matching embedded image
                image_data = None
                matched_xref = None
                for xref, img_data in embedded_images.items():
                    img_w, img_h = img_data["width"], img_data["height"]
                    if img_w >= MIN_IMAGE_WIDTH and img_h >= MIN_IMAGE_HEIGHT:
                        image_data = img_data
                        matched_xref = xref
                        break
                
                if image_data and matched_xref:
                    del embedded_images[matched_xref]
                    image_index += 1
                    filename = f"img_{page_num + 1:03d}_{image_index:03d}.{image_data['ext']}"
                    
                    # Get AI description
                    ai_description = self._get_image_description(
                        image_data["image"], image_data["ext"]
                    )
                    
                    extracted_image = ExtractedImage(
                        data=image_data["image"],
                        filename=filename,
                        page_or_sheet=f"Page {page_num + 1}",
                        position=image_index,
                        ai_description=ai_description,
                        width=image_data["width"],
                        height=image_data["height"],
                        format=image_data["ext"],
                    )
                    extracted_images.append(extracted_image)
                    
                    # Add image to content
                    img_markdown = f"\n![{filename}](../04_images/{filename})\n"
                    if ai_description:
                        img_markdown += f"\n> **図の説明:** {ai_description}\n"
                    
                    content_items.append((y_pos, "image", img_markdown))
        
        # 3. Detect and extract diagrams from drawing objects
        drawings = page.get_drawings()
        if len(drawings) >= MIN_DRAWING_OBJECTS:
            diagram_regions = self._find_diagram_regions(drawings, page)
            
            for region in diagram_regions:
                diagram_index += 1
                diagram_result = self._extract_diagram(
                    page, region, page_num, diagram_index
                )
                
                if diagram_result:
                    extracted_images.append(diagram_result["image"])
                    content_items.append((
                        region["y"],
                        "diagram",
                        diagram_result["markdown"]
                    ))
        
        # Sort by Y position
        content_items.sort(key=lambda x: x[0])
        
        # Build markdown
        markdown_lines = []
        for _, content_type, content in content_items:
            markdown_lines.append(content)
        
        return {
            "markdown": "\n\n".join(markdown_lines),
            "images": extracted_images,
            "image_count": image_index - image_start_index,
            "diagram_count": diagram_index - diagram_start_index,
        }

    def _find_diagram_regions(
        self,
        drawings: List,
        page: "fitz.Page"
    ) -> List[Dict]:
        """Find regions containing diagrams based on drawing object clustering.
        
        Distinguishes between tables and diagrams:
        - Tables: mostly rectangles (>70%), few curves (<5) → SKIP
        - Diagrams: many curves (>=5) or lines (>50) with few rects (<50%) → EXTRACT
        
        Args:
            drawings: List of drawing objects from page.get_drawings()
            page: PyMuPDF page
            
        Returns:
            List of diagram regions with bbox and y position
        """
        if not drawings:
            return []
        
        # Analyze drawing object composition
        line_count = 0
        rect_count = 0
        curve_count = 0
        
        for d in drawings:
            items = d.get("items", [])
            for item in items:
                item_type = item[0]
                if item_type == 'l':  # line
                    line_count += 1
                elif item_type == 're':  # rectangle
                    rect_count += 1
                elif item_type == 'c':  # curve (Bezier)
                    curve_count += 1
        
        total_items = line_count + rect_count + curve_count
        if total_items == 0:
            return []
        
        rect_ratio = rect_count / total_items
        
        # Table detection: mostly rectangles, few curves
        # Tables have rect_ratio > 0.7 and curves < 5
        is_likely_table = rect_ratio > 0.7 and curve_count < 5
        
        # Diagram detection: has curves OR many lines with few rectangles
        is_likely_diagram = curve_count >= 5 or (line_count > 50 and rect_ratio < 0.5)
        
        # Skip tables, only extract diagrams
        if is_likely_table and not is_likely_diagram:
            logger.debug(
                f"Skipping table: rect={rect_count}({rect_ratio:.0%}), "
                f"line={line_count}, curve={curve_count}"
            )
            return []
        
        if not is_likely_diagram:
            return []
        
        logger.debug(
            f"Extracting diagram: rect={rect_count}({rect_ratio:.0%}), "
            f"line={line_count}, curve={curve_count}"
        )
        
        # Collect all drawing bboxes
        all_rects = []
        for d in drawings:
            rect = d.get("rect")
            if rect:
                all_rects.append(fitz.Rect(rect))
        
        if not all_rects:
            return []
        
        # Calculate the union of all drawing rectangles
        union_rect = all_rects[0]
        for rect in all_rects[1:]:
            union_rect = union_rect | rect  # Union
        
        # Check if the diagram area is large enough
        area = union_rect.width * union_rect.height
        if area < MIN_DIAGRAM_AREA:
            return []
        
        # Skip if it covers almost the entire page (probably decorative)
        page_rect = page.rect
        page_area = page_rect.width * page_rect.height
        if area > page_area * 0.9:
            return []
        
        # Add padding
        padding = 10
        union_rect.x0 = max(0, union_rect.x0 - padding)
        union_rect.y0 = max(0, union_rect.y0 - padding)
        union_rect.x1 = min(page_rect.width, union_rect.x1 + padding)
        union_rect.y1 = min(page_rect.height, union_rect.y1 + padding)
        
        return [{
            "rect": union_rect,
            "y": union_rect.y0,
            "width": union_rect.width,
            "height": union_rect.height,
        }]

    def _extract_diagram(
        self,
        page: "fitz.Page",
        region: Dict,
        page_num: int,
        diagram_index: int,
    ) -> Optional[Dict]:
        """Extract a diagram region as an image.
        
        Args:
            page: PyMuPDF page
            region: Diagram region info with 'rect'
            page_num: Page number (0-indexed)
            diagram_index: Diagram index for filename
            
        Returns:
            Dict with 'image' (ExtractedImage) and 'markdown', or None
        """
        rect = region["rect"]
        
        try:
            # Calculate zoom for good quality
            max_width = _get_image_max_width()
            zoom = max_width / rect.width if rect.width > 0 else 1.0
            zoom = min(zoom, 3.0)  # Cap at 3x
            zoom = max(zoom, 1.5)  # Minimum 1.5x for readability
            
            # Render the region as image
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, clip=rect)
            
            image_bytes = pix.tobytes("png")
            filename = f"diagram_{page_num + 1:03d}_{diagram_index:03d}.png"
            
            # Get AI description
            ai_description = self._get_image_description(image_bytes, "png")
            
            extracted_image = ExtractedImage(
                data=image_bytes,
                filename=filename,
                page_or_sheet=f"Page {page_num + 1}",
                position=diagram_index,
                ai_description=ai_description,
                width=pix.width,
                height=pix.height,
                format="png",
            )
            
            # Build markdown
            img_markdown = f"\n![{filename}](../04_images/{filename})\n"
            if ai_description:
                img_markdown += f"\n> **図の説明:** {ai_description}\n"
            
            return {
                "image": extracted_image,
                "markdown": img_markdown,
            }
            
        except Exception as e:
            logger.warning(f"Failed to extract diagram from page {page_num + 1}: {e}")
            return None

    def _get_image_description(self, image_bytes: bytes, image_ext: str) -> Optional[str]:
        """Get AI-generated description of an image.
        
        Args:
            image_bytes: Image data
            image_ext: Image format extension
            
        Returns:
            AI-generated description or None
        """
        ocr_client = self._get_ocr_client()
        if ocr_client is None:
            return None
        
        try:
            import tempfile
            
            with tempfile.NamedTemporaryFile(suffix=f".{image_ext}", delete=False) as f:
                f.write(image_bytes)
                temp_path = Path(f.name)
            
            try:
                # デフォルトプロンプト（ocr_client.py で定義）を使用
                success, description, error = ocr_client.image_to_markdown(temp_path)
                
                if success and description:
                    # 切り詰めなし - ベクトル検索のため全文を保持
                    return description.strip()
                    
            finally:
                if temp_path.exists():
                    temp_path.unlink()
                    
        except Exception as e:
            logger.warning(f"Failed to get image description: {e}")
        
        return None
