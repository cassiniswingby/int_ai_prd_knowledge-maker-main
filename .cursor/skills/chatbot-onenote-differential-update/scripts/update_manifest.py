# -*- coding: utf-8 -*-
"""
manifest 更新 & onenote_mapping.json 生成スクリプト (差分更新 Step 4)

diff_plan.json と KM 処理結果を使って:
1. _manifest_current.json を生成（_manifest_prev.json の次回分）
2. onenote_mapping.json を生成（chatbot プロジェクトに渡す）

Usage:
    python scripts/update_manifest.py \
        --diff-plan     diff_plan.json \
        --pre-knowledge pre-knowledge/ \
        --output-manifest _manifest_current.json \
        --output-mapping  onenote_mapping.json \
        [--prev-manifest _manifest_prev.json]
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="manifest 更新 & mapping.json 生成")
    parser.add_argument("--diff-plan", required=True, help="diff_plan.json のパス")
    parser.add_argument("--pre-knowledge", default="pre-knowledge", help="pre-knowledge ディレクトリ")
    parser.add_argument("--output-manifest", default="_manifest_current.json", help="出力 manifest ファイル名")
    parser.add_argument("--output-mapping", default="onenote_mapping.json", help="出力 mapping.json ファイル名")
    parser.add_argument("--prev-manifest", default=None, help="前回の manifest（unchanged ページの情報を引き継ぐ）")
    args = parser.parse_args()

    plan_path = Path(args.diff_plan)
    pk_dir = Path(args.pre_knowledge).resolve()
    out_manifest = Path(args.output_manifest)
    out_mapping = Path(args.output_mapping)

    if not plan_path.exists():
        print(f"[ERROR] diff_plan.json が見つかりません: {plan_path}")
        sys.exit(1)

    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    # unchanged ページは前回 manifest から引き継ぐ
    prev_by_id: dict = {}
    if args.prev_manifest:
        prev_path = Path(args.prev_manifest)
        if prev_path.exists():
            with open(prev_path, "r", encoding="utf-8") as f:
                prev = json.load(f)
            prev_by_id = {p["page_id"]: p for p in prev.get("pages", [])}

    print("=" * 60)
    print("manifest 更新 & mapping.json 生成")
    print(f"  new: {plan['summary']['new']}, changed: {plan['summary']['changed']}, unchanged: {plan['summary']['unchanged']}")
    print("=" * 60)

    all_pages = []
    mapping = []
    entry_counter = [0]

    def make_entry(page: dict, prev_page: dict | None = None) -> tuple[dict, dict | None]:
        """manifest エントリと mapping エントリを生成する。1ページ = 1エントリ。"""
        entry_counter[0] += 1
        entry_id = f"onenote_{entry_counter[0]:04d}"

        pdf_stem = Path(page.get("pdf_file", "")).stem
        pk_folder = pk_dir / pdf_stem
        formatted_path = pk_folder / "03_formatted_markdown" / "formatted.md"
        transcribed_path = pk_folder / "02_transcribed_markdown" / "transcribed.md"

        content_preview = ""
        if formatted_path.exists():
            text = formatted_path.read_text(encoding="utf-8")
            content_preview = text[:200].replace("\n", " ")
        else:
            if page.get("pdf_file"):
                print(f"  [WARN] formatted.md が見つかりません: {formatted_path}")

        manifest_entry = {
            "page_id": page.get("page_id", ""),
            "notebook": page.get("notebook", ""),
            "section": page.get("section", ""),
            "page_title": page.get("page_title", ""),
            "page_url": page.get("page_url", ""),
            "last_modified_time": page.get("last_modified_time", ""),
            "content_hash": page.get("content_hash", ""),
            "html_file": page.get("html_file_name", page.get("html_file", "")),
            "pdf_file": page.get("pdf_file", ""),
            "chunk_ids": prev_page.get("chunk_ids", [entry_id]) if prev_page else [entry_id],
        }

        if not formatted_path.exists() and prev_page:
            # unchanged ページは mapping を prev から引き継ぐ
            return manifest_entry, None

        mapping_entry = {
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
                    "original_pdf_path": str(Path("input") / page.get("pdf_file", "")),
                    "pre_knowledge_folder": pdf_stem,
                    "transcribed_path": str(transcribed_path),
                    "formatted_path": str(formatted_path),
                    "content_preview": content_preview,
                }
            ],
        }
        return manifest_entry, mapping_entry

    # new ページ
    for page in plan.get("new", []):
        m_entry, map_entry = make_entry(page)
        all_pages.append(m_entry)
        if map_entry:
            mapping.append(map_entry)

    # changed ページ
    for page in plan.get("changed", []):
        prev = prev_by_id.get(page.get("page_id", ""))
        m_entry, map_entry = make_entry(page, prev)
        all_pages.append(m_entry)
        if map_entry:
            mapping.append(map_entry)

    # unchanged ページ（前回 manifest からそのまま引き継ぐ）
    for page in plan.get("unchanged", []):
        page_id = page.get("page_id", "")
        if page_id in prev_by_id:
            all_pages.append(prev_by_id[page_id])
        else:
            m_entry, _ = make_entry(page)
            all_pages.append(m_entry)

    # deleted ページは all_pages に入れない（AI Search からも削除対象）

    # manifest 出力
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_folder": plan.get("run_folder", ""),
        "pages": all_pages,
    }
    with open(out_manifest, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # mapping 出力
    with open(out_mapping, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)

    print()
    print(f"manifest: {out_manifest} ({len(all_pages)} ページ)")
    print(f"mapping:  {out_mapping} ({len(mapping)} エントリ)")
    print()
    print("次のステップ:")
    print(f"  1. {out_manifest} を _manifest_prev.json にリネーム")
    print(f"  2. {out_mapping} をチャットボットプロジェクトの data/onenote/mapping.json に配置")
    if plan["summary"]["deleted"] > 0:
        print(f"  3. 削除ページ {plan['summary']['deleted']} 件を AI Search から削除: python scripts/delete_onenote_chunks.py")


if __name__ == "__main__":
    main()
