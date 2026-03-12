"""Free keyword research tools using Google Autocomplete + KeywordsPeopleUse API."""

import time
import httpx
from config import KEYWORDS_PEOPLE_USE_API_KEY

_AUTOCOMPLETE_URL = "http://suggestqueries.google.com/complete/search"
_KPU_BASE_URL = "https://api.keywordspeopleuse.com/v1"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# --- Google Autocomplete (always free, no key) ---

def google_autocomplete(query: str, lang: str = "en", country: str = "us") -> list[str]:
    """
    Get Google Autocomplete suggestions for a query.

    Completely free, no API key needed. Returns 8-10 suggestions.
    """
    try:
        resp = httpx.get(
            _AUTOCOMPLETE_URL,
            params={"client": "firefox", "q": query, "hl": lang, "gl": country},
            headers=_HEADERS,
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return data[1] if len(data) > 1 else []
    except Exception:
        return []


def expand_keyword(seed: str, lang: str = "en", country: str = "us") -> dict:
    """
    Expand a seed keyword using Google Autocomplete alphabet technique.

    Queries "seed a", "seed b", ..., "seed z" plus question prefixes.
    Returns a structured keyword map. Uses ~32 free API calls.
    """
    results = {
        "seed": seed,
        "base_suggestions": [],
        "alphabet_expansions": {},
        "question_expansions": {},
        "all_keywords": set(),
    }

    # Base suggestions
    base = google_autocomplete(seed, lang, country)
    results["base_suggestions"] = base
    results["all_keywords"].update(base)
    time.sleep(0.3)

    # Alphabet expansion
    for letter in "abcdefghijklmnopqrstuvwxyz":
        suggestions = google_autocomplete(f"{seed} {letter}", lang, country)
        if suggestions:
            results["alphabet_expansions"][letter] = suggestions
            results["all_keywords"].update(suggestions)
        time.sleep(0.3)

    # Question prefix expansion
    for prefix in ["how to", "what is", "why", "when to", "best"]:
        suggestions = google_autocomplete(f"{prefix} {seed}", lang, country)
        if suggestions:
            results["question_expansions"][prefix] = suggestions
            results["all_keywords"].update(suggestions)
        time.sleep(0.3)

    # Convert set to sorted list
    results["all_keywords"] = sorted(results["all_keywords"])
    return results


def get_related_questions(keyword: str) -> list[str]:
    """
    Get question-form keywords related to a topic using autocomplete.

    Queries with question starters to find what people ask.
    """
    questions = []
    prefixes = [
        f"what is {keyword}",
        f"how does {keyword}",
        f"why {keyword}",
        f"when to {keyword}",
        f"is {keyword}",
        f"can {keyword}",
        f"do I need {keyword}",
        f"{keyword} vs",
    ]

    for prefix in prefixes:
        suggestions = google_autocomplete(prefix)
        questions.extend(suggestions)
        time.sleep(0.3)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for q in questions:
        if q.lower() not in seen:
            seen.add(q.lower())
            unique.append(q)

    return unique


# --- KeywordsPeopleUse API (free tier with API key) ---

def _kpu_available() -> bool:
    return bool(KEYWORDS_PEOPLE_USE_API_KEY)


def kpu_people_also_ask(keyword: str, country: str = "us", language: str = "en") -> list[dict]:
    """
    Get People Also Ask questions using KeywordsPeopleUse API.

    Returns list of {question, snippet} from Google's PAA boxes.
    Requires KEYWORDS_PEOPLE_USE_API_KEY to be set.
    """
    if not _kpu_available():
        return []

    try:
        resp = httpx.get(
            f"{_KPU_BASE_URL}/paa",
            params={"keyword": keyword, "country": country, "language": language},
            headers={"Authorization": f"Bearer {KEYWORDS_PEOPLE_USE_API_KEY}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", data) if isinstance(data, dict) else data
    except Exception:
        return []


def kpu_autocomplete(keyword: str, country: str = "us", language: str = "en") -> list[str]:
    """
    Get autocomplete suggestions via KeywordsPeopleUse.

    Alternative to Google Autocomplete with potentially different results.
    """
    if not _kpu_available():
        return []

    try:
        resp = httpx.get(
            f"{_KPU_BASE_URL}/autocomplete",
            params={"keyword": keyword, "country": country, "language": language},
            headers={"Authorization": f"Bearer {KEYWORDS_PEOPLE_USE_API_KEY}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("results", []) if isinstance(data, dict) else []
    except Exception:
        return []


def kpu_semantic_keywords(keyword: str, country: str = "us", language: str = "en") -> list[str]:
    """
    Get semantically related keywords via KeywordsPeopleUse.

    Returns keywords that are semantically related to the input.
    """
    if not _kpu_available():
        return []

    try:
        resp = httpx.get(
            f"{_KPU_BASE_URL}/semantic",
            params={"keyword": keyword, "country": country, "language": language},
            headers={"Authorization": f"Bearer {KEYWORDS_PEOPLE_USE_API_KEY}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("results", []) if isinstance(data, dict) else []
    except Exception:
        return []


def full_keyword_research(keyword: str) -> dict:
    """
    Run comprehensive keyword research combining all available tools.

    Uses Google Autocomplete (always free) + KeywordsPeopleUse (if key set).
    Returns a unified keyword research package.
    """
    research = {
        "keyword": keyword,
        "autocomplete_suggestions": [],
        "alphabet_expansion": {},
        "questions_from_autocomplete": [],
        "people_also_ask": [],
        "semantic_keywords": [],
        "kpu_autocomplete": [],
        "all_keywords": [],
        "all_questions": [],
    }

    # Google Autocomplete (always available)
    expansion = expand_keyword(keyword)
    research["autocomplete_suggestions"] = expansion["base_suggestions"]
    research["alphabet_expansion"] = expansion["alphabet_expansions"]
    research["all_keywords"] = expansion["all_keywords"]

    related_q = get_related_questions(keyword)
    research["questions_from_autocomplete"] = related_q
    research["all_questions"] = related_q[:]

    # KeywordsPeopleUse (if API key available)
    if _kpu_available():
        paa = kpu_people_also_ask(keyword)
        research["people_also_ask"] = paa
        # Extract just the question text if it's a list of dicts
        if paa and isinstance(paa[0], dict):
            paa_questions = [item.get("question", "") for item in paa if item.get("question")]
            research["all_questions"].extend(paa_questions)
        elif paa and isinstance(paa[0], str):
            research["all_questions"].extend(paa)

        semantic = kpu_semantic_keywords(keyword)
        research["semantic_keywords"] = semantic

        kpu_ac = kpu_autocomplete(keyword)
        research["kpu_autocomplete"] = kpu_ac
        research["all_keywords"] = sorted(set(research["all_keywords"] + kpu_ac))

    # Deduplicate questions
    seen = set()
    unique_q = []
    for q in research["all_questions"]:
        q_lower = q.lower() if isinstance(q, str) else str(q).lower()
        if q_lower not in seen:
            seen.add(q_lower)
            unique_q.append(q)
    research["all_questions"] = unique_q

    return research
