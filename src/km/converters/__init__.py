"""Unified converters for Knowledge Maker.

This module automatically registers all converters when imported,
making them available through ConverterFactory.
"""

from .pdf_converter import PDFConverter
from .ppt_converter import PPTConverter
from .docx_converter import DOCXConverter
from .xlsx_converter import XLSXConverter
from .doc_converter import DOCConverter
from .xls_converter import XLSConverter
from .csv_converter import CSVConverter
from ..core.factory import ConverterFactory

__all__ = [
    'PDFConverter',
    'PPTConverter',
    'DOCXConverter',
    'XLSXConverter',
    'DOCConverter',
    'XLSConverter',
    'CSVConverter',
]

# Auto-register all converters
ConverterFactory.register_converter('.pdf', PDFConverter)
ConverterFactory.register_converter('.xlsx', XLSXConverter)
ConverterFactory.register_converter('.xls', XLSConverter)
ConverterFactory.register_converter('.csv', CSVConverter)
ConverterFactory.register_converter('.docx', DOCXConverter)
ConverterFactory.register_converter('.doc', DOCConverter)
ConverterFactory.register_converter('.ppt', PPTConverter)
ConverterFactory.register_converter('.pptx', PPTConverter)
