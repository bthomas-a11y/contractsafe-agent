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

# --- DataForSEO ---
# Real Google SERP data for SEO analysis. Sign up at https://app.dataforseo.com
# When set, Agent 4 uses actual SERP rankings instead of Tavily search proxies
DATAFORSEO_LOGIN = os.environ.get("DATAFORSEO_LOGIN", "")
DATAFORSEO_PASSWORD = os.environ.get("DATAFORSEO_PASSWORD", "")

# --- HubSpot CMS ---
HUBSPOT_ACCESS_TOKEN = os.environ.get("HUBSPOT_ACCESS_TOKEN", "")
HUBSPOT_CONTENT_GROUP_ID = os.environ.get("HUBSPOT_CONTENT_GROUP_ID", "")

# --- Asana ---
ASANA_ACCESS_TOKEN = os.environ.get("ASANA_ACCESS_TOKEN", "")
ASANA_WORKSPACE_GID = os.environ.get("ASANA_WORKSPACE_GID", "")

# --- Model Configuration (used with claude CLI) ---
RESEARCH_MODEL = "sonnet"
WRITER_MODEL = "opus"        # Agents 7 (Content Writer) and 8 (Brand Voice) use Opus
EDITING_MODEL = "sonnet"     # Agents 9-11 (Fact Check, SEO, AEO) use Sonnet
HAIKU_MODEL = "haiku"        # Agent 12 (Social Copy) uses Haiku for speed

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
MAX_RETRIES = 1       # No retries. If it fails, investigate immediately.
RETRY_BASE_DELAY = 2  # seconds (unused with MAX_RETRIES=1, kept for interface)

# --- Web Fetch ---
WEB_FETCH_TIMEOUT = 15  # seconds
WEB_FETCH_MAX_CONTENT_LENGTH = 50000  # characters, truncate after this

# --- Tavily ---
TAVILY_SEARCH_DEPTH = "basic"  # "basic" = 1 credit, "advanced" = 2 credits
TAVILY_MAX_RESULTS = 10

# --- Pipeline Time Budget ---
PIPELINE_BUDGET_SECONDS = 600  # 10 minutes hard cap for entire pipeline (research + writing + editing)

# Budget for just the editing tail (agents 8-13, after article is written).
# These agents are editing an existing article — should take ~2 min total.
EDITING_BUDGET_SECONDS = 180  # 3 minutes hard cap for agents 8-13

# Expected times per agent (seconds). If an agent exceeds this, a warning is printed.
# These are caps, not targets — the agent should finish well within.
AGENT_EXPECTED_TIMES = {
    7: 240,   # Writer (Opus) — multi-call: 3-4 calls × ~60s each, cap at 240
    8: 60,    # Brand Voice (Sonnet) — expect ~40s, cap at 60
    9: 5,     # Fact Check — programmatic, near-instant
    10: 60,   # SEO Pass (Sonnet) — expect ~30s after prompt reduction, cap at 60
    11: 90,   # AEO Pass (Sonnet) — expect ~60s, cap at 90
    12: 15,   # Social Copy (Haiku) — expect ~5s, cap at 15
    13: 5,    # Final Validator — programmatic, near-instant
}
