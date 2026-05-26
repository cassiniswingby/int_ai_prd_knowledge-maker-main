# -*- coding: utf-8 -*-
"""
OneNote 差分比較スクリプト (差分更新 Step 0)

前回の manifest と今回の PA 出力を比較し、
new / changed / unchanged / deleted に分類して diff_plan.json を生成する。
差分ページ（new + changed）のみ HTML → PDF 変換して input/ に配置する。

Usage:
    python scripts/compare_manifest.py \
        --new-mapping 20260307_onenote/page_html_mapping.json \
        --html-dir    20260307_onenote/ \
        --output-dir  input/ \
        --plan-output diff_plan.json \
        [--prev-manifest _manifest_prev.json]
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows コンソールの cp932 エンコーディング問題を回避
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|]')


def sanitize_filename(name: str) -> str:
    safe = UNSAFE_CHARS.sub("_", name)
    safe = re.sub(r"_+", "_", safe)
    return safe.strip("_")


def build_pdf_filename(page: dict) -> str:
    parts = [
        sanitize_filename(page.get("notebook", "unknown")),
        sanitize_filename(page.get("section", "unknown")),
        sanitize_filename(page.get("page_title", page.get("page_id", "unknown"))),
    ]
    return "__".join(parts) + ".pdf"


def compute_content_hash(html_path: Path) -> str:
    """HTML ファイルの SHA-256 ハッシュを計算する。"""
    sha256 = hashlib.sha256()
    with open(html_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return f"sha256:{sha256.hexdigest()}"


def convert_html_to_pdf(html_path: Path, pdf_path: Path) -> bool:
    """Playwright で HTML を PDF に変換する。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[ERROR] playwright が見つかりません。pip install playwright && playwright install chromium を実行してください。")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(html_path.as_uri())
            page.wait_for_load_state("networkidle")
            page.pdf(
                path=str(pdf_path),
                format="A4",
                margin={"top": "15mm", "right": "15mm", "bottom": "15mm", "left": "15mm"},
                print_background=True,
            )
            return True
        except Exception as e:
            print(f"  [ERROR] PDF変換失敗: {html_path.name} -> {e}")
            return False
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description="OneNote 差分比較")
    parser.add_argument("--new-mapping", required=True, help="今回の page_html_mapping.json のパス")
    parser.add_argument("--html-dir", required=True, help="HTML ファイルが置かれたディレクトリ")
    parser.add_argument("--output-dir", default="input", help="差分 PDF の出力先 (デフォルト: input/)")
    parser.add_argument("--plan-output", default="diff_plan.json", help="差分計画ファイルの出力パス")
    parser.add_argument("--prev-manifest", default=None, help="前回の manifest ファイル (なければ全件 new 扱い)")
    args = parser.parse_args()

    new_mapping_path = Path(args.new_mapping).resolve()
    html_dir = Path(args.html_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    plan_output = Path(args.plan_output)

    if not new_mapping_path.exists():
        print(f"[ERROR] new-mapping が見つかりません: {new_mapping_path}")
        sys.exit(1)

    with open(new_mapping_path, "r", encoding="utf-8") as f:
        new_mapping = json.load(f)

    curr_pages = new_mapping.get("pages", [])

    # 前回 manifest ロード
    prev_pages_by_id: dict = {}
    if args.prev_manifest:
        prev_path = Path(args.prev_manifest)
        if prev_path.exists():
            with open(prev_path, "r", encoding="utf-8") as f:
                prev_manifest = json.load(f)
            prev_pages_by_id = {p["page_id"]: p for p in prev_manifest.get("pages", [])}
            print(f"前回 manifest 読み込み: {len(prev_pages_by_id)} ページ")
        else:
            print(f"[INFO] 前回 manifest が見つかりません: {prev_path} → 全件 new 扱い")
    else:
        print("[INFO] --prev-manifest 未指定 → 全件 new 扱い")

    output_dir.mkdir(parents=True, exist_ok=True)

    new_list = []
    changed_list = []
    unchanged_list = []
    deleted_list = []

    print("=" * 60)
    print("差分比較")
    print(f"  今回ページ数: {len(curr_pages)}")
    print(f"  前回ページ数: {len(prev_pages_by_id)}")
    print("=" * 60)

    curr_ids = set()

    for page in curr_pages:
        page_id = page.get("page_id", "")
        if not page_id:
            print(f"  [WARN] page_id がありません: {page.get('page_title', '')}")
            continue

        curr_ids.add(page_id)

        html_file_name = page.get("html_file_name", "")
        html_path = html_dir / html_file_name

        if not html_path.exists():
            print(f"  [WARN] HTML が見つかりません: {html_file_name}")
            continue

        content_hash = compute_content_hash(html_path)
        pdf_name = build_pdf_filename(page)

        enriched = {
            **page,
            "content_hash": content_hash,
            "pdf_file": pdf_name,
        }

        if page_id not in prev_pages_by_id:
            new_list.append(enriched)
            status = "NEW"
        elif prev_pages_by_id[page_id].get("content_hash") != content_hash:
            changed_list.append(enriched)
            status = "CHANGED"
        else:
            unchanged_list.append(enriched)
            status = "UNCHANGED"

        print(f"  [{status}] {page.get('notebook', '')} / {page.get('section', '')} / {page.get('page_title', '')}")

    # 削除判定
    for page_id, page in prev_pages_by_id.items():
        if page_id not in curr_ids:
            deleted_list.append(page)
            print(f"  [DELETED] {page.get('notebook', '')} / {page.get('section', '')} / {page.get('page_title', '')}")

    print()
    print(f"new: {len(new_list)}, changed: {len(changed_list)}, unchanged: {len(unchanged_list)}, deleted: {len(deleted_list)}")

    # 差分ページ（new + changed）を PDF に変換
    targets = new_list + changed_list
    if targets:
        print(f"\n差分 PDF 変換開始: {len(targets)} ページ")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            pw_page = browser.new_page()

            for i, page in enumerate(targets, 1):
                html_file_name = page.get("html_file_name", "")
                html_path = html_dir / html_file_name
                pdf_path = output_dir / page["pdf_file"]

                print(f"  [{i}/{len(targets)}] {html_file_name} → {page['pdf_file']}")

                try:
                    pw_page.goto(html_path.as_uri())
                    pw_page.wait_for_load_state("networkidle")
                    pw_page.pdf(
                        path=str(pdf_path),
                        format="A4",
                        margin={"top": "15mm", "right": "15mm", "bottom": "15mm", "left": "15mm"},
                        print_background=True,
                    )
                except Exception as e:
                    print(f"  [ERROR] PDF変換失敗: {e}")

            browser.close()
    else:
        print("\n差分ページなし。PDF 変換をスキップ。")

    # diff_plan.json 出力
    plan = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_folder": new_mapping.get("run_folder", ""),
        "summary": {
            "new": len(new_list),
            "changed": len(changed_list),
            "unchanged": len(unchanged_list),
            "deleted": len(deleted_list),
        },
        "new": new_list,
        "changed": changed_list,
        "unchanged": unchanged_list,
        "deleted": deleted_list,
    }

    with open(plan_output, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(f"\ndiff_plan.json: {plan_output}")
    print("完了")


if __name__ == "__main__":
    main()
