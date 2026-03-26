"""Agent 2: Subject Matter Researcher - fully programmatic.

No LLM calls. Finds and reads authoritative sources to extract verifiable
facts and statistics. Filters out competitor pages and marketing copy.

A blog writer researches by finding independent, authoritative sources —
industry reports, .gov data, nonprofit association studies — and extracting
specific, verifiable facts with clear attribution. They skip competitor
product pages and their own company's marketing.
"""

import re
from datetime import datetime
from urllib.parse import urlparse
from agents.base import BaseAgent
from state import PipelineState
from tools.web_search import web_search
from tools.web_fetch import web_fetch
from link_policy import is_blocked, is_internal


class SubjectResearcherAgent(BaseAgent):
    name = "Subject Researcher"
    description = "Deep-dive research on the article's subject matter"
    agent_number = 2
    emoji = "\U0001f4da"

    # Sources a blog writer would skip
    _SKIP_URL_PATTERNS = [
        "/solutions/", "/features/", "/pricing/", "/demo",
        "/product/", "/platform/", "/get-started",
    ]

    def run(self, state: PipelineState) -> PipelineState:
        self.progress("Researching from authoritative sources...")
        current_year = str(datetime.now().year)
        kw = state.target_keyword

        # ── Web searches — target authoritative, independent sources ──
        # A blog writer searches for KNOWN authoritative sources by name,
        # not generic "statistics" queries that return listicles.
        search_queries = [
            # Named authoritative sources
            f"World Commerce Contracting contract management report cost revenue",
            f"Nonprofit HR staffing survey {current_year} OR {int(current_year)-1}",
            f"National Council of Nonprofits compliance contract challenges report",
            # Government / regulatory
            f"nonprofit grant compliance audit statistics site:gao.gov OR site:gov",
            # Industry data
            f"contract management market report nonprofit {current_year}",
            # Topic-specific research
            f"{kw} statistics survey research",
        ]

        # If keyword cluster identified content gaps, search for data on those
        cluster = state.keyword_cluster or {}
        for gap in cluster.get("content_gaps", [])[:3]:
            gap_topic = gap.get("topic", "")
            if gap_topic:
                search_queries.append(f"{gap_topic} statistics report nonprofit")

        if state.additional_instructions:
            search_queries.append(f"{kw} {state.additional_instructions}")

        all_search_results = []
        for query in search_queries:
            self.progress(f"Searching: {query}")
            results = web_search(query)
            all_search_results.extend(results)

        # Deduplicate by URL
        seen_urls = set()
        unique_results = []
        for r in all_search_results:
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_results.append(r)

        # ── Filter out competitor pages, our own marketing, and product pages ──
        filtered_results = []
        for r in unique_results:
            url = r.get("url", "")
            if not url:
                continue
            # Skip competitor domains
            if is_blocked(url):
                self.progress(f"  Skipping competitor: {url[:60]}")
                continue
            # Skip our own marketing (not independent research)
            if is_internal(url):
                self.progress(f"  Skipping own site: {url[:60]}")
                continue
            # Skip product/solution pages (marketing, not research)
            if any(p in url.lower() for p in self._SKIP_URL_PATTERNS):
                self.progress(f"  Skipping product page: {url[:60]}")
                continue
            filtered_results.append(r)

        self.progress(f"Filtered to {len(filtered_results)} independent sources (from {len(unique_results)} total)")

        # ── Score and fetch top pages ──
        topic_words = self._get_topic_words(state)

        def snippet_relevance(result):
            text = (result.get("snippet", "") + " " + result.get("title", "")).lower()
            return sum(1 for w in topic_words if w in text)

        scored = sorted(filtered_results, key=snippet_relevance, reverse=True)
        fetch_targets = scored[:5]  # Fetch more pages for better research coverage

        fetched_pages = []
        for r in fetch_targets:
            url = r["url"]
            self.progress(f"Reading: {url[:80]}...")
            data = web_fetch(url)
            if data.get("content") and not data.get("error"):
                fetched_pages.append({
                    "url": url,
                    "title": r.get("title", "Unknown"),
                    "content": data["content"][:8000],
                })

        # ── Extract statistics programmatically ──
        self.progress("Extracting statistics from sources...")
        statistics = []
        for page in fetched_pages:
            page_stats = self._extract_statistics(
                page["content"], page["url"], page["title"], topic_words
            )
            statistics.extend(page_stats)

        # Also extract from search snippets (often contain key stats)
        for r in unique_results[:15]:
            snippet = r.get("snippet", "")
            if snippet:
                snippet_stats = self._extract_statistics(
                    snippet, r.get("url", ""), r.get("title", ""), topic_words
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

        # ── Deprioritize previously-cited stats ──
        # Read the ledger of stats already used in other articles.
        # Move previously-cited stats to the end so the writer sees fresh data first.
        previously_cited = self._load_cited_stats()
        if previously_cited and statistics:
            new_stats = []
            reused_stats = []
            for s in statistics:
                stat_text = s.get("stat", "").lower()[:80]
                is_reused = any(cited in stat_text or stat_text in cited for cited in previously_cited)
                if is_reused:
                    reused_stats.append(s)
                else:
                    new_stats.append(s)
            if reused_stats:
                self.progress(f"Deprioritized {len(reused_stats)} previously-cited stats (new stats shown first)")
            statistics = new_stats + reused_stats

        state.subject_research = "\n".join(research_parts)
        state.key_facts = key_facts[:15]
        state.statistics = statistics[:10]

        self.log(f"Found {len(key_facts)} key facts and {len(statistics)} statistics "
                 f"({len([s for s in statistics[:10] if s not in (reused_stats if previously_cited else [])])} new)")
        return state

    def _load_cited_stats(self) -> list[str]:
        """Load previously-cited stat fingerprints from the ledger."""
        from config import KNOWLEDGE_DIR
        ledger_path = KNOWLEDGE_DIR / "cited_stats.md"
        if not ledger_path.exists():
            return []
        try:
            content = ledger_path.read_text()
            # Extract stat text from ledger lines: "stat text | source | slug | date"
            stats = []
            for line in content.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("<!--") and "|" in line:
                    stat_text = line.split("|")[0].strip().lower()[:80]
                    if stat_text:
                        stats.append(stat_text)
            return stats
        except Exception:
            return []

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

    def _extract_statistics(
        self, text: str, source_url: str, source_name: str, topic_words: set[str] = None
    ) -> list[dict]:
        """Extract statistics that are actually about our topic.

        A blog writer only uses stats from pages they've read that are
        directly relevant to the article's subject. A stat about "CRM market
        size" should not appear in a contract management article.
        """
        stats = []
        sentences = re.split(r"(?<=[.!?])\s+", text)

        for sentence in sentences:
            clean = sentence.strip()
            if not clean or clean.startswith("#") or len(clean) < 20:
                continue

            has_stat = bool(re.search(
                r"\d+(?:\.\d+)?%|\$[\d,.]+\s*(?:billion|million|trillion)?|\d+\s*(?:billion|million|trillion|percent)",
                clean, re.IGNORECASE
            ))

            if has_stat:
                stat_text = clean[:200].strip()
                if stat_text and len(stat_text.split()) >= 5:
                    stat_lower = stat_text.lower()

                    # Skip marketing language in stats
                    if any(s in stat_lower for s in [
                        "state-of-the-art", "comprehensive", "cutting-edge",
                        "industry-leading", "best-in-class", "download free",
                        "free sample", "get started", "sign up", "contact us",
                    ]):
                        continue

                    # The stat text itself must be about our topic
                    if topic_words:
                        topic_matches = sum(1 for w in topic_words if w in stat_lower)
                        if topic_matches < 2:
                            continue

                    stats.append({
                        "stat": stat_text,
                        "source_name": source_name.split("|")[0].split("-")[0].strip()[:50],
                        "source_url": source_url,
                    })

            if len(stats) >= 5:
                break

        return stats

    def _extract_key_facts(self, text: str, source_url: str, topic_words: set[str]) -> list[dict]:
        """Extract verifiable factual statements from authoritative sources.

        A blog writer extracts FACTS — verifiable claims with specific details.
        Not marketing copy ("streamline your workflow"), not opinions
        ("it's necessary to implement"), not table-of-contents entries.
        """
        facts = []
        sentences = re.split(r"(?<=[.!?])\s+", text)

        # Marketing language signals — these indicate the sentence is selling,
        # not stating facts
        marketing_signals = [
            "click here", "subscribe", "sign up", "cookie", "privacy policy",
            "terms of service", "all rights reserved", "schedule a demo",
            "get started", "learn more", "contact us", "free trial",
            # Sales language
            "streamline", "transform", "leverage", "empower", "solution to help",
            "it's necessary to implement", "wise decision", "certainly a",
            "beacon", "guiding", "navigate the byways",
            # UI/navigation
            "content:-", "table of contents", "trusted by",
            # Testimonials
            "director,", "manager,", "you can't go wrong",
        ]

        for sentence in sentences:
            clean = sentence.strip()
            if not clean or len(clean) < 30:
                continue

            # Skip headings, list formatting, table of contents
            if clean.startswith("#") or clean.startswith("|") or clean.startswith("-"):
                continue

            lower = clean.lower()

            # Skip marketing copy
            if any(signal in lower for signal in marketing_signals):
                continue

            # Must be a declarative sentence (not a question)
            if clean.endswith("?"):
                continue

            # Score by topic word matches
            matches = sum(1 for w in topic_words if w in lower)

            # Must have 3+ topic word matches
            if matches < 3:
                continue

            # Prefer sentences with specific details: named sources, years, numbers
            has_specifics = bool(re.search(
                r'\d{4}|\d+%|\$[\d,]+|according to|report|survey|study|found that',
                clean, re.IGNORECASE
            ))

            facts.append({
                "fact": clean[:200],
                "source": source_url,
                "relevance": matches + (2 if has_specifics else 0),
            })

            if len(facts) >= 5:
                break

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
