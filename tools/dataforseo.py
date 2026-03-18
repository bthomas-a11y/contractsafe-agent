"""DataForSEO SERP API integration for real Google SERP analysis.

Provides actual Google search result data: organic rankings, People Also Ask,
featured snippets, AI Overview content with citations, and SERP feature detection.

Used by Agent 4 (SEO Researcher) for SERP analysis and AI citability analysis.

Gracefully returns empty results when DATAFORSEO credentials are not set.

API docs: https://docs.dataforseo.com/v3/serp/google/organic/live/advanced/
Auth: HTTP Basic with login + password from https://app.dataforseo.com/api-access
"""

from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from config import DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD

_BASE_URL = "https://api.dataforseo.com/v3"
_TIMEOUT = 30


def _available() -> bool:
    return bool(DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD)


def _auth() -> tuple[str, str]:
    return (DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD)


# ---------------------------------------------------------------------------
# Core SERP fetch
# ---------------------------------------------------------------------------

def serp_organic(
    keyword: str,
    location_code: int = 2840,
    language_code: str = "en",
    depth: int = 10,
    device: str = "desktop",
    load_ai_overview: bool = True,
) -> dict:
    """Fetch live Google organic SERP results for a keyword.

    Returns dict with organic results, PAA, featured snippet, related searches,
    AI Overview content (when present), and SERP feature list.
    """
    if not _available():
        return _empty_result()

    payload = [{
        "keyword": keyword,
        "location_code": location_code,
        "language_code": language_code,
        "depth": depth,
        "device": device,
        "load_async_ai_overview": load_ai_overview,
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

    tasks = data.get("tasks", [])
    if not tasks or tasks[0].get("status_code") != 20000:
        return _empty_result()

    results = tasks[0].get("result", [])
    if not results:
        return _empty_result()

    result = results[0]
    items = result.get("items", [])
    item_types = result.get("item_types", [])

    organic = []
    people_also_ask = []
    featured_snippet = None
    related_searches = []
    ai_overview = None

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
            paa_items = item.get("items", [])
            if not paa_items:
                paa_items = [item]
            for paa_item in paa_items:
                if not isinstance(paa_item, dict):
                    continue
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
                if isinstance(rs, dict) and rs.get("title"):
                    related_searches.append(rs["title"])
                elif isinstance(rs, str) and rs:
                    related_searches.append(rs)

        elif item_type == "ai_overview":
            ai_overview = _parse_ai_overview(item)

    return {
        "organic": organic,
        "people_also_ask": people_also_ask,
        "featured_snippet": featured_snippet,
        "related_searches": related_searches,
        "ai_overview": ai_overview,
        "serp_features": item_types,
        "item_types": item_types,
        "keyword": result.get("keyword", keyword),
        "total_results": result.get("se_results_count", 0),
    }


def _parse_ai_overview(item: dict) -> dict:
    """Parse an ai_overview SERP item into structured data."""
    sections = []
    for sub in item.get("items") or []:
        if not isinstance(sub, dict):
            continue
        sections.append({
            "title": sub.get("title"),
            "text": sub.get("text", ""),
            "markdown": sub.get("markdown", ""),
            "references": [
                {"source": r.get("source", ""), "domain": r.get("domain", ""),
                 "url": r.get("url", ""), "title": r.get("title", "")}
                for r in (sub.get("references") or []) if isinstance(r, dict)
            ],
        })

    references = [
        {"source": r.get("source", ""), "domain": r.get("domain", ""),
         "url": r.get("url", ""), "title": r.get("title", ""),
         "text": r.get("text", "")}
        for r in (item.get("references") or []) if isinstance(r, dict)
    ]

    return {
        "has_ai_overview": True,
        "markdown": item.get("markdown", ""),
        "sections": sections,
        "references": references,
    }


def _empty_result() -> dict:
    return {
        "organic": [],
        "people_also_ask": [],
        "featured_snippet": None,
        "related_searches": [],
        "ai_overview": None,
        "serp_features": [],
        "item_types": [],
        "keyword": "",
        "total_results": 0,
    }


# ---------------------------------------------------------------------------
# AI Overview citation pattern analysis
# ---------------------------------------------------------------------------

def _analyze_citation_patterns(ai_overviews: list[dict]) -> dict:
    """Analyze structural patterns across multiple AI Overview markdowns."""
    definition_blocks = 0
    bold_label_lists = 0
    numbered_steps = 0
    data_backed_claims = 0
    table_formats = 0

    for aio in ai_overviews:
        md = aio.get("markdown", "")
        if not md:
            continue

        # Definition blocks: "X is a..." at section/paragraph start
        definition_blocks += len(re.findall(
            r'(?:^|\n)[A-Z][^.\n]*\b(?:is|are|refers to|means)\b[^.\n]+\.',
            md,
        ))

        # Bold-label lists: "**Label:** description" or "**Label** description"
        bold_label_lists += len(re.findall(
            r'\*\*[^*]+\*\*:?\s+\S',
            md,
        ))

        # Numbered steps
        numbered_steps += len(re.findall(r'(?:^|\n)\d+\.\s+', md))

        # Data-backed claims: stats near citation markers [[N]]
        data_backed_claims += len(re.findall(
            r'(?:\d+%|\$[\d,.]+|\d+\s*(?:billion|million))[^[]{0,80}\[\[\d+\]\]',
            md, re.IGNORECASE,
        ))
        # Also check citation before stat
        data_backed_claims += len(re.findall(
            r'\[\[\d+\]\][^[]{0,80}(?:\d+%|\$[\d,.]+|\d+\s*(?:billion|million))',
            md, re.IGNORECASE,
        ))

        # Tables
        table_formats += len(re.findall(r'(?:^|\n)\|.+\|', md))

    return {
        "definition_blocks": definition_blocks,
        "bold_label_lists": bold_label_lists,
        "numbered_steps": numbered_steps,
        "data_backed_claims": data_backed_claims,
        "table_formats": table_formats,
    }


# ---------------------------------------------------------------------------
# Query fanout citability analysis
# ---------------------------------------------------------------------------

def query_fanout_citability(
    target_keyword: str,
    paa_questions: list[str],
    related_searches: list[str],
    our_domain: str = "contractsafe.com",
) -> dict:
    """Analyze AI Overview citations across a keyword's query fanout.

    Fires DataForSEO for the target keyword + top PAA questions + top related
    searches. Produces competitive citability intelligence: who gets cited,
    what patterns get cited, and whether our domain appears.

    Cost: ~$0.0035 per query × up to 9 queries = ~$0.03.
    """
    if not _available():
        return {}

    # Build query list: target + top 4 PAA + top 4 related
    queries = [target_keyword]
    for q in paa_questions[:4]:
        if q and q not in queries:
            queries.append(q)
    for rs in related_searches[:4]:
        if rs and rs not in queries:
            queries.append(rs)

    # Fetch SERP + AI Overview for each query (parallel, 3 at a time)
    per_query = []

    def _fetch(query: str) -> dict:
        result = serp_organic(query, load_ai_overview=True)
        aio = result.get("ai_overview")
        cited_domains = []
        if aio and aio.get("has_ai_overview"):
            cited_domains = [r["domain"] for r in aio.get("references", [])]
        return {
            "query": query,
            "has_ai_overview": bool(aio and aio.get("has_ai_overview")),
            "ai_overview": aio,
            "cited_domains": cited_domains,
            "our_domain_cited": any(our_domain in d for d in cited_domains),
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_fetch, q): q for q in queries}
        for future in as_completed(futures):
            try:
                per_query.append(future.result())
            except Exception:
                per_query.append({
                    "query": futures[future],
                    "has_ai_overview": False,
                    "ai_overview": None,
                    "cited_domains": [],
                    "our_domain_cited": False,
                })

    # Sort by original query order
    query_order = {q: i for i, q in enumerate(queries)}
    per_query.sort(key=lambda x: query_order.get(x["query"], 999))

    # Aggregate citation data
    domain_counts: Counter = Counter()
    our_citation_count = 0
    ai_overviews_for_patterns = []

    for qr in per_query:
        if not qr["has_ai_overview"]:
            continue
        for d in qr["cited_domains"]:
            domain_counts[d] += 1
        if qr["our_domain_cited"]:
            our_citation_count += 1
        if qr.get("ai_overview"):
            ai_overviews_for_patterns.append(qr["ai_overview"])

    top_cited = [
        {"domain": d, "count": c}
        for d, c in domain_counts.most_common(10)
    ]

    queries_with_aio = sum(1 for qr in per_query if qr["has_ai_overview"])

    # Analyze citation patterns across all AI Overviews
    citation_patterns = _analyze_citation_patterns(ai_overviews_for_patterns)

    return {
        "queries_analyzed": len(per_query),
        "queries_with_ai_overview": queries_with_aio,
        "our_domain_cited": our_citation_count > 0,
        "our_citation_count": our_citation_count,
        "domain_citation_frequency": dict(domain_counts),
        "top_cited_domains": top_cited,
        "citation_patterns": citation_patterns,
        "per_query_results": [
            {
                "query": qr["query"],
                "has_ai_overview": qr["has_ai_overview"],
                "cited_domains": qr["cited_domains"],
                "our_domain_cited": qr["our_domain_cited"],
            }
            for qr in per_query
        ],
    }
