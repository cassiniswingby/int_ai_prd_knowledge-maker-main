#!/usr/bin/env python3
"""
km-structure CLI command for Stage2 text-to-JSON conversion.
"""

import argparse
import logging
import sys
from pathlib import Path

from ..pipeline.stage2_structurer import Stage2Structurer, Stage2Config


def setup_logging(log_file: Path, verbose: bool = False):
    """Set up logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    
    # Configure root logger
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )


def main():
    """Main entry point for km-structure command."""
    parser = argparse.ArgumentParser(
        description='Structure text files to JSON (Stage2)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all text files from Stage1 output
  km-structure
  
  # Process with custom directories
  km-structure --input-dir custom/text --output-dir custom/json
  
  # Process specific files from a list
  km-structure --paths-file targets.txt
  
  # Verbose mode with custom log
  km-structure -v --log-file stage2_debug.log
        """
    )
    
    # Directory options
    parser.add_argument(
        '--input-dir',
        type=Path,
        default=Path('02_converted/01_raw_text'),
        help='Input directory with text files (default: 02_converted/01_raw_text)'
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('02_converted/02_raw_json'),
        help='Output directory for JSON files (default: 02_converted/02_raw_json)'
    )
    
    # File selection
    parser.add_argument(
        '--paths-file',
        type=Path,
        help='File containing list of specific paths to process'
    )
    parser.add_argument(
        '--retry-failed',
        action='store_true',
        help='Retry previously failed files'
    )
    
    # Output control
    parser.add_argument(
        '--progress-file',
        type=Path,
        default=Path('.archive/reports/stage2_progress.json'),
        help='Progress tracking file'
    )
    parser.add_argument(
        '--results-file',
        type=Path,
        default=Path('.archive/reports/stage2_results.json'),
        help='Results summary file'
    )
    parser.add_argument(
        '--log-file',
        type=Path,
        default=Path('stage2_structure.log'),
        help='Log file path'
    )
    
    # Processing options
    parser.add_argument(
        '--batch-size',
        type=int,
        default=10,
        help='Number of files to process before saving progress'
    )
    parser.add_argument(
        '--max-files',
        type=int,
        help='Maximum number of files to process'
    )
    
    # Debug options
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be processed without actually processing'
    )
    
    args = parser.parse_args()
    
    # Set up logging
    setup_logging(args.log_file, args.verbose)
    logger = logging.getLogger(__name__)
    
    try:
        # Create configuration
        config = Stage2Config(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            progress_path=args.progress_file,
            results_path=args.results_file,
            log_path=args.log_file,
            retry_failed=args.retry_failed,
            paths_file=args.paths_file
        )
        
        # Prepare directories
        prepared_config = config.prepare()
        
        # Create and run structurer
        structurer = Stage2Structurer(prepared_config)
        
        if args.dry_run:
            logger.info("DRY RUN MODE - Showing files that would be processed:")
            files = structurer.collect_target_files()
            
            if args.max_files:
                files = files[:args.max_files]
            
            for i, file_path in enumerate(files, 1):
                print(f"{i:4d}. {file_path.relative_to(prepared_config.input_dir)}")
            
            print(f"\nTotal files to process: {len(files)}")
            return 0
        
        # Run the structuring process
        logger.info("Starting Stage2 text-to-JSON structuring...")
        exit_code = structurer.run(
            batch_size=args.batch_size,
            max_files=args.max_files
        )
        
        if exit_code == 0:
            logger.info("Stage2 structuring completed successfully")
        else:
            logger.error(f"Stage2 structuring failed with exit code: {exit_code}")
        
        return exit_code
        
    except KeyboardInterrupt:
        logger.warning("Process interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())