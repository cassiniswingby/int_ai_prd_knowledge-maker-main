"""Template management for Knowledge Maker.

Templates are used to format transcribed markdown into structured documents.
"""

from .loader import load_template, get_default_template, get_template_prompt

__all__ = ["load_template", "get_default_template", "get_template_prompt"]

