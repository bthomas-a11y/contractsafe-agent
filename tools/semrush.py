"""SEMrush API integration for keyword research and competitor analysis.

Calls the SEMrush API directly (same endpoints the MCP server wraps).
Gracefully returns empty results when SEMRUSH_API_KEY is not set.

API docs: https://developer.semrush.com/api/
"""

import httpx
from config import SEMRUSH_API_KEY

_BASE_URL = "https://api.semrush.com/"
_TIMEOUT = 15


def _available() -> bool:
    return bool(SEMRUSH_API_KEY)


def _parse_response(text: str) -> list[dict]:
    """Parse SEMrush semicolon-delimited response into list of dicts."""
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return []
    headers = [h.strip() for h in lines[0].split(";")]
    results = []
    for line in lines[1:]:
        values = [v.strip() for v in line.split(";")]
        if len(values) == len(headers):
            results.append(dict(zip(headers, values)))
    return results


def _api_call(params: dict) -> list[dict]:
    """Make a SEMrush API call with standard error handling."""
    if not _available():
        return []
    try:
        params["key"] = SEMRUSH_API_KEY
        resp = httpx.get(_BASE_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        if "ERROR" in resp.text[:50]:
            return []
        return _parse_response(resp.text)
    except Exception:
        return []


# --- Keyword Research ---

def keyword_overview(keyword: str, database: str = "us") -> list[dict]:
    """
    Get keyword analytics: volume, CPC, competition, trend, SERP features.

    Returns list with one dict per keyword containing:
    - Keyword, Search Volume, CPC, Competition, Number of Results, Trends
    """
    return _api_call({
        "type": "phrase_this",
        "phrase": keyword,
        "database": database,
        "export_columns": "Ph,Nq,Cp,Co,Nr,Td",
    })


def batch_keyword_overview(keywords: list[str], database: str = "us") -> list[dict]:
    """
    Analyze up to 100 keywords at once.

    Returns volume, CPC, competition for each keyword.
    """
    if not _available() or not keywords:
        return []
    # SEMrush batch endpoint uses semicolons between keywords
    phrase = ";".join(keywords[:100])
    return _api_call({
        "type": "phrase_these",
        "phrase": phrase,
        "database": database,
        "export_columns": "Ph,Nq,Cp,Co,Nr,Td",
    })


def related_keywords(keyword: str, database: str = "us", limit: int = 20) -> list[dict]:
    """
    Get related keywords with volume and difficulty data.

    Returns keywords semantically related to the input.
    """
    return _api_call({
        "type": "phrase_related",
        "phrase": keyword,
        "database": database,
        "display_limit": str(limit),
        "export_columns": "Ph,Nq,Cp,Co,Nr,Td,Kd",
    })


def keyword_questions(keyword: str, database: str = "us", limit: int = 20) -> list[dict]:
    """
    Get question-based keywords (People Also Ask style).

    Returns questions people search containing the keyword.
    """
    return _api_call({
        "type": "phrase_questions",
        "phrase": keyword,
        "database": database,
        "display_limit": str(limit),
        "export_columns": "Ph,Nq,Cp,Co,Nr",
    })


def keyword_difficulty(keywords: list[str], database: str = "us") -> list[dict]:
    """
    Get keyword difficulty index (0-100) for up to 100 keywords.

    Higher = harder to rank for.
    """
    if not _available() or not keywords:
        return []
    phrase = ";".join(keywords[:100])
    return _api_call({
        "type": "phrase_kdi",
        "phrase": phrase,
        "database": database,
    })


def broad_match_keywords(keyword: str, database: str = "us", limit: int = 20) -> list[dict]:
    """
    Get broad match / alternate search queries.

    Returns keyword variations and long-tail phrases.
    """
    return _api_call({
        "type": "phrase_fullsearch",
        "phrase": keyword,
        "database": database,
        "display_limit": str(limit),
        "export_columns": "Ph,Nq,Cp,Co,Nr,Td,Kd",
    })


# --- Competitor / Domain Analysis ---

def domain_organic_keywords(domain: str, database: str = "us", limit: int = 20) -> list[dict]:
    """
    Get organic keywords a domain ranks for.

    Returns keywords, positions, volume, traffic estimates.
    """
    return _api_call({
        "type": "domain_organic",
        "domain": domain,
        "database": database,
        "display_limit": str(limit),
        "export_columns": "Ph,Po,Nq,Cp,Ur,Tr,Tc,Co,Nr,Td",
    })


def domain_competitors(domain: str, database: str = "us", limit: int = 10) -> list[dict]:
    """
    Get organic search competitors for a domain.

    Returns competing domains with overlap metrics.
    """
    return _api_call({
        "type": "domain_organic_organic",
        "domain": domain,
        "database": database,
        "display_limit": str(limit),
    })


def domain_overview(domain: str, database: str = "us") -> list[dict]:
    """
    Get domain overview: organic traffic, keywords count, backlinks, etc.
    """
    return _api_call({
        "type": "domain_rank",
        "domain": domain,
        "database": database,
    })


# --- Convenience Functions ---

def full_keyword_analysis(keyword: str, database: str = "us") -> dict:
    """
    Run comprehensive keyword analysis using all available SEMrush endpoints.

    Returns a unified research package. Gracefully returns empty dict if
    SEMRUSH_API_KEY is not set.
    """
    if not _available():
        return {"available": False}

    overview = keyword_overview(keyword, database)
    related = related_keywords(keyword, database)
    questions = keyword_questions(keyword, database)
    broad = broad_match_keywords(keyword, database)

    # Extract just the keyword phrases for difficulty check
    all_kws = [keyword]
    all_kws.extend(r.get("Keyword", r.get("Ph", "")) for r in related[:10])
    difficulty = keyword_difficulty(all_kws, database)

    return {
        "available": True,
        "keyword": keyword,
        "overview": overview[0] if overview else {},
        "related_keywords": related,
        "questions": questions,
        "broad_match": broad,
        "difficulty": difficulty,
        "search_volume": overview[0].get("Search Volume", "N/A") if overview else "N/A",
        "keyword_difficulty": (
            difficulty[0].get("Keyword Difficulty Index", "N/A")
            if difficulty else "N/A"
        ),
    }


def competitor_keyword_gap(our_domain: str, competitor_domain: str, database: str = "us") -> dict:
    """
    Find keywords a competitor ranks for that we don't.

    Useful for content gap analysis.
    """
    if not _available():
        return {"available": False}

    our_kws = domain_organic_keywords(our_domain, database, limit=50)
    their_kws = domain_organic_keywords(competitor_domain, database, limit=50)

    our_phrases = {r.get("Keyword", r.get("Ph", "")).lower() for r in our_kws}
    gap_keywords = [
        r for r in their_kws
        if r.get("Keyword", r.get("Ph", "")).lower() not in our_phrases
    ]

    return {
        "available": True,
        "our_keyword_count": len(our_kws),
        "competitor_keyword_count": len(their_kws),
        "gap_keywords": gap_keywords,
    }
