#!/usr/bin/env python3
"""CLI for Stage 3: Knowledge Deployer - Integrate and deploy knowledge.

Usage:
    # Deploy with interactive confirmation (auto-detect new/update mode)
    python -m src.km.cli.deploy --target pre-knowledge/ --output knowledge/

    # Force overwrite existing knowledge folder
    python -m src.km.cli.deploy --target pre-knowledge/ --output knowledge/ --force

    # Exclude specific documents
    python -m src.km.cli.deploy --target pre-knowledge/ --output knowledge/ --exclude "チャットデータ"

    # Validate links after deployment
    python -m src.km.cli.deploy --target pre-knowledge/ --output knowledge/ --validate-links
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional


def setup_logging(log_level: str = "INFO") -> None:
    """Set up logging configuration."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Stage 3: Integrate and deploy knowledge from pre-knowledge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Output Structure:
    knowledge/
    ├── 00_用語集.md                # Auto-generated glossary
    ├── 01_業務マニュアル/          # Category folder
    │   ├── 01_概要・体制・基準.md  # Knowledge file (with YAML frontmatter)
    │   ├── 02_サービス提供可否.md
    │   └── images/                # Images for this category
    ├── 02_定型文・テンプレート/
    │   └── ...
    ├── mapping.json               # Input-output mapping (for AI updates)
    ├── readme.md                  # Folder structure & mapping table
    └── UPDATE_REPORT.md           # Update report (when updating existing knowledge)

Examples:
    # Basic usage (auto-detect new/update mode)
    python -m src.km.cli.deploy --target pre-knowledge/ --output knowledge/

    # Force overwrite without confirmation
    python -m src.km.cli.deploy --target pre-knowledge/ --output knowledge/ --force

    # Exclude documents containing specific patterns
    python -m src.km.cli.deploy --target pre-knowledge/ --output knowledge/ --exclude "チャットデータ" "生ログ"

    # Exclude with regex pattern
    python -m src.km.cli.deploy --target pre-knowledge/ --output knowledge/ --exclude-regex ".*チャットデータ.*"

    # Validate links after deployment
    python -m src.km.cli.deploy --target pre-knowledge/ --output knowledge/ --validate-links
        """,
    )
    
    # Basic options
    parser.add_argument(
        "--target", "-t",
        type=str,
        required=True,
        help="Pre-knowledge directory to process (required)",
    )
    
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="knowledge",
        help="Output directory for final knowledge (default: knowledge/)",
    )
    
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force execution without confirmation",
    )
    
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-5.1",
        help="OpenAI model to use (default: gpt-5.1)",
    )
    
    # Exclusion options
    parser.add_argument(
        "--exclude",
        nargs="+",
        default=[],
        help="Documents to exclude (by name, multiple allowed)",
    )
    
    parser.add_argument(
        "--exclude-regex",
        nargs="+",
        default=[],
        help="Documents to exclude (regex patterns, multiple allowed)",
    )
    
    # Validation options
    parser.add_argument(
        "--validate-links",
        action="store_true",
        help="Validate all links after deployment",
    )
    
    # Generation options
    parser.add_argument(
        "--no-glossary",
        action="store_true",
        help="Skip glossary generation",
    )
    
    parser.add_argument(
        "--no-split-summary",
        action="store_true",
        help="Skip summary generation for split chapters (saves API cost)",
    )
    
    parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Skip UPDATE_REPORT.md generation",
    )
    
    # Logging
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    
    return parser.parse_args(argv)


def detect_mode(output_dir: Path) -> str:
    """Detect whether to create new or update existing knowledge.
    
    Returns:
        "new" if output directory is empty or doesn't exist
        "update" if output directory has existing knowledge
    """
    if not output_dir.exists():
        return "new"
    
    # Check for mapping.json or any .md files
    mapping_path = output_dir / "mapping.json"
    if mapping_path.exists():
        return "update"
    
    md_files = list(output_dir.rglob("*.md"))
    if md_files:
        return "update"
    
    return "new"


def run_new_mode(
    target_dir: Path,
    output_dir: Path,
    args: argparse.Namespace,
    logger: logging.Logger,
) -> int:
    """Run in new creation mode."""
    from ..pipeline.knowledge_deployer import KnowledgeDeployer
    
    logger.info("Mode: 新規作成（既存ナレッジなし）")
    
    # Create and run deployer
    deployer = KnowledgeDeployer(
        target_dir=target_dir,
        output_dir=output_dir,
        generate_glossary=not args.no_glossary,
        generate_split_summary=not args.no_split_summary,
        skip_confirmation=args.force,
    )
    
    return deployer.run()


def run_restructure_mode(
    target_dir: Path,
    output_dir: Path,
    args: argparse.Namespace,
    logger: logging.Logger,
) -> int:
    """Run in restructure mode - rebuild knowledge structure from scratch."""
    from ..pipeline.knowledge_deployer import KnowledgeDeployer
    import shutil
    import tempfile
    
    logger.info("Mode: 抜本的変更（既存ナレッジを含めて再構成）")
    
    print("\n" + "═" * 80)
    print("                        🔄 抜本的変更モード")
    print("═" * 80)
    print("")
    print("  既存ナレッジと新規ファイルを統合して、新しい構成を提案します。")
    print("")
    
    # バックアップを作成
    backup_dir = output_dir.parent / f"{output_dir.name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if output_dir.exists():
        shutil.copytree(output_dir, backup_dir)
        logger.info(f"Backup created: {backup_dir}")
        print(f"  📦 バックアップを作成しました: {backup_dir}")
    
    # 既存ナレッジの内容を一時フォルダにコピー
    # 新規作成モードと同じロジックで再構成
    
    # Create and run deployer with restructure flag
    deployer = KnowledgeDeployer(
        target_dir=target_dir,
        output_dir=output_dir,
        generate_glossary=not args.no_glossary,
        generate_split_summary=not args.no_split_summary,
        skip_confirmation=args.force,
        include_existing_knowledge=True,  # 既存ナレッジも含める
    )
    
    result = deployer.run()
    
    if result != 0:
        # 失敗した場合はバックアップから復元
        print(f"\n  ⚠️ 処理が失敗しました。バックアップから復元してください: {backup_dir}")
    else:
        print(f"\n  💡 問題がある場合はバックアップから復元できます: {backup_dir}")
    
    return result


def run_update_mode(
    target_dir: Path,
    output_dir: Path,
    args: argparse.Namespace,
    logger: logging.Logger,
) -> int:
    """Run in update mode."""
    from ..pipeline.proposal_generator import ProposalGenerator, UpdateMode, ActionType
    from ..pipeline.knowledge_updater import KnowledgeUpdater
    from ..pipeline.link_validator import LinkValidator
    
    # Generate proposal
    logger.info("Analyzing documents and generating proposal...")
    
    generator = ProposalGenerator(
        pre_knowledge_dir=target_dir,
        output_dir=output_dir,
        exclude_patterns=args.exclude,
        exclude_regex=args.exclude_regex,
    )
    
    proposal = generator.generate()
    
    # Display proposal in terminal
    print(generator.format_terminal_output(proposal))
    
    # Check if restructure is recommended
    if proposal.needs_restructure:
        print("\n  ⚠️ 抜本的変更が推奨されています。")
        print(f"  理由: {proposal.restructure_reason}")
        print("\n  [Y] 増分更新で続行  [R] 抜本的変更を実行  [N] キャンセル")
        
        if not args.force:
            response = input("  > ").strip().lower()
            if response == 'n':
                logger.info("処理がキャンセルされました")
                return 0
            elif response == 'r':
                # 抜本的変更モードを実行
                return run_restructure_mode(target_dir, output_dir, args, logger)
    else:
        while True:
            print("\n  [Y] この内容で更新  [R] テキストを入力して更新方法を指示する  [N] キャンセル")
            
            if args.force:
                break
            
            response = input("  > ").strip().lower()
            if response == 'n':
                logger.info("処理がキャンセルされました")
                return 0
            elif response == 'r':
                # ユーザーからの指示を受け取って再提案
                print("\n  修正内容を入力してください（例: 「コンロ見積の資料は02_契約に配置して、ファイル名は12_コンロ見積.mdにして」）")
                user_instruction = input("  > ").strip()
                
                if user_instruction:
                    print("\n  再提案中...")
                    proposal = generator.regenerate_with_instruction(user_instruction)
                    print(generator.format_terminal_output(proposal))
                continue
            elif response == 'y':
                break
            else:
                print("  Y, R, N のいずれかを入力してください")
                continue
    
    # Check if there are any actions to perform
    actionable = [a for a in proposal.actions if a.action_type != ActionType.SKIP]
    if not actionable:
        print("\n  ✅ 更新対象のドキュメントがありません。")
        return 0
    
    # Execute update
    logger.info("Executing update...")
    
    updater = KnowledgeUpdater(
        pre_knowledge_dir=target_dir,
        output_dir=output_dir,
        proposal=proposal,
    )
    
    results = updater.execute()
    
    # Generate and save update report
    if not args.skip_report:
        report_path = updater.save_update_report()
        print(f"\n  📄 更新レポートを保存しました: {report_path}")
    
    # Validate links if requested
    if args.validate_links:
        logger.info("Validating links...")
        
        validator = LinkValidator(output_dir)
        issues = validator.validate_all()
        
        if issues:
            print(f"\n  ⚠️ リンク問題を検出: {len(issues)}件")
            
            # Save link check report
            report = validator.generate_report()
            report_path = output_dir / "link_check_report.md"
            report_path.write_text(report, encoding="utf-8")
            print(f"  📄 リンク検証レポートを保存しました: {report_path}")
            
            return 1
        else:
            print(f"\n  ✅ リンク検証: 問題なし（{validator.get_summary()['total_files']}ファイル検証済み）")
    
    # Print summary
    print("\n" + "═" * 80)
    print("                        ✅ 更新完了")
    print("═" * 80)
    print(f"\n  ✨ 新規追加: {len(results['created'])}件")
    print(f"  📝 更新: {len(results['updated'])}件")
    print(f"  ⏭️ スキップ: {len(results['skipped'])}件")
    
    if results['errors']:
        print(f"  ❌ エラー: {len(results['errors'])}件")
        return 1
    
    print("\n  💡 変更をコミットしてPRを作成してください。")
    print("     UPDATE_REPORT.md をレビューに使用できます。")
    print("")
    
    return 0


def main(argv: Optional[list] = None) -> int:
    """Main entry point for the deploy command."""
    args = parse_args(argv)
    
    # Set up logging
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)
    
    try:
        # Set model environment variable
        import os
        os.environ["OPENAI_DEPLOY_MODEL"] = args.model
        
        target_dir = Path(args.target)
        output_dir = Path(args.output)
        
        # Check target directory exists
        if not target_dir.exists():
            logger.error(f"Target directory not found: {target_dir}")
            return 1
        
        # Detect mode
        mode = detect_mode(output_dir)
        
        logger.info(f"Starting Stage 3 deployment")
        logger.info(f"Target: {target_dir}")
        logger.info(f"Output: {output_dir}")
        logger.info(f"Model: {args.model}")
        logger.info(f"Detected mode: {mode}")
        
        if mode == "new":
            # New creation mode
            exit_code = run_new_mode(target_dir, output_dir, args, logger)
        else:
            # Update mode
            exit_code = run_update_mode(target_dir, output_dir, args, logger)
        
        if exit_code == 0:
            logger.info("Deployment completed successfully")
        else:
            logger.warning("Deployment completed with errors")
        
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
