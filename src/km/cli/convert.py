#!/usr/bin/env python3
"""CLI for Stage 1: Knowledge Converter - Initial Markdown creation.

Usage:
    python -m src.km.cli.convert
    python -m src.km.cli.convert --input ./documents --output ./knowledge
"""

import argparse
import sys
from pathlib import Path

# Windows コンソールの cp932 エンコーディング問題を回避
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main():
    """Main entry point for the convert CLI."""
    parser = argparse.ArgumentParser(
        description="Stage 1: Convert documents to Markdown with page images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Default: input/ -> knowledge/ (files deleted after processing)
    python -m src.km.cli.convert

    # Custom input/output
    python -m src.km.cli.convert --input ./documents --output ./knowledge

    # Keep original files in input folder (don't delete)
    python -m src.km.cli.convert --keep-input

    # Disable AI image descriptions (faster, no API cost)
    python -m src.km.cli.convert --no-ai-description

Output Structure:
    knowledge/
    └── {document_name}/
        ├── 01_input/              # Original file (copy)
        ├── 02_transcribed_markdown/  # Markdown with page images
        │   └── transcribed.md
        ├── 03_formatted_markdown/    # Formatted markdown (Stage2)
        │   └── formatted.md
        └── 04_images/                # Page images (page_001.png, page_002.png, ...)
        """,
    )
    
    parser.add_argument(
        "--input", "-i",
        type=str,
        default="input",
        help="Input directory containing files to convert (default: input/)",
    )
    
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="pre-knowledge",
        help="Output directory (default: pre-knowledge/)",
    )
    
    parser.add_argument(
        "--keep-input",
        action="store_true",
        help="Keep original files in input folder (don't delete after processing)",
    )
    
    parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Don't copy original files to 01_input/",
    )
    
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Disable image extraction",
    )
    
    parser.add_argument(
        "--no-ai-description",
        action="store_true",
        help="Disable AI-generated image descriptions",
    )
    
    parser.add_argument(
        "--image-max-width",
        type=int,
        default=1200,
        help="Maximum image width in pixels (default: 1200)",
    )

    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=1,
        help=(
            "ファイル間並列処理のワーカー数 (デフォルト: 1=逐次)。"
            "30程度を指定すると大幅に高速化できます。"
            "環境変数 CONVERT_PARALLEL_WORKERS でも設定可能。"
        ),
    )

    args = parser.parse_args()
    
    # Check input directory exists
    input_dir = Path(args.input)
    if not input_dir.exists():
        print(f"[Error] Input directory not found: {input_dir}")
        print(f"[Info] Create the directory and add files to convert:")
        print(f"       mkdir {args.input}")
        sys.exit(1)
    
    # Check if input directory has files
    target_extensions = {".pdf", ".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ppt", ".csv"}
    files = [f for f in input_dir.rglob("*") if f.is_file() and f.suffix.lower() in target_extensions]
    if not files:
        print(f"[Error] No supported files found in: {input_dir}")
        print(f"[Info] Supported formats: PDF, Excel, Word, PowerPoint, CSV")
        sys.exit(1)
    
    print(f"[Info] Found {len(files)} files to convert")
    
    # Import here to avoid slow startup
    from ..pipeline.knowledge_config import KnowledgeConfig
    from ..pipeline.knowledge_converter import KnowledgeConverter
    
    # Create configuration
    config = KnowledgeConfig(
        input_dir=input_dir,
        output_dir=Path(args.output),
        copy_original=not args.no_copy,
        enable_image_extraction=not args.no_images,
        enable_image_ai_description=not args.no_ai_description,
        image_max_width=args.image_max_width,
        delete_after_process=not args.keep_input,
    )
    
    # Run converter
    converter = KnowledgeConverter(config, parallel_workers=args.parallel_workers)
    exit_code = converter.run()
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

