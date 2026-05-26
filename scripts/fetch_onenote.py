"""
OneNote PDF フェッチツール

## コマンド一覧

### fastdiscover  -- ページURL一覧の収集（最初に1回実行）
  python scripts/fetch_onenote.py fastdiscover \\
      --url "<OneNote の URL>" \\
      --output data/onenote_XXX
  → data/onenote_XXX/manifest.json を生成（所要: ~10分）

### fetch  -- PDF を一括取得
  # Pass 1: 高速バルクフェッチ（tabs=12 推奨、~70分）
  python scripts/fetch_onenote.py fetch \\
      --manifest data/onenote_XXX/manifest.json \\
      --tabs 12

  # Pass 2: 品質チェックでNGとなったページのリトライ（低速・確実）
  python scripts/fetch_onenote.py fetch \\
      --manifest data/onenote_XXX/manifest.json \\
      --retry
  → --retry 時: status='retry' のページのみ対象、tabs=2, quiet_ms=3000ms

## マルチパス運用フロー（詳細は scripts/README.md 参照）

  Step 1: fastdiscover         (~10分)
  Step 2: fetch                (~70分)
  Step 3: quality_check check  (~3分) → NGを自動検出
  Step 4: quality_check mark   (~1分) → manifest に status=retry を書き込み
  Step 5: fetch --retry        (~20分)
  Step 6: Step 3-5 を繰り返してNGが0になったら完了

## 認証について

  - 初回は Edge ブラウザが開く。SharePoint/OneNote で社内 SSO ログインしてください。
  - Cookie は data/.onenote_cookies.json に保存され、次回以降は自動ログインされます。
  - Cookie の有効期限は数時間です。期限切れの場合は再度ブラウザが開きます。
"""

from __future__ import annotations

import asyncio
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COOKIE_FILE = PROJECT_ROOT / "data" / ".onenote_cookies.json"

DEFAULT_URL = (
    "https://tokyogasgroup-my.sharepoint.com/personal/"
    "karen-hotta_tokyo-gas_co_jp/_layouts/15/Doc.aspx"
    "?sourcedoc={6f52bf99-8b45-4419-a454-4ecbe1e769d1}"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "onenote_discover_fetch"

PDF_MARGIN = {"top": "4mm", "right": "4mm", "bottom": "4mm", "left": "4mm"}
PDF_SCALE = 0.7
DEFAULT_VP_HEIGHT = 900

_log_handle = None


def log(msg: str):
    print(msg, flush=True)
    if _log_handle:
        _log_handle.write(msg + "\n")
        _log_handle.flush()


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|\n\r\t]', "_", name).strip()[:120]


def load_cookies(context) -> bool:
    if COOKIE_FILE.exists():
        cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        context.add_cookies(cookies)
        log(f"[COOKIE] {len(cookies)} cookies 復元")
        return True
    log("[COOKIE] Cookie なし")
    return False


def save_cookies(context):
    cookies = context.cookies()
    COOKIE_FILE.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[COOKIE] {len(cookies)} cookies 保存")


async def load_cookies_async(context) -> bool:
    if COOKIE_FILE.exists():
        cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        await context.add_cookies(cookies)
        log(f"[COOKIE] {len(cookies)} cookies 復元")
        return True
    log("[COOKIE] Cookie なし")
    return False


async def save_cookies_async(context):
    cookies = await context.cookies()
    COOKIE_FILE.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[COOKIE] {len(cookies)} cookies 保存")


def get_inner_frame(page):
    for f in page.frames:
        if f.name == "WebApplicationFrame":
            return f
    return None


def wait_for_ready(page, timeout_sec=90) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        inner = get_inner_frame(page)
        if inner:
            try:
                ti_count = inner.evaluate('() => document.querySelectorAll(\'[role="treeitem"]\').length')
                has_main = inner.evaluate('() => document.querySelectorAll(\'[role="main"]\').length > 0')
                if ti_count > 0 and has_main:
                    return True
            except Exception:
                pass
        time.sleep(2)
    return False


def wait_for_main(page, timeout_sec=20) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        inner = get_inner_frame(page)
        if not inner:
            time.sleep(0.5)
            continue
        try:
            has_main = inner.evaluate('() => document.querySelectorAll(\'[role="main"]\').length > 0')
            if has_main:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


async def wait_for_ready_async(page, timeout_sec=90) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        inner = get_inner_frame(page)
        if inner:
            try:
                ti_count = await inner.evaluate('() => document.querySelectorAll(\'[role="treeitem"]\').length')
                has_main = await inner.evaluate('() => document.querySelectorAll(\'[role="main"]\').length > 0')
                if ti_count > 0 and has_main:
                    return True
            except Exception:
                pass
        await asyncio.sleep(2)
    return False


async def wait_for_main_async(page, timeout_sec=20) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        inner = get_inner_frame(page)
        if not inner:
            await asyncio.sleep(0.5)
            continue
        try:
            has_main = await inner.evaluate('() => document.querySelectorAll(\'[role="main"]\').length > 0')
            if has_main:
                return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False


async def wait_for_content_stable_async(page, quiet_ms: int = 1500, max_ms: int = 15000) -> bool:
    """DOM 変更静止 + 画像ロード完了まで待機する。

    Phase 1: MutationObserver で [role=main] 配下の DOM 挿入が静止するまで待つ。
    Phase 2: DOM 静止後、未ロードの <img> 要素がすべて complete になるまで待つ。

    Args:
        page: Playwright Page オブジェクト
        quiet_ms: DOM 変更停止とみなすまでの無音時間 (ms)
        max_ms: 最大待機時間 (ms)。超過しても True を返して続行する

    Returns:
        True (常に続行。タイムアウトした場合も True を返す)
    """
    inner = get_inner_frame(page)
    if not inner:
        return True

    # Phase 1: DOM 変更静止
    try:
        await inner.evaluate(
            f"""
            () => new Promise(resolve => {{
                const main = document.querySelector('[role="main"]');
                if (!main) {{ resolve(true); return; }}
                let quietTimer = null;
                const QUIET_MS = {quiet_ms};
                const maxTimer = setTimeout(() => {{
                    observer.disconnect();
                    resolve(true);
                }}, {max_ms});
                const settle = () => {{
                    clearTimeout(quietTimer);
                    quietTimer = setTimeout(() => {{
                        observer.disconnect();
                        clearTimeout(maxTimer);
                        resolve(true);
                    }}, QUIET_MS);
                }};
                const observer = new MutationObserver(settle);
                observer.observe(main, {{ childList: true, subtree: true }});
                settle();
            }})
            """
        )
    except Exception:
        pass

    # Phase 2: 画像ロード完了待機
    try:
        await inner.evaluate(
            """
            () => new Promise(resolve => {
                const main = document.querySelector('[role="main"]');
                if (!main) { resolve(true); return; }
                const imgs = [...main.querySelectorAll('img')];
                const pending = imgs.filter(i => !i.complete && i.src);
                if (pending.length === 0) { resolve(true); return; }

                const MAX_IMG_MS = 10000;
                let done = false;
                const finish = () => {
                    if (done) return;
                    done = true;
                    resolve(true);
                };
                setTimeout(finish, MAX_IMG_MS);

                let remaining = pending.length;
                const tick = () => { if (--remaining <= 0) finish(); };
                pending.forEach(img => {
                    img.addEventListener('load', tick, {once: true});
                    img.addEventListener('error', tick, {once: true});
                });
            })
            """
        )
    except Exception:
        pass

    return True


def decode_wd(url: str) -> dict:
    m = re.search(r"[?&]wd=([^&]+)", url)
    if not m:
        return {}
    wd = unquote(m.group(1))
    tm = re.match(r"target\((.+?)\.one\|([^/]+)/(.+?)\|([^/]+)/\)", wd)
    if tm:
        return {
            "section_name": tm.group(1),
            "section_guid": tm.group(2),
            "page_title": tm.group(3),
            "page_guid": tm.group(4),
        }
    return {"raw": wd}


def get_sections(inner_frame) -> list[dict]:
    return inner_frame.evaluate(
        """
        () => {
            const items = document.querySelectorAll('[role="treeitem"]');
            return Array.from(items)
              .map((el, i) => ({
                  index: i,
                  text: (el.innerText || '').trim().slice(0, 120),
                  className: (el.className || ''),
                  isSection: (el.className || '').includes('sectionItem')
              }))
              .filter(x => x.isSection);
        }
        """
    )


def get_page_items(inner_frame) -> list[dict]:
    return inner_frame.evaluate(
        """
        () => {
            const items = document.querySelectorAll('[class*="pageItem"]');
            return Array.from(items).map((el, i) => ({
                index: i,
                text: (el.innerText || '').trim().slice(0, 120),
                role: el.getAttribute('role'),
                className: (el.className || '').slice(0, 200),
            }));
        }
        """
    )


def click_section(inner_frame, section_index: int):
    inner_frame.evaluate(
        f'() => document.querySelectorAll(\'[role="treeitem"]\')[{section_index}]?.click()'
    )


def click_page_item(inner_frame, page_idx: int):
    inner_frame.evaluate(
        f'() => document.querySelectorAll(\'[class*="pageItem"]\')[{page_idx}]?.click()'
    )


def wait_for_section_url(page, expected_section_name: str, timeout_sec: int = 10) -> bool:
    """
    URL (wd target) が期待セクション名に切り替わるまで待つ。
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        wd = decode_wd(page.url)
        current = wd.get("section_name", "")
        if current == expected_section_name:
            return True
        time.sleep(0.4)
    return False


def wait_page_items_updated(inner_frame, prev_first_text: str, timeout_sec: int = 10) -> list[dict]:
    """
    セクション切替後の pageItem 更新待機。
    前セクションの stale list を誤読しないため、先頭テキストの変化を待つ。
    """
    deadline = time.time() + timeout_sec
    last_items: list[dict] = []
    while time.time() < deadline:
        items = get_page_items(inner_frame)
        last_items = items
        if not items:
            time.sleep(0.4)
            continue
        first = items[0].get("text", "")
        if not prev_first_text:
            return items
        if first != prev_first_text:
            return items
        time.sleep(0.4)
    return last_items


async def scroll_and_measure_async(inner_frame) -> int:
    prev_sh = 0
    stable = 0
    for _ in range(100):
        state = await inner_frame.evaluate(
            """
            () => {
                const m = document.querySelector('[role="main"]');
                if (!m) return { sh: 0, atBottom: true };
                m.scrollTop += m.clientHeight * 0.8;
                return {
                    sh: m.scrollHeight,
                    atBottom: m.scrollTop + m.clientHeight >= m.scrollHeight - 10
                };
            }
            """
        )
        sh = state.get("sh", 0)
        if sh == prev_sh:
            stable += 1
        else:
            stable = 0
        prev_sh = sh
        if state.get("atBottom") and stable >= 2:
            break
        await asyncio.sleep(0.3)

    await inner_frame.evaluate(
        '() => { const m = document.querySelector(\'[role="main"]\'); if (m) m.scrollTop = 0; }'
    )
    await asyncio.sleep(0.3)
    return await inner_frame.evaluate(
        '() => { const m = document.querySelector(\'[role="main"]\'); return m ? m.scrollHeight : 0; }'
    )


async def expand_containers_async(page, inner_frame, target_h: int):
    await inner_frame.evaluate(
        f"""
        () => {{
            const h = {target_h};
            const m = document.querySelector('[role="main"]');
            if (!m) return;
            m.style.setProperty('overflow', 'visible', 'important');
            m.style.setProperty('height', h + 'px', 'important');
            m.style.setProperty('max-height', h + 'px', 'important');
            let el = m.parentElement;
            while (el && el !== document.documentElement) {{
                el.style.setProperty('overflow', 'visible', 'important');
                el.style.setProperty('height', h + 'px', 'important');
                el.style.setProperty('max-height', h + 'px', 'important');
                el.style.setProperty('min-height', h + 'px', 'important');
                el = el.parentElement;
            }}
        }}
        """
    )
    await page.evaluate(
        f"""
        () => {{
            const h = {target_h + 200};
            const iframe = document.getElementById('WebApplicationFrame');
            if (!iframe) return;
            iframe.style.setProperty('height', h + 'px', 'important');
            iframe.style.setProperty('min-height', h + 'px', 'important');
            iframe.style.setProperty('max-height', h + 'px', 'important');
            iframe.style.setProperty('overflow', 'visible', 'important');
        }}
        """
    )


async def reset_layout_async(page, inner_frame):
    await page.set_viewport_size({"width": 1440, "height": DEFAULT_VP_HEIGHT})
    try:
        await inner_frame.evaluate(
            """
            () => {
                const m = document.querySelector('[role="main"]');
                if (!m) return;
                m.style.removeProperty('overflow');
                m.style.removeProperty('height');
                m.style.removeProperty('max-height');
                let el = m.parentElement;
                while (el && el !== document.documentElement) {
                    el.style.removeProperty('overflow');
                    el.style.removeProperty('height');
                    el.style.removeProperty('max-height');
                    el.style.removeProperty('min-height');
                    el = el.parentElement;
                }
            }
            """
        )
        await page.evaluate(
            """
            () => {
                const iframe = document.getElementById('WebApplicationFrame');
                if (!iframe) return;
                iframe.style.removeProperty('height');
                iframe.style.removeProperty('min-height');
                iframe.style.removeProperty('max-height');
                iframe.style.removeProperty('overflow');
            }
            """
        )
    except Exception:
        pass


async def _wait_layout_stable(page, timeout_ms: int = 800) -> None:
    """ネットワークアイドル or タイムアウトまで待機（固定 sleep の代替）"""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


async def _verify_cleanup_applied(inner_frame) -> None:
    """CSS injection 後にナビパネルが非表示になったことを検証する。

    OneNote の print CSS 適用がレースし、CSS injection 直後にナビパネルが
    再表示されるケースに対処するため、visible なナビパネルが残っていれば
    inline style で直接非表示にする。
    """
    try:
        await inner_frame.evaluate("""
        () => {
            const navSelectors = [
                '#applicationWACNavigationPanel',
                '#NavPane',
                '#navpaneFocusZone',
                '[class*="wacNavPane"]',
                '[class*="navPane__"]',
                '[class*="wacNavTwoPane"]',
                '[class*="NavRail"]'
            ];
            for (const sel of navSelectors) {
                const els = document.querySelectorAll(sel);
                els.forEach(el => {
                    const cs = getComputedStyle(el);
                    if (cs.display !== 'none' && cs.visibility !== 'hidden') {
                        el.style.setProperty('display', 'none', 'important');
                        el.style.setProperty('visibility', 'hidden', 'important');
                    }
                });
            }
            // canvas 要素も直接非表示
            document.querySelectorAll('canvas').forEach(c => {
                c.style.setProperty('display', 'none', 'important');
            });
        }
        """)
    except Exception:
        pass


async def _inject_print_cleanup_css(inner_frame) -> None:
    """printモード時にナビゲーションパネル・編集UIを非表示にする。

    DOM調査で判明した白箱問題の原因:
    - #applicationWACNavigationPanel / #NavPane: 426px幅のナビペインが
      printモードでWACViewPanelの下に残り、コンテンツ左列を覆う
    - <canvas>: テーブル編集ハンドルが描画されたキャンバスが重なる
    - .DragHandle等: テーブル操作UIが印刷物に混入する
    """
    await inner_frame.evaluate("""
    () => {
        const style = document.createElement('style');
        style.id = '_fetch_print_cleanup';
        style.textContent = `
            /* ナビゲーションパネル（白箱の主因：x=0に残り左列を隠す） */
            #applicationWACNavigationPanel,
            #NavPane,
            #navpaneFocusZone,
            [class*="wacNavPane"],
            [class*="navPane__"],
            [class*="wacNavTwoPane"],
            [class*="NavRail"],

            /* テーブル編集ハンドル類 */
            .DragHandle,
            .TableLastResizeAnchor,
            .TableColumnHandleAnchor,
            .TableColumnHandle,
            .TableRowHandle,
            .GrippersContainer,
            [class*="TableHandle"],
            [class*="tableBorder"],
            [class*="TableBorder"],

            /* 編集用キャンバス・オーバーレイ */
            canvas,
            [class*="WACImageOverlay"],
            [class*="SelectionLayer"],
            [class*="CursorCanvas"],
            [class*="OverlayCanvas"],

            /* Fluent UI の固定レイヤー（ダイアログ等） */
            .ms-Layer--fixed,
            [class*="ms-Layer"]
            {
                display: none !important;
                visibility: hidden !important;
            }

            /* --- テーブル左列クリップ修正 --- */

            /* コンテンツの直接コンテナのみ overflow 解除（子孫全体に適用すると
               画像ブロック等のサイズ制御が壊れるため、対象を限定する） */
            [role="main"],
            [class*="OutlineSized"],
            [class*="OutlineContainer"],
            [class*="PageContentOrigin"],
            [class*="WACViewPanel"] {
                overflow: visible !important;
                clip: auto !important;
                clip-path: none !important;
            }

            /* ナビペイン非表示後のコンテンツ左寄せ補正 */
            [class*="WACViewPanel"],
            [class*="FlexPane"],
            [class*="ContentOrigin"] {
                left: 0 !important;
                margin-left: 0 !important;
                padding-left: 0 !important;
                transform: none !important;
            }

            /* テーブルとその親 OutlineElement のみ overflow 解除 */
            [class*="OutlineElement"]:has(table) {
                overflow: visible !important;
            }
            table {
                overflow: visible !important;
                max-width: none !important;
            }
            td, th {
                overflow: visible !important;
            }
        `;
        // 既に注入済みなら上書き
        const existing = document.getElementById('_fetch_print_cleanup');
        if (existing) existing.remove();
        document.head.appendChild(style);
    }
    """)


async def generate_full_pdf_async(page, inner_frame, pdf_path: Path) -> dict:
    confirmed_h = await scroll_and_measure_async(inner_frame)
    needs_expansion = confirmed_h > DEFAULT_VP_HEIGHT
    if needs_expansion:
        await page.set_viewport_size({"width": 1440, "height": confirmed_h + 200})
        # 固定 sleep 1.2s → networkidle 待機 (最大 800ms) に短縮
        await _wait_layout_stable(page, 800)
        await expand_containers_async(page, inner_frame, confirmed_h)
        # 固定 sleep 1.2s → networkidle 待機 (最大 600ms) に短縮
        await _wait_layout_stable(page, 600)

    await page.emulate_media(media="print")
    await _wait_layout_stable(page, 800)
    await _inject_print_cleanup_css(inner_frame)
    # CSS injection 後に print 再レイアウトが安定するまで待機
    await _wait_layout_stable(page, 600)
    # ナビパネルが確実に非表示になったことを確認
    await _verify_cleanup_applied(inner_frame)
    try:
        await page.pdf(
            path=str(pdf_path),
            format="A4",
            scale=PDF_SCALE,
            print_background=True,
            margin=PDF_MARGIN,
            display_header_footer=False,
        )
        size_kb = pdf_path.stat().st_size / 1024
        result = {"ok": True, "size_kb": round(size_kb, 1), "scroll_h": confirmed_h}
    except Exception as e:
        result = {"ok": False, "error": str(e)[:180], "scroll_h": confirmed_h}
    await page.emulate_media(media="screen")

    if needs_expansion:
        await reset_layout_async(page, inner_frame)
        # 固定 sleep 0.8s → networkidle 待機 (最大 500ms) に短縮
        await _wait_layout_stable(page, 500)
    return result


def is_transient_error(msg: str) -> bool:
    text = msg.lower()
    transient_tokens = [
        "429",
        "timeout",
        "timed out",
        "navigation",
        "target closed",
        "net::err",
        "connection reset",
        "socket",
    ]
    return any(token in text for token in transient_tokens)


async def fetch_one(
    context,
    sem: asyncio.Semaphore,
    seq: int,
    total: int,
    sec: dict,
    pg: dict,
    output_dir: Path,
    pdf_dir: Path,
    manifest: dict,
    manifest_file: Path,
    lock: asyncio.Lock,
    progress: dict,
    max_retries: int = 3,
    quiet_ms: int = 1500,
):
    title = pg.get("title") or "untitled"
    deep_link = pg.get("deep_link") or ""
    sec_name = sec.get("name") or "section"

    async with sem:
        if not deep_link:
            async with lock:
                pg["status"] = "error"
                pg["error"] = "deep_link missing"
                manifest["updated_at"] = datetime.now().isoformat()
                manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                progress["err"] += 1
            log(f"  [{seq}/{total}] {sec_name!r} / {title!r}")
            log("    [ERROR] deep_link missing")
            return

        safe = sanitize_filename(f"{sec_name}_{title}")
        pdf_name = f"{seq:04d}_{safe}.pdf"
        pdf_path = pdf_dir / pdf_name
        log(f"  [{seq}/{total}] {sec_name!r} / {title!r}")

        # テレメトリ・分析系リソースをブロック（CPU/帯域節約）
        _TELEMETRY_PATTERNS = [
            "**/*aria.microsoft.com*",
            "**/*browser.events.data*",
            "**/*telemetry*",
            "**/*onedsproddweus2.blob*",
            "**/*log.microsoft.com*",
            "**/*collector.azure.com*",
        ]

        async def _block_route(route):
            await route.abort()

        last_error = "unknown error"
        for attempt in range(1, max_retries + 1):
            page = await context.new_page()
            # テレメトリブロック設定
            for pat in _TELEMETRY_PATTERNS:
                await page.route(pat, _block_route)
            start = time.time()
            try:
                await page.goto(deep_link, wait_until="domcontentloaded", timeout=60_000)
                if not await wait_for_main_async(page, timeout_sec=20):
                    raise RuntimeError("main content wait timeout")
                # DOM 変更が静止するまで待機（コンテンツ未ロード状態での PDF 化を防ぐ）
                await wait_for_content_stable_async(page, quiet_ms=quiet_ms, max_ms=max(15000, quiet_ms * 10))
                inner = get_inner_frame(page)
                if not inner:
                    raise RuntimeError("inner frame not found")

                result = await generate_full_pdf_async(page, inner, pdf_path)
                if not result.get("ok"):
                    raise RuntimeError(result.get("error", "pdf error"))

                wd = decode_wd(page.url)
                elapsed = time.time() - start
                async with lock:
                    pg["status"] = "done"
                    pg["pdf_path"] = str(pdf_path.relative_to(output_dir))
                    pg["fetched_at"] = datetime.now().isoformat()
                    pg["error"] = None
                    pg["deep_link"] = page.url
                    if wd.get("page_guid"):
                        pg["page_guid"] = wd["page_guid"]
                    manifest["updated_at"] = datetime.now().isoformat()
                    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                    progress["done"] += 1
                log(f"    -> {pdf_name} ({result['size_kb']} KB, {elapsed:.1f}s)")
                await page.close()
                return
            except Exception as e:
                last_error = str(e)[:240]
                await page.close()
                if attempt < max_retries and is_transient_error(last_error):
                    backoff = 1.5**attempt
                    log(f"    [WARN] retry {attempt}/{max_retries - 1} after transient error: {last_error}")
                    await asyncio.sleep(backoff)
                    continue

                async with lock:
                    pg["status"] = "error"
                    pg["error"] = last_error
                    manifest["updated_at"] = datetime.now().isoformat()
                    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                    progress["err"] += 1
                log(f"    [ERROR] {last_error}")
                return


async def run_fetch(manifest_file: Path, limit: int, tabs: int, retry_mode: bool = False):
    if not manifest_file.exists():
        log(f"[ERROR] manifest が見つかりません: {manifest_file}")
        return 1
    output_dir = manifest_file.parent
    pdf_dir = output_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    notebook_url = manifest.get("notebook_url", DEFAULT_URL)

    # --retry 時: status='retry' のページのみ対象、tabs=2, quiet_ms=3000
    if retry_mode:
        tabs = min(tabs, 2)  # --tabs で上書き指定された場合も最大2に制限
        quiet_ms = 3000
        target_status = "retry"
        log(f"[FETCH] --retry モード: tabs={tabs}, quiet_ms={quiet_ms}ms")
    else:
        quiet_ms = 1500
        target_status = None  # 'done' 以外すべて

    pending = []
    for sec in manifest.get("sections", []):
        for pg in sec.get("pages", []):
            if retry_mode:
                if pg.get("status") == "retry":
                    pending.append((sec, pg))
            else:
                if pg.get("status") != "done":
                    pending.append((sec, pg))
    if limit:
        pending = pending[:limit]

    log(f"[FETCH] pending: {len(pending)} pages")
    if not pending:
        return 0

    pending_with_index: list[tuple[int, dict, dict]] = [(i, sec, pg) for i, (sec, pg) in enumerate(pending, start=1)]
    log(f"[FETCH] tabs={tabs}")

    async with async_playwright() as pw:
        # Phase 1: headed で認証チェック（画面中央に表示）
        phase1_browser = await pw.chromium.launch(
            channel="msedge",
            headless=False,
            slow_mo=100,
            args=["--start-maximized"],
        )
        phase1_context = await phase1_browser.new_context(
            viewport={"width": 1440, "height": DEFAULT_VP_HEIGHT},
            ignore_https_errors=True,
        )
        if not await load_cookies_async(phase1_context):
            await phase1_browser.close()
            return 1

        phase1_page = await phase1_context.new_page()
        await phase1_page.goto(notebook_url, wait_until="domcontentloaded", timeout=60_000)
        log("[INFO] Phase1: OneNote 認証確認中... セッション切れの場合は開いたブラウザで手動ログインしてください（最大10分待機）")
        if not await wait_for_ready_async(phase1_page, timeout_sec=600):
            log("[ERROR] 初期ロード失敗")
            await phase1_context.close()
            await phase1_browser.close()
            return 1
        await save_cookies_async(phase1_context)
        await phase1_context.close()
        await phase1_browser.close()

        # Phase 2: headed（画面外）で並列 fetch
        browser = await pw.chromium.launch(
            channel="msedge",
            headless=False,
            slow_mo=100,
            args=["--window-position=-2000,0"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": DEFAULT_VP_HEIGHT},
            ignore_https_errors=True,
        )
        if not await load_cookies_async(context):
            await browser.close()
            return 1

        lock = asyncio.Lock()
        sem = asyncio.Semaphore(max(1, tabs))
        progress = {"done": 0, "err": 0}
        tasks = [
            fetch_one(
                context=context,
                sem=sem,
                seq=seq,
                total=len(pending_with_index),
                sec=sec,
                pg=pg,
                output_dir=output_dir,
                pdf_dir=pdf_dir,
                manifest=manifest,
                manifest_file=manifest_file,
                lock=lock,
                progress=progress,
                quiet_ms=quiet_ms,
            )
            for seq, sec, pg in pending_with_index
        ]
        await asyncio.gather(*tasks)

        log(f"[FETCH] done={progress['done']} error={progress['err']}")
        await save_cookies_async(context)
        await asyncio.sleep(2)
        await context.close()
        await browser.close()
    return 0


def run_fastdiscover(url: str, output_dir: Path, section_limit: int):
    """
    高速 discover:
    - history.pushState をオーバーライドして URL 変化を全捕捉
    - 全ページを 300ms 間隔でクリック → URL 収集（待機不要）
    - クリック後まとめて GUID を解析
    期待: 17min → ~2min (396p)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="msedge",
            headless=False,
            slow_mo=0,
            args=["--start-maximized"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": DEFAULT_VP_HEIGHT}, ignore_https_errors=True)
        if not load_cookies(context):
            log("[INFO] Cookie なし。ログインしてください（最大10分待機）")

        page = context.new_page()

        # history.pushState をオーバーライドして URL 変化を捕捉
        page.add_init_script("""
        window._capturedUrls = [];
        const _origPush = history.pushState.bind(history);
        history.pushState = function(state, title, url) {
            if (url && url.includes('wd=')) {
                window._capturedUrls.push(typeof url === 'string' ? url : window.location.href);
            }
            return _origPush(state, title, url);
        };
        """)

        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        if not wait_for_ready(page, timeout_sec=600):
            log("[ERROR] 準備タイムアウト")
            browser.close()
            return 1

        save_cookies(context)
        inner = get_inner_frame(page)
        if not inner:
            log("[ERROR] inner frame なし")
            browser.close()
            return 1

        sections = get_sections(inner)
        if section_limit:
            sections = sections[:section_limit]

        log(f"[FASTDISCOVER] sections: {len(sections)} 件")
        manifest_sections = []
        prev_first_text = ""

        for si, sec in enumerate(sections, start=1):
            sec_idx = sec["index"]
            sec_name = sec.get("text") or f"section_{sec_idx}"
            log(f"  [{si}/{len(sections)}] セクション: {sec_name!r}")

            # セクション切替
            section_ok = False
            for _ in range(3):
                click_section(inner, sec_idx)
                if wait_for_section_url(page, sec_name, timeout_sec=4):
                    section_ok = True
                    break
                time.sleep(0.6)
            if not section_ok:
                log("    [WARN] セクションURL切替未確認のまま継続")

            page_items = wait_page_items_updated(inner, prev_first_text, timeout_sec=10)
            prev_first_text = page_items[0]["text"] if page_items else prev_first_text
            n_items = len(page_items)
            log(f"    pageItem: {n_items} 件")

            # セクション先頭ページの URL を保存（既選択状態で pushState が呼ばれないため）
            first_page_url = page.url

            # 捕捉バッファをクリア
            page.evaluate("window._capturedUrls = []")

            # 全ページを 350ms 間隔で連続クリック（setTimeout で非同期発火）
            interval_ms = 350
            inner.evaluate(f"""
            () => {{
                const items = document.querySelectorAll('[class*="pageItem"]');
                let delay = 50;
                items.forEach(item => {{
                    setTimeout(() => item.click(), delay);
                    delay += {interval_ms};
                }});
            }}
            """)

            # 全クリックが終わるまで待機 (n_items × interval_ms + 3s バッファ)
            wait_sec = (n_items * interval_ms / 1000) + 3
            time.sleep(wait_sec)

            # 捕捉した URL を回収（先頭ページ URL を先頭に追加）
            captured_urls = [first_page_url] + page.evaluate("window._capturedUrls")
            log(f"    capturedUrls: {len(captured_urls)} 件")

            # URL → GUID 解析
            pages = []
            seen_guid = set()
            for curl in captured_urls:
                wd = decode_wd(curl)
                page_guid = wd.get("page_guid", "")
                section_guid = wd.get("section_guid", "")
                wd_section = wd.get("section_name", "")
                title = wd.get("page_title", "")

                if wd_section and wd_section != sec_name:
                    continue
                if not page_guid:
                    continue
                if page_guid in seen_guid:
                    continue
                seen_guid.add(page_guid)

                pages.append({
                    "title": title,
                    "deep_link": curl,
                    "page_guid": page_guid,
                    "section_guid": section_guid,
                    "status": "pending",
                    "pdf_path": None,
                    "error": None,
                })

            log(f"    pages (guid取得): {len(pages)} 件 / pageItem: {n_items} 件")
            if len(pages) < n_items:
                log(f"    [WARN] {n_items - len(pages)} 件の GUID が取得できませんでした")

            manifest_sections.append({
                "name": sec_name,
                "index": sec_idx,
                "page_count": len(pages),
                "pages": pages,
            })

        total_pages = sum(s["page_count"] for s in manifest_sections)
        manifest = {
            "generated_at": datetime.now().isoformat(),
            "mode": "fastdiscover",
            "notebook_url": url,
            "total_sections": len(manifest_sections),
            "total_pages": total_pages,
            "sections": manifest_sections,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"\n[FASTDISCOVER] 完了: {len(manifest_sections)} sections / {total_pages} pages")
        log(f"  出力: {manifest_path}")

        save_cookies(context)
        context.close()
        browser.close()
        return 0


def main():
    global _log_handle

    parser = argparse.ArgumentParser(description="OneNote フェッチツール (fastdiscover / fetch)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fdisc = sub.add_parser("fastdiscover")
    p_fdisc.add_argument("--url", default=DEFAULT_URL)
    p_fdisc.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    p_fdisc.add_argument("--section-limit", type=int, default=0)

    p_fet = sub.add_parser("fetch")
    p_fet.add_argument("--manifest", required=True)
    p_fet.add_argument("--limit", type=int, default=0)
    p_fet.add_argument("--tabs", type=int, default=4)
    p_fet.add_argument("--retry", action="store_true",
                       help="status='retry' のページのみ対象。tabs=2, quiet_ms=3000 で丁寧にフェッチ")

    args = parser.parse_args()

    if args.command == "fastdiscover":
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        log_file = out_dir / "fastdiscover_log.txt"
        _log_handle = open(log_file, "w", encoding="utf-8")
        try:
            rc = run_fastdiscover(args.url, out_dir, args.section_limit)
        finally:
            _log_handle.close()
            _log_handle = None
        raise SystemExit(rc)

    if args.command == "fetch":
        manifest = Path(args.manifest)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        log_file = manifest.parent / "fetch_log.txt"
        _log_handle = open(log_file, "w", encoding="utf-8")
        try:
            rc = asyncio.run(run_fetch(manifest, args.limit, args.tabs, retry_mode=args.retry))
        finally:
            _log_handle.close()
            _log_handle = None
        raise SystemExit(rc)


if __name__ == "__main__":
    main()

