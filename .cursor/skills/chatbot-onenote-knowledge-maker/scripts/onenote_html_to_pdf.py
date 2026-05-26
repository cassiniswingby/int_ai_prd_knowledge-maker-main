# -*- coding: utf-8 -*-
"""
OneNote HTML → PDF 変換スクリプト (Step 0)

PA フロー出力の page_html_mapping.json と HTML ファイル群を読み込み、
Playwright で各 HTML を PDF に変換して KM の input/ に配置する。

Usage:
    python scripts/onenote_html_to_pdf.py \
        --mapping path/to/page_html_mapping.json \
        --html-dir path/to/yyyymmdd_onenote/ \
        --output input/
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows コンソールの cp932 エンコーディング問題を回避
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


try:
    from playwright.sync_api import Page as PlaywrightPage
except ImportError:
    PlaywrightPage = object  # type: ignore

UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|]')


def sanitize_filename(name: str) -> str:
    """ファイル名に使えない文字を _ に置換し、連続 _ を正規化する。"""
    safe = UNSAFE_CHARS.sub("_", name)
    safe = re.sub(r"_+", "_", safe)
    return safe.strip("_")


def build_pdf_filename(page: dict) -> str:
    """notebook__section__page_title.pdf 形式のファイル名を生成する。"""
    parts = [
        sanitize_filename(page.get("notebook", "unknown")),
        sanitize_filename(page.get("section", "unknown")),
        sanitize_filename(page.get("page_title", page.get("page_id", "unknown"))),
    ]
    return "__".join(parts) + ".pdf"


def convert_html_to_pdf(html_path: Path, pdf_path: Path, page: PlaywrightPage) -> bool:
    """Playwright で HTML を PDF に変換する。"""
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


def main():
    parser = argparse.ArgumentParser(description="OneNote HTML → PDF 変換")
    parser.add_argument("--mapping", required=True, help="page_html_mapping.json のパス")
    parser.add_argument("--html-dir", required=True, help="HTML ファイルが置かれたディレクトリ")
    parser.add_argument("--output", default="input", help="PDF 出力先 (デフォルト: input/)")
    args = parser.parse_args()

    mapping_path = Path(args.mapping).resolve()
    html_dir = Path(args.html_dir).resolve()
    output_dir = Path(args.output).resolve()

    if not mapping_path.exists():
        print(f"[ERROR] mapping ファイルが見つかりません: {mapping_path}")
        sys.exit(1)

    with open(mapping_path, "r", encoding="utf-8") as f:
        mapping_data = json.load(f)

    pages = mapping_data.get("pages", [])
    if not pages:
        print("[WARN] pages が空です。処理するページがありません。")
        sys.exit(0)

    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("OneNote HTML → PDF 変換 (Step 0)")
    print(f"  入力: {mapping_path}")
    print(f"  HTML: {html_dir}")
    print(f"  出力: {output_dir}")
    print(f"  ページ数: {len(pages)}")
    print("=" * 60)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[ERROR] playwright が見つかりません。pip install playwright && playwright install chromium を実行してください。")
        sys.exit(1)

    manifest_pages = []
    success_count = 0
    fail_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pw_page = browser.new_page()

        for i, page_info in enumerate(pages, 1):
            html_file_name = page_info.get("html_file_name", "")
            html_path = html_dir / html_file_name

            if not html_path.exists():
                print(f"  [{i}/{len(pages)}] SKIP: HTML が見つかりません: {html_file_name}")
                fail_count += 1
                continue

            pdf_name = build_pdf_filename(page_info)
            pdf_path = output_dir / pdf_name

            print(f"  [{i}/{len(pages)}] {html_file_name} → {pdf_name}")

            ok = convert_html_to_pdf(html_path, pdf_path, pw_page)
            if ok:
                success_count += 1
                manifest_pages.append({
                    "notebook": page_info.get("notebook", ""),
                    "section": page_info.get("section", ""),
                    "page_title": page_info.get("page_title", ""),
                    "page_id": page_info.get("page_id", ""),
                    "page_url": page_info.get("page_url", ""),
                    "html_file": html_file_name,
                    "pdf_file": pdf_name,
                    "pdf_path": str(output_dir / pdf_name),
                })
            else:
                fail_count += 1

        browser.close()

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_mapping": str(mapping_path),
        "pages": manifest_pages,
    }
    manifest_path = output_dir / "_onenote_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print()
    print(f"完了: 成功 {success_count} / 失敗 {fail_count} / 合計 {len(pages)}")
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
