"""File inventory generator module."""

import csv
import json
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Iterable, Optional, Set

try:
    import msoffcrypto
except ImportError:
    msoffcrypto = None  # type: ignore

try:
    from PyPDF2 import PdfReader
except ImportError:
    PdfReader = None  # type: ignore


# File extension categories
SUPPORTED_EXT = {'.pdf', '.xlsx', '.xls', '.xlsm', '.docx', '.doc', '.pptx', '.ppt', '.png', '.jpg', '.jpeg', '.tif', '.tiff'}
CONVERTIBLE_EXT = {'.pdf', '.xlsx', '.xlsm', '.docx'}
LIBRE_EXT = {'.xls', '.doc', '.ppt', '.pptx'}
PASSWORD_EXT = {'.xlsx', '.xls', '.xlsm', '.docx', '.doc', '.pptx', '.ppt'}
STAGE1_TEXT_EXT = {'.pdf', '.xlsx', '.xlsm', '.docx'}
IMAGE_OCR_EXT = {'.png', '.jpg', '.jpeg', '.tif', '.tiff'}
OTHER_EXCLUDED_EXT = {'.lnk', '.db', '.mp4', '.ai', '.zip', '.tmp', '.svg'}


def load_ocr_manifest(manifest_path: Path) -> List[str]:
    """Load OCR manifest and extract file paths."""
    if not manifest_path.exists():
        return []

    try:
        data = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return []

    files = data.get('files', []) if isinstance(data, dict) else data
    result = []

    for item in files:
        if isinstance(item, dict):
            path = item.get('path')
        else:
            path = item
        if path:
            result.append(path)

    return result


def detect_password(path: Path) -> bool:
    """Detect if a file is password-protected."""
    suffix = path.suffix.lower()

    # Check PDF encryption
    if suffix == '.pdf':
        if PdfReader is None:
            return False
        try:
            reader = PdfReader(str(path))
            return bool(reader.is_encrypted)
        except Exception:
            return False

    # Skip non-password formats
    if suffix not in PASSWORD_EXT:
        return False

    # Skip temporary files
    if path.name.startswith('~$'):
        return False

    # Check Office encryption with msoffcrypto
    if msoffcrypto:
        try:
            with open(path, 'rb') as f:
                office = msoffcrypto.OfficeFile(f)
                if office.is_encrypted():
                    return True
        except Exception:
            pass

    # Check for OLE container (legacy Excel with password)
    if suffix in {'.xlsx', '.xlsm'}:
        try:
            if not zipfile.is_zipfile(path):
                with open(path, 'rb') as fh:
                    header = fh.read(8)
                    if header == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
                        return True
        except Exception:
            return False

    return False


def normalise_original_path(root: Path, path_value: str) -> Optional[str]:
    """Normalize original path relative to root directory."""
    if not path_value:
        return None

    candidate = Path(path_value)

    # Handle paths starting with 'home/'
    if path_value.startswith('home/'):
        candidate = Path('/' + path_value)

    # Try to resolve absolute path
    try:
        if candidate.is_absolute():
            return str(candidate.resolve().relative_to(root))
    except Exception:
        pass

    # Handle prefixed paths
    if path_value.startswith('2_機器_環境商材/'):
        return path_value[len('2_機器_環境商材/'):]

    return path_value


def categorize_file(
    file_path: Path,
    rel_root: str,
    ocr_paths: Set[str],
    raw_text_dir: Path,
    txt_exists: bool = False
) -> str:
    """Categorize a file based on its properties and extension."""
    ext = file_path.suffix.lower()

    # Exclude temp/lock files
    if ext in OTHER_EXCLUDED_EXT or file_path.name.startswith('~$'):
        return 'other_excluded'

    # Check password protection first
    if detect_password(file_path):
        category = 'password_protected'
    elif ext == '.pdf' and rel_root in ocr_paths:
        category = 'requires_ocr'
    elif ext in IMAGE_OCR_EXT:
        category = 'requires_ocr'
    # PPT/PPTX requires OCR if no raw_text exists
    elif ext in {'.ppt', '.pptx'} and not txt_exists:
        category = 'requires_ocr'
    elif ext in LIBRE_EXT:
        category = 'requires_libre'
    elif ext in CONVERTIBLE_EXT:
        category = 'convertible_as_is'
    else:
        category = 'other_excluded'

    # Override based on existing text file
    if txt_exists:
        if category == 'requires_ocr' and ext == '.pdf':
            category = 'convertible_as_is'
        elif category == 'password_protected':
            if ext in LIBRE_EXT:
                category = 'requires_libre'
            elif ext in CONVERTIBLE_EXT or ext == '.pdf':
                category = 'convertible_as_is'

    return category


class InventoryGenerator:
    """Generate file inventory for Knowledge Maker."""

    def __init__(
        self,
        base_dir: Path,
        raw_text_dir: Path,
        raw_json_dir: Path,
        results_path: Optional[Path] = None,
        manifest_path: Optional[Path] = None,
    ):
        """Initialize inventory generator."""
        self.base_dir = base_dir.resolve()
        self.raw_text_dir = raw_text_dir.resolve()
        self.raw_json_dir = raw_json_dir.resolve()
        self.results_path = results_path
        self.manifest_path = manifest_path

        self.ocr_paths: Set[str] = set()
        self.failed_paths: Set[str] = set()
        self.json_originals: Set[str] = set()

    def load_metadata(self) -> None:
        """Load OCR manifest and failed files metadata."""
        # Load OCR manifest
        if self.manifest_path:
            for entry in load_ocr_manifest(self.manifest_path):
                normalised = normalise_original_path(self.base_dir, entry)
                if normalised:
                    self.ocr_paths.add(normalised)

        # Load failed files from results
        if self.results_path and self.results_path.exists():
            try:
                stats = json.loads(self.results_path.read_text(encoding='utf-8'))
                failed_entries = stats.get('failed_files', []) if isinstance(stats, dict) else []
                self.failed_paths = {
                    item.get('file')
                    for item in failed_entries
                    if isinstance(item, dict) and item.get('file')
                }
            except (json.JSONDecodeError, OSError):
                pass

    def build_json_originals(self, all_files: List[Path]) -> None:
        """Build set of original files present in JSON index."""
        # Create no-extension mapping
        noext_map: Dict[str, List[str]] = {}
        for file_path in all_files:
            rel_noext = Path(file_path.relative_to(self.base_dir)).with_suffix('')
            key = rel_noext.as_posix().lower()
            noext_map.setdefault(key, []).append(str(file_path.relative_to(self.base_dir)))

        # Process JSON files
        for jf in self.raw_json_dir.glob('DOC_*.json'):
            try:
                obj = json.loads(jf.read_text(encoding='utf-8'))
                op = obj.get('original_path', '') or ''
                fmt = (obj.get('file_format', '') or '').strip('.').lower()
                norm = normalise_original_path(self.base_dir, op) or ''

                if not norm:
                    continue

                candidates = [norm]
                if fmt and not norm.lower().endswith('.' + fmt):
                    candidates.append(f"{norm}.{fmt}")

                for candidate in candidates:
                    self.json_originals.add(candidate)
                    key = Path(candidate).with_suffix('').as_posix().lower()
                    for resolved in noext_map.get(key, []):
                        self.json_originals.add(resolved)
            except Exception:
                continue

    def generate_inventory(self) -> List[Dict[str, str]]:
        """Generate inventory rows for all files."""
        self.load_metadata()

        # Collect all files
        all_files = [p for p in self.base_dir.rglob('*') if p.is_file()]
        self.build_json_originals(all_files)

        rows = []
        for file_path in all_files:
            rel_root = str(file_path.relative_to(self.base_dir))
            ext = file_path.suffix.lower()

            # Check if raw text exists
            txt_path = self.raw_text_dir / Path(rel_root).with_suffix('.txt')
            txt_exists = txt_path.exists()

            # Categorize file
            category = categorize_file(
                file_path, rel_root, self.ocr_paths,
                self.raw_text_dir, txt_exists
            )

            # Check if JSON exists
            json_exists = rel_root in self.json_originals

            # Get file stats once to avoid race condition
            try:
                file_stat = file_path.stat()
                size_bytes = str(file_stat.st_size)
                mtime_iso = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            except (OSError, IOError) as e:
                # Handle case where file was deleted between rglob and stat
                print(f"Warning: Could not stat {file_path}: {e}")
                continue

            rows.append({
                'relative_path': rel_root,
                'prefixed_path': str(Path('2_機器_環境商材') / rel_root),
                'size_bytes': size_bytes,
                'mtime_iso': mtime_iso,
                'ext': ext,
                'category': category,
                'raw_text': '1' if txt_exists else '0',
                'raw_json': '1' if json_exists else '0',
            })

        return rows

    def save_csv(self, rows: List[Dict[str, str]], output_path: Path) -> None:
        """Save inventory rows to CSV file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        fieldnames = [
            'relative_path', 'prefixed_path', 'size_bytes',
            'mtime_iso', 'ext', 'category', 'raw_text', 'raw_json'
        ]

        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def generate_summary(self, rows: List[Dict[str, str]], output_path: Path) -> Dict:
        """Generate summary statistics."""
        summary: Dict[str, int] = {}
        for row in rows:
            cat = row['category']
            summary[cat] = summary.get(cat, 0) + 1

        return {
            'generated_at': datetime.now().isoformat(),
            'output': str(output_path),
            'total': len(rows),
            'by_category': summary,
            'raw_text': sum(1 for r in rows if r['raw_text'] == '1'),
            'raw_json': sum(1 for r in rows if r['raw_json'] == '1'),
            'failed_entries': len(self.failed_paths),
        }