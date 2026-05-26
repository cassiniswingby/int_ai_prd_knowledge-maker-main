"""
OneNote PDF 品質チェックツール

## コマンド一覧

### check  -- 全PDFの品質チェック（白ボックス検出 + サイズ/テキスト検査）
  python scripts/quality_check.py check \\
      --manifest data/onenote_XXX/manifest.json

  → quality_check/auto_check_report.txt にレポートを出力
  → quality_check/check_results.json に機械判定結果を保存（mark コマンドで参照）

### mark  -- NG判定ページを manifest に status=retry として書き戻し
  python scripts/quality_check.py mark \\
      --manifest data/onenote_XXX/manifest.json

  → check_results.json の WHITE_BOX / EMPTY / TINY を status='retry' に更新
  → 次の fetch --retry の対象になる

## 白ボックス検出アルゴリズム

  PDF 1ページ目を低解像度（50%）でレンダリングし、左30%の白色率で判定:
  - Left30% white ratio > 90% かつ テキスト文字数 > 30  → WHITE_BOX（ナビパネル残留）
  - Left30% white ratio > 95% かつ テキスト文字数 ≤ 30  → EMPTY（コンテンツ未ロード）
  - ファイルサイズ < 5KB                                  → TINY（ほぼ空）
  - それ以外                                              → CLEAN

  ※ 閾値90%は料金T業務ノートブック60件の実測で検証済み
     (WHITE_BOX群: 90.6%〜98.1%, CLEAN群: 64.3%〜89.4% と明確に分離)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False
    print("[WARN] PyMuPDF が見つかりません。白ボックス検出とテキスト抽出は省略されます。")
    print("       pip install PyMuPDF でインストールしてください。")

try:
    import numpy as np
    from PIL import Image
    HAS_PIXEL = True
except ImportError:
    HAS_PIXEL = False
    print("[WARN] numpy / Pillow が見つかりません。白ボックス検出は省略されます。")
    print("       pip install numpy Pillow でインストールしてください。")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 白ボックス検出の閾値
WHITE_BOX_THRESHOLD = 0.90   # left30% 白色率 > 90% → WHITE_BOX
EMPTY_THRESHOLD = 0.95       # left30% 白色率 > 95% かつ テキスト少 → EMPTY
TINY_SIZE_KB = 5             # ファイルサイズ < 5KB → TINY


def detect_white_box(pdf_path: Path) -> tuple[str, float, int]:
    """PDF の1ページ目を低解像度レンダリングして白ボックスを検出する。

    Returns:
        tuple: (verdict, white_ratio, text_chars)
            verdict: "CLEAN" / "WHITE_BOX" / "EMPTY" / "TINY" / "ERROR" / "NO_FITZ"
            white_ratio: 左30%の白色率 (0.0〜1.0)、計算不可の場合は 0.0
            text_chars: テキスト文字数
    """
    size_kb = pdf_path.stat().st_size / 1024
    if size_kb < TINY_SIZE_KB:
        return ("TINY", 0.0, 0)

    if not HAS_FITZ:
        return ("NO_FITZ", 0.0, 0)

    try:
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        text_chars = len(page.get_text().strip())

        if not HAS_PIXEL:
            doc.close()
            return ("NO_PIXEL", 0.0, text_chars)

        pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        arr = np.array(img)

        left30 = arr[:, :int(pix.width * 0.30), :]
        white_ratio = float(np.all(left30 > 248, axis=2).mean())

        doc.close()

        if white_ratio > WHITE_BOX_THRESHOLD and text_chars > 30:
            return ("WHITE_BOX", white_ratio, text_chars)
        elif white_ratio > EMPTY_THRESHOLD and text_chars <= 30:
            return ("EMPTY", white_ratio, text_chars)
        else:
            return ("CLEAN", white_ratio, text_chars)

    except Exception as e:
        return ("ERROR", 0.0, 0)


def run_check(manifest_file: Path) -> list[dict]:
    """全PDFの品質チェックを実施してレポートを出力する。

    Args:
        manifest_file: manifest.json のパス

    Returns:
        チェック結果のリスト
    """
    if not manifest_file.exists():
        print(f"[ERROR] manifest が見つかりません: {manifest_file}")
        return []

    output_dir = manifest_file.parent
    pdf_dir = output_dir / "pdfs"
    quality_dir = output_dir / "quality_check"
    quality_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

    # manifest から done ページの PDF パスを収集
    done_pages: list[dict] = []
    for sec in manifest.get("sections", []):
        for pg in sec.get("pages", []):
            if pg.get("status") == "done" and pg.get("pdf_path"):
                pdf_path = output_dir / pg["pdf_path"]
                done_pages.append({
                    "section": sec.get("name", ""),
                    "title": pg.get("title", ""),
                    "pdf_path": str(pdf_path),
                    "pdf_name": pdf_path.name,
                    "page_status": pg.get("status"),
                })

    # pdfs/ フォルダのファイルも直接スキャン（manifest 外のファイルも拾う）
    pdf_files_in_dir = sorted(pdf_dir.glob("*.pdf")) if pdf_dir.exists() else []

    # manifest ベースのリストと dir スキャンをマージ（重複排除）
    manifest_names = {p["pdf_name"] for p in done_pages}
    extra_files = [f for f in pdf_files_in_dir if f.name not in manifest_names]

    all_targets: list[dict] = done_pages + [
        {"section": "", "title": f.stem, "pdf_path": str(f), "pdf_name": f.name, "page_status": "done"}
        for f in extra_files
    ]

    print(f"[CHECK] 対象: {len(all_targets)} ファイル")
    if not HAS_FITZ or not HAS_PIXEL:
        print("[CHECK] 白ボックス検出は利用不可（依存ライブラリ不足）。サイズ/テキストのみチェックします。")

    results: list[dict] = []
    counts = {"CLEAN": 0, "WHITE_BOX": 0, "EMPTY": 0, "TINY": 0, "ERROR": 0, "NO_FITZ": 0, "NO_PIXEL": 0}

    for i, target in enumerate(all_targets, 1):
        pdf_path = Path(target["pdf_path"])
        if not pdf_path.exists():
            record = {**target, "verdict": "MISSING", "white_ratio": 0.0, "text_chars": 0, "size_kb": 0.0}
            results.append(record)
            counts["ERROR"] = counts.get("ERROR", 0) + 1
            continue

        size_kb = pdf_path.stat().st_size / 1024
        verdict, white_ratio, text_chars = detect_white_box(pdf_path)

        record = {
            **target,
            "verdict": verdict,
            "white_ratio": round(white_ratio, 3),
            "text_chars": text_chars,
            "size_kb": round(size_kb, 1),
        }
        results.append(record)
        counts[verdict] = counts.get(verdict, 0) + 1

        if i % 50 == 0:
            print(f"  ... {i}/{len(all_targets)} 完了")

    total = len(results)
    ng_count = counts.get("WHITE_BOX", 0) + counts.get("EMPTY", 0) + counts.get("TINY", 0) + counts.get("ERROR", 0)
    ng_rate = ng_count / total * 100 if total > 0 else 0.0

    # レポート出力
    report_path = quality_dir / "auto_check_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("=" * 60 + "\n")
        f.write("OneNote PDF 品質チェックレポート\n")
        f.write(f"実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"対象ファイル数: {total}\n")
        f.write("=" * 60 + "\n\n")

        f.write("【サマリー】\n")
        f.write(f"  CLEAN     : {counts.get('CLEAN', 0):4d} 件\n")
        f.write(f"  WHITE_BOX : {counts.get('WHITE_BOX', 0):4d} 件  ← ナビパネルが白箱で左列を覆う\n")
        f.write(f"  EMPTY     : {counts.get('EMPTY', 0):4d} 件  ← コンテンツが読み込まれていない\n")
        f.write(f"  TINY      : {counts.get('TINY', 0):4d} 件  ← ファイルが {TINY_SIZE_KB}KB 未満（ほぼ空）\n")
        f.write(f"  ERROR     : {counts.get('ERROR', 0) + counts.get('MISSING', 0):4d} 件\n")
        f.write(f"  NG合計    : {ng_count:4d} 件 ({ng_rate:.1f}%)\n\n")

        if counts.get("WHITE_BOX", 0) > 0:
            f.write("【WHITE_BOX 判定ファイル（要リトライ）】\n")
            for r in results:
                if r["verdict"] == "WHITE_BOX":
                    f.write(f"  {r['pdf_name']}: left30%={r['white_ratio']:.1%}, size={r['size_kb']}KB, text={r['text_chars']}文字\n")
            f.write("\n")

        if counts.get("EMPTY", 0) > 0:
            f.write("【EMPTY 判定ファイル（要リトライ）】\n")
            for r in results:
                if r["verdict"] == "EMPTY":
                    f.write(f"  {r['pdf_name']}: left30%={r['white_ratio']:.1%}, size={r['size_kb']}KB\n")
            f.write("\n")

        if counts.get("TINY", 0) > 0:
            f.write(f"【TINY 判定ファイル（{TINY_SIZE_KB}KB 未満、要確認）】\n")
            for r in results:
                if r["verdict"] == "TINY":
                    f.write(f"  {r['pdf_name']}: size={r['size_kb']}KB\n")
            f.write("\n")

        f.write("【全ファイル一覧】\n")
        f.write(f"  {'ファイル名':<55} {'判定':<10} {'left30%':>7} {'サイズ':>8} {'テキスト':>8}\n")
        f.write("  " + "-" * 96 + "\n")
        for r in sorted(results, key=lambda x: (-x.get("white_ratio", 0))):
            name = r["pdf_name"][:54]
            verdict = r["verdict"]
            wr = f"{r['white_ratio']:.1%}" if r.get("white_ratio") else "  n/a  "
            sz = f"{r['size_kb']}KB"
            tx = f"{r['text_chars']}文字" if r.get("text_chars") is not None else "  n/a"
            f.write(f"  {name:<55} {verdict:<10} {wr:>7} {sz:>8} {tx:>8}\n")

    # 機械判定結果を JSON 保存（mark コマンドで参照）
    results_json_path = quality_dir / "check_results.json"
    results_json_path.write_text(
        json.dumps({"checked_at": datetime.now().isoformat(), "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"\n[CHECK] 完了")
    print(f"  CLEAN     : {counts.get('CLEAN', 0)} 件")
    print(f"  WHITE_BOX : {counts.get('WHITE_BOX', 0)} 件")
    print(f"  EMPTY     : {counts.get('EMPTY', 0)} 件")
    print(f"  TINY      : {counts.get('TINY', 0)} 件")
    print(f"  ERROR     : {counts.get('ERROR', 0)} 件")
    print(f"  NG合計    : {ng_count} 件 ({ng_rate:.1f}%)")
    print(f"\n  レポート  : {report_path}")
    print(f"  結果JSON  : {results_json_path}")

    if ng_count > 0:
        print(f"\n  [次のステップ] NG {ng_count} 件を manifest に retry として記録:")
        print(f"    python scripts/quality_check.py mark --manifest {manifest_file}")

    return results


def run_mark(manifest_file: Path) -> int:
    """check_results.json の NG 判定を manifest の status='retry' として書き戻す。

    Args:
        manifest_file: manifest.json のパス

    Returns:
        retry に設定した件数
    """
    if not manifest_file.exists():
        print(f"[ERROR] manifest が見つかりません: {manifest_file}")
        return 0

    output_dir = manifest_file.parent
    quality_dir = output_dir / "quality_check"
    results_json_path = quality_dir / "check_results.json"

    if not results_json_path.exists():
        print(f"[ERROR] check_results.json が見つかりません: {results_json_path}")
        print("  先に quality_check.py check を実行してください。")
        return 0

    results_data = json.loads(results_json_path.read_text(encoding="utf-8"))
    check_results = results_data.get("results", [])

    # NG 判定のファイル名セットを作成
    ng_verdicts = {"WHITE_BOX", "EMPTY", "TINY"}
    ng_names: set[str] = {r["pdf_name"] for r in check_results if r.get("verdict") in ng_verdicts}

    print(f"[MARK] NG 判定ファイル: {len(ng_names)} 件 → status='retry' に更新")
    if not ng_names:
        print("  NG 判定がありません。リトライ対象なし。")
        return 0

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

    marked = 0
    for sec in manifest.get("sections", []):
        for pg in sec.get("pages", []):
            if pg.get("pdf_path"):
                pdf_name = Path(pg["pdf_path"]).name
                if pdf_name in ng_names and pg.get("status") == "done":
                    pg["status"] = "retry"
                    marked += 1

    manifest["updated_at"] = datetime.now().isoformat()
    manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"[MARK] {marked} 件を status='retry' に更新しました")
    print(f"  [次のステップ] リトライを実行:")
    print(f"    python scripts/fetch_onenote.py fetch --manifest {manifest_file} --retry")
    return marked


def run_samples(manifest_file: Path) -> int:
    """quality_check/ に各セクション代表・最小・最大のサンプルをコピーする。

    Args:
        manifest_file: manifest.json のパス

    Returns:
        コピーしたファイル数
    """
    if not manifest_file.exists():
        print(f"[ERROR] manifest が見つかりません: {manifest_file}")
        return 0

    output_dir = manifest_file.parent
    pdf_dir = output_dir / "pdfs"
    quality_dir = output_dir / "quality_check"
    quality_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

    copied_names: set[str] = set()
    copy_list: list[tuple[Path, Path]] = []

    # 各セクションの代表ページ（中央付近）
    for sec in manifest.get("sections", []):
        pages_done = [pg for pg in sec.get("pages", []) if pg.get("status") == "done" and pg.get("pdf_path")]
        if not pages_done:
            continue
        mid_pg = pages_done[len(pages_done) // 2]
        pdf_src = output_dir / mid_pg["pdf_path"]
        if pdf_src.exists() and pdf_src.name not in copied_names:
            dst = quality_dir / f"sample_{pdf_src.name}"
            copy_list.append((pdf_src, dst))
            copied_names.add(pdf_src.name)

    # サイズ最小5件と最大5件
    pdf_files = sorted(pdf_dir.glob("*.pdf")) if pdf_dir.exists() else []
    size_sorted = sorted(pdf_files, key=lambda f: f.stat().st_size)
    for pdf_src in size_sorted[:5]:
        if pdf_src.name not in copied_names:
            copy_list.append((pdf_src, quality_dir / f"smallest_{pdf_src.name}"))
            copied_names.add(pdf_src.name)
    for pdf_src in size_sorted[-5:]:
        if pdf_src.name not in copied_names:
            copy_list.append((pdf_src, quality_dir / f"largest_{pdf_src.name}"))
            copied_names.add(pdf_src.name)

    count = 0
    for src, dst in copy_list:
        shutil.copy2(src, dst)
        count += 1

    print(f"[SAMPLES] {count} 件のサンプルを quality_check/ にコピーしました")
    return count


def main():
    parser = argparse.ArgumentParser(description="OneNote PDF 品質チェックツール")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="全PDFの品質チェック（白ボックス検出）")
    p_check.add_argument("--manifest", required=True, help="manifest.json のパス")

    p_mark = sub.add_parser("mark", help="NG判定を manifest に status=retry として書き戻し")
    p_mark.add_argument("--manifest", required=True, help="manifest.json のパス")

    p_samples = sub.add_parser("samples", help="quality_check/ にサンプルPDFをコピー")
    p_samples.add_argument("--manifest", required=True, help="manifest.json のパス")

    args = parser.parse_args()

    if args.command == "check":
        run_check(Path(args.manifest))

    elif args.command == "mark":
        run_mark(Path(args.manifest))

    elif args.command == "samples":
        run_samples(Path(args.manifest))


if __name__ == "__main__":
    main()
