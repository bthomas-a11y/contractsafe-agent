"""DataForSEO SERP API integration for real Google SERP analysis.

Provides actual Google search result data: organic rankings, People Also Ask,
featured snippets, and SERP feature detection. Used by Agent 4 (SEO Researcher)
to supplement or replace Tavily-based SERP analysis.

Gracefully returns empty results when DATAFORSEO credentials are not set.

API docs: https://docs.dataforseo.com/v3/serp/google/organic/live/advanced/
Auth: HTTP Basic with login + password from https://app.dataforseo.com/api-access
"""

from __future__ import annotations

import httpx
from config import DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD

_BASE_URL = "https://api.dataforseo.com/v3"
_TIMEOUT = 30


def _available() -> bool:
    return bool(DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD)


def _auth() -> tuple[str, str]:
    return (DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD)


def serp_organic(
    keyword: str,
    location_code: int = 2840,
    language_code: str = "en",
    depth: int = 10,
    device: str = "desktop",
) -> dict:
    """Fetch live Google organic SERP results for a keyword.

    Args:
        keyword: Search query (max 700 chars)
        location_code: DataForSEO location code (2840 = United States)
        language_code: Language code (default "en")
        depth: Number of results (default 10, max 200)
        device: "desktop" or "mobile"

    Returns dict with:
        organic: list of {position, title, url, domain, snippet, breadcrumb}
        people_also_ask: list of {question, expanded_element}
        featured_snippet: {title, url, description} or None
        related_searches: list of strings
        serp_features: list of detected feature type strings
        item_types: list of all SERP element types present
    """
    if not _available():
        return _empty_result()

    payload = [{
        "keyword": keyword,
        "location_code": location_code,
        "language_code": language_code,
        "depth": depth,
        "device": device,
    }]

    try:
        resp = httpx.post(
            f"{_BASE_URL}/serp/google/organic/live/advanced",
            auth=_auth(),
            json=payload,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return _empty_result()

    # Navigate response structure
    tasks = data.get("tasks", [])
    if not tasks or tasks[0].get("status_code") != 20000:
        return _empty_result()

    results = tasks[0].get("result", [])
    if not results:
        return _empty_result()

    result = results[0]
    items = result.get("items", [])
    item_types = result.get("item_types", [])

    # Parse items by type
    organic = []
    people_also_ask = []
    featured_snippet = None
    related_searches = []

    for item in items:
        item_type = item.get("type", "")

        if item_type == "organic":
            organic.append({
                "position": item.get("rank_absolute", 0),
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "domain": item.get("domain", ""),
                "snippet": item.get("description", ""),
                "breadcrumb": item.get("breadcrumb", ""),
            })

        elif item_type == "people_also_ask":
            for paa_item in item.get("items", [item]):
                question = paa_item.get("title", "")
                if question:
                    people_also_ask.append({
                        "question": question,
                        "url": paa_item.get("url", ""),
                        "snippet": paa_item.get("description", ""),
                    })

        elif item_type == "featured_snippet":
            featured_snippet = {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "description": item.get("description", ""),
                "domain": item.get("domain", ""),
            }

        elif item_type == "related_searches":
            for rs in item.get("items", []):
                if rs.get("title"):
                    related_searches.append(rs["title"])

    return {
        "organic": organic,
        "people_also_ask": people_also_ask,
        "featured_snippet": featured_snippet,
        "related_searches": related_searches,
        "serp_features": item_types,
        "item_types": item_types,
        "keyword": result.get("keyword", keyword),
        "total_results": result.get("se_results_count", 0),
    }


def _empty_result() -> dict:
    return {
        "organic": [],
        "people_also_ask": [],
        "featured_snippet": None,
        "related_searches": [],
        "serp_features": [],
        "item_types": [],
        "keyword": "",
        "total_results": 0,
    }
