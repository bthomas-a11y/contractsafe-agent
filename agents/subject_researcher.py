"""Agent 2: Subject Matter Researcher - fully programmatic.

No LLM calls. Extracts key facts and statistics from web search results
using regex and keyword matching. Builds structured research output.
"""

import re
from datetime import datetime
from agents.base import BaseAgent
from state import PipelineState
from tools.web_search import web_search
from tools.web_fetch import web_fetch


class SubjectResearcherAgent(BaseAgent):
    name = "Subject Researcher"
    description = "Deep-dive research on the article's subject matter"
    agent_number = 2
    emoji = "\U0001f4da"

    def run(self, state: PipelineState) -> PipelineState:
        self.progress("Extracting industry statistics and key facts...")
        current_year = str(datetime.now().year)

        # ── Web searches ──
        search_queries = [
            f"{state.topic} definition overview",
            f"{state.topic} statistics data {current_year}",
            f"{state.topic} best practices",
            f"{state.topic} common mistakes",
        ]

        if state.additional_instructions:
            search_queries.append(f"{state.topic} {state.additional_instructions}")

        all_search_results = []
        for query in search_queries:
            self.progress(f"Searching: {query}")
            results = web_search(query)
            all_search_results.extend(results)

        # Deduplicate by URL
        seen_urls = set()
        unique_results = []
        for r in all_search_results:
            if r["url"] and r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                unique_results.append(r)

        # ── Score and fetch top pages by relevance ──
        topic_words = self._get_topic_words(state)

        def snippet_relevance(result):
            text = (result.get("snippet", "") + " " + result.get("title", "")).lower()
            return sum(1 for w in topic_words if w in text)

        scored = [r for r in unique_results if r.get("url")]
        scored.sort(key=snippet_relevance, reverse=True)
        fetch_urls = scored[:3]

        fetched_pages = []
        for r in fetch_urls:
            url = r["url"]
            self.progress(f"Fetching: {url[:80]}...")
            data = web_fetch(url)
            if data["content"]:
                fetched_pages.append({
                    "url": url,
                    "title": r.get("title", "Unknown"),
                    "content": data["content"][:6000],
                })

        # ── Extract statistics programmatically ──
        self.progress("Extracting statistics from sources...")
        statistics = []
        for page in fetched_pages:
            page_stats = self._extract_statistics(page["content"], page["url"], page["title"])
            statistics.extend(page_stats)

        # Also extract from search snippets (often contain key stats)
        for r in unique_results[:15]:
            snippet = r.get("snippet", "")
            if snippet:
                snippet_stats = self._extract_statistics(
                    snippet, r.get("url", ""), r.get("title", "")
                )
                for s in snippet_stats:
                    # Deduplicate by checking if the stat text is already captured
                    if not any(s["stat"][:30] in existing["stat"] for existing in statistics):
                        statistics.append(s)

        # ── Extract key facts programmatically ──
        self.progress("Extracting key facts from sources...")
        key_facts = []
        for page in fetched_pages:
            page_facts = self._extract_key_facts(page["content"], page["url"], topic_words)
            key_facts.extend(page_facts)

        # ── Build research summary ──
        research_parts = [f"# Subject Research: {state.topic}\n"]

        # Search results overview
        research_parts.append("## Sources Analyzed")
        for r in unique_results[:10]:
            research_parts.append(f"- [{r.get('title', 'Untitled')}]({r.get('url', '')})")
            if r.get("snippet"):
                research_parts.append(f"  {r['snippet'][:150]}")
        research_parts.append("")

        # Key facts
        if key_facts:
            research_parts.append("## Key Facts")
            for fact in key_facts:
                source = fact.get("source", "")
                research_parts.append(f"- {fact['fact']}" + (f" (Source: {source})" if source else ""))
            research_parts.append("")

        # Statistics
        if statistics:
            research_parts.append("## Statistics")
            for stat in statistics:
                source = stat.get("source_name", "")
                research_parts.append(f"- {stat['stat']}" + (f" — {source}" if source else ""))
            research_parts.append("")

        # Relevant excerpts from fetched pages
        if fetched_pages:
            research_parts.append("## Source Excerpts")
            for page in fetched_pages:
                relevant = self._extract_relevant_passages(page["content"], topic_words)
                if relevant:
                    research_parts.append(f"\n### {page['title']}")
                    research_parts.append(f"URL: {page['url']}")
                    research_parts.append(relevant)
            research_parts.append("")

        state.subject_research = "\n".join(research_parts)
        state.key_facts = key_facts[:15]
        state.statistics = statistics[:10]

        self.log(f"Found {len(key_facts)} key facts and {len(statistics)} statistics")
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

    def _extract_statistics(self, text: str, source_url: str, source_name: str) -> list[dict]:
        """Extract statistics (percentages, dollar amounts, large numbers) with context."""
        stats = []
        sentences = re.split(r"(?<=[.!?])\s+", text)

        for sentence in sentences:
            # Skip headings, short lines, list markers
            clean = sentence.strip()
            if not clean or clean.startswith("#") or len(clean) < 20:
                continue

            # Look for statistical patterns
            has_stat = bool(re.search(
                r"\d+(?:\.\d+)?%|\$[\d,.]+\s*(?:billion|million|trillion)?|\d+\s*(?:billion|million|trillion|percent)",
                clean, re.IGNORECASE
            ))

            if has_stat:
                # Clean and truncate
                stat_text = clean[:200].strip()
                if stat_text and len(stat_text.split()) >= 5:
                    stats.append({
                        "stat": stat_text,
                        "source_name": source_name.split("|")[0].split("-")[0].strip()[:50],
                        "source_url": source_url,
                    })

            if len(stats) >= 5:  # Cap per source
                break

        return stats

    def _extract_key_facts(self, text: str, source_url: str, topic_words: set[str]) -> list[dict]:
        """Extract factual statements with high topic relevance."""
        facts = []
        sentences = re.split(r"(?<=[.!?])\s+", text)

        for sentence in sentences:
            clean = sentence.strip()
            if not clean or clean.startswith("#") or len(clean) < 30:
                continue

            # Score by topic word matches
            lower = clean.lower()
            matches = sum(1 for w in topic_words if w in lower)

            # Must have 3+ topic word matches and be a declarative sentence
            if matches >= 3 and not clean.endswith("?"):
                # Skip obvious non-factual content
                if any(skip in lower for skip in [
                    "click here", "subscribe", "sign up", "cookie", "privacy policy",
                    "terms of service", "all rights reserved",
                ]):
                    continue

                facts.append({
                    "fact": clean[:200],
                    "source": source_url,
                    "relevance": matches,
                })

            if len(facts) >= 5:  # Cap per source
                break

        # Sort by relevance
        facts.sort(key=lambda f: f["relevance"], reverse=True)
        return facts

    def _extract_relevant_passages(self, content: str, topic_words: set[str]) -> str:
        """Extract the most relevant paragraphs from a page."""
        paragraphs = content.split("\n\n")
        relevant = []

        for para in paragraphs:
            para = para.strip()
            if not para or para.startswith("#") or len(para) < 50:
                continue

            lower = para.lower()
            matches = sum(1 for w in topic_words if w in lower)
            if matches >= 3:
                relevant.append(para[:300])

            if len(relevant) >= 3:
                break

        return "\n\n".join(relevant) if relevant else ""
