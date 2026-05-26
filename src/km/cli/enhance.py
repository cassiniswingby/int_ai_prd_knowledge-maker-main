#!/usr/bin/env python3
"""CLI for Stage 2: Knowledge Enhancer - Markdown formatting with templates.

Usage:
    # Enhance all documents with default template
    python -m src.km.cli.enhance --target knowledge/

    # Use custom template
    python -m src.km.cli.enhance --target knowledge/ --template templates/manual.md

    # Process specific documents
    python -m src.km.cli.enhance --target knowledge/ --documents "見積書" "仕様書"
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

# Windows コンソールの cp932 エンコーディング問題を回避
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def setup_logging(log_level: str = "INFO") -> None:
    """Set up logging configuration."""
    # Windows コンソールの cp932 問題を回避
    if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Stage 2: Format documents with AI-powered templates",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Output Structure:
    knowledge/{document_name}/
    └── 04_formatted_markdown/formatted.md

Examples:
    # Format all documents with default template
    python -m src.km.cli.enhance --target ./knowledge

    # Use custom template
    python -m src.km.cli.enhance --target ./knowledge --template templates/manual.md

    # Process specific documents only
    python -m src.km.cli.enhance --target ./knowledge --documents "見積書" "仕様書"

    # Use different AI model
    python -m src.km.cli.enhance --target ./knowledge --model gpt-4o
        """,
    )
    
    parser.add_argument(
        "--target", "-t",
        type=str,
        default="pre-knowledge",
        help="Pre-knowledge directory to enhance (default: pre-knowledge/)",
    )
    
    parser.add_argument(
        "--template", "-T",
        type=str,
        default=None,
        help="Template file path (default: templates/default.md)",
    )
    
    parser.add_argument(
        "--documents", "-d",
        type=str,
        nargs="+",
        help="Specific document folder names to process",
    )
    
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-5.1",
        help="OpenAI model to use (default: gpt-5.1)",
    )
    
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    
    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=1,
        help=(
            "ドキュメント間並列処理のワーカー数 (デフォルト: 1=逐次)。"
            "30程度を指定するとAPI並列呼び出しで大幅に高速化できます。"
            "環境変数 ENHANCE_DOCUMENT_PARALLEL_WORKERS でも設定可能。"
        ),
    )

    # Quality check options
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry only documents that failed quality check previously",
    )
    
    parser.add_argument(
        "--auto-fix",
        action="store_true",
        help="Automatically retry failed documents (max 2 times)",
    )
    
    parser.add_argument(
        "--skip-quality-check",
        action="store_true",
        help="Skip quality validation after formatting",
    )

    # Regeneration options
    parser.add_argument(
        "--backup-old",
        action="store_true",
        help=(
            "Backup existing 03_formatted_markdown/ to 03_formatted_markdown_old_YYYYMMDDHHMMSS/ "
            "before re-generating formatted.md. (Recommended for re-run)"
        ),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing formatted.md (no backup). Use with caution.",
    )
    
    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    """Main entry point for the enhance command."""
    args = parse_args(argv)
    
    # Set up logging
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)
    
    try:
        # Set model environment variable
        import os
        os.environ["OPENAI_ENHANCE_MODEL"] = args.model
        
        # Import here to avoid slow startup
        from ..pipeline.knowledge_enhancer import KnowledgeEnhancer
        from ..templates import load_template
        
        knowledge_dir = Path(args.target)
        
        if not knowledge_dir.exists():
            logger.error(f"Knowledge directory not found: {knowledge_dir}")
            return 1
        
        # Load template
        template_path = Path(args.template) if args.template else None
        try:
            template = load_template(template_path)
            template_name = args.template if args.template else "default"
            logger.info(f"Using template: {template_name}")
        except FileNotFoundError as e:
            logger.error(f"Template not found: {e}")
            return 1
        
        logger.info(f"Starting Stage 2 formatting")
        logger.info(f"Target: {knowledge_dir}")
        logger.info(f"Model: {args.model}")
        
        # Handle --retry-failed option
        document_names = args.documents
        if args.retry_failed:
            import json
            failed_path = knowledge_dir / "_reports" / "quality_issues.json"
            if failed_path.exists():
                with open(failed_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    document_names = data.get("failed_documents", [])
                logger.info(f"Retrying {len(document_names)} failed documents")
                
                # Remove existing formatted files for retry
                from ..pipeline.knowledge_config import DocumentFolderManager
                folder_manager = DocumentFolderManager(knowledge_dir)
                for doc_name in document_names:
                    doc_folder = folder_manager.get_document_folder(doc_name)
                    if doc_folder.enhanced_md_path.exists():
                        doc_folder.enhanced_md_path.unlink()
                        logger.debug(f"Removed existing formatted.md for {doc_name}")
            else:
                logger.warning("No quality_issues.json found - nothing to retry")
                return 0

        # Handle regeneration options (backup or overwrite)
        if args.backup_old or args.overwrite:
            from datetime import datetime
            from ..pipeline.knowledge_config import (
                DocumentFolderManager,
                FOLDER_FORMATTED_MARKDOWN,
            )

            folder_manager = DocumentFolderManager(knowledge_dir)
            targets = document_names if document_names else [
                d.document_name for d in folder_manager.list_documents()
            ]

            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            for doc_name in targets:
                doc_folder = folder_manager.get_document_folder(doc_name)
                formatted_dir = doc_folder.formatted_markdown_dir

                if not formatted_dir.exists():
                    continue

                if args.backup_old:
                    backup_dir = formatted_dir.parent / f"{FOLDER_FORMATTED_MARKDOWN}_old_{ts}"
                    if backup_dir.exists():
                        # extremely unlikely, but keep it safe
                        backup_dir = formatted_dir.parent / f"{FOLDER_FORMATTED_MARKDOWN}_old_{ts}_{doc_name}"
                    formatted_dir.rename(backup_dir)
                    logger.info(f"Backed up formatted dir: {formatted_dir} -> {backup_dir}")
                else:
                    # overwrite: remove only formatted.md so enhancer will re-run
                    if doc_folder.enhanced_md_path.exists():
                        doc_folder.enhanced_md_path.unlink()
                        logger.info(f"Removed existing formatted.md for overwrite: {doc_name}")
        
        # Create and run enhancer
        enhancer = KnowledgeEnhancer(
            knowledge_dir=knowledge_dir,
            template=template,
            auto_fix=args.auto_fix,
            skip_quality_check=args.skip_quality_check,
            parallel_workers=args.parallel_workers,
        )
        
        exit_code = enhancer.run(document_names=document_names)
        
        if exit_code == 0:
            logger.info("Formatting completed successfully")
        else:
            logger.warning("Formatting completed with errors")
        
        return exit_code
        
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
