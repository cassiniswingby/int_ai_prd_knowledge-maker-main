"""Stage 1 extraction pipeline implementation."""

from __future__ import annotations

import gc
import json
import logging
import os
import signal
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

import psutil

from src.km.core import ConverterFactory, normalise_converter_result, ConversionResult, ExtractedImage

from .config import Stage1Config


class Stage1Extractor:
    """Run Stage 1 extraction pipeline."""

    _OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    _FACTORY_PRIMED = False

    def __init__(self, config: Stage1Config, *, logger: Optional[logging.Logger] = None) -> None:
        self.config = config.prepare()
        self.base_dir = self.config.base_dir
        self.output_dir = self.config.output_dir
        self.manifest_path = self.config.manifest_path
        self.progress_path = self.config.progress_path
        self.results_path = self.config.results_path
        self.retry_failed = self.config.retry_failed
        self.paths_file = self.config.paths_file
        self.memory_threshold_mb = self.config.memory_threshold_mb
        self.max_memory_mb = self.config.max_memory_mb

        self.logger = logger or self._build_logger()
        self.logger.info("Stage 1 extractor initialised: base_dir=%s", self.base_dir)

        self._prime_converter_factory()

        self.progress = self.load_progress()
        self.results = self._load_results()
        self._reconcile_statistics()

        self.original_failed_set = {
            item.get("file")
            for item in self.results.get("failed_files", [])
            if item.get("file")
        }
        self.failed_targets: List[Path] = []
        if self.retry_failed:
            for rel in self.original_failed_set:
                candidate = self.base_dir / rel
                if candidate.exists():
                    self.failed_targets.append(candidate)

        signal.signal(signal.SIGINT, self.handle_interrupt)
        self.interrupted = False

    def _build_logger(self) -> logging.Logger:
        logger = logging.getLogger("km.pipeline.stage1")
        if not logger.handlers:
            logger.setLevel(logging.INFO)
            formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            file_handler = logging.FileHandler(self.config.log_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            file_handler.setLevel(logging.INFO)
            stream_handler = logging.StreamHandler()
            stream_handler.setFormatter(formatter)
            stream_handler.setLevel(logging.INFO)
            logger.addHandler(file_handler)
            logger.addHandler(stream_handler)
        logger.propagate = False
        return logger

    def _prime_converter_factory(self) -> None:
        if Stage1Extractor._FACTORY_PRIMED:
            return

        try:
            from src.km.converters import (
                PDFConverter, PPTConverter,
                DOCXConverter, XLSXConverter,
                DOCConverter, XLSConverter
            )

            # Register all converters from km package
            ConverterFactory.register_converter(".pdf", PDFConverter, override=True)
            ConverterFactory.register_converter(".ppt", PPTConverter, override=True)
            ConverterFactory.register_converter(".pptx", PPTConverter, override=True)
            ConverterFactory.register_converter(".docx", DOCXConverter, override=True)
            ConverterFactory.register_converter(".xlsx", XLSXConverter, override=True)
            ConverterFactory.register_converter(".xlsm", XLSXConverter, override=True)
            ConverterFactory.register_converter(".xltx", XLSXConverter, override=True)
            ConverterFactory.register_converter(".xltm", XLSXConverter, override=True)
            ConverterFactory.register_converter(".doc", DOCConverter, override=True)
            ConverterFactory.register_converter(".xls", XLSConverter, override=True)
        except Exception as exc:  # pragma: no cover
            self.logger.debug("Skipping km converter registration: %s", exc)

        try:
            from converters.base_converter import ConverterFactory as LegacyFactory  # type: ignore
        except ImportError:
            Stage1Extractor._FACTORY_PRIMED = True
            return

        legacy_map = getattr(LegacyFactory, "_converters", {})
        if isinstance(legacy_map, dict):
            for ext, converter_cls in legacy_map.items():
                try:
                    ConverterFactory.register_converter(ext, converter_cls, override=True)
                except Exception as exc:  # pragma: no cover
                    self.logger.debug("Unable to register legacy converter for %s: %s", ext, exc)

        Stage1Extractor._FACTORY_PRIMED = True

    def load_progress(self) -> Dict[str, object]:
        if self.progress_path.exists():
            try:
                with open(self.progress_path, "r", encoding="utf-8") as fh:
                    progress = json.load(fh)
                processed = progress.get("processed_files", [])
                if isinstance(processed, list):
                    self.logger.info(
                        "Resuming from previous run: %d files already processed",
                        len(processed),
                    )
                else:
                    progress["processed_files"] = []
                progress.setdefault("last_file", None)
                progress.setdefault("timestamp", None)
                return progress
            except (OSError, json.JSONDecodeError) as exc:
                self.logger.warning(
                    "Failed to load progress file %s: %s", self.progress_path, exc
                )
        return {"processed_files": [], "last_file": None, "timestamp": None}

    def save_progress(self) -> None:
        self.progress["timestamp"] = datetime.now().isoformat()
        with open(self.progress_path, "w", encoding="utf-8") as fh:
            json.dump(self.progress, fh, ensure_ascii=False, indent=2)

    def handle_interrupt(self, signum, frame) -> None:  # pragma: no cover
        self.logger.warning("Interrupt received. Saving progress before exit.")
        self.interrupted = True
        self.save_progress()
        self.save_results()
        sys.exit(0)

    def _fresh_results(self) -> Dict[str, object]:
        return {
            "metadata": {
                "start_time": datetime.now().isoformat(),
                "base_dir": str(self.base_dir),
                "output_dir": str(self.output_dir),
            },
            "statistics": {
                "total": 0,
                "processed": 0,
                "success": 0,
                "requires_ocr": 0,
                "failed": 0,
                "skipped": 0,
            },
            "memory_stats": {
                "max_memory_mb": 0,
                "avg_memory_mb": [],
                "high_memory_files": [],
            },
            "ocr_manifest": [],
            "failed_files": [],
            "processing_times": [],
        }

    def _load_results(self) -> Dict[str, object]:
        if self.results_path.exists():
            try:
                with open(self.results_path, "r", encoding="utf-8") as fh:
                    results = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                self.logger.warning(
                    "Failed to load results file %s: %s", self.results_path, exc
                )
                results = self._fresh_results()
        else:
            results = self._fresh_results()

        memory_stats = results.setdefault("memory_stats", {})
        avg = memory_stats.get("avg_memory_mb", [])
        if isinstance(avg, (int, float)):
            avg = [float(avg)]
        memory_stats["avg_memory_mb"] = list(avg)
        memory_stats.setdefault("max_memory_mb", 0)
        memory_stats.setdefault("high_memory_files", [])

        stats = results.setdefault("statistics", {})
        stats.setdefault("total", 0)
        stats.setdefault("processed", 0)
        stats.setdefault("success", 0)
        stats.setdefault("requires_ocr", 0)
        stats.setdefault("failed", 0)
        stats.setdefault("skipped", 0)

        results.setdefault("ocr_manifest", [])
        results.setdefault("failed_files", [])
        results.setdefault("processing_times", [])

        metadata = results.setdefault("metadata", {})
        metadata.setdefault("start_time", datetime.now().isoformat())
        metadata["base_dir"] = str(self.base_dir)
        metadata["output_dir"] = str(self.output_dir)

        return results

    def collect_target_files(self) -> List[Path]:
        self.logger.info("Collecting target files from %s", self.base_dir)

        if self.paths_file:
            try:
                content = self.paths_file.read_text(encoding="utf-8")
            except OSError as exc:
                self.logger.error("Failed to read paths file %s: %s", self.paths_file, exc)
                content = ""
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            subset = []
            for rel in lines:
                candidate = (self.base_dir / rel).resolve()
                if candidate.exists() and not candidate.name.startswith("~$"):
                    subset.append(candidate)
            unique_subset = sorted({p for p in subset})
            self.results["statistics"]["total"] = len(unique_subset)
            self.logger.info("Subset mode enabled. Found %d files.", len(unique_subset))
            return unique_subset

        if self.retry_failed:
            unique_failed = sorted({p.resolve() for p in self.failed_targets})
            self.results["statistics"]["total"] = len(unique_failed)
            self.logger.info(
                "Retry mode enabled. Found %d failed files to reprocess.", len(unique_failed)
            )
            return unique_failed

        target_extensions = (
            ".pdf",
            ".xlsx",
            ".xls",
            ".xlsm",
            ".docx",
            ".doc",
            ".pptx",
            ".ppt",
        )
        all_files: List[Path] = []
        for ext in target_extensions:
            for candidate in self.base_dir.rglob(f"*{ext}"):
                if candidate.name.startswith("~$"):
                    continue
                all_files.append(candidate.resolve())
        all_files = sorted({p for p in all_files})
        self.results["statistics"]["total"] = len(all_files)
        self.logger.info("Found %d target files", len(all_files))
        return all_files

    def check_memory(self) -> Dict[str, float]:
        process = psutil.Process()
        memory_mb = process.memory_info().rss / (1024 * 1024)
        if memory_mb > self.max_memory_mb:
            raise MemoryError(
                f"Memory usage ({memory_mb:.1f}MB) exceeds limit ({self.max_memory_mb}MB)"
            )
        return {"current_mb": round(memory_mb, 2), "percent": psutil.virtual_memory().percent}

    def process_file(self, file_path: Path) -> Dict[str, object]:
        processed = self.progress.get("processed_files", [])
        if not isinstance(processed, list):
            processed = self.progress["processed_files"] = []
        if not self.retry_failed and str(file_path) in processed:
            return {"status": "skipped", "reason": "already_processed"}

        mem_before = self.check_memory()
        if mem_before["current_mb"] > 2000:
            self.logger.info(
                "Memory usage %.0fMB > 2GB before processing. Running garbage collection...",
                mem_before["current_mb"],
            )
            gc.collect()
            mem_before = self.check_memory()
            self.logger.info("Memory after GC: %.0fMB", mem_before["current_mb"])

        try:
            rel_str = str(file_path.resolve().relative_to(self.base_dir))
        except ValueError:
            rel_str = file_path.resolve().name

        try:
            size_mb = round(file_path.stat().st_size / (1024 * 1024), 2)
        except OSError:
            size_mb = 0

        result: Dict[str, object] = {"file": rel_str, "size_mb": size_mb}

        classification = self._classify_openxml_container(file_path)
        if classification:
            message = self._build_openxml_classification_message(classification)
            result.update({"status": "failed", "message": message, "issue": classification, "time_sec": 0})
            self.logger.warning("[%s] %s", file_path.name, message)
            self._record_failure(result["file"], message, classification)
            processed.append(str(file_path))
            self.progress["last_file"] = str(file_path)
            self.results["processing_times"].append(0)
            self.results["memory_stats"]["avg_memory_mb"].append(0)
            return result

        start_time = time.time()
        status = "error"
        text: Optional[str] = None
        message = ""
        images: List[ExtractedImage] = []

        try:
            converter = ConverterFactory.get_converter(file_path)
            if not converter:
                result["status"] = "no_converter"
                result["error"] = f"No converter for {file_path.suffix}"
                return result
            raw_result = converter.convert(file_path)
            
            # Handle both legacy tuple and new ConversionResult format
            if isinstance(raw_result, ConversionResult):
                conv_result = raw_result
                status = conv_result.status
                text = conv_result.text
                message = conv_result.message
                images = conv_result.images or []
            else:
                status, text, message = normalise_converter_result(
                    file_path,
                    raw_result,
                    self.logger,
                )
                images = []
            
            result["status"] = status
            result["message"] = message
            mem_after_snapshot = self.check_memory()
            memory_used = mem_after_snapshot["current_mb"] - mem_before["current_mb"]
            result["memory_mb"] = round(memory_used, 2)
            if memory_used > self.memory_threshold_mb:
                self.results["memory_stats"]["high_memory_files"].append(
                    {"file": result["file"], "memory_mb": memory_used}
                )
        except MemoryError:
            raise
        except Exception as exc:
            message = str(exc)
            result["status"] = "error"
            result["error"] = message
            self._record_failure(result["file"], message)
        else:
            if status == "success":
                result["chars"] = len(text) if text else 0
                result["images"] = len(images)
                if text:
                    self.save_document(file_path, text, images, rel_str)
                self._remove_failure(result["file"])
            elif status == "requires_ocr":
                self.results["ocr_manifest"].append(
                    {
                        "path": str(file_path),
                        "size_mb": result["size_mb"],
                        "message": message,
                        "detected_chars": len(text) if text else 0,
                    }
                )
                self._remove_failure(result["file"])
            else:
                self._record_failure(result["file"], message)

        duration = round(time.time() - start_time, 2)
        result["time_sec"] = duration
        self.results["processing_times"].append(duration)

        processed.append(str(file_path))
        self.progress["last_file"] = str(file_path)

        memory_value = float(result.get("memory_mb", 0))
        self.results["memory_stats"]["avg_memory_mb"].append(memory_value)
        if memory_value > self.results["memory_stats"]["max_memory_mb"]:
            self.results["memory_stats"]["max_memory_mb"] = memory_value

        mem_after = self.check_memory()
        if mem_after["current_mb"] > 2000:
            self.logger.info(
                "Memory usage %.0fMB > 2GB after processing. Running garbage collection...",
                mem_after["current_mb"],
            )
            gc.collect()
            mem_after_gc = self.check_memory()
            self.logger.info(
                "Memory after GC: %.0fMB (freed %.0fMB)",
                mem_after_gc["current_mb"],
                mem_after["current_mb"] - mem_after_gc["current_mb"],
            )

        return result

    def save_text(self, file_path: Path, text: str) -> None:
        """Legacy method for backward compatibility. Use save_document instead."""
        self.save_document(file_path, text, [])

    def save_document(
        self,
        file_path: Path,
        text: str,
        images: List[ExtractedImage],
        original_relative_path: Optional[str] = None,
    ) -> Path:
        """Save extracted document with images to the new folder structure.
        
        Output structure:
            {output_dir}/{filename}/
                ├── content.md
                └── images/
                    ├── img_001.png
                    └── ...
        
        Args:
            file_path: Original file path
            text: Extracted text content
            images: List of extracted images
            original_relative_path: Relative path from base_dir (for metadata)
            
        Returns:
            Path to the output directory
        """
        # Determine output folder name (based on filename, with deduplication)
        base_name = file_path.stem
        output_folder = self._get_unique_folder_name(base_name)
        output_folder.mkdir(parents=True, exist_ok=True)
        
        # Determine original relative path for metadata
        if original_relative_path is None:
            try:
                original_relative_path = str(file_path.resolve().relative_to(self.base_dir))
            except ValueError:
                original_relative_path = file_path.name
        
        # Save images first (so we can reference them in content.md)
        saved_images: List[Dict[str, str]] = []
        if images:
            images_dir = output_folder / "images"
            images_dir.mkdir(exist_ok=True)
            
            for img in images:
                img_path = images_dir / img.filename
                with open(img_path, "wb") as f:
                    f.write(img.data)
                saved_images.append({
                    "filename": img.filename,
                    "page_or_sheet": img.page_or_sheet,
                    "ai_description": img.ai_description,
                })
        
        # Generate Markdown content with metadata and images
        markdown_content = self._generate_markdown_content(
            text=text,
            original_path=original_relative_path,
            file_format=file_path.suffix.lstrip(".").lower(),
            saved_images=saved_images,
        )
        
        # Save content.md
        content_path = output_folder / "content.md"
        with open(content_path, "w", encoding="utf-8") as fh:
            fh.write(markdown_content)
        
        self.logger.debug(f"Saved document to {output_folder} with {len(saved_images)} images")
        return output_folder

    def _get_unique_folder_name(self, base_name: str) -> Path:
        """Get a unique folder name, adding suffix if needed.
        
        Args:
            base_name: Base folder name (filename without extension)
            
        Returns:
            Unique folder path
        """
        # Track used folder names in this run
        if not hasattr(self, "_used_folder_names"):
            self._used_folder_names: Set[str] = set()
            # Scan existing folders
            if self.output_dir.exists():
                for item in self.output_dir.iterdir():
                    if item.is_dir():
                        self._used_folder_names.add(item.name)
        
        # Find unique name
        folder_name = base_name
        counter = 2
        while folder_name in self._used_folder_names:
            folder_name = f"{base_name}_{counter}"
            counter += 1
        
        self._used_folder_names.add(folder_name)
        return self.output_dir / folder_name

    def _generate_markdown_content(
        self,
        text: str,
        original_path: str,
        file_format: str,
        saved_images: List[Dict[str, str]],
    ) -> str:
        """Generate Markdown content with metadata and images.
        
        Args:
            text: Extracted text content
            original_path: Original file path (relative)
            file_format: File format (e.g., 'pdf', 'xlsx')
            saved_images: List of saved image info
            
        Returns:
            Markdown formatted content
        """
        lines = []
        
        # Add metadata header
        lines.append(f"# {Path(original_path).stem}")
        lines.append("")
        lines.append(f"**元ファイル:** {original_path}  ")
        lines.append(f"**ファイル形式:** {file_format}  ")
        lines.append(f"**抽出日時:** {datetime.now().isoformat()}  ")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # Add main text content
        lines.append(text)
        
        # Add images section if any
        if saved_images:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("## 📷 抽出画像")
            lines.append("")
            
            for img_info in saved_images:
                filename = img_info["filename"]
                page_or_sheet = img_info.get("page_or_sheet", "")
                ai_description = img_info.get("ai_description", "")
                
                # Image heading
                if page_or_sheet:
                    lines.append(f"### {filename} ({page_or_sheet})")
                else:
                    lines.append(f"### {filename}")
                lines.append("")
                
                # Image embed
                lines.append(f"![{filename}](images/{filename})")
                lines.append("")
                
                # AI description if available
                if ai_description:
                    lines.append(f"> **AI説明:** {ai_description}")
                    lines.append("")
        
        return "\n".join(lines)

    def _record_failure(self, relative_path: str, message: str, category: Optional[str] = None) -> None:
        failures = self.results.setdefault("failed_files", [])
        failures = [item for item in failures if item.get("file") != relative_path]
        entry = {"file": relative_path, "error": message}
        if category:
            entry["category"] = category
        failures.append(entry)
        self.results["failed_files"] = failures

    def _remove_failure(self, relative_path: str) -> None:
        failures = self.results.setdefault("failed_files", [])
        if failures:
            self.results["failed_files"] = [
                item for item in failures if item.get("file") != relative_path
            ]

    def _classify_openxml_container(self, file_path: Path) -> Optional[str]:
        openxml_exts = {".xlsx", ".xlsm", ".xltx", ".xltm"}
        suffix = file_path.suffix.lower()
        if suffix not in openxml_exts:
            return None

        try:
            if zipfile.is_zipfile(file_path):
                return None
        except Exception:
            return "openxml_invalid_container"

        try:
            with open(file_path, "rb") as fh:
                header = fh.read(len(self._OLE_MAGIC))
                if header != self._OLE_MAGIC:
                    return "openxml_invalid_container"
                while True:
                    chunk = fh.read(8192)
                    if not chunk:
                        break
                    if b"EncryptedPackage" in chunk or b"Encrypted Summary" in chunk:
                        return "openxml_password_protected"
        except OSError:
            return "openxml_unreadable"

        return "openxml_legacy_ole"

    @staticmethod
    def _build_openxml_classification_message(classification: str) -> str:
        mapping = {
            "openxml_password_protected": "Password-protected workbook detected (EncryptedPackage present). Please remove the password and retry.",
            "openxml_legacy_ole": "Workbook saved as legacy OLE container with .xlsx extension. Re-save as modern .xlsx before rerunning.",
            "openxml_invalid_container": "Invalid OpenXML container (not a ZIP archive). Re-save the workbook before rerunning.",
            "openxml_unreadable": "Failed to inspect workbook contents (I/O error). Check file integrity before rerunning.",
        }
        return mapping.get(
            classification,
            "Unable to process workbook due to unsupported container format.",
        )

    def _reconcile_statistics(self) -> None:
        stats = self.results.setdefault("statistics", {})
        stats.setdefault("total", 0)
        stats.setdefault("processed", 0)
        stats.setdefault("success", 0)
        stats.setdefault("requires_ocr", 0)
        stats.setdefault("failed", 0)
        stats.setdefault("skipped", 0)

        processed_files = self.progress.get("processed_files", [])
        unique_processed = len(processed_files) if isinstance(processed_files, list) else 0
        if unique_processed:
            stats["processed"] = unique_processed
        accounted = stats.get("success", 0) + stats.get("requires_ocr", 0) + stats.get("failed", 0)
        if stats["processed"] < accounted:
            stats["processed"] = accounted
        stats["skipped"] = max(0, stats["processed"] - accounted)

    def save_results(self) -> None:
        self._reconcile_statistics()

        memory_stats = self.results.get("memory_stats", {})
        avg_entries = memory_stats.get("avg_memory_mb", [])
        if avg_entries:
            memory_stats["avg_memory_calculated"] = round(
                sum(avg_entries) / len(avg_entries),
                2,
            )
        else:
            memory_stats["avg_memory_calculated"] = 0

        if self.results["processing_times"]:
            self.results["avg_processing_time"] = round(
                sum(self.results["processing_times"]) / len(self.results["processing_times"]),
                2,
            )

        metadata = self.results.setdefault("metadata", {})
        metadata["last_run"] = datetime.now().isoformat()

        with open(self.manifest_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "timestamp": datetime.now().isoformat(),
                    "files": self.results["ocr_manifest"],
                    "total": len(self.results["ocr_manifest"]),
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )

        with open(self.results_path, "w", encoding="utf-8") as fh:
            json.dump(self.results, fh, ensure_ascii=False, indent=2)

    def run(self) -> int:
        self.logger.info("=" * 60)
        self.logger.info("PRODUCTION BATCH CONVERTER")
        self.logger.info("=" * 60)

        all_files = self.collect_target_files()
        total_files = len(all_files)
        if total_files == 0:
            self.logger.info("No files to process.")
            self.save_progress()
            self.save_results()
            self.print_summary()
            return 0

        for index, file_path in enumerate(all_files, 1):
            if self.interrupted:
                break

            if index % 10 == 0:
                pct = (index / total_files) * 100
                self.logger.info("Progress: %d/%d (%.1f%%)", index, total_files, pct)

            try:
                result = self.process_file(file_path)
            except MemoryError:
                self.logger.error("Memory limit exceeded. Saving progress and exiting.")
                self.save_progress()
                self.save_results()
                return 1

            status = result.get("status")
            rel_path = result.get("file")

            if status == "skipped":
                if index % 50 == 0:
                    self.save_results()
                continue

            if not self.retry_failed:
                self.results["statistics"]["processed"] += 1

            if status == "success":
                if self.retry_failed:
                    self.results["statistics"]["success"] += 1
                    if rel_path in self.original_failed_set:
                        self.results["statistics"]["failed"] = max(
                            0,
                            self.results["statistics"]["failed"] - 1,
                        )
                    if rel_path:
                        self._remove_failure(rel_path)
                else:
                    self.results["statistics"]["success"] += 1
                    if rel_path:
                        self._remove_failure(rel_path)
                self.logger.info(
                    "[%d] ✅ %s - %s chars",
                    index,
                    file_path.name,
                    f"{result.get('chars', 0):,}",
                )
            elif status == "requires_ocr":
                if self.retry_failed:
                    if rel_path in self.original_failed_set:
                        self.results["statistics"]["failed"] = max(
                            0,
                            self.results["statistics"]["failed"] - 1,
                        )
                    self.results["statistics"]["requires_ocr"] += 1
                    if rel_path:
                        self._remove_failure(rel_path)
                else:
                    self.results["statistics"]["requires_ocr"] += 1
                self.logger.info("[%d] 🔍 %s - Requires OCR", index, file_path.name)
            else:
                if not self.retry_failed:
                    self.results["statistics"]["failed"] += 1
                self.logger.error(
                    "[%d] ❌ %s - %s",
                    index,
                    file_path.name,
                    result.get("error") or result.get("message", "Failed"),
                )

            if index % 50 == 0:
                self.save_progress()
                self.save_results()
                self.logger.info("Progress checkpoint saved.")

        self.save_progress()
        self.save_results()
        self.print_summary()
        return 0

    def print_summary(self) -> None:
        stats = self.results["statistics"]
        total = stats.get("total", 0)
        denom = total if total else 1
        success_pct = (stats.get("success", 0) / denom * 100) if total else 0.0
        ocr_pct = (stats.get("requires_ocr", 0) / denom * 100) if total else 0.0
        failed_pct = (stats.get("failed", 0) / denom * 100) if total else 0.0

        print("\n" + "=" * 60)
        print("PRODUCTION BATCH COMPLETE")
        print("=" * 60)

        print("\n📊 Final Statistics:")
        print(f"  Total files: {total}")
        print(f"  Processed: {stats.get('processed', 0)}")
        print(f"  Success: {stats.get('success', 0)} ({success_pct:.1f}%)")
        print(f"  Requires OCR: {stats.get('requires_ocr', 0)} ({ocr_pct:.1f}%)")
        print(f"  Failed: {stats.get('failed', 0)} ({failed_pct:.1f}%)")
        print(f"  Skipped: {stats.get('skipped', 0)}")

        memory_stats = self.results.get("memory_stats", {})
        per_file_mem = memory_stats.get("avg_memory_mb", []) or []
        if per_file_mem:
            avg_memory = (
                sum(per_file_mem) / len(per_file_mem)
                if isinstance(per_file_mem, list)
                else memory_stats.get("avg_memory_calculated", 0)
            )
        else:
            avg_memory = memory_stats.get("avg_memory_calculated", 0)

        print("\n💾 Memory Statistics:")
        print(f"  Max memory: {memory_stats.get('max_memory_mb', 0):.1f}MB")
        print(f"  Avg memory: {avg_memory:.1f}MB")
        print(f"  High memory files: {len(memory_stats.get('high_memory_files', []))}")

        print("\n⏱️ Performance:")
        if self.results.get("avg_processing_time"):
            print(f"  Avg time/file: {self.results['avg_processing_time']:.2f}s")
            est_total = self.results["avg_processing_time"] * total / 60 if total else 0
            print(f"  Estimated total time: {est_total:.1f} minutes")

        print("\n📁 Output:")
        print(f"  Text files: {self.output_dir}")
        print(f"  OCR manifest: {self.manifest_path}")
        print(f"  Results: {self.results_path}")

        if self.results["failed_files"]:
            print(f"\n⚠️ Failed files: {len(self.results['failed_files'])}")
            for failure in self.results["failed_files"][:5]:
                print(f"  - {failure['file']}: {failure['error'][:50]}")

        print("\n" + "=" * 60)
