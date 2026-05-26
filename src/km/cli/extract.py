from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from src.km.pipeline.config import Stage1Config
from src.km.pipeline.stage1_extractor import Stage1Extractor

DEFAULTS = Stage1Config()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract raw text from source documents",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input",
        "--base-dir",
        dest="base_dir",
        default=str(DEFAULTS.base_dir),
        help="Directory containing original documents",
    )
    parser.add_argument(
        "--output",
        "--output-dir",
        dest="output_dir",
        default=str(DEFAULTS.output_dir),
        help="Directory for extracted text files",
    )
    parser.add_argument(
        "--manifest",
        dest="manifest_path",
        default=str(DEFAULTS.manifest_path),
        help="OCR manifest JSON path",
    )
    parser.add_argument(
        "--progress",
        "--progress-file",
        dest="progress_path",
        default=str(DEFAULTS.progress_path),
        help="Progress checkpoint JSON",
    )
    parser.add_argument(
        "--results",
        "--results-file",
        dest="results_path",
        default=str(DEFAULTS.results_path),
        help="Aggregate batch results JSON output",
    )
    parser.add_argument(
        "--log-file",
        dest="log_path",
        default=str(DEFAULTS.log_path),
        help="Path to the extraction log file",
    )
    parser.add_argument(
        "--paths-file",
        dest="paths_file",
        default=None,
        help="Optional newline-delimited list of relative paths to process",
    )
    parser.add_argument(
        "--retry-failed",
        dest="retry_failed",
        action="store_true",
        default=DEFAULTS.retry_failed,
        help="Retry files recorded as failed in the results JSON",
    )
    parser.add_argument(
        "--memory-threshold",
        dest="memory_threshold",
        type=int,
        default=DEFAULTS.memory_threshold_mb,
        help="Per-file memory warning threshold in MB",
    )
    parser.add_argument(
        "--max-memory",
        dest="max_memory",
        type=int,
        default=DEFAULTS.max_memory_mb,
        help="Hard memory usage limit in MB before aborting",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = Stage1Config(
        base_dir=Path(args.base_dir),
        output_dir=Path(args.output_dir),
        manifest_path=Path(args.manifest_path),
        progress_path=Path(args.progress_path),
        results_path=Path(args.results_path),
        log_path=Path(args.log_path),
        retry_failed=args.retry_failed,
        paths_file=Path(args.paths_file) if args.paths_file else None,
        memory_threshold_mb=args.memory_threshold,
        max_memory_mb=args.max_memory,
    )
    extractor = Stage1Extractor(config)
    return extractor.run()


if __name__ == "__main__":
    sys.exit(main())