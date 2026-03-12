"""Configuration for the ContractSafe Content Agent System."""

import os
from pathlib import Path

# --- Load .env file if present ---
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _key, _val = _key.strip(), _val.strip()
                if _key and _key not in os.environ:
                    os.environ[_key] = _val

# --- Tavily API Key (free tier: 1000 searches/month) ---
# Set via environment variable or in .env file
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

# --- KeywordsPeopleUse ---
# Free tier, sign up at https://keywordspeopleuse.com for an API key
KEYWORDS_PEOPLE_USE_API_KEY = os.environ.get("KEYWORDS_PEOPLE_USE_API_KEY", "")

# --- SEMrush API ---
# Requires SEMrush API subscription (SEO Business plan or standalone API access)
# When set, enables: search volume, keyword difficulty, related keywords,
# People Also Ask, competitor organic keywords, keyword gap analysis
SEMRUSH_API_KEY = os.environ.get("SEMRUSH_API_KEY", "")

# --- Model Configuration (used with claude CLI) ---
RESEARCH_MODEL = "sonnet"
WRITER_MODEL = "sonnet"

# --- Paths ---
PROJECT_ROOT = Path(__file__).parent
KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"
NORTH_STAR_DIR = KNOWLEDGE_DIR / "north_star_articles"
OUTPUT_DIR = PROJECT_ROOT / "output"

# --- Knowledge Files ---
BRAND_VOICE_FILE = KNOWLEDGE_DIR / "brand_voice.md"
PRODUCT_INFO_FILE = KNOWLEDGE_DIR / "product_info.md"
STYLE_RULES_FILE = KNOWLEDGE_DIR / "style_rules.md"

# --- Claude CLI ---
CLAUDE_CLI = "claude"
CLAUDE_TIMEOUT = 600  # seconds per LLM call
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # seconds, exponential backoff

# --- Web Fetch ---
WEB_FETCH_TIMEOUT = 15  # seconds
WEB_FETCH_MAX_CONTENT_LENGTH = 50000  # characters, truncate after this

# --- Tavily ---
TAVILY_SEARCH_DEPTH = "basic"  # "basic" = 1 credit, "advanced" = 2 credits
TAVILY_MAX_RESULTS = 10
