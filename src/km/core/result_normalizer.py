#!/usr/bin/env python3
"""Helpers for normalising converter results to a unified status tuple."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple


NormalizedResult = Tuple[str, Optional[str], str]

_VALID_STATUSES = {"success", "requires_ocr", "failed"}
_OCR_HINT_KEYWORDS = (
    "requires ocr",
    "ocr",
    "image",
    "画像",
    "スキャン",
    "scan",
)


def normalise_converter_result(
    file_path: Path,
    raw_result: Sequence[Any],
    logger: Optional[logging.Logger] = None,
) -> NormalizedResult:
    """Coerce a converter response into (status, text, message).

    The existing converters are in transition from bool-based success flags to
    string-based status codes. This helper absorbs the differences so that the
    downstream pipeline can rely on a single contract.
    """

    if len(raw_result) < 3:
        # Pad missing fields to keep downstream logic simple.
        padded = list(raw_result) + [None] * (3 - len(raw_result))
        raw_result = padded  # type: ignore[assignment]

    raw_status, raw_text, raw_message = raw_result[:3]

    status = _coerce_status(raw_status)
    text = raw_text if isinstance(raw_text, str) else None
    message = _coerce_message(raw_message)

    # Empty text with "success" typically means we actually need OCR.
    # Promote PPT/PPTX files to requires_ocr when empty (Known Issue fix)
    if status == "success" and (not text or not text.strip()):
        lower_message = message.lower()
        file_ext = file_path.suffix.lower()
        
        # PDF and PPT/PPTX files should go to OCR when empty
        if file_ext in {".pdf", ".ppt", ".pptx"} or any(
            hint in lower_message for hint in _OCR_HINT_KEYWORDS
        ):
            status = "requires_ocr"
            if not message:
                if file_ext in {".ppt", ".pptx"}:
                    message = "PowerPoint with empty text requires OCR"
                else:
                    message = "Image-based PDF detected during normalisation"
        else:
            status = "failed"
            if not message:
                message = "Converter returned empty text"

    if status == "requires_ocr" and not message:
        message = "Marked as requires_ocr"

    if status == "failed" and not message:
        message = "Conversion failed"

    # For non-success statuses we do not propagate partial text.
    if status != "success":
        text = None

    if logger and status not in _VALID_STATUSES:
        logger.warning(
            "Normalised converter result to '%s' for %s", status, file_path.name
        )

    return status, text, message


def _coerce_status(raw_status: Any) -> str:
    if isinstance(raw_status, bool):
        return "success" if raw_status else "failed"

    if isinstance(raw_status, str):
        lowered = raw_status.strip().lower()
        if lowered in _VALID_STATUSES:
            return lowered
        # Preserve unexpected statuses for logging, but treat as failure.
        return "failed"

    return "failed"


def _coerce_message(raw_message: Any) -> str:
    if raw_message is None:
        return ""
    return str(raw_message).strip()