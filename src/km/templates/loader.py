"""Template loader for Knowledge Maker.

Loads templates from files and generates prompts for AI formatting.
"""

from pathlib import Path
from typing import Optional, List, Dict
import logging

logger = logging.getLogger(__name__)

# Default template embedded in code (fallback)
_DEFAULT_TEMPLATE = """# 業務マニュアル整形テンプレート

## 出力フォーマット

以下の構造に従ってMarkdownを生成してください：

```markdown
# {ドキュメントタイトル}

## サマリー
- 要点1（このドキュメントで最も重要なポイント）
- 要点2
- 要点3

---

## {セクション1のタイトル}
- 内容を箇条書きまたは文章で記載

---

## {セクション2のタイトル}
- 内容
```

## 整形ルール

1. 元のドキュメントの内容を論理的なセクションに分割
2. 各セクションにはH2見出しを付ける
3. セクション間は `---` で区切る
4. サマリーで3〜5個の要点をまとめる
5. 手順や列挙は箇条書きを使用
6. 比較情報や一覧は表形式を使用
7. 画像参照は元のパスを維持
8. 元のドキュメントにない情報を追加しない
"""


def get_default_template() -> str:
    """Get the default template.
    
    First tries to load from src/km/templates/default.md, falls back to embedded template.
    
    Returns:
        Template content as string
    """
    # Try to load from file (src/km/templates/default.md)
    default_paths = [
        Path(__file__).parent / "default.md",  # src/km/templates/default.md
        Path("src/km/templates/default.md"),
    ]
    
    for path in default_paths:
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
                logger.debug(f"Loaded default template from {path}")
                return content
            except Exception as e:
                logger.warning(f"Failed to load template from {path}: {e}")
    
    logger.debug("Using embedded default template")
    return _DEFAULT_TEMPLATE


def load_template(template_path: Optional[Path] = None) -> str:
    """Load a template from file or return default.
    
    Args:
        template_path: Path to template file. If None, uses default template.
        
    Returns:
        Template content as string
        
    Raises:
        FileNotFoundError: If specified template file doesn't exist
    """
    if template_path is None:
        return get_default_template()
    
    template_path = Path(template_path)
    
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    
    content = template_path.read_text(encoding="utf-8")
    logger.info(f"Loaded template from {template_path}")
    return content


def format_images_info(images_dir: Path) -> str:
    """Format images directory info for prompt.
    
    Args:
        images_dir: Path to 04_images/ directory
        
    Returns:
        Formatted string with image information
    """
    if not images_dir.exists():
        return "（画像なし）"
    
    image_files = sorted(images_dir.glob("*"))
    if not image_files:
        return "（画像なし）"
    
    lines = []
    for img_path in image_files:
        if img_path.is_file() and img_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            lines.append(f"- `{img_path.name}`: ../04_images/{img_path.name}")
    
    if not lines:
        return "（画像なし）"
    
    return "\n".join(lines)


def get_template_prompt(
    transcribed_content: str,
    template: str,
    images_dir: Optional[Path] = None,
    document_name: str = "",
) -> str:
    """Generate the full prompt for AI formatting.
    
    Args:
        transcribed_content: Content from transcribed.md
        template: Template content
        images_dir: Path to 04_images/ directory (optional)
        document_name: Name of the document being processed
        
    Returns:
        Complete prompt for AI
    """
    images_info = format_images_info(images_dir) if images_dir else "（画像情報なし）"
    
    prompt = f"""以下のテンプレートに従って、ドキュメントを整形してください。

⚠️ 重要: これは業務マニュアルです。内容を省略・要約せず、すべての詳細を維持してください。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【テンプレート（整形ルール）】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{template}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【元のドキュメント（文字起こし）】
ドキュメント名: {document_name}
文字数: {len(transcribed_content):,}文字
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{transcribed_content}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【利用可能な画像ファイル】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{images_info}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【出力指示】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. テンプレートの構造・ルールに従って整形してください
2. ⚠️ 内容を省略・要約しないでください
3. ⚠️ 元のドキュメントと同等以上の文字数を出力してください
4. 画像参照は `![説明](../04_images/filename.png)` 形式で維持してください
5. 元のドキュメントにない情報を追加しないでください
6. Markdownのみを出力してください（説明不要）
"""
    
    return prompt


def get_system_prompt() -> str:
    """Get the system prompt for AI formatting.
    
    Returns:
        System prompt string
    """
    return """あなたは業務マニュアルを整形する専門家です。

⚠️ 最重要ルール:
- 内容を省略しない
- 要約しない
- 文字数を減らさない
- 元のドキュメントのすべての情報を維持する

あなたの役割:
1. 文字起こしされたドキュメントを「見やすく整形」する（要約ではない）
2. 指定されたテンプレートのフォーマットに従う
3. すべての情報を漏らさず、正確に整形する
4. 画像参照を適切な位置に配置する

注意事項:
- 元のドキュメントにない情報を追加しない
- 画像パスは変更しない（../04_images/filename.png 形式を維持）
- 専門用語や固有名詞は変更しない
- 業務手順、担当者、システム名、期日などの詳細はすべて維持する
"""

