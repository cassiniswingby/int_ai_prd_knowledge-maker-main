"""Command-line entry points for Knowledge Maker.

Available Commands:
    convert  - Stage 1: Convert documents to Markdown with image extraction
    enhance  - Stage 2: Enhance documents with AI cleanup and metadata

Usage:
    python -m src.km.cli.convert --input ./documents --output ./knowledge
    python -m src.km.cli.enhance --target ./knowledge --mode all
"""