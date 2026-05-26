"""Configuration for the new Knowledge folder structure.

Folder Structure:
    pre-knowledge/
    └── {document_name}/
        ├── 01_input/                 # Original file (copy)
        ├── 02_transcribed_markdown/  # Stage1: 文字起こしMarkdown
        ├── 03_formatted_markdown/    # Stage2: きれい化Markdown
        └── 04_images/                # Extracted images
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Set


# Folder names
FOLDER_INPUT = "01_input"
FOLDER_TRANSCRIBED_MARKDOWN = "02_transcribed_markdown"  # 文字起こしMarkdown
FOLDER_FORMATTED_MARKDOWN = "03_formatted_markdown"  # きれい化Markdown
FOLDER_IMAGES = "04_images"  # 抽出画像
FOLDER_REPORTS = "_reports"

# Legacy aliases (for backward compatibility)
FOLDER_INITIAL_MARKDOWN = FOLDER_TRANSCRIBED_MARKDOWN
FOLDER_FINAL_MARKDOWN = FOLDER_FORMATTED_MARKDOWN

# File names
FILE_CONTENT_MD = "transcribed.md"  # 文字起こしMarkdown
FILE_ENHANCED_MD = "formatted.md"  # きれい化Markdown
FILE_METADATA_JSON = "metadata.json"  # Legacy (not used in new workflow)

# Legacy folder (for backward compatibility only)
FOLDER_JSON = "05_json"


@dataclass
class KnowledgeConfig:
    """Configuration for Knowledge conversion pipeline.
    
    Attributes:
        input_dir: Directory containing source files to convert
        output_dir: Root output directory (default: 'knowledge/')
        copy_original: Whether to copy original files to 01_input/
        enable_image_extraction: Whether to extract images from documents
        enable_image_ai_description: Whether to generate AI descriptions for images
        image_max_width: Maximum image width in pixels (for resizing)
        delete_after_process: Whether to delete files from input after processing
    """
    
    input_dir: Path = field(default_factory=lambda: Path("input"))
    output_dir: Path = field(default_factory=lambda: Path("knowledge"))
    copy_original: bool = True
    enable_image_extraction: bool = True
    enable_image_ai_description: bool = True
    image_max_width: int = 1200
    delete_after_process: bool = True  # Delete files from input after successful processing
    
    # Progress tracking
    progress_path: Optional[Path] = None
    results_path: Optional[Path] = None
    
    # Memory settings
    memory_threshold_mb: int = 50
    max_memory_mb: int = 4000
    
    def __post_init__(self):
        """Initialize paths and load from environment."""
        self.input_dir = Path(self.input_dir)
        self.output_dir = Path(self.output_dir)
        
        # Load from environment variables
        self.enable_image_extraction = os.getenv(
            "ENABLE_IMAGE_EXTRACTION", "true"
        ).lower() in ("true", "1", "yes")
        
        self.enable_image_ai_description = os.getenv(
            "ENABLE_IMAGE_AI_DESCRIPTION", "true"
        ).lower() in ("true", "1", "yes")
        
        try:
            self.image_max_width = int(os.getenv("IMAGE_MAX_WIDTH", "1024"))
        except ValueError:
            self.image_max_width = 1024
        
        # Set default paths if not provided
        if self.progress_path is None:
            self.progress_path = self.output_dir / FOLDER_REPORTS / "progress.json"
        if self.results_path is None:
            self.results_path = self.output_dir / FOLDER_REPORTS / "results.json"
    
    def resolve(self) -> "KnowledgeConfig":
        """Resolve all paths to absolute paths."""
        self.input_dir = self.input_dir.expanduser().resolve()
        self.output_dir = self.output_dir.expanduser().resolve()
        if self.progress_path:
            self.progress_path = self.progress_path.expanduser().resolve()
        if self.results_path:
            self.results_path = self.results_path.expanduser().resolve()
        return self
    
    def ensure_directories(self) -> None:
        """Ensure output directories exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        reports_dir = self.output_dir / FOLDER_REPORTS
        reports_dir.mkdir(parents=True, exist_ok=True)
    
    def prepare(self) -> "KnowledgeConfig":
        """Prepare configuration (resolve paths and create directories)."""
        self.resolve()
        self.ensure_directories()
        return self


class DocumentFolder:
    """Manages the folder structure for a single document.
    
    Structure:
        {document_name}/
        ├── 01_input/
        ├── 02_transcribed_markdown/
        ├── 03_formatted_markdown/
        └── 04_images/
    """
    
    def __init__(self, base_path: Path, document_name: str):
        """Initialize document folder.
        
        Args:
            base_path: Base output directory (e.g., knowledge/)
            document_name: Name of the document (without extension)
        """
        self.base_path = Path(base_path)
        self.document_name = document_name
        self.root = self.base_path / document_name
    
    @property
    def input_dir(self) -> Path:
        """Path to 01_input/ folder."""
        return self.root / FOLDER_INPUT
    
    @property
    def transcribed_markdown_dir(self) -> Path:
        """Path to 02_transcribed_markdown/ folder (文字起こしMarkdown)."""
        return self.root / FOLDER_TRANSCRIBED_MARKDOWN
    
    @property
    def initial_markdown_dir(self) -> Path:
        """Alias for transcribed_markdown_dir (backward compatibility)."""
        return self.transcribed_markdown_dir
    
    @property
    def formatted_markdown_dir(self) -> Path:
        """Path to 03_formatted_markdown/ folder (きれい化Markdown)."""
        return self.root / FOLDER_FORMATTED_MARKDOWN
    
    @property
    def images_dir(self) -> Path:
        """Path to 04_images/ folder."""
        return self.root / FOLDER_IMAGES
    
    @property
    def final_markdown_dir(self) -> Path:
        """Alias for formatted_markdown_dir (backward compatibility)."""
        return self.formatted_markdown_dir
    
    @property
    def content_md_path(self) -> Path:
        """Path to content.md file (文字起こしMarkdown)."""
        return self.transcribed_markdown_dir / FILE_CONTENT_MD
    
    @property
    def enhanced_md_path(self) -> Path:
        """Path to enhanced.md file (きれい化Markdown)."""
        return self.formatted_markdown_dir / FILE_ENHANCED_MD
    
    @property
    def json_dir(self) -> Path:
        """Path to 05_json/ folder (legacy, not used in new workflow)."""
        return self.root / FOLDER_JSON
    
    @property
    def metadata_json_path(self) -> Path:
        """Path to metadata.json file (legacy, not used in new workflow)."""
        return self.json_dir / FILE_METADATA_JSON
    
    def create_structure(self) -> None:
        """Create all subdirectories for Stage1."""
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.transcribed_markdown_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        # 03_formatted_markdown is created on demand (Stage2)
    
    def exists(self) -> bool:
        """Check if document folder exists."""
        return self.root.exists()


class DocumentFolderManager:
    """Manages document folders and handles name deduplication."""
    
    def __init__(self, base_path: Path):
        """Initialize folder manager.
        
        Args:
            base_path: Base output directory (e.g., knowledge/)
        """
        self.base_path = Path(base_path)
        self._used_names: Set[str] = set()
        self._scan_existing()
    
    def _scan_existing(self) -> None:
        """Scan existing folders to track used names."""
        if self.base_path.exists():
            for item in self.base_path.iterdir():
                if item.is_dir() and not item.name.startswith("_"):
                    self._used_names.add(item.name)
    
    def get_unique_name(self, base_name: str) -> str:
        """Get a unique folder name, adding suffix if needed.
        
        Args:
            base_name: Base name (typically filename without extension)
            
        Returns:
            Unique folder name
        """
        # Clean the name
        clean_name = base_name.strip()
        if not clean_name:
            clean_name = "document"
        
        # Find unique name
        name = clean_name
        counter = 2
        while name in self._used_names:
            name = f"{clean_name}_{counter}"
            counter += 1
        
        self._used_names.add(name)
        return name
    
    def create_document_folder(self, source_file: Path) -> DocumentFolder:
        """Create a new document folder for a source file.
        
        Args:
            source_file: Path to the source file
            
        Returns:
            DocumentFolder instance
        """
        base_name = source_file.stem
        unique_name = self.get_unique_name(base_name)
        
        doc_folder = DocumentFolder(self.base_path, unique_name)
        doc_folder.create_structure()
        
        return doc_folder
    
    def get_document_folder(self, document_name: str) -> DocumentFolder:
        """Get an existing document folder.
        
        Args:
            document_name: Name of the document folder
            
        Returns:
            DocumentFolder instance
        """
        return DocumentFolder(self.base_path, document_name)
    
    def list_documents(self) -> list:
        """List all document folders.
        
        Returns:
            List of DocumentFolder instances
        """
        documents = []
        if self.base_path.exists():
            for item in sorted(self.base_path.iterdir()):
                if item.is_dir() and not item.name.startswith("_"):
                    documents.append(DocumentFolder(self.base_path, item.name))
        return documents

