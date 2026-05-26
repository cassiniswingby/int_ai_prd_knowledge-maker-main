"""Configuration helpers for Knowledge Maker pipelines."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional


@dataclass
class Stage3Config:
    """Configuration for Stage 3 AI enhancement."""
    
    input_dir: Path = Path("02_converted/02_raw_json")
    output_dir: Path = Path("02_converted/03_AIenhanced_json")
    progress_path: Path = Path(".archive/reports/stage3_progress.json")
    results_path: Path = Path(".archive/reports/stage3_results.json")
    log_path: Path = Path("stage3_enhance.log")
    retry_failed: bool = False
    paths_file: Optional[Path] = None
    
    # OpenAI settings
    model: str = "gpt-5"
    temperature: float = 0.1
    max_tokens: int = 2000
    
    def resolve(self) -> "Stage3Config":
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
    
    def prepare(self) -> "Stage3Config":
        """Prepare configuration (resolve paths and create directories)."""
        cfg = self.resolve()
        cfg.ensure_directories()
        return cfg


@dataclass
class Stage1Config:
    base_dir: Path = Path("01_original/2_機器_環境商材")
    output_dir: Path = Path("02_converted/01_raw_text")
    manifest_path: Path = Path(".archive/reports/requires_ocr_manifest.json")
    progress_path: Path = Path(".archive/reports/batch_progress.json")
    results_path: Path = Path(".archive/reports/production_batch_results.json")
    log_path: Path = Path("production_batch.log")
    retry_failed: bool = False
    paths_file: Optional[Path] = None
    memory_threshold_mb: int = 50
    max_memory_mb: int = 4000

    def resolve(self) -> "Stage1Config":
        return replace(
            self,
            base_dir=self.base_dir.expanduser().resolve(),
            output_dir=self.output_dir.expanduser().resolve(),
            manifest_path=self.manifest_path.expanduser().resolve(),
            progress_path=self.progress_path.expanduser().resolve(),
            results_path=self.results_path.expanduser().resolve(),
            log_path=self.log_path.expanduser().resolve(),
            paths_file=self.paths_file.expanduser().resolve() if self.paths_file else None,
        )

    def ensure_directories(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for path in (self.manifest_path, self.progress_path, self.results_path, self.log_path):
            parent = path.parent
            if parent and not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)

    def prepare(self) -> "Stage1Config":
        cfg = self.resolve()
        cfg.ensure_directories()
        return cfg