"""Agent 3: Competitor / Keyword Researcher - fully programmatic.

No LLM calls. Extracts competitor H2 structure, word counts, and gaps
programmatically from fetched pages. Builds keyword intelligence from
Google Autocomplete + SEMrush data.
"""

import re
from agents.base import BaseAgent
from state import PipelineState
from tools.web_search import web_search
from tools.web_fetch import web_fetch
from tools.keyword_research import full_keyword_research
from tools.semrush import full_keyword_analysis as semrush_analysis
from config import SEMRUSH_API_KEY


class CompetitorKWAgent(BaseAgent):
    name = "Competitor/KW Research"
    description = "Analyze competitor content and build keyword intelligence"
    agent_number = 3
    emoji = "\U0001f3f7\ufe0f"

    def run(self, state: PipelineState) -> PipelineState:
        self.progress("Building keyword intelligence and analyzing competitors...")
        # --- Keyword Research (Google Autocomplete + KeywordsPeopleUse, free) ---
        self.progress(f"Running keyword research for: {state.target_keyword}")
        keyword_research = full_keyword_research(state.target_keyword)

        # --- SEMrush keyword data (if API key set) ---
        semrush_data = {}
        if SEMRUSH_API_KEY:
            self.progress("Pulling SEMrush keyword analytics...")
            semrush_data = semrush_analysis(state.target_keyword)
            if semrush_data.get("available"):
                self.log(
                    f"SEMrush: volume={semrush_data.get('search_volume', 'N/A')}, "
                    f"difficulty={semrush_data.get('keyword_difficulty', 'N/A')}"
                )
        else:
            self.progress("SEMrush not configured, using free keyword tools only")

        # --- Competitor Search ---
        search_queries = [
            state.target_keyword,
            f"{state.target_keyword} guide",
            f"what is {state.target_keyword}",
        ]

        all_results = []
        for query in search_queries:
            self.progress(f"Searching: {query}")
            results = web_search(query)
            all_results.extend(results)

        # Deduplicate
        seen = set()
        unique = []
        for r in all_results:
            if r["url"] and r["url"] not in seen:
                seen.add(r["url"])
                unique.append(r)

        # Score competitors by snippet relevance, fetch top 3
        topic_words = self._get_topic_words(state)

        def snippet_relevance(result):
            text = (result.get("snippet", "") + " " + result.get("title", "")).lower()
            return sum(1 for w in topic_words if w in text)

        scored = [r for r in unique if r.get("url")]
        scored.sort(key=snippet_relevance, reverse=True)
        fetch_urls = [r["url"] for r in scored[:3]]

        # --- Fetch and analyze competitor pages programmatically ---
        competitor_pages = []
        for url in fetch_urls:
            title = next((r["title"] for r in unique if r["url"] == url), "Unknown")
            self.progress(f"Fetching competitor: {url[:80]}...")
            data = web_fetch(url)
            if data["content"]:
                page_analysis = self._analyze_page(
                    data["content"][:8000], url, title, topic_words
                )
                competitor_pages.append(page_analysis)

        state.competitor_pages = competitor_pages

        # --- Build keyword_data programmatically ---
        # Derive secondary keywords from autocomplete + SEMrush
        secondary_kws = self._derive_secondary_keywords(
            state, keyword_research, semrush_data
        )

        # Build questions list
        all_questions = list(keyword_research["all_questions"][:15])

        state.keyword_data = {
            "primary_kw": state.target_keyword,
            "secondary_kws": secondary_kws,
            "questions_people_ask": all_questions,
            "related_terms": keyword_research["all_keywords"][:20],
            "autocomplete_suggestions": keyword_research["autocomplete_suggestions"],
            "semantic_keywords": keyword_research.get("semantic_keywords", []),
            "people_also_ask": keyword_research.get("people_also_ask", []),
        }

        # Enrich with SEMrush data — store raw data so Agent 4 can read it
        # instead of making duplicate SEMrush API calls
        if semrush_data.get("available"):
            state.keyword_data["semrush"] = {
                "search_volume": semrush_data.get("search_volume"),
                "keyword_difficulty": semrush_data.get("keyword_difficulty"),
                "overview": semrush_data.get("overview", {}),
                "related_keywords": semrush_data.get("related_keywords", []),
                "questions": semrush_data.get("questions", []),
                "broad_match": semrush_data.get("broad_match", []),
                "difficulty": semrush_data.get("difficulty", []),
            }
            # Merge SEMrush questions into the main questions list
            for q in semrush_data.get("questions", []):
                phrase = q.get("Keyword", q.get("Ph", ""))
                if phrase and phrase not in state.keyword_data["questions_people_ask"]:
                    state.keyword_data["questions_people_ask"].append(phrase)

        self.log(
            f"Analyzed {len(state.competitor_pages)} competitor pages, "
            f"found {len(state.keyword_data['related_terms'])} related terms, "
            f"{len(state.keyword_data['questions_people_ask'])} questions"
            + (f", SEMrush vol={semrush_data.get('search_volume', 'N/A')}"
               if semrush_data.get("available") else "")
        )
        return state

    def _get_topic_words(self, state: PipelineState) -> set[str]:
        """Extract meaningful words from topic and keyword."""
        stopwords = {
            "a", "an", "the", "and", "or", "but", "for", "of", "to", "in",
            "on", "at", "by", "is", "are", "was", "were", "be", "been",
            "your", "our", "their", "this", "that", "with", "how", "what",
            "why", "when", "best", "top", "guide", "tips", "practices",
        }
        words = set()
        for text in [state.topic, state.target_keyword]:
            for w in re.findall(r"[a-z]+", text.lower()):
                if len(w) > 3 and w not in stopwords:
                    words.add(w)
        return words

    def _analyze_page(
        self, content: str, url: str, title: str, topic_words: set[str]
    ) -> dict:
        """Extract structured data from a competitor page programmatically."""
        # Extract H2 headings
        h2s = re.findall(r"^##\s+(.+)$", content, re.MULTILINE)
        if not h2s:
            # Try HTML-style headings
            h2s = re.findall(r"<h2[^>]*>([^<]+)</h2>", content, re.IGNORECASE)

        # Word count
        words = content.split()
        word_count = len(words)

        # Identify gaps: topics in our keyword set not covered by this page
        content_lower = content.lower()
        covered_topics = {w for w in topic_words if w in content_lower}
        missing_topics = topic_words - covered_topics

        # Check for specific content features
        has_stats = bool(re.search(
            r"\d+%|\$[\d,]+|\d+\s*(billion|million|trillion)", content, re.IGNORECASE
        ))
        has_lists = bool(re.search(r"^\s*[-*]\s", content, re.MULTILINE))
        has_tables = bool(re.search(r"\|.*\|.*\|", content))
        has_faq = bool(re.search(r"(?:FAQ|frequently asked|common questions)", content, re.IGNORECASE))

        gaps_description = ""
        if missing_topics:
            gaps_description = f"Missing topics: {', '.join(sorted(missing_topics)[:5])}"
        if not has_stats:
            gaps_description += "; No statistics"
        if not has_faq:
            gaps_description += "; No FAQ section"

        return {
            "url": url,
            "title": title,
            "h2s": h2s[:10],
            "word_count": word_count,
            "gaps": gaps_description.strip("; ") or "Well-covered",
            "has_stats": has_stats,
            "has_lists": has_lists,
            "has_tables": has_tables,
            "has_faq": has_faq,
        }

    def _derive_secondary_keywords(
        self, state: PipelineState, keyword_research: dict, semrush_data: dict
    ) -> list[str]:
        """Derive secondary keywords from autocomplete + SEMrush data."""
        candidates = []

        # Start with user-provided secondary keywords
        candidates.extend(state.secondary_keywords)

        # Add from autocomplete (high-signal, real user queries)
        for kw in keyword_research.get("autocomplete_suggestions", [])[:10]:
            kw_lower = kw.lower().strip()
            if kw_lower and kw_lower != state.target_keyword.lower():
                candidates.append(kw_lower)

        # Add from SEMrush related keywords (sorted by volume)
        if semrush_data.get("available"):
            related = semrush_data.get("related_keywords", [])
            # Sort by volume descending
            related.sort(
                key=lambda x: int(x.get("Search Volume", x.get("Nq", "0")) or 0),
                reverse=True,
            )
            for r in related[:10]:
                phrase = r.get("Keyword", r.get("Ph", "")).lower().strip()
                if phrase and phrase != state.target_keyword.lower():
                    candidates.append(phrase)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for kw in candidates:
            if kw not in seen:
                seen.add(kw)
                unique.append(kw)

        return unique[:15]
