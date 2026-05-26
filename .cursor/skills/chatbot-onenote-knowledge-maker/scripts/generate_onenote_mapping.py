# -*- coding: utf-8 -*-
"""
OneNote 用 mapping.json 生成スクリプト (Step 4)

Step 0 で生成した _onenote_manifest.json と、KM Step 1-2 の出力から、
tool-ec-chatbot の build_chunks_from_onenote.py が読める形式の mapping.json を生成する。

Usage:
    python scripts/generate_onenote_mapping.py \
        --manifest input/_onenote_manifest.json \
        --pre-knowledge pre-knowledge/ \
        --output onenote_mapping.json
"""

import argparse
import json
import sys
from pathlib import Path

# Windows コンソールの cp932 エンコーディング問題を回避
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(description="OneNote 用 mapping.json 生成")
    parser.add_argument("--manifest", required=True, help="_onenote_manifest.json のパス")
    parser.add_argument("--pre-knowledge", default="pre-knowledge", help="pre-knowledge ディレクトリ")
    parser.add_argument("--output", default="onenote_mapping.json", help="出力ファイル名")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    pk_dir = Path(args.pre_knowledge).resolve()
    output_path = Path(args.output)

    if not manifest_path.exists():
        print(f"[ERROR] manifest が見つかりません: {manifest_path}")
        sys.exit(1)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    pages = manifest.get("pages", [])
    if not pages:
        print("[WARN] manifest に pages がありません。")
        sys.exit(0)

    print("=" * 60)
    print("OneNote mapping.json 生成 (Step 4)")
    print(f"  manifest: {manifest_path}")
    print(f"  pre-knowledge: {pk_dir}")
    print(f"  出力: {output_path}")
    print(f"  ページ数: {len(pages)}")
    print("=" * 60)

    mapping = []
    found = 0
    missing = 0

    for i, page in enumerate(pages):
        entry_id = f"onenote_{i + 1:04d}"

        pdf_stem = Path(page["pdf_file"]).stem
        pk_folder = pk_dir / pdf_stem

        formatted_path = pk_folder / "03_formatted_markdown" / "formatted.md"
        transcribed_path = pk_folder / "02_transcribed_markdown" / "transcribed.md"

        content_preview = ""
        if formatted_path.exists():
            found += 1
            text = formatted_path.read_text(encoding="utf-8")
            content_preview = text[:200].replace("\n", " ")
        else:
            missing += 1
            print(f"  [WARN] formatted.md が見つかりません: {formatted_path}")

        entry = {
            "id": entry_id,
            "onenote": {
                "notebook": page.get("notebook", ""),
                "section": page.get("section", ""),
                "page_name": page.get("page_title", ""),
                "link": page.get("page_url", ""),
            },
            "pages": [
                {
                    "pdf_page_num": 1,
                    "original_pdf_path": page.get("pdf_path", ""),
                    "pre_knowledge_folder": pdf_stem,
                    "transcribed_path": str(transcribed_path),
                    "formatted_path": str(formatted_path),
                    "content_preview": content_preview,
                }
            ],
        }
        mapping.append(entry)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)

    print()
    print(f"完了: {len(mapping)} エントリ生成")
    print(f"  formatted.md あり: {found}")
    print(f"  formatted.md なし: {missing}")
    print(f"出力: {output_path}")


if __name__ == "__main__":
    main()
