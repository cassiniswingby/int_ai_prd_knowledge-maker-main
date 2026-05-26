"""Conversion result data structures for Stage 1 extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class ExtractedImage:
    """Represents an extracted image from a document."""
    
    data: bytes
    """Raw image bytes."""
    
    filename: str
    """Suggested filename (e.g., 'page_001_img_001.png')."""
    
    page_or_sheet: Optional[str] = None
    """Page number or sheet name where the image was found."""
    
    position: Optional[int] = None
    """Position/index within the page/sheet (for ordering)."""
    
    ai_description: Optional[str] = None
    """AI-generated description of the image content."""
    
    width: Optional[int] = None
    """Image width in pixels."""
    
    height: Optional[int] = None
    """Image height in pixels."""
    
    format: str = "png"
    """Image format (png, jpg, etc.)."""


@dataclass
class ConversionResult:
    """Result of a document conversion operation.
    
    This replaces the previous Tuple[bool, Optional[str], str] return type
    to support additional data like extracted images.
    """
    
    success: bool
    """Whether the conversion was successful."""
    
    text: Optional[str] = None
    """Extracted text content (Markdown format preferred)."""
    
    message: str = ""
    """Status message or error description."""
    
    images: List[ExtractedImage] = field(default_factory=list)
    """List of extracted images."""
    
    original_path: Optional[Path] = None
    """Path to the original file."""
    
    file_format: Optional[str] = None
    """Original file format (e.g., 'pdf', 'xlsx')."""
    
    requires_ocr: bool = False
    """Whether the document requires OCR for full extraction."""
    
    @classmethod
    def from_tuple(
        cls,
        result: tuple,
        original_path: Optional[Path] = None
    ) -> "ConversionResult":
        """Create ConversionResult from legacy tuple format.
        
        Supports both old (bool, str, str) and new formats.
        
        Args:
            result: Conversion result tuple
            original_path: Original file path
            
        Returns:
            ConversionResult instance
        """
        if isinstance(result, ConversionResult):
            return result
        
        if len(result) == 3:
            success, text, message = result
            return cls(
                success=success,
                text=text,
                message=message,
                original_path=original_path,
            )
        elif len(result) == 4:
            success, text, message, images = result
            return cls(
                success=success,
                text=text,
                message=message,
                images=images or [],
                original_path=original_path,
            )
        else:
            raise ValueError(f"Unexpected result format: {result}")
    
    def to_tuple(self) -> tuple:
        """Convert to legacy tuple format for backward compatibility.
        
        Returns:
            (success, text, message) tuple
        """
        return (self.success, self.text, self.message)
    
    @property
    def status(self) -> str:
        """Get status string for compatibility."""
        if self.requires_ocr:
            return "requires_ocr"
        elif self.success:
            return "success"
        else:
            return "failed"

