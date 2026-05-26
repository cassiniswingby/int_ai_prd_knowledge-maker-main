#!/usr/bin/env python3
"""
Common PDF processing utilities.
Handles OCR detection, text extraction, and password detection.
"""

import logging
import subprocess
from pathlib import Path
from typing import Tuple, Optional
import PyPDF2


logger = logging.getLogger(__name__)


def check_pdf_requires_ocr(pdf_path: Path, min_text_length: int = 100) -> bool:
    """
    Check if PDF requires OCR processing.
    
    Args:
        pdf_path: Path to PDF file
        min_text_length: Minimum text length to consider as non-OCR
        
    Returns:
        True if OCR is required, False otherwise
    """
    try:
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            
            # Check if PDF is encrypted
            if pdf_reader.is_encrypted:
                logger.info(f"PDF is encrypted: {pdf_path.name}")
                return False  # Can't process encrypted PDFs
            
            # Check first few pages for text
            pages_to_check = min(3, len(pdf_reader.pages))
            total_text = ""
            
            for page_num in range(pages_to_check):
                page = pdf_reader.pages[page_num]
                text = page.extract_text() or ""
                total_text += text
                
                # If we have enough text, it doesn't need OCR
                if len(total_text) >= min_text_length:
                    return False
            
            # If very little text found, needs OCR
            return len(total_text.strip()) < min_text_length
            
    except Exception as e:
        logger.warning(f"Error checking PDF for OCR: {e}")
        return True  # Assume OCR needed if we can't check


def extract_text_with_pdftotext(pdf_path: Path, encoding: str = 'utf-8') -> Tuple[bool, Optional[str], str]:
    """
    Extract text from PDF using pdftotext command.
    
    Args:
        pdf_path: Path to PDF file
        encoding: Text encoding to use
        
    Returns:
        Tuple of (success, text, message)
    """
    try:
        # Run pdftotext command
        result = subprocess.run(
            ['pdftotext', '-enc', encoding, str(pdf_path), '-'],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            text = result.stdout
            if text and len(text.strip()) > 0:
                return True, text, ""
            else:
                return False, None, "No text extracted from PDF"
        else:
            error_msg = f"pdftotext failed: {result.stderr}"
            logger.error(error_msg)
            return False, None, error_msg
            
    except subprocess.TimeoutExpired:
        return False, None, "pdftotext command timed out"
    except FileNotFoundError:
        return False, None, "pdftotext command not found. Please install poppler-utils."
    except Exception as e:
        return False, None, f"Error running pdftotext: {str(e)}"


def extract_text_with_pypdf2(pdf_path: Path) -> Tuple[bool, Optional[str], str]:
    """
    Extract text from PDF using PyPDF2 library.
    
    Args:
        pdf_path: Path to PDF file
        
    Returns:
        Tuple of (success, text, message)
    """
    try:
        text_parts = []
        
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            
            # Check if encrypted
            if pdf_reader.is_encrypted:
                return False, None, "PDF is password protected"
            
            # Extract text from all pages
            for page_num, page in enumerate(pdf_reader.pages, 1):
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(f"=== Page {page_num} ===")
                        text_parts.append(page_text)
                except Exception as e:
                    logger.warning(f"Failed to extract text from page {page_num}: {e}")
                    continue
        
        if text_parts:
            text = "\n\n".join(text_parts)
            return True, text, ""
        else:
            return False, None, "No text could be extracted from PDF"
            
    except Exception as e:
        error_msg = f"PyPDF2 extraction error: {str(e)}"
        logger.error(error_msg)
        return False, None, error_msg


def detect_pdf_password(pdf_path: Path) -> bool:
    """
    Check if PDF is password protected.
    
    Args:
        pdf_path: Path to PDF file
        
    Returns:
        True if password protected, False otherwise
    """
    try:
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            return pdf_reader.is_encrypted
    except Exception as e:
        logger.warning(f"Error checking PDF password: {e}")
        return False


def get_pdf_page_count(pdf_path: Path) -> Optional[int]:
    """
    Get the number of pages in a PDF.
    
    Args:
        pdf_path: Path to PDF file
        
    Returns:
        Number of pages or None if error
    """
    try:
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            return len(pdf_reader.pages)
    except Exception as e:
        logger.warning(f"Error getting PDF page count: {e}")
        return None


def is_scanned_pdf(pdf_path: Path) -> bool:
    """
    Heuristic check if PDF is likely a scanned document.
    
    Args:
        pdf_path: Path to PDF file
        
    Returns:
        True if likely scanned, False otherwise
    """
    # Check if it requires OCR (has very little extractable text)
    requires_ocr = check_pdf_requires_ocr(pdf_path, min_text_length=50)
    
    # Additionally check file size vs page count ratio
    # Scanned PDFs tend to be larger per page
    try:
        file_size = pdf_path.stat().st_size
        page_count = get_pdf_page_count(pdf_path)
        
        if page_count and page_count > 0:
            size_per_page = file_size / page_count
            # If average page size > 500KB, likely scanned
            if size_per_page > 500 * 1024:
                return True
                
    except Exception as e:
        logger.debug(f"Error checking if PDF is scanned: {e}")
    
    return requires_ocr