"""Command-line interface for inventory generation."""

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from src.km.inventory import InventoryGenerator


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser for inventory CLI."""
    parser = argparse.ArgumentParser(
        description="Generate file inventory CSV",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--base-dir",
        default="01_original/2_機器_環境商材",
        help="Base directory containing original files",
    )
    parser.add_argument(
        "--raw-text-dir",
        default="02_converted/01_raw_text",
        help="Directory containing extracted text files",
    )
    parser.add_argument(
        "--raw-json-dir",
        default="02_converted/02_raw_json",
        help="Directory containing structured JSON files",
    )
    parser.add_argument(
        "--results",
        default=".archive/reports/production_batch_results.json",
        help="Path to production batch results JSON",
    )
    parser.add_argument(
        "--manifest",
        default=".archive/reports/requires_ocr_manifest.json",
        help="Path to OCR manifest JSON",
    )
    parser.add_argument(
        "--output",
        default="docs/reports/file-inventory/latest.csv",
        help="Output CSV file path",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="Print summary to stdout",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = build_parser()
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point for inventory generation."""
    args = parse_args(argv)

    generator = InventoryGenerator(
        base_dir=Path(args.base_dir),
        raw_text_dir=Path(args.raw_text_dir),
        raw_json_dir=Path(args.raw_json_dir),
        results_path=Path(args.results),
        manifest_path=Path(args.manifest),
    )

    # Generate inventory
    rows = generator.generate_inventory()

    # Save CSV
    output_path = Path(args.output)
    generator.save_csv(rows, output_path)

    # Generate and print summary if requested
    if args.print_summary:
        summary = generator.generate_summary(rows, output_path)
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())