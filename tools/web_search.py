"""Web search tool using Tavily API (free tier: 1000 searches/month)."""

from tavily import TavilyClient
from config import TAVILY_API_KEY, TAVILY_SEARCH_DEPTH, TAVILY_MAX_RESULTS

_client = None


def _get_client() -> TavilyClient:
    global _client
    if _client is None:
        if not TAVILY_API_KEY:
            raise RuntimeError(
                "TAVILY_API_KEY not set. Sign up for a free key at https://tavily.com "
                "(1,000 searches/month, no credit card required). "
                "Then: export TAVILY_API_KEY='tvly-...'"
            )
        _client = TavilyClient(api_key=TAVILY_API_KEY)
    return _client


def web_search(query: str, num_results: int = TAVILY_MAX_RESULTS) -> list[dict]:
    """
    Search the web using Tavily.

    Returns list of {title, url, snippet, score}.
    Each call uses 1 credit (basic) or 2 credits (advanced).
    """
    try:
        client = _get_client()
        response = client.search(
            query=query,
            search_depth=TAVILY_SEARCH_DEPTH,
            max_results=min(num_results, 20),
        )

        results = []
        for item in response.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
                "score": item.get("score", 0),
            })
        return results

    except Exception as e:
        return [{
            "title": "Search Error",
            "url": "",
            "snippet": f"Tavily search failed: {e}",
            "score": 0,
        }]


def web_search_with_answer(query: str, num_results: int = TAVILY_MAX_RESULTS) -> dict:
    """
    Search with Tavily's AI-generated answer included.

    Returns {answer, results: [{title, url, snippet, score}]}.
    Uses 1 credit (basic) or 2 credits (advanced).
    """
    try:
        client = _get_client()
        response = client.search(
            query=query,
            search_depth=TAVILY_SEARCH_DEPTH,
            max_results=min(num_results, 20),
            include_answer=True,
        )

        results = []
        for item in response.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
                "score": item.get("score", 0),
            })

        return {
            "answer": response.get("answer", ""),
            "results": results,
        }

    except Exception as e:
        return {
            "answer": "",
            "results": [{"title": "Search Error", "url": "", "snippet": str(e), "score": 0}],
        }
