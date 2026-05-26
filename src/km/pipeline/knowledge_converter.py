"""Stage 1: Knowledge Converter - Initial Markdown creation with image extraction.

This module handles the first stage of the knowledge conversion pipeline:
1. Copy original file to 01_input/
2. Extract text and convert to Markdown in 02_transcribed_markdown/
3. Extract images to 04_images/
"""

from __future__ import annotations

import gc
import json
import logging
import os
import shutil
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import psutil

from ..core import ConverterFactory, ConversionResult, ExtractedImage
from .knowledge_config import (
    KnowledgeConfig,
    DocumentFolder,
    DocumentFolderManager,
    FOLDER_REPORTS,
    FILE_CONTENT_MD,
)


logger = logging.getLogger(__name__)


class KnowledgeConverter:
    """Stage 1: Convert documents to initial Markdown with images.
    
    Output Structure:
        pre-knowledge/{document_name}/
        ├── 01_input/{original_file}
        ├── 02_transcribed_markdown/transcribed.md
        └── 04_images/{extracted_images}
    """
    
    _FACTORY_PRIMED = False
    
    def __init__(
        self,
        config: KnowledgeConfig,
        *,
        logger: Optional[logging.Logger] = None,
        parallel_workers: int = 1,
    ) -> None:
        """Initialize the Knowledge Converter.
        
        Args:
            config: Configuration for the conversion
            logger: Optional logger instance
            parallel_workers: ファイル間並列処理のワーカー数 (1=逐次)
        """
        self.config = config.prepare()
        self.logger = logger or self._build_logger()
        
        self._prime_converter_factory()
        
        self.folder_manager = DocumentFolderManager(self.config.output_dir)
        self.progress = self._load_progress()
        self.results = self._fresh_results()
        self._state_lock = threading.Lock()
        
        # 並列処理設定（環境変数優先）
        env_workers = os.getenv("CONVERT_PARALLEL_WORKERS")
        self.parallel_workers: int = int(env_workers) if env_workers else parallel_workers

        # Interrupt handling
        signal.signal(signal.SIGINT, self._handle_interrupt)
        self.interrupted = False
        
        self.logger.info(
            f"KnowledgeConverter initialized: input={self.config.input_dir}, "
            f"output={self.config.output_dir}, parallel_workers={self.parallel_workers}"
        )
    
    def _build_logger(self) -> logging.Logger:
        """Build a logger for the converter."""
        log = logging.getLogger("km.knowledge_converter")
        if not log.handlers:
            log.setLevel(logging.INFO)
            formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            
            # Console handler
            console = logging.StreamHandler()
            console.setFormatter(formatter)
            console.setLevel(logging.INFO)
            log.addHandler(console)
            
            # File handler
            log_dir = self.config.output_dir / FOLDER_REPORTS
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "converter.log"
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            file_handler.setLevel(logging.DEBUG)
            log.addHandler(file_handler)
        
        log.propagate = False
        return log
    
    def _prime_converter_factory(self) -> None:
        """Register all converters."""
        if KnowledgeConverter._FACTORY_PRIMED:
            return
        
        try:
            from ..converters import (
                PDFConverter, PPTConverter,
                DOCXConverter, XLSXConverter,
                DOCConverter, XLSConverter,
                CSVConverter,
            )
            
            ConverterFactory.register_converter(".pdf", PDFConverter, override=True)
            ConverterFactory.register_converter(".ppt", PPTConverter, override=True)
            ConverterFactory.register_converter(".pptx", PPTConverter, override=True)
            ConverterFactory.register_converter(".odp", PPTConverter, override=True)
            ConverterFactory.register_converter(".docx", DOCXConverter, override=True)
            ConverterFactory.register_converter(".xlsx", XLSXConverter, override=True)
            ConverterFactory.register_converter(".xlsm", XLSXConverter, override=True)
            ConverterFactory.register_converter(".xltx", XLSXConverter, override=True)
            ConverterFactory.register_converter(".xltm", XLSXConverter, override=True)
            ConverterFactory.register_converter(".doc", DOCConverter, override=True)
            ConverterFactory.register_converter(".xls", XLSConverter, override=True)
            ConverterFactory.register_converter(".csv", CSVConverter, override=True)
        except Exception as e:
            self.logger.warning(f"Failed to register some converters: {e}")
        
        KnowledgeConverter._FACTORY_PRIMED = True
    
    def _load_progress(self) -> Dict:
        """Load progress from previous runs."""
        if self.config.progress_path and self.config.progress_path.exists():
            try:
                with open(self.config.progress_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                self.logger.warning(f"Failed to load progress: {e}")
        
        return {
            "processed_files": [],
            "last_file": None,
            "timestamp": None,
        }
    
    def _save_progress(self) -> None:
        """Save current progress."""
        self.progress["timestamp"] = datetime.now().isoformat()
        
        if self.config.progress_path:
            self.config.progress_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config.progress_path, "w", encoding="utf-8") as f:
                json.dump(self.progress, f, ensure_ascii=False, indent=2)
    
    def _fresh_results(self) -> Dict:
        """Create fresh results structure."""
        return {
            "metadata": {
                "start_time": datetime.now().isoformat(),
                "input_dir": str(self.config.input_dir),
                "output_dir": str(self.config.output_dir),
            },
            "statistics": {
                "total": 0,
                "processed": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
            },
            "documents": [],
            "failed_files": [],
        }
    
    def _save_results(self) -> None:
        """Save results."""
        self.results["metadata"]["end_time"] = datetime.now().isoformat()
        
        if self.config.results_path:
            self.config.results_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config.results_path, "w", encoding="utf-8") as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
    
    def _handle_interrupt(self, signum, frame) -> None:
        """Handle interrupt signal."""
        self.logger.warning("Interrupt received. Saving progress...")
        self.interrupted = True
        self._save_progress()
        self._save_results()
        sys.exit(0)
    
    def _check_memory(self) -> float:
        """Check current memory usage."""
        process = psutil.Process()
        memory_mb = process.memory_info().rss / (1024 * 1024)
        
        if memory_mb > self.config.max_memory_mb:
            raise MemoryError(
                f"Memory usage ({memory_mb:.1f}MB) exceeds limit "
                f"({self.config.max_memory_mb}MB)"
            )
        
        return memory_mb
    
    def collect_files(self) -> List[Path]:
        """Collect target files from input directory.
        
        Returns:
            List of file paths to process
        """
        target_extensions = {
            ".pdf", ".xlsx", ".xls", ".xlsm", ".xltx", ".xltm",
            ".docx", ".doc", ".pptx", ".ppt", ".odp", ".csv",
        }
        
        files = []
        for item in self.config.input_dir.rglob("*"):
            if item.is_file() and not item.name.startswith("~$"):
                if item.suffix.lower() in target_extensions:
                    files.append(item.resolve())
        
        # Remove duplicates and sort
        files = sorted(set(files))
        self.logger.info(f"Found {len(files)} files to process")
        
        return files
    
    def convert_file(self, file_path: Path) -> Dict:
        """Convert a single file.
        
        Args:
            file_path: Path to the source file
            
        Returns:
            Result dictionary
        """
        file_path = Path(file_path).resolve()
        
        # Check if already processed
        processed = self.progress.get("processed_files", [])
        if str(file_path) in processed:
            return {"status": "skipped", "reason": "already_processed"}
        
        # Check memory
        mem_before = self._check_memory()
        if mem_before > 2000:
            gc.collect()
        
        result = {
            "file": str(file_path),
            "filename": file_path.name,
            "status": "pending",
        }
        
        start_time = time.time()
        
        try:
            # Get converter
            converter = ConverterFactory.get_converter(file_path)
            if not converter:
                result["status"] = "failed"
                result["error"] = f"No converter for {file_path.suffix}"
                return result
            
            # Convert file
            self.logger.info(f"Converting: {file_path.name}")
            conv_result = converter.convert(file_path)
            
            # Handle result (support both ConversionResult and tuple)
            if isinstance(conv_result, ConversionResult):
                success = conv_result.success
                text = conv_result.text
                message = conv_result.message
                images = conv_result.images or []
            else:
                success, text, message = conv_result
                images = []
            
            if not success:
                result["status"] = "failed"
                result["error"] = message
                return result
            
            # Create document folder
            doc_folder = self.folder_manager.create_document_folder(file_path)
            result["document_folder"] = doc_folder.document_name
            
            # 1. Copy original file to 01_input/
            if self.config.copy_original:
                dest_file = doc_folder.input_dir / file_path.name
                shutil.copy2(file_path, dest_file)
                self.logger.debug(f"Copied original to: {dest_file}")
            
            # 2. Save images to 04_images/
            saved_images = []
            if images and self.config.enable_image_extraction:
                for img in images:
                    img_path = doc_folder.images_dir / img.filename
                    with open(img_path, "wb") as f:
                        f.write(img.data)
                    saved_images.append({
                        "filename": img.filename,
                        "page_or_sheet": img.page_or_sheet,
                        "ai_description": img.ai_description,
                    })
                self.logger.debug(f"Saved {len(saved_images)} images")
            
            # 3. Generate and save content.md to 02_initial_markdown/
            # For PDF/DOCX/XLSX/PPTX files, images are already embedded in the text
            ext = file_path.suffix.lower()
            images_embedded = ext in (".pdf", ".docx", ".xlsx", ".pptx", ".ppt", ".odp")
            markdown_content = self._generate_content_md(
                text=text or "",
                original_file=file_path,
                images=saved_images,
                images_embedded=images_embedded,
            )
            
            content_path = doc_folder.content_md_path
            with open(content_path, "w", encoding="utf-8") as f:
                f.write(markdown_content)
            
            result["status"] = "success"
            result["chars"] = len(text) if text else 0
            result["images"] = len(saved_images)
            result["output_path"] = str(doc_folder.root)
            
            # Update progress
            processed.append(str(file_path))
            self.progress["last_file"] = str(file_path)
            
            # Delete original file from input if configured
            if self.config.delete_after_process:
                try:
                    file_path.unlink()
                    self.logger.debug(f"Deleted input file: {file_path}")
                except Exception as e:
                    self.logger.warning(f"Failed to delete input file {file_path}: {e}")
            
        except MemoryError:
            raise
        except Exception as e:
            self.logger.error(f"Conversion error for {file_path.name}: {e}")
            result["status"] = "failed"
            result["error"] = str(e)
        
        result["time_sec"] = round(time.time() - start_time, 2)
        
        # Garbage collection for large files
        mem_after = self._check_memory()
        if mem_after > 2000:
            gc.collect()
        
        return result
    
    def _generate_content_md(
        self,
        text: str,
        original_file: Path,
        images: List[Dict],
        images_embedded: bool = False,
    ) -> str:
        """Generate content.md with metadata and image references.
        
        Args:
            text: Extracted text content (may already contain image references)
            original_file: Path to original file
            images: List of saved image info
            images_embedded: If True, images are already embedded in text (e.g., PDF)
            
        Returns:
            Markdown content
        """
        lines = []
        
        # Header with metadata
        lines.append(f"# {original_file.stem}")
        lines.append("")
        lines.append(f"**元ファイル:** {original_file.name}  ")
        lines.append(f"**ファイル形式:** {original_file.suffix.lstrip('.').lower()}  ")
        lines.append(f"**抽出日時:** {datetime.now().isoformat()}  ")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # Main content
        lines.append(text)
        
        # Add images section only if images are NOT already embedded in text
        # (e.g., for Excel, Word, PowerPoint where images are extracted separately)
        if images and not images_embedded:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("## 抽出画像")
            lines.append("")
            
            for img in images:
                filename = img["filename"]
                page_or_sheet = img.get("page_or_sheet", "")
                ai_description = img.get("ai_description", "")
                
                # Image heading
                if page_or_sheet:
                    lines.append(f"### {filename} ({page_or_sheet})")
                else:
                    lines.append(f"### {filename}")
                lines.append("")
                
                # Image reference (relative path from 02_transcribed_markdown to 04_images)
                lines.append(f"![{filename}](../04_images/{filename})")
                lines.append("")
                
                # AI description（全行を > で囲む）
                if ai_description:
                    desc_lines = ai_description.split('\n')
                    quoted_desc = '\n'.join(f"> {line}" for line in desc_lines)
                    lines.append(f"> **図の説明:**\n>\n{quoted_desc}")
                    lines.append("")
        
        return "\n".join(lines)
    
    def run(self) -> int:
        """Run the conversion process.
        
        Returns:
            Exit code (0 for success)
        """
        self.logger.info("=" * 60)
        self.logger.info("KNOWLEDGE CONVERTER - Stage 1")
        self.logger.info("=" * 60)
        
        # Collect files
        files = self.collect_files()
        self.results["statistics"]["total"] = len(files)
        
        if not files:
            self.logger.info("No files to process")
            self._save_results()
            return 0
        
        workers = max(1, self.parallel_workers)
        self.logger.info(f"Processing {len(files)} files (workers={workers})")

        completed_count = 0

        def _process_one(args: tuple) -> None:
            nonlocal completed_count
            index, file_path = args

            if self.interrupted:
                return

            try:
                result = self.convert_file(file_path)
            except MemoryError:
                self.logger.error("Memory limit exceeded.")
                self.interrupted = True
                return

            status = result.get("status")

            with self._state_lock:
                if status == "skipped":
                    self.results["statistics"]["skipped"] += 1
                else:
                    self.results["statistics"]["processed"] += 1
                    if status == "success":
                        self.results["statistics"]["success"] += 1
                        time_sec = result.get("time_sec", 0)
                        self.results["documents"].append({
                            "name": result.get("document_folder"),
                            "original_file": result.get("filename"),
                            "chars": result.get("chars", 0),
                            "images": result.get("images", 0),
                            "time_min": round(time_sec / 60, 2),
                        })
                        self.logger.info(
                            f"[{index}/{len(files)}] ✅ {file_path.name} → "
                            f"{result.get('document_folder')}/ "
                            f"({result.get('chars', 0):,} chars, {result.get('images', 0)} images)"
                        )
                    else:
                        self.results["statistics"]["failed"] += 1
                        time_sec = result.get("time_sec", 0)
                        self.results["failed_files"].append({
                            "file": str(file_path),
                            "error": result.get("error", "Unknown error"),
                            "time_min": round(time_sec / 60, 2),
                        })
                        self.logger.error(
                            f"[{index}/{len(files)}] ❌ {file_path.name}: "
                            f"{result.get('error', 'Failed')}"
                        )

                completed_count += 1
                if completed_count % 50 == 0:
                    pct = completed_count / len(files) * 100
                    self.logger.info(f"Progress: {completed_count}/{len(files)} ({pct:.1f}%)")
                    self._save_progress()
                    self._save_results()

        if workers == 1:
            # 逐次処理（従来の動作）
            for args in enumerate(files, 1):
                _process_one(args)
        else:
            # ファイル間並列処理
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_process_one, (i, fp)): fp
                    for i, fp in enumerate(files, 1)
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        fp = futures[future]
                        self.logger.error(f"Unexpected error for {fp.name}: {exc}")
                        with self._state_lock:
                            self.results["statistics"]["failed"] += 1
                            self.results["failed_files"].append({
                                "file": str(fp),
                                "error": str(exc),
                            })

        # Final save
        self._save_progress()
        self._save_results()
        self._print_summary()
        
        return 0 if self.results["statistics"]["failed"] == 0 else 1
    
    def _print_summary(self) -> None:
        """Print summary of the conversion."""
        stats = self.results["statistics"]
        
        print("\n" + "=" * 60)
        print("CONVERSION COMPLETE")
        print("=" * 60)
        
        print("\n[Statistics]")
        print(f"  Total files: {stats['total']}")
        print(f"  Processed: {stats['processed']}")
        print(f"  Success: {stats['success']}")
        print(f"  Failed: {stats['failed']}")
        print(f"  Skipped: {stats['skipped']}")
        
        print(f"\n[Output] {self.config.output_dir}")
        print(f"  Documents created: {len(self.results['documents'])}")
        
        if self.results["failed_files"]:
            print(f"\n[Warning] Failed files: {len(self.results['failed_files'])}")
            for failure in self.results["failed_files"][:5]:
                print(f"  - {Path(failure['file']).name}: {failure['error'][:50]}")
        
        print("\n" + "=" * 60)

