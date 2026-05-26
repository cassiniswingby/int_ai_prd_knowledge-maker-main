#!/usr/bin/env python3
"""Vision API client for OCR Markdown extraction."""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from .openai_client import get_openai_client, get_model_name

logger = logging.getLogger(__name__)


def _detect_image_mime_type(image_bytes: bytes) -> str:
    """画像のマジックバイトから MIME タイプを判定する."""
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    elif image_bytes[:3] == b'\xff\xd8\xff':
        return "image/jpeg"
    elif image_bytes[:6] in (b'GIF87a', b'GIF89a'):
        return "image/gif"
    elif image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return "image/webp"
    return "image/png"  # fallback


# デフォルトプロンプト（PDF/Word/Excel用）
# チャットボット向けナレッジベース用の詳細プロンプト
_DEFAULT_PROMPT_TEXT = """この図・画像を分析し、チャットボット用ナレッジベースに最適な形式で出力してください。

【スキップ判定】※非常に厳格に判定 - 迷ったら必ず分析する

以下の**全て**に該当し、かつ**確信がある場合のみ**「SKIP」と回答：
- 企業ロゴ**単体**（ロゴだけで他に何もない）
- 装飾用アイコン**単体**（意味のない飾り）
- 背景画像**のみ**（単なる模様や色）

※以下は**絶対にスキップしない**（必ず分析する）：
- 表、一覧表、テーブルを含む画像
- メールテンプレート、帳票、フォーム
- テキストが含まれる画像（少量でも）
- フローチャート、図解
- スクリーンショット

⚠️ 重要: 判断に迷う場合は「SKIP」ではなく必ず分析してください

【図の種類を判定】

■ タイプA「書き起こし優先」:
  - メールテンプレート、メール文面
  - 申込書、契約書、請求書などの帳票
  - 表、一覧表、料金表
  - フォーム、入力画面
  → テキストを完全に書き起こすことが最重要

■ タイプB「説明優先」:
  - フローチャート、ワークフロー図
  - 組織図、階層図
  - 概念図、システム構成図
  → 図が何を説明しているかの理解が最重要

【出力形式】

▼ タイプAの場合（書き起こし優先）:
1. 図の種類を1行で記載
2. 図内のテキストを**省略せず完全に**書き起こす
3. 補足情報を簡潔に

▼ 表形式の場合:
※チャットボットで正確に参照されるため、丁寧に記載
1. 表のタイトル・目的を記載
2. ヘッダー行を明示
3. **全ての行・列を省略せずに**書き起こす
4. 検索キーワードを列挙

▼ タイプBの場合（説明優先）:
1. 図の種類を1行で記載
2. 図が説明している内容・目的を記載
3. プロセスの流れや要素間の関係を説明
4. 重要なキーワードを列挙

【共通ルール】
- 日本語で出力
- 500文字以内を目安（表やメール文面は全文書き起こし必要なので超過OK）
- ベクトル検索でヒットするようにキーワードを含める"""


# PPTX専用プロンプト（スクショ必要性判定付き）
_PPTX_PROMPT_TEXT = """このスライドを分析し、チャットボット用ナレッジベースに最適な形式で出力してください。

【ステップ0: スクショ必要性判定】※回答の最初に必ず出力

以下に該当する場合は「SCREENSHOT_NEEDED」と出力:
- フローチャート、業務フロー、プロセス図、ワークフロー図
- 複雑な表（3行×3列以上、または入り組んだ構造）
- 組織図、構成図、概念図、システム構成図
- 複雑な図形やオブジェクトが多いスライド
- 図解、ダイアグラム

該当しない場合は「TEXT_ONLY」と出力:
- シンプルなテキストのみのスライド
- セクションタイトル、章見出し
- 箇条書きのみのスライド
- 単純な表（2行×2列以下）

【ステップ1: スキップ判定】

以下の**全て**に該当する場合のみ「SKIP」と回答：
- 企業ロゴ**単体**
- 装飾用アイコン**単体**
- 背景画像**のみ**

⚠️ 迷ったら必ず分析してください

【ステップ2: 内容説明】

■ タイプA「書き起こし優先」（表、帳票、フォーム等）:
1. 図の種類を1行で記載
2. 図内のテキストを**省略せず完全に**書き起こす
3. 表は全行・全列を書き起こす

■ タイプB「説明優先」（フローチャート、組織図等）:
1. 図の種類を1行で記載
2. 図が説明している内容・目的を記載
3. プロセスの流れや要素間の関係を説明
4. 重要なキーワードを列挙

■ タイプC「セクションタイトル」:
1. 「セクションタイトル：〇〇」の形式で記載
2. 何の章・節かを簡潔に説明

【共通ルール】
- 日本語で出力
- ベクトル検索でヒットするようにキーワードを含める"""


def get_pptx_prompt() -> str:
    """PPTX専用プロンプトを取得."""
    return os.getenv("OPENAI_PPTX_OCR_PROMPT", _PPTX_PROMPT_TEXT)


def is_skip_response(response: str) -> bool:
    """Check if the OCR response indicates the image should be skipped.

    Args:
        response: OCR API response text

    Returns:
        True if the image should be skipped (logo, icon, decorative)
    """
    if not response:
        return False
    # SKIPで始まるか、SKIPのみの応答かチェック
    normalized = response.strip().upper()
    return normalized == "SKIP" or normalized.startswith("SKIP")


def needs_screenshot(response: str) -> bool:
    """Check if the OCR response indicates a screenshot is needed.

    Args:
        response: OCR API response text

    Returns:
        True if the slide needs a screenshot (complex diagram, flowchart, table)
    """
    if not response:
        return False
    # SCREENSHOT_NEEDEDで始まるかチェック
    normalized = response.strip().upper()
    return normalized.startswith("SCREENSHOT_NEEDED")


def get_default_prompt() -> str:
    """環境変数からプロンプトを取得、未設定ならデフォルトを返す."""
    return os.getenv("OPENAI_OCR_PROMPT", _DEFAULT_PROMPT_TEXT)


class OCRClient:
    """Thin wrapper around OpenAI/Azure OpenAI Vision API for slide OCR."""

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None):
        """Initialize OCR client.

        Args:
            model: Model or deployment name to use. If None, automatically selected
                   based on environment (Azure: AZURE_OPENAI_DEPLOYMENT_VISION,
                   OpenAI: OPENAI_VISION_MODEL)
            api_key: API key (optional, will use environment variable if not provided)
        """
        # Get client (automatically detects OpenAI vs Azure OpenAI)
        # Vision用のエンドポイント・APIキーを使用
        self.client, self._is_azure = get_openai_client(timeout=1500.0, purpose="vision")

        # モデル名の決定（引数 > 環境変数 > デフォルト）
        if model:
            self.model = model
        else:
            self.model = get_model_name(purpose="vision", is_azure=self._is_azure)

        # OpenAI 直通では Responses API の画像入力を優先する。
        # Chat Completions より現行のマルチモーダル入力と相性がよい。
        self._use_responses_api = (
            not self._is_azure
            and bool(os.environ.get("OPENAI_API_KEY"))
            and hasattr(self.client, "responses")
        )

        logger.info(f"OCRClient initialized with model={self.model}, azure={self._is_azure}")

    def image_to_markdown(
        self, image_path: Path, prompt: Optional[str] = None
    ) -> Tuple[bool, Optional[str], str]:
        """Send a single image to the Vision model and return Markdown text.

        Args:
            image_path: Path to the image file
            prompt: OCR prompt (defaults to OPENAI_OCR_PROMPT env var or built-in default)

        Returns:
            Tuple of (success, markdown_text, error_message)
        """
        if not image_path.exists():
            return False, None, f"Image not found: {image_path}"

        # プロンプトの決定（引数 > 環境変数 > デフォルト）
        actual_prompt = prompt if prompt is not None else get_default_prompt()

        try:
            image_bytes = image_path.read_bytes()
            b64 = base64.b64encode(image_bytes).decode("utf-8")
            mime_type = _detect_image_mime_type(image_bytes)

            if self._use_responses_api:
                response = self.client.responses.create(
                    model=self.model,
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": actual_prompt},
                                {
                                    "type": "input_image",
                                    "image_url": f"data:{mime_type};base64,{b64}",
                                },
                            ],
                        }
                    ],
                )
                content = (response.output_text or "").strip()
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": actual_prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                                },
                            ],
                        }
                    ],
                )
                content = response.choices[0].message.content.strip() if response and response.choices else ""

            if not content:
                return False, None, "Vision API returned empty content"
            return True, content, ""
        except Exception as e:
            logger.error(f"OCR request failed: {e}")
            return False, None, f"OCR request failed: {e}"
