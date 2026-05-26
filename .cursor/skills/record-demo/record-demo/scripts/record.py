"""
record.py - YAML 駆動の汎用デモ録画エンジン v4.0.0

シナリオ YAML を読み込み、Playwright で自動操作しながら動画を録画する。
capture フラグ付きステップや screenshot アクションで重要場面をキャプチャする。
インライン JS キャプション（デフォルト）または ASS 字幕 + ffmpeg で字幕を表示。

使い方:
  python record.py --scenario scenario.yaml --output ./output
  python record.py --scenario scenario.yaml --dry-run
  python record.py --scenario scenario.yaml --output ./output --headed
"""

from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import yaml
from playwright.async_api import Page, async_playwright

# ── キャプションスタイル定数（固定） ──────────────────
# 2026-02-25 承認。変更時はユーザー確認を取ること。

JS_CAPTION_STYLE = {
    "font_size": "24px",
    "font_family": "'BIZ UDPGothic', 'Noto Sans JP', sans-serif",
    "bg": "rgba(0,0,0,0.82)",
    "color": "#fff",
    "padding": "14px 36px",
    "border_radius": "8px",
    "max_width": "80%",
    "line_height": "1.6",
    "fade_in_ms": 300,
    "fade_out_ms": 500,
    "bottom": "28px",
}

# ── バリデーション ────────────────────────────────────

VALID_ACTIONS = {
    "goto", "click", "wait_for_hitl", "approve", "wait_for_complete",
    "pause", "screenshot",
    # v4.0 追加
    "fill", "upload", "scroll_to", "highlight", "wait_for_url",
    # v4.1 追加
    "go_back",
}
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


def validate_scenario(data: dict) -> list[str]:
    """シナリオ YAML を検証し、エラーメッセージのリストを返す。"""
    errors = []
    warnings = []

    if not isinstance(data, dict):
        errors.append("[root] YAML がオブジェクトではありません（空ファイル？）")
        return errors

    # config
    config = data.get("config")
    if not config:
        errors.append("[config] セクションが必要です")
        return errors

    # config.base_url: 必須 + URL 形式チェック
    base_url = config.get("base_url")
    if not base_url:
        errors.append("[config.base_url] 必須です")
    elif not base_url.startswith(("http://", "https://")):
        errors.append("[config.base_url] http:// or https:// で始まる必要があります")

    # config.output_name: 必須 + 禁止文字チェック
    output_name = config.get("output_name")
    if not output_name:
        errors.append("[config.output_name] 必須です")
    elif INVALID_FILENAME_CHARS.search(str(output_name)):
        errors.append(f'[config.output_name] ファイル名に使えない文字が含まれています: {output_name}')

    # config.timeout: 正の数
    timeout = config.get("timeout")
    if timeout is not None:
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            errors.append("[config.timeout] 正の数が必要です")

    # config.width / config.height: 正の整数
    for dim in ("width", "height"):
        val = config.get(dim)
        if val is not None and (not isinstance(val, int) or val <= 0):
            errors.append(f"[config.{dim}] 正の整数が必要です")

    # config.caption_min_duration: 正の数
    cmd = config.get("caption_min_duration")
    if cmd is not None:
        if not isinstance(cmd, (int, float)) or cmd <= 0:
            errors.append("[config.caption_min_duration] 正の数が必要です")

    # config.caption_mode: js or ass
    cm = config.get("caption_mode")
    if cm is not None and cm not in ("js", "ass"):
        errors.append("[config.caption_mode] 'js' または 'ass' が必要です")

    # config.health_check: bool
    hc = config.get("health_check")
    if hc is not None and not isinstance(hc, bool):
        errors.append("[config.health_check] true または false が必要です")

    # steps
    steps = data.get("steps")
    if not steps or not isinstance(steps, list):
        errors.append("[steps] 1つ以上のステップが必要です")
        return errors

    for i, step in enumerate(steps, 1):
        action = step.get("action")
        if action not in VALID_ACTIONS:
            errors.append(f"[step {i}] action '{action}' は無効です。有効値: {sorted(VALID_ACTIONS)}")
            continue

        if action == "goto" and not step.get("url"):
            errors.append(f"[step {i}] goto には url が必要です")
        if action == "click" and not step.get("selector"):
            errors.append(f"[step {i}] click には selector が必要です")
        if action == "pause":
            dur = step.get("duration")
            if dur is None:
                errors.append(f"[step {i}] pause には duration が必要です")
            elif not isinstance(dur, (int, float)) or dur <= 0:
                errors.append(f"[step {i}] pause の duration は正の数が必要です")
        if action == "fill":
            if not step.get("selector"):
                errors.append(f"[step {i}] fill には selector が必要です")
            if step.get("value") is None:
                errors.append(f"[step {i}] fill には value が必要です")
        if action == "upload":
            if not step.get("selector"):
                errors.append(f"[step {i}] upload には selector が必要です")
            if not step.get("file"):
                errors.append(f"[step {i}] upload には file が必要です")
        if action == "scroll_to" and not step.get("selector"):
            errors.append(f"[step {i}] scroll_to には selector が必要です")
        if action == "highlight":
            if not step.get("selector"):
                errors.append(f"[step {i}] highlight には selector が必要です")
        if action == "wait_for_url" and not step.get("url_pattern"):
            errors.append(f"[step {i}] wait_for_url には url_pattern が必要です")

        # pause_after: 負数チェック
        pa = step.get("pause_after")
        if pa is not None and (not isinstance(pa, (int, float)) or pa < 0):
            errors.append(f"[step {i}] pause_after は 0 以上の数が必要です")

        # wait_for_hitl: step_label なし時に警告
        if action == "wait_for_hitl" and not step.get("step_label"):
            warnings.append(f"[step {i}] wait_for_hitl に step_label がありません（厳密検証なし）")

    # 警告出力（エラーではないので errors には含めない）
    for w in warnings:
        print(f"  WARNING: {w}")

    return errors


# ── ヘルパー ──────────────────────────────────────────

async def save_error_screenshot(page: Page, output_dir: Path, label: str) -> Path:
    """エラー発生時にスクリーンショットを保存。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"error_{label}_{ts}.png"
    await page.screenshot(path=str(path), full_page=True)
    return path


async def save_capture(page: Page, captures_dir: Path, step_num: int, label: str) -> Path:
    """重要場面のスクリーンショットをキャプチャ保存。"""
    captures_dir.mkdir(parents=True, exist_ok=True)
    # ラベルからファイル名安全な文字列を作る
    safe_label = re.sub(r'[<>:"/\\|?*\s]+', '_', label).strip('_') if label else "capture"
    path = captures_dir / f"{step_num:02d}_{safe_label}.png"
    await page.screenshot(path=str(path), full_page=False)
    print(f"  [CAP] Captured: {path.name}")
    return path


def rename_video(video_path: Path, output_dir: Path, output_name: str) -> Path:
    """Playwright のハッシュ名動画を分かりやすい名前にリネームして移動。
    同名衝突時はミリ秒付きタイムスタンプでリトライ（最大3回）。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for attempt in range(3):
        suffix = f"_{attempt}" if attempt > 0 else ""
        dest = output_dir / f"{output_name}_{ts}{suffix}.webm"
        if not dest.exists():
            try:
                shutil.move(str(video_path), str(dest))
                return dest
            except OSError as e:
                print(f"  WARNING: リネーム失敗 (attempt {attempt + 1}): {e}")
                time.sleep(0.5)
                continue

    # フォールバック: ミリ秒付き
    ts_ms = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = output_dir / f"{output_name}_{ts_ms}.webm"
    shutil.move(str(video_path), str(dest))
    return dest


# ── インライン JS キャプション ────────────────────────

async def show_js_caption(page: Page, text: str, duration: float, font_size: str | None = None) -> None:
    """ページ DOM にキャプションを注入。録画にそのまま映り込む（WYSIWYG）。"""
    s = JS_CAPTION_STYLE
    fs = font_size or s["font_size"]
    await page.evaluate(
        """([text, dur, style]) => {
            // 既存キャプション削除
            document.querySelectorAll('.demo-caption').forEach(e => e.remove());

            // アニメーション定義（初回のみ）
            if (!document.getElementById('demo-caption-style')) {
                const s = document.createElement('style');
                s.id = 'demo-caption-style';
                s.textContent = `
                    @keyframes demoFadeIn {
                        from { opacity: 0; transform: translateX(-50%) translateY(10px); }
                        to   { opacity: 1; transform: translateX(-50%) translateY(0); }
                    }
                `;
                document.head.appendChild(s);
            }

            const el = document.createElement('div');
            el.className = 'demo-caption';
            el.textContent = text;
            el.style.cssText = `
                position: fixed;
                bottom: ${style.bottom};
                left: 50%;
                transform: translateX(-50%);
                background: ${style.bg};
                color: ${style.color};
                padding: ${style.padding};
                border-radius: ${style.border_radius};
                font-size: ${style.font_size};
                font-family: ${style.font_family};
                max-width: ${style.max_width};
                line-height: ${style.line_height};
                white-space: pre-line;
                text-align: center;
                z-index: 99999;
                animation: demoFadeIn ${style.fade_in_ms}ms ease;
                pointer-events: none;
            `;
            document.body.appendChild(el);

            // フェードアウト → 削除
            setTimeout(() => {
                el.style.transition = `opacity ${style.fade_out_ms}ms`;
                el.style.opacity = '0';
                setTimeout(() => el.remove(), style.fade_out_ms);
            }, dur * 1000);
        }""",
        [text, duration, {**s, "font_size": fs}],
    )


async def clear_js_caption(page: Page) -> None:
    """キャプションを即座に消す。"""
    await page.evaluate("document.querySelectorAll('.demo-caption').forEach(e => e.remove())")


# ── ASS 字幕生成（ffmpeg 後処理用・オプション） ──────

def _format_ass_time(seconds: float) -> str:
    """秒数を ASS タイムコード (H:MM:SS.CC) に変換。"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


GAP_BETWEEN_CAPTIONS = 0.2  # 字幕間の最小ギャップ（秒）
MIN_CAPTION_DURATION = 3.0  # 字幕の最小表示時間（秒）


def _resolve_overlaps(
    timings: list[tuple[float, float, str]],
) -> list[tuple[float, float, str]]:
    """字幕の重なりを解消する。各字幕は最低 MIN_CAPTION_DURATION を保証。"""
    if len(timings) <= 1:
        return list(timings)

    result = list(timings)
    for i in range(len(result) - 1):
        cur_start, cur_end, cur_text = result[i]
        next_start, next_end, next_text = result[i + 1]

        boundary = next_start - GAP_BETWEEN_CAPTIONS
        if cur_end > boundary:
            if boundary - cur_start >= MIN_CAPTION_DURATION:
                result[i] = (cur_start, boundary, cur_text)
            else:
                new_cur_end = cur_start + MIN_CAPTION_DURATION
                new_next_start = new_cur_end + GAP_BETWEEN_CAPTIONS
                new_next_end = max(next_end, new_next_start + MIN_CAPTION_DURATION)
                result[i] = (cur_start, new_cur_end, cur_text)
                result[i + 1] = (new_next_start, new_next_end, next_text)

    return result


def generate_ass_subtitles(
    timings: list[tuple[float, float, str]],
    width: int = 1280,
    height: int = 720,
) -> str:
    """キャプションタイミングから ASS 字幕ファイルを生成。"""
    timings = _resolve_overlaps(timings)
    header = (
        "[Script Info]\n"
        "Title: Demo Captions\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Caption,BIZ UDPGothic,32,&H00FFFFFF,&H000000FF,"
        "&H00000000,&HC0000000,-1,0,0,0,100,100,1,0,3,14,0,2,30,30,28,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, "
        "MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines = []
    for start, end, text in timings:
        start_str = _format_ass_time(start)
        end_str = _format_ass_time(end)
        escaped = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        lines.append(
            f"Dialogue: 0,{start_str},{end_str},Caption,,0,0,0,,"
            f"{{\\fad(300,300)}}{escaped}"
        )

    return header + "\n".join(lines) + "\n"


def _find_ffmpeg() -> str | None:
    """ffmpeg 実行ファイルを探す。PATH / Playwright バンドル / winget を検索。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    if sys.platform == "win32":
        import os
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            # Playwright がインストールした ffmpeg を優先検索
            ms_playwright = Path(local_app) / "ms-playwright"
            if ms_playwright.exists():
                for ffmpeg_exe in ms_playwright.rglob("ffmpeg.exe"):
                    return str(ffmpeg_exe)
            # winget パッケージディレクトリ
            winget_dir = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
            if winget_dir.exists():
                for ffmpeg_exe in winget_dir.rglob("ffmpeg.exe"):
                    return str(ffmpeg_exe)
    return None


def _get_video_duration(input_path: Path, ffmpeg_exe: str) -> float | None:
    """ffmpeg -i で動画の長さ（秒）を取得。"""
    result = subprocess.run(
        [ffmpeg_exe, "-i", str(input_path)],
        capture_output=True, text=True, timeout=30,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", result.stderr)
    if match:
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)
    return None


def apply_speed_segments(
    input_path: Path,
    output_dir: Path,
    output_name: str,
    speed_segments: list[tuple[float, float, float]],
) -> Path | None:
    """ffmpeg で指定した時間区間を N 倍速に変換した動画を生成。

    speed_segments: [(start_sec, end_sec, speed_factor), ...]
    """
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        print("WARNING: ffmpeg が見つかりません。速度変更処理をスキップします。")
        return None

    total_duration = _get_video_duration(input_path, ffmpeg)
    if total_duration is None:
        print("WARNING: 動画の長さを取得できませんでした。速度変更処理をスキップします。")
        return None

    # 重複なし・時系列順に整理
    sorted_segs = sorted(speed_segments, key=lambda x: x[0])

    # 全セグメントリストを構築（通常速 1.0 と高速を交互に並べる）
    all_segs: list[tuple[float, float, float]] = []
    cursor = 0.0
    for seg_start, seg_end, spd in sorted_segs:
        if seg_start > cursor + 0.01:
            all_segs.append((cursor, seg_start, 1.0))
        all_segs.append((seg_start, seg_end, spd))
        cursor = seg_end
    if cursor < total_duration - 0.01:
        all_segs.append((cursor, total_duration, 1.0))

    if not all_segs:
        return None

    # filter_complex を組み立て
    filter_parts = []
    labels = []
    for idx, (start, end, spd) in enumerate(all_segs):
        pts_factor = 1.0 / spd
        filter_parts.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},"
            f"setpts={pts_factor:.6f}*(PTS-STARTPTS)[v{idx}]"
        )
        labels.append(f"[v{idx}]")

    n = len(all_segs)
    concat_filter = "".join(labels) + f"concat=n={n}:v=1:a=0[vout]"
    filter_complex = ";".join(filter_parts) + ";" + concat_filter

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"{output_name}_{ts}_speed.webm"

    cmd = [
        ffmpeg, "-y",
        "-i", str(input_path),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-c:v", "libvpx-vp9",
        "-b:v", "2M",
        "-avoid_negative_ts", "make_zero",
        str(output_path),
    ]

    print(f"  ffmpeg: 速度変更処理中... ({len(sorted_segs)} セグメント / 合計 {n} ピース)")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode == 0 and output_path.exists():
        print(f"  OK: {output_path.name}")
        return output_path
    else:
        stderr_tail = result.stderr[-1500:] if result.stderr else "(no stderr)"
        print(f"  ffmpeg エラー:\n{stderr_tail}")
        return None


def burn_captions_ffmpeg(
    raw_video: Path,
    ass_path: Path,
    output_path: Path,
) -> Path | None:
    """ffmpeg で ASS 字幕を動画に焼き込む。"""
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        print("WARNING: ffmpeg が見つかりません。キャプション付き動画は生成されません。")
        print("  インストール: https://ffmpeg.org/download.html")
        return None

    print(f"  ffmpeg: キャプション焼き込み中... ({ffmpeg})")

    work_dir = Path(tempfile.mkdtemp(prefix="ffmpeg_work_"))
    try:
        temp_raw = work_dir / "raw.webm"
        temp_ass = work_dir / "captions.ass"
        temp_out = work_dir / "captioned.webm"

        shutil.copy2(raw_video, temp_raw)
        shutil.copy2(ass_path, temp_ass)

        cmd = [
            ffmpeg, "-y",
            "-i", "raw.webm",
            "-vf", "ass=captions.ass",
            "-c:v", "libvpx-vp9",
            "-b:v", "2M",
            "captioned.webm",
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(work_dir)
        )

        if result.returncode == 0 and temp_out.exists():
            shutil.copy2(temp_out, output_path)
            print(f"  OK: {output_path.name}")
            return output_path
        else:
            stderr_tail = result.stderr[-500:] if result.stderr else "(no stderr)"
            print(f"  ffmpeg エラー:\n{stderr_tail}")
            return None
    except subprocess.TimeoutExpired:
        print("  ffmpeg タイムアウト (300s)")
        return None
    except Exception as e:
        print(f"  ffmpeg 実行エラー: {e}")
        return None
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ── Readiness チェック ────────────────────────────────

async def check_readiness(page: Page, config: dict) -> bool:
    """アプリの起動確認。health_check: true なら /health、false なら base_url への HTTP 疎通。"""
    base_url = config["base_url"]
    use_health = config.get("health_check", True)
    check_url = f"{base_url}/health" if use_health else base_url

    # 指数バックオフで最大3回リトライ
    for attempt in range(3):
        try:
            resp = await page.request.get(check_url)
            if resp.ok:
                if use_health:
                    data = await resp.json()
                    print(f"  OK: {data}")
                else:
                    print(f"  OK: {check_url} (status {resp.status})")
                return True
            print(f"  attempt {attempt + 1}: status {resp.status}")
        except Exception as e:
            print(f"  attempt {attempt + 1}: {e}")

        if attempt < 2:
            wait_sec = 2 ** attempt  # 1s, 2s
            print(f"  {wait_sec}s 後にリトライ...")
            await asyncio.sleep(wait_sec)

    print(f"FAIL: アプリが応答しません: {check_url}")
    return False


# ── アクション実行 ────────────────────────────────────

async def exec_goto(page: Page, step: dict, config: dict) -> None:
    url = config["base_url"].rstrip("/") + step["url"]
    wait_until = step.get("wait_until", "networkidle")
    print(f"  goto: {url}")
    await page.goto(url, wait_until=wait_until)


async def exec_click(page: Page, step: dict, config: dict) -> None:
    selector = step["selector"]
    has_text = step.get("has_text")
    wait_for = step.get("wait_for")
    remove_target_blank = step.get("remove_target_blank", False)
    timeout_ms = config.get("timeout", 60) * 1000

    print(f"  click: {selector}" + (f" (text: {has_text})" if has_text else ""))

    # target="_blank" を除去して同タブで開くようにする
    if remove_target_blank:
        removed = await page.evaluate(
            """() => {
                const links = document.querySelectorAll('a[target]');
                links.forEach(a => a.removeAttribute('target'));
                return links.length;
            }"""
        )
        print(f"  remove_target_blank: {removed} 件の target を除去")

    if has_text:
        locator = page.locator(selector, has_text=has_text)
        await locator.click(timeout=timeout_ms)
    else:
        await page.click(selector, timeout=timeout_ms)

    if wait_for:
        print(f"  wait_for: {wait_for}")
        await page.wait_for_selector(wait_for, state="visible", timeout=timeout_ms)


async def exec_wait_for_hitl(page: Page, step: dict, config: dict) -> None:
    timeout_ms = config.get("timeout", 60) * 1000
    step_id = step.get("step_id")
    step_label = step.get("step_label")

    print(f"  wait_for_hitl" + (f" (step_id: {step_id})" if step_id else ""))

    await page.wait_for_selector(
        "#hitl-footer:not(.hidden)",
        state="visible",
        timeout=timeout_ms,
    )

    await page.wait_for_function(
        "() => document.getElementById('chat-log')?.innerText.includes('確認を待っています')",
        timeout=timeout_ms,
    )

    if step_id:
        actual_step_id = await page.evaluate("() => window.currentStepId")
        print(f"  currentStepId: {actual_step_id} (expected: {step_id})")
        if actual_step_id != step_id:
            raise RuntimeError(
                f"step_id mismatch: expected '{step_id}', got '{actual_step_id}'"
            )

    if step_label:
        chat_text = await page.evaluate(
            "() => document.getElementById('chat-log')?.innerText || ''"
        )
        review_lines = [line for line in chat_text.split("\n") if "確認を待っています" in line]
        if review_lines:
            latest = review_lines[-1]
            print(f"  latest review: {latest.strip()}")
            if step_label not in latest:
                raise RuntimeError(
                    f"step_label mismatch: expected '{step_label}' in '{latest.strip()}'"
                )


async def exec_approve(page: Page, step: dict, config: dict) -> None:
    timeout_ms = config.get("timeout", 60) * 1000
    print("  approve: 承認して次へ")

    approve_btn = page.locator("#hitl-footer button", has_text="承認して次へ")
    await approve_btn.click(timeout=timeout_ms)


async def exec_wait_for_complete(page: Page, step: dict, config: dict) -> None:
    timeout_ms = config.get("timeout", 60) * 1000
    print("  wait_for_complete: 全処理完了")

    await page.wait_for_function(
        "() => document.getElementById('chat-log')?.innerText.includes('全処理完了')",
        timeout=timeout_ms,
    )


async def exec_pause(page: Page, step: dict, config: dict) -> None:
    duration = step["duration"]
    speed = step.get("speed")
    print(f"  pause: {duration}s" + (f" [x{speed}速]" if speed else ""))

    if speed and speed > 1:
        # この区間を速度変更対象として記録（タイムスタンプ計測）
        video_start = config.get("_video_start_time", time.monotonic())
        seg_start = time.monotonic() - video_start
        await asyncio.sleep(duration)
        seg_end = time.monotonic() - video_start
        segs = config.setdefault("_speed_segments", [])
        segs.append((seg_start, seg_end, float(speed)))
    else:
        await asyncio.sleep(duration)


async def exec_screenshot(page: Page, step: dict, config: dict) -> None:
    """screenshot アクション: 実際のキャプチャは run_recording 側で統一処理。"""
    label = step.get("caption", step.get("label", "screenshot"))
    print(f"  screenshot: {label}")


async def exec_fill(page: Page, step: dict, config: dict) -> None:
    """テキスト入力。force: true でアクションabilityチェックをスキップ。"""
    selector = step["selector"]
    value = str(step["value"])
    force = step.get("force", False)
    timeout_ms = config.get("timeout", 60) * 1000
    print(f"  fill: {selector} = '{value[:40]}{'...' if len(value) > 40 else ''}'")
    locator = page.locator(selector).first
    await locator.wait_for(state="visible", timeout=timeout_ms)
    await locator.click(timeout=timeout_ms)
    await locator.fill(value, timeout=timeout_ms, force=force)


async def exec_upload(page: Page, step: dict, config: dict) -> None:
    """ファイルアップロード（input[type=file] にファイルをセット）。"""
    selector = step["selector"]
    file_path = step["file"]
    timeout_ms = config.get("timeout", 60) * 1000

    # 相対パスの場合、シナリオ YAML のディレクトリからの相対パスとして解決
    resolved = Path(file_path)
    if not resolved.is_absolute():
        scenario_dir = config.get("_scenario_dir")
        if scenario_dir:
            resolved = Path(scenario_dir) / file_path

    if not resolved.exists():
        raise FileNotFoundError(f"upload ファイルが見つかりません: {resolved}")

    print(f"  upload: {selector} <- {resolved.name}")
    locator = page.locator(selector)
    await locator.set_input_files(str(resolved), timeout=timeout_ms)


async def exec_scroll_to(page: Page, step: dict, config: dict) -> None:
    """要素までスムーズスクロール。"""
    selector = step["selector"]
    print(f"  scroll_to: {selector}")
    found = await page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (el) {
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                return true;
            }
            return false;
        }""",
        selector,
    )
    if not found:
        print(f"  WARNING: 要素が見つかりません: {selector}")


async def exec_highlight(page: Page, step: dict, config: dict) -> None:
    """要素を赤枠でハイライト（注目させる演出）。
    selector_last: true を指定すると querySelectorAll の末尾要素を対象にする。
    """
    selector = step["selector"]
    duration = step.get("duration", 2.0)
    use_last = step.get("selector_last", False)
    print(f"  highlight: {selector} ({duration}s)" + (" [last]" if use_last else ""))
    await page.evaluate(
        """([sel, dur, useLast]) => {
            let el;
            if (useLast) {
                const els = document.querySelectorAll(sel);
                el = els.length > 0 ? els[els.length - 1] : null;
            } else {
                el = document.querySelector(sel);
            }
            if (!el) return;
            el.style.outline = '3px solid #e63946';
            el.style.outlineOffset = '2px';
            el.style.borderRadius = '4px';
            el.style.transition = 'outline 0.3s';
            setTimeout(() => {
                el.style.outline = '';
                el.style.outlineOffset = '';
            }, dur * 1000);
        }""",
        [selector, duration, use_last],
    )


async def exec_wait_for_url(page: Page, step: dict, config: dict) -> None:
    """URL パターンへの遷移を待機。"""
    url_pattern = step["url_pattern"]
    timeout_ms = config.get("timeout", 60) * 1000
    print(f"  wait_for_url: {url_pattern}")
    await page.wait_for_url(f"**{url_pattern}**", timeout=timeout_ms)
    await page.wait_for_load_state("networkidle")


async def exec_go_back(page: Page, step: dict, config: dict) -> None:
    """ブラウザの戻るボタン相当。前のページに戻る。"""
    wait_until = step.get("wait_until", "networkidle")
    print(f"  go_back (wait_until={wait_until})")
    try:
        await page.go_back(wait_until=wait_until, timeout=30000)
    except Exception:
        # タイムアウト等でも戻れていれば問題ない
        pass


ACTION_MAP = {
    "goto": exec_goto,
    "click": exec_click,
    "wait_for_hitl": exec_wait_for_hitl,
    "approve": exec_approve,
    "wait_for_complete": exec_wait_for_complete,
    "pause": exec_pause,
    "screenshot": exec_screenshot,
    "go_back": exec_go_back,
    # v4.0 追加
    "fill": exec_fill,
    "upload": exec_upload,
    "scroll_to": exec_scroll_to,
    "highlight": exec_highlight,
    "wait_for_url": exec_wait_for_url,
}


# ── メイン録画関数 ────────────────────────────────────

async def run_recording(
    scenario: dict,
    output_dir: Path,
    headed: bool,
) -> tuple[Path | None, Path | None, list[Path]]:
    """シナリオに従って操作・録画し、(rawパス, captionedパス, キャプチャリスト) を返す。"""
    config = scenario["config"]
    steps = scenario["steps"]
    width = config.get("width", 1280)
    height = config.get("height", 720)
    caption_mode = config.get("caption_mode", "js")
    caption_font_size = config.get("caption_font_size")
    font_size_str = f"{caption_font_size}px" if caption_font_size else None
    video_tmp = tempfile.mkdtemp(prefix="demo_video_")

    output_name = config.get("output_name", "recording")
    captures_dir = output_dir / f"{output_name}_captures"
    captured_files: list[Path] = []
    caption_timings: list[tuple[float, float, str]] = []

    # プロキシ設定：config["proxy"] → 環境変数 HTTPS_PROXY / HTTP_PROXY の順で取得
    import os as _os
    proxy_server = (
        config.get("proxy")
        or _os.environ.get("HTTPS_PROXY")
        or _os.environ.get("HTTP_PROXY")
    )
    launch_proxy = {"server": proxy_server} if proxy_server else None
    if launch_proxy:
        print(f"  Proxy: {proxy_server}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not headed,
            proxy=launch_proxy,
        )
        # ignore_https_errors: プロキシ SSL 検査（MITM）環境での証明書エラー回避
        ignore_ssl = config.get("ignore_https_errors", bool(launch_proxy))
        context = await browser.new_context(
            viewport={"width": width, "height": height},
            record_video_dir=video_tmp,
            record_video_size={"width": width, "height": height},
            locale="ja-JP",
            ignore_https_errors=ignore_ssl,
        )
        page = await context.new_page()

        # 録画開始直後の背景色（config で上書き可）
        init_bg = config.get("init_bg", "#1a1a2e")
        await page.evaluate(
            f"document.documentElement.style.backgroundColor = '{init_bg}';"
            f"document.body.style.backgroundColor = '{init_bg}';"
        )
        video_start_time = time.monotonic()
        # exec_pause が速度区間を記録できるよう config に注入
        config["_video_start_time"] = video_start_time
        config.setdefault("_speed_segments", [])

        try:
            # Readiness チェック
            print("[0] Readiness check...")
            if not await check_readiness(page, config):
                return None, None, captured_files

            # ステップ実行
            for i, step in enumerate(steps, 1):
                action = step["action"]
                pause_after = step.get("pause_after", 0)
                caption = step.get("caption")

                print(f"[{i}/{len(steps)}] {action}")

                # JS キャプション: アクション実行前に表示
                min_duration = config.get("caption_min_duration", 3.0)
                if caption and caption_mode == "js":
                    cap_dur = max(pause_after, min_duration) if pause_after > 0 else min_duration
                    await show_js_caption(page, caption, cap_dur, font_size_str)

                try:
                    handler = ACTION_MAP[action]
                    await handler(page, step, config)
                except Exception as e:
                    print(f"FAIL at step {i} ({action}): {e}")
                    screenshot_path = await save_error_screenshot(page, output_dir, f"step{i}_{action}")
                    print(f"  Screenshot: {screenshot_path}")
                    print(f"  Re-run command: python {__file__} --scenario <yaml> --output {output_dir}")
                    raise

                # ASS 用タイミング記録（両モードで記録。ASS 使わなくてもログ用に便利）
                cap_start = time.monotonic() - video_start_time

                if pause_after > 0:
                    await asyncio.sleep(pause_after)

                cap_end = time.monotonic() - video_start_time
                if caption:
                    if cap_end - cap_start < min_duration:
                        cap_end = cap_start + min_duration
                    caption_timings.append((cap_start, cap_end, caption))
                    print(f"  caption: [{cap_start:.1f}s-{cap_end:.1f}s] {caption}")

                # capture フラグ or screenshot アクション → スクリーンショット保存
                should_capture = step.get("capture", False) or action == "screenshot"
                if should_capture:
                    label = step.get("caption", step.get("step_label", action))
                    cap_path = await save_capture(page, captures_dir, i, label)
                    captured_files.append(cap_path)

            print("[OK] 全ステップ完了")

            if captured_files:
                print(f"\n[CAP] キャプチャ {len(captured_files)} 枚:")
                for cap in captured_files:
                    print(f"  - {cap.name}")

        except Exception:
            raise

        finally:
            video_path_tmp = await page.video.path()
            await page.close()
            await context.close()
            await browser.close()

    # 動画ファイルをリネーム・移動
    if video_path_tmp and Path(video_path_tmp).exists():
        raw_dest = rename_video(Path(video_path_tmp), output_dir, output_name)
        print(f"\n動画を保存しました: {raw_dest}")
        if captured_files:
            print(f"キャプチャ保存先: {captures_dir}")
        shutil.rmtree(video_tmp, ignore_errors=True)

        # 速度変換後処理（speed 指定の pause があった場合）
        speed_segments = config.get("_speed_segments", [])
        speed_dest = None
        if speed_segments:
            print(f"\n[後処理] 速度変換処理 ({len(speed_segments)} 区間)...")
            speed_dest = apply_speed_segments(raw_dest, output_dir, output_name, speed_segments)
            if speed_dest:
                print(f"  速度変換済み動画: {speed_dest.name}")
            else:
                print("  速度変換に失敗しました（元動画は保持されています）")

        # ASS モード: ffmpeg でキャプション字幕を焼き込み
        captioned_dest = None
        if caption_mode == "ass" and caption_timings:
            print(f"\n[後処理] キャプション字幕の焼き込み ({len(caption_timings)} 件)...")
            ts = raw_dest.stem.replace(output_name + "_", "")
            ass_path = output_dir / f"{output_name}_{ts}.ass"
            ass_content = generate_ass_subtitles(caption_timings, width, height)
            ass_path.write_text(ass_content, encoding="utf-8")
            print(f"  ASS 字幕ファイル: {ass_path.name}")

            captioned_dest_path = output_dir / f"{output_name}_{ts}_captioned.webm"
            captioned_dest = burn_captions_ffmpeg(raw_dest, ass_path, captioned_dest_path)

        return raw_dest, captioned_dest or speed_dest, captured_files
    else:
        print("WARNING: 動画ファイルが見つかりませんでした")
        return None, None, captured_files


# ── CLI ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="YAML 駆動の汎用デモ録画エンジン v4.0")
    parser.add_argument("--scenario", required=True, help="シナリオ YAML ファイルのパス")
    parser.add_argument("--output", default="./output", help="出力ディレクトリ (default: ./output)")
    parser.add_argument("--headed", action="store_true", help="ブラウザを表示して実行")
    parser.add_argument("--timeout", type=int, help="タイムアウト秒数（YAML の設定を上書き）")
    parser.add_argument("--dry-run", action="store_true", help="構文検証のみ（録画しない）")
    args = parser.parse_args()

    # YAML 読み込み
    scenario_path = Path(args.scenario)
    if not scenario_path.exists():
        print(f"ERROR: シナリオファイルが見つかりません: {scenario_path}")
        sys.exit(1)

    with open(scenario_path, "r", encoding="utf-8") as f:
        scenario = yaml.safe_load(f)

    if not scenario:
        print("ERROR: シナリオファイルが空です")
        sys.exit(1)

    # シナリオディレクトリを config に保存（upload の相対パス解決用）
    scenario.setdefault("config", {})["_scenario_dir"] = str(scenario_path.parent.resolve())

    # タイムアウト上書き
    if args.timeout:
        scenario["config"]["timeout"] = args.timeout

    # バリデーション
    errors = validate_scenario(scenario)
    if errors:
        print("ERROR: シナリオの検証に失敗しました:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    # dry-run
    if args.dry_run:
        config = scenario["config"]
        steps = scenario["steps"]
        print("=== dry-run: 構文検証OK ===")
        print(f"  base_url:      {config.get('base_url')}")
        print(f"  output_name:   {config.get('output_name')}")
        print(f"  resolution:    {config.get('width', 1280)}x{config.get('height', 720)}")
        print(f"  timeout:       {config.get('timeout', 60)}s")
        print(f"  caption_mode:  {config.get('caption_mode', 'js')}")
        print(f"  health_check:  {config.get('health_check', True)}")
        print(f"  steps:         {len(steps)}")
        capture_count = 0
        for i, s in enumerate(steps, 1):
            detail = ""
            action = s["action"]
            if action == "goto":
                detail = f" url={s.get('url')}"
            elif action == "click":
                detail = f" selector={s.get('selector')}"
            elif action == "wait_for_hitl":
                detail = f" step_id={s.get('step_id', '(any)')}"
            elif action == "pause":
                spd = s.get("speed")
                detail = f" duration={s.get('duration')}s" + (f" [x{spd}速]" if spd else "")
            elif action == "screenshot":
                detail = f" label={s.get('caption', s.get('label', ''))}"
            elif action == "fill":
                detail = f" selector={s.get('selector')}"
            elif action == "upload":
                detail = f" file={s.get('file')}"
            elif action == "scroll_to":
                detail = f" selector={s.get('selector')}"
            elif action == "highlight":
                last = " [last]" if s.get("selector_last") else ""
                detail = f" selector={s.get('selector')} dur={s.get('duration', 2.0)}s{last}"
            elif action == "wait_for_url":
                detail = f" pattern={s.get('url_pattern')}"
            elif action == "go_back":
                detail = f" wait_until={s.get('wait_until', 'networkidle')}"
            pause = f" +{s.get('pause_after')}s" if s.get("pause_after") else ""
            cap = " [CAP]" if s.get("capture") or action == "screenshot" else ""
            caption = f' "{s["caption"]}"' if s.get("caption") else ""
            if s.get("capture") or action == "screenshot":
                capture_count += 1
            print(f"    [{i}] {action}{detail}{pause}{cap}{caption}")
        print(f"  captures:      {capture_count}")
        print("=== OK ===")
        sys.exit(0)

    # 録画実行
    output_dir = Path(args.output).resolve()

    print("=" * 50)
    print("  デモ録画エンジン v4.0")
    print("=" * 50)
    print(f"  Scenario: {scenario_path}")
    print(f"  Output:   {output_dir}")
    print(f"  Headed:   {args.headed}")
    print(f"  Caption:  {scenario['config'].get('caption_mode', 'js')}")
    print()

    try:
        raw_path, captioned_path, captures = asyncio.run(
            run_recording(
                scenario=scenario,
                output_dir=output_dir,
                headed=args.headed,
            )
        )
    except Exception:
        print("\n録画に失敗しました。")
        sys.exit(1)

    if raw_path:
        print(f"\n{'=' * 50}")
        print("  録画完了!")
        print(f"{'=' * 50}")
        print(f"  元動画:          {raw_path}")
        if captioned_path and captioned_path != raw_path:
            label = "速度変換済み:" if "_speed" in captioned_path.name else "字幕付き動画:"
            print(f"  {label}    {captioned_path}")
        if captures:
            print(f"  キャプチャ ({len(captures)} 枚):")
            for cap in captures:
                print(f"    - {cap}")
    else:
        print("\n録画に失敗しました。")
        sys.exit(1)


if __name__ == "__main__":
    main()
