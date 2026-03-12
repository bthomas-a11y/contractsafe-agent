"""Shared utility for force-loading knowledge files into agent context."""

from config import KNOWLEDGE_DIR, NORTH_STAR_DIR


def load_north_star_articles() -> str:
    """
    Force-load ALL North Star articles. Returns combined content.

    These are NOT optional. Every agent that receives this content
    MUST include it in its context. The articles define the voice,
    style, and quality bar for all ContractSafe content.
    """
    articles = []
    if NORTH_STAR_DIR.exists():
        for f in sorted(NORTH_STAR_DIR.glob("*.md")):
            content = f.read_text()
            articles.append(f"### NORTH STAR: {f.stem.replace('_', ' ').title()}\n\n{content}")

    if not articles:
        return "[WARNING: No North Star articles found in knowledge/north_star_articles/]"

    return "\n\n---\n\n".join(articles)


def load_brand_voice() -> str:
    """Force-load the brand voice guide."""
    path = KNOWLEDGE_DIR / "brand_voice.md"
    if path.exists():
        return path.read_text()
    return "[WARNING: brand_voice.md not found]"


def load_style_rules() -> str:
    """Force-load the style rules."""
    path = KNOWLEDGE_DIR / "style_rules.md"
    if path.exists():
        return path.read_text()
    return "[WARNING: style_rules.md not found]"


def load_product_info() -> str:
    """Force-load product knowledge."""
    path = KNOWLEDGE_DIR / "product_info.md"
    if path.exists():
        return path.read_text()
    return "[WARNING: product_info.md not found]"


def load_full_knowledge_pack() -> str:
    """
    Load the complete knowledge pack: brand voice + style rules + North Stars.

    Use this for agents that need full voice/style context.
    """
    return f"""## BRAND VOICE GUIDE (MANDATORY)
{load_brand_voice()}

## STYLE RULES (MANDATORY)
{load_style_rules()}

## NORTH STAR ARTICLES (MANDATORY REFERENCE)
These articles define the voice, thinking patterns, and quality bar.
Read them for HOW they think and write, not WHAT they reference.

{load_north_star_articles()}"""
