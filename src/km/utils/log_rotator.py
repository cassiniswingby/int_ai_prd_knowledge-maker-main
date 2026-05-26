"""Log rotation utilities for Knowledge Maker."""

import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional


class LogRotator:
    """Manage log directory rotation and cleanup."""

    def __init__(self, base_path: Path, retention_days: int = 90):
        """Initialize log rotator.

        Args:
            base_path: Base path for log directories
            retention_days: Number of days to retain logs
        """
        self.base_path = Path(base_path)
        self.retention_days = retention_days

    def get_old_directories(self) -> List[Path]:
        """Find directories older than retention period.

        Returns:
            List of directories to be cleaned up
        """
        if not self.base_path.exists():
            return []

        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        old_dirs = []

        # Check lo_runs directories with date pattern
        for lo_dir in self.base_path.glob("lo_runs*"):
            if not lo_dir.is_dir():
                continue

            # Try to parse date from directory name
            dir_name = lo_dir.name
            date_str = None

            # Pattern 1: lo_runs_YYYYMMDD
            if dir_name.startswith("lo_runs_"):
                date_str = dir_name[8:16]  # Extract YYYYMMDD
            # Pattern 2: lo_runs/YYYYMMDD
            elif dir_name == "lo_runs":
                for subdir in lo_dir.iterdir():
                    if subdir.is_dir() and len(subdir.name) == 8:
                        try:
                            dir_date = datetime.strptime(subdir.name, "%Y%m%d")
                            if dir_date < cutoff_date:
                                old_dirs.append(subdir)
                        except ValueError:
                            continue
                continue

            # Parse date from pattern 1
            if date_str and len(date_str) == 8:
                try:
                    dir_date = datetime.strptime(date_str, "%Y%m%d")
                    if dir_date < cutoff_date:
                        old_dirs.append(lo_dir)
                except ValueError:
                    continue

        return old_dirs

    def rotate(self, dry_run: bool = False) -> List[Path]:
        """Perform log rotation.

        Args:
            dry_run: If True, only report what would be deleted

        Returns:
            List of deleted (or would-be deleted) directories
        """
        old_dirs = self.get_old_directories()

        if not old_dirs:
            return []

        if dry_run:
            print(f"[DRY RUN] Would delete {len(old_dirs)} directories:")
            for dir_path in old_dirs:
                print(f"  - {dir_path}")
        else:
            for dir_path in old_dirs:
                try:
                    shutil.rmtree(dir_path)
                    print(f"Deleted: {dir_path}")
                except Exception as e:
                    print(f"Error deleting {dir_path}: {e}")

        return old_dirs

    def cleanup_archive(self, archive_path: Optional[Path] = None) -> int:
        """Clean up archive directory.

        Args:
            archive_path: Path to archive directory (default: .archive/)

        Returns:
            Number of files cleaned up
        """
        if archive_path is None:
            archive_path = Path(".archive")

        if not archive_path.exists():
            return 0

        count = 0
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)

        # Clean up old reports
        reports_dir = archive_path / "reports"
        if reports_dir.exists():
            for file_path in reports_dir.glob("*"):
                if not file_path.is_file():
                    continue

                try:
                    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if file_mtime < cutoff_date:
                        file_path.unlink()
                        count += 1
                        print(f"Deleted archive file: {file_path}")
                except Exception as e:
                    print(f"Error processing {file_path}: {e}")

        return count


def main():
    """Command-line interface for log rotation."""
    import argparse

    parser = argparse.ArgumentParser(description="Rotate and clean up old logs")
    parser.add_argument(
        "--path",
        default="docs/reports",
        help="Base path for log directories",
    )
    parser.add_argument(
        "--retention",
        type=int,
        default=90,
        help="Number of days to retain logs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    parser.add_argument(
        "--include-archive",
        action="store_true",
        help="Also clean up .archive directory",
    )

    args = parser.parse_args()

    rotator = LogRotator(Path(args.path), args.retention)
    deleted = rotator.rotate(args.dry_run)

    if args.include_archive and not args.dry_run:
        archive_count = rotator.cleanup_archive()
        print(f"Cleaned up {archive_count} archive files")

    print(f"Processed {len(deleted)} directories")


if __name__ == "__main__":
    main()