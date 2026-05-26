"""Stage 2 text-to-JSON structuring pipeline implementation."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class Stage2Config:
    """Configuration for Stage 2 structuring."""
    
    input_dir: Path = Path("02_converted/01_raw_text")
    output_dir: Path = Path("02_converted/02_raw_json")
    progress_path: Path = Path(".archive/reports/stage2_progress.json")
    results_path: Path = Path(".archive/reports/stage2_results.json")
    log_path: Path = Path("stage2_structure.log")
    retry_failed: bool = False
    paths_file: Optional[Path] = None
    
    def resolve(self) -> "Stage2Config":
        """Resolve all paths to absolute paths."""
        return replace(
            self,
            input_dir=self.input_dir.expanduser().resolve(),
            output_dir=self.output_dir.expanduser().resolve(),
            progress_path=self.progress_path.expanduser().resolve(),
            results_path=self.results_path.expanduser().resolve(),
            log_path=self.log_path.expanduser().resolve(),
            paths_file=self.paths_file.expanduser().resolve() if self.paths_file else None,
        )
    
    def ensure_directories(self) -> None:
        """Ensure all required directories exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for path in (self.progress_path, self.results_path, self.log_path):
            parent = path.parent
            if parent and not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
    
    def prepare(self) -> "Stage2Config":
        """Prepare configuration (resolve paths and create directories)."""
        cfg = self.resolve()
        cfg.ensure_directories()
        return cfg


class Stage2Structurer:
    """Stage 2 text-to-JSON structurer."""
    
    def __init__(self, config: Stage2Config):
        """Initialize structurer with configuration.
        
        Args:
            config: Stage2 configuration
        """
        self.config = config
        self.logger = self._setup_logger()
        self.progress = self.load_progress()
        self.results = {
            "start_time": datetime.now().isoformat(),
            "statistics": {
                "total": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
            },
            "failed_files": [],
        }
    
    def _setup_logger(self) -> logging.Logger:
        """Set up file-specific logger."""
        logger = logging.getLogger(f"{__name__}.{id(self)}")
        logger.setLevel(logging.INFO)
        
        # Remove any existing handlers
        logger.handlers.clear()
        
        # Add file handler
        if self.config.log_path:
            handler = logging.FileHandler(self.config.log_path)
            handler.setFormatter(
                logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            )
            logger.addHandler(handler)
        
        logger.propagate = False
        return logger
    
    def load_progress(self) -> Dict[str, Any]:
        """Load progress from previous runs."""
        if self.config.progress_path.exists():
            try:
                with open(self.config.progress_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                self.logger.warning(f"Could not load progress: {e}")
        
        return {
            "processed_files": [],
            "last_file": None,
            "timestamp": None,
        }
    
    def save_progress(self) -> None:
        """Save current progress."""
        self.progress["timestamp"] = datetime.now().isoformat()
        
        try:
            with open(self.config.progress_path, 'w', encoding='utf-8') as f:
                json.dump(self.progress, f, indent=2, ensure_ascii=False)
        except OSError as e:
            self.logger.error(f"Could not save progress: {e}")
    
    def collect_target_files(self) -> List[Path]:
        """Collect text files to process."""
        if self.config.paths_file and self.config.paths_file.exists():
            # Read specific paths from file
            with open(self.config.paths_file, 'r', encoding='utf-8') as f:
                paths = [line.strip() for line in f if line.strip()]
            
            files = []
            for path in paths:
                file_path = self.config.input_dir / path
                if file_path.exists() and file_path.suffix == '.txt':
                    files.append(file_path)
            
            self.logger.info(f"Found {len(files)} files from paths file")
            return sorted(files)
        
        # Collect all .txt files
        if not self.config.input_dir.exists():
            self.logger.warning(f"Input directory does not exist: {self.config.input_dir}")
            return []
        
        files = list(self.config.input_dir.rglob("*.txt"))
        
        # Filter out already processed files if not retrying
        if not self.config.retry_failed:
            processed = set(self.progress.get("processed_files", []))
            files = [f for f in files if str(f) not in processed]
        
        self.logger.info(f"Found {len(files)} text files to process")
        return sorted(files)
    
    def structure_text(self, text_path: Path) -> Dict[str, Any]:
        """Structure text content into JSON format.
        
        Args:
            text_path: Path to text file
            
        Returns:
            Structured JSON data
        """
        try:
            # Read text content
            with open(text_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract metadata
            lines = content.split('\n')
            total_lines = len(lines)
            total_chars = len(content)
            
            # Basic structure
            structured = {
                "original_path": str(text_path.relative_to(self.config.input_dir)),
                "extraction_timestamp": datetime.now().isoformat(),
                "metadata": {
                    "total_lines": total_lines,
                    "total_characters": total_chars,
                    "file_size_bytes": text_path.stat().st_size,
                },
                "content": {
                    "full_text": content,
                    "sections": self._extract_sections(content),
                },
                "processing": {
                    "stage": "raw_text_to_json",
                    "version": "1.0",
                },
            }
            
            return structured
            
        except Exception as e:
            self.logger.error(f"Error structuring {text_path}: {e}")
            raise
    
    def _extract_sections(self, content: str) -> List[Dict[str, Any]]:
        """Extract sections from text content.
        
        Args:
            content: Text content
            
        Returns:
            List of sections
        """
        sections = []
        current_section = []
        section_markers = ["===", "---", "###", "##", "#"]
        
        for line in content.split('\n'):
            # Check if line is a section header
            is_header = any(line.strip().startswith(marker) for marker in section_markers)
            
            if is_header and current_section:
                # Save previous section
                sections.append({
                    "content": '\n'.join(current_section),
                    "line_count": len(current_section),
                })
                current_section = []
            
            current_section.append(line)
        
        # Save last section
        if current_section:
            sections.append({
                "content": '\n'.join(current_section),
                "line_count": len(current_section),
            })
        
        return sections
    
    def process_file(self, text_path: Path) -> Dict[str, Any]:
        """Process a single text file.
        
        Args:
            text_path: Path to text file
            
        Returns:
            Processing result
        """
        relative_path = text_path.relative_to(self.config.input_dir)
        
        # Check if already processed
        if str(text_path) in self.progress.get("processed_files", []):
            return {
                "file": str(relative_path),
                "status": "skipped",
                "reason": "already_processed",
            }
        
        try:
            # Structure the text
            structured = self.structure_text(text_path)
            
            # Save as JSON
            output_path = self.config.output_dir / relative_path.with_suffix('.json')
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(structured, f, indent=2, ensure_ascii=False)
            
            # Update progress
            self.progress.setdefault("processed_files", []).append(str(text_path))
            self.progress["last_file"] = str(text_path)
            
            self.logger.info(f"Processed: {relative_path}")
            
            return {
                "file": str(relative_path),
                "status": "success",
                "output": str(output_path),
            }
            
        except Exception as e:
            self.logger.error(f"Failed to process {relative_path}: {e}")
            
            return {
                "file": str(relative_path),
                "status": "failed",
                "error": str(e),
            }
    
    def run(
        self,
        batch_size: int = 10,
        max_files: Optional[int] = None
    ) -> int:
        """Run the structuring process.
        
        Args:
            batch_size: Number of files to process before saving progress
            max_files: Maximum number of files to process
            
        Returns:
            Exit code (0 for success)
        """
        self.logger.info("Starting Stage 2 text-to-JSON structuring")
        
        # Collect target files
        files = self.collect_target_files()
        
        if max_files:
            files = files[:max_files]
        
        self.results["statistics"]["total"] = len(files)
        
        if not files:
            self.logger.info("No files to process")
            self.save_results()
            return 0
        
        # Process files
        for i, text_path in enumerate(files, 1):
            self.logger.info(f"Processing {i}/{len(files)}: {text_path.name}")
            
            result = self.process_file(text_path)
            
            # Update statistics
            if result["status"] == "success":
                self.results["statistics"]["success"] += 1
            elif result["status"] == "skipped":
                self.results["statistics"]["skipped"] += 1
            else:
                self.results["statistics"]["failed"] += 1
                self.results["failed_files"].append(result)
            
            # Save progress periodically
            if i % batch_size == 0:
                self.save_progress()
                self.logger.info(f"Progress saved at {i}/{len(files)}")
        
        # Final save
        self.save_progress()
        self.save_results()
        
        self.logger.info(
            f"Completed: {self.results['statistics']['success']} success, "
            f"{self.results['statistics']['failed']} failed, "
            f"{self.results['statistics']['skipped']} skipped"
        )
        
        return 0 if self.results["statistics"]["failed"] == 0 else 1
    
    def save_results(self) -> None:
        """Save final results."""
        self.results["end_time"] = datetime.now().isoformat()
        
        try:
            with open(self.config.results_path, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"Results saved to {self.config.results_path}")
        except OSError as e:
            self.logger.error(f"Could not save results: {e}")