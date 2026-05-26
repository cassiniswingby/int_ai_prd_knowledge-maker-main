"""Global configuration management for Knowledge Maker."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any


@dataclass
class KMConfig:
    """Global configuration for Knowledge Maker."""

    # Base directories
    base_dir: Path = field(default_factory=lambda: Path("01_original/2_機器_環境商材"))
    output_dir: Path = field(default_factory=lambda: Path("02_converted/01_raw_text"))
    json_dir: Path = field(default_factory=lambda: Path("02_converted/02_raw_json"))

    # Report paths
    reports_dir: Path = field(default_factory=lambda: Path("docs/reports"))
    archive_dir: Path = field(default_factory=lambda: Path(".archive"))

    # Stage 1 extraction settings
    manifest_path: Path = field(default_factory=lambda: Path("requires_ocr_manifest.json"))
    progress_path: Path = field(default_factory=lambda: Path("batch_progress.json"))
    results_path: Path = field(default_factory=lambda: Path("production_batch_results.json"))
    log_path: Path = field(default_factory=lambda: Path("production_batch.log"))

    # Memory limits
    memory_threshold_mb: int = 50
    max_memory_mb: int = 4000

    # Log rotation
    log_retention_days: int = 90

    # LibreOffice settings
    soffice_path: str = "soffice"
    pdftotext_path: str = "pdftotext"

    @classmethod
    def from_env(cls) -> "KMConfig":
        """Create config from environment variables.

        Environment variables:
        - KM_BASE_DIR: Base directory for original files
        - KM_OUTPUT_DIR: Output directory for converted files
        - KM_LOG_DIR: Log directory
        - KM_MEMORY_LIMIT: Maximum memory in MB
        - KM_LOG_RETENTION: Days to retain logs
        """
        config = cls()

        # Override from environment
        if env_base := os.getenv("KM_BASE_DIR"):
            config.base_dir = Path(env_base)

        if env_output := os.getenv("KM_OUTPUT_DIR"):
            config.output_dir = Path(env_output)

        if env_json := os.getenv("KM_JSON_DIR"):
            config.json_dir = Path(env_json)

        if env_reports := os.getenv("KM_REPORTS_DIR"):
            config.reports_dir = Path(env_reports)

        if env_archive := os.getenv("KM_ARCHIVE_DIR"):
            config.archive_dir = Path(env_archive)

        if env_memory := os.getenv("KM_MEMORY_LIMIT"):
            try:
                config.max_memory_mb = int(env_memory)
            except ValueError:
                pass

        if env_retention := os.getenv("KM_LOG_RETENTION"):
            try:
                config.log_retention_days = int(env_retention)
            except ValueError:
                pass

        if env_soffice := os.getenv("KM_SOFFICE_PATH"):
            config.soffice_path = env_soffice

        return config

    def resolve_paths(self) -> "KMConfig":
        """Resolve all paths to absolute paths."""
        self.base_dir = self.base_dir.resolve()
        self.output_dir = self.output_dir.resolve()
        self.json_dir = self.json_dir.resolve()
        self.reports_dir = self.reports_dir.resolve()
        self.archive_dir = self.archive_dir.resolve()

        # Resolve file paths
        if not self.manifest_path.is_absolute():
            self.manifest_path = self.archive_dir / "reports" / self.manifest_path
        if not self.progress_path.is_absolute():
            self.progress_path = self.archive_dir / "reports" / self.progress_path
        if not self.results_path.is_absolute():
            self.results_path = self.archive_dir / "reports" / self.results_path
        if not self.log_path.is_absolute():
            self.log_path = self.reports_dir / self.log_path

        return self

    def ensure_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.json_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        (self.archive_dir / "reports").mkdir(parents=True, exist_ok=True)

        # Ensure parent directories for files
        for path in (self.manifest_path, self.progress_path, self.results_path, self.log_path):
            if path.parent:
                path.parent.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "base_dir": str(self.base_dir),
            "output_dir": str(self.output_dir),
            "json_dir": str(self.json_dir),
            "reports_dir": str(self.reports_dir),
            "archive_dir": str(self.archive_dir),
            "manifest_path": str(self.manifest_path),
            "progress_path": str(self.progress_path),
            "results_path": str(self.results_path),
            "log_path": str(self.log_path),
            "memory_threshold_mb": self.memory_threshold_mb,
            "max_memory_mb": self.max_memory_mb,
            "log_retention_days": self.log_retention_days,
            "soffice_path": self.soffice_path,
            "pdftotext_path": self.pdftotext_path,
        }


# Global default config instance
default_config = KMConfig()