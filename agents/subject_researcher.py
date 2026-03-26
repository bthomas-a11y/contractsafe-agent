"""Agent 2: Subject Matter Researcher - keyword-by-keyword research.

No LLM calls. For each keyword in the cluster, searches for authoritative
pages, reads them, and extracts facts/stats tagged with the keyword they
support. This connects each fact to a specific article section and its
source URL — enabling the writer to cite sources and Agent 10 to link
the keyword to its research source.

A blog writer researches each subtopic of their article individually:
"What data exists about donor agreements?" "What's the compliance picture
for grant contracts?" They don't just search the head keyword and hope
for the best.
"""

import re
from datetime import datetime
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

    _SKIP_URL_PATTERNS = [
        "/solutions/", "/features/", "/pricing/", "/demo",
        "/product/", "/platform/", "/get-started",
    ]

    # Paywalled market research sites that show metadata but no actual content
    _SKIP_DOMAINS = [
        "researchandmarkets.com", "dataintelo.com", "marketintelo.com",
        "grandviewresearch.com", "mordorintelligence.com",
        "alliedmarketresearch.com", "precedenceresearch.com",
        "slashdot.org", "catalog.data.gov",
    ]

    _MARKETING_SIGNALS = [
        "click here", "subscribe", "sign up", "cookie", "privacy policy",
        "terms of service", "all rights reserved", "schedule a demo",
        "get started", "learn more", "contact us", "free trial",
        "streamline", "transform", "leverage", "empower", "solution to help",
        "it's necessary to implement", "wise decision", "certainly a",
        "beacon", "guiding", "navigate the byways",
        "content:-", "table of contents", "trusted by",
        "report description", "table of content", "methodology",
        "editor:", "report analysis",
        "director,", "manager,", "you can't go wrong",
    ]

    def run(self, state: PipelineState) -> PipelineState:
        self.progress("Researching each keyword topic from authoritative sources...")
        topic_words = self._get_topic_words(state)

        # ── Phase 1: Keyword-by-keyword research ──
        # Each cluster keyword is a research assignment.
        cluster = state.keyword_cluster or {}
        supporting_kws = [
            kw.get("keyword", "") for kw in cluster.get("supporting_keywords", [])
            if kw.get("keyword")
        ]

        all_statistics = []
        all_key_facts = []
        all_pages_read = []  # Track every page we read for the research summary
        fetched_urls = set()

        # Research each supporting keyword individually
        for kw in supporting_kws[:8]:
            kw_stats, kw_facts, kw_pages = self._research_keyword(
                kw, topic_words, fetched_urls
            )
            all_statistics.extend(kw_stats)
            all_key_facts.extend(kw_facts)
            all_pages_read.extend(kw_pages)

        # ── Phase 2: General topic research ──
        # Broader searches for stats that don't map to a specific keyword
        general_stats, general_facts, general_pages = self._research_general(
            state, topic_words, fetched_urls
        )
        all_statistics.extend(general_stats)
        all_key_facts.extend(general_facts)
        all_pages_read.extend(general_pages)

        # ── Deduplicate ──
        all_statistics = self._dedup_stats(all_statistics)
        all_key_facts = self._dedup_facts(all_key_facts)

        # ── Deprioritize previously-cited stats ──
        previously_cited = self._load_cited_stats()
        if previously_cited and all_statistics:
            new_stats = []
            reused_stats = []
            for s in all_statistics:
                stat_text = s.get("stat", "").lower()[:80]
                is_reused = any(
                    cited in stat_text or stat_text in cited
                    for cited in previously_cited
                )
                if is_reused:
                    reused_stats.append(s)
                else:
                    new_stats.append(s)
            if reused_stats:
                self.progress(
                    f"Deprioritized {len(reused_stats)} previously-cited stats"
                )
            all_statistics = new_stats + reused_stats

        # ── Build research summary ──
        research_parts = self._build_research_summary(
            state, all_statistics, all_key_facts, all_pages_read
        )

        state.subject_research = "\n".join(research_parts)
        state.key_facts = all_key_facts[:15]
        state.statistics = all_statistics[:12]

        # Report coverage
        kws_with_data = set()
        for s in all_statistics:
            if s.get("keyword"):
                kws_with_data.add(s["keyword"])
        for f in all_key_facts:
            if f.get("keyword"):
                kws_with_data.add(f["keyword"])

        self.log(
            f"Found {len(all_key_facts)} facts and {len(all_statistics)} stats "
            f"covering {len(kws_with_data)}/{len(supporting_kws[:8])} cluster keywords"
        )
        return state

    # ══════════════════════════════════════════════════════════════
    # KEYWORD-BY-KEYWORD RESEARCH
    # ══════════════════════════════════════════════════════════════

    def _research_keyword(
        self, keyword: str, topic_words: set[str], fetched_urls: set
    ) -> tuple[list, list, list]:
        """Research a single keyword: search, fetch, read, extract facts."""
        self.progress(f"Researching: \"{keyword}\"")

        # Build keyword-specific search query
        # Strip generic modifiers to get the core topic
        core = re.sub(
            r'\b(best|software|tools?|platform|management|for|small|nonprofits?)\b',
            '', keyword, flags=re.IGNORECASE,
        ).strip()
        core = re.sub(r'\s+', ' ', core).strip()
        if len(core) < 4:
            core = keyword  # Fallback to full keyword if core is too short

        search_queries = [
            f"{keyword} statistics research report",
            f"{core} nonprofit data survey",
        ]

        # Search
        results = []
        for query in search_queries:
            self.progress(f"  Searching: {query}")
            search_results = web_search(query)
            results.extend(search_results)

        # Filter
        filtered = self._filter_results(results, fetched_urls)

        if not filtered:
            self.progress(f"  No independent sources found for \"{keyword}\"")
            return [], [], []

        # Fetch and read top 1-2 pages for this keyword
        keyword_topic_words = topic_words | set(
            w for w in re.findall(r'[a-z]+', keyword.lower()) if len(w) > 3
        )

        stats = []
        facts = []
        pages = []

        for r in filtered[:2]:
            url = r["url"]
            if url in fetched_urls:
                continue
            fetched_urls.add(url)

            self.progress(f"  Reading: {url[:70]}...")
            data = web_fetch(url)
            if not data.get("content") or data.get("error"):
                continue

            content = data["content"][:8000]
            title = r.get("title", "Unknown")

            pages.append({"url": url, "title": title, "keyword": keyword})

            # Extract stats tagged with this keyword
            page_stats = self._extract_statistics(
                content, url, title, keyword_topic_words
            )
            for s in page_stats:
                s["keyword"] = keyword
            stats.extend(page_stats)

            # Extract facts tagged with this keyword
            page_facts = self._extract_key_facts(
                content, url, keyword_topic_words
            )
            for f in page_facts:
                f["keyword"] = keyword
            facts.extend(page_facts)

        found = len(stats) + len(facts)
        if found > 0:
            self.progress(f"  Found {len(stats)} stats, {len(facts)} facts for \"{keyword}\"")
        else:
            self.progress(f"  No usable data found for \"{keyword}\"")

        return stats, facts, pages

    def _research_general(
        self, state: PipelineState, topic_words: set[str], fetched_urls: set
    ) -> tuple[list, list, list]:
        """Broader research not tied to a specific keyword."""
        self.progress("General topic research...")
        current_year = str(datetime.now().year)

        search_queries = [
            f"World Commerce Contracting contract management report cost revenue",
            f"Nonprofit HR staffing survey {current_year} OR {int(current_year)-1}",
            f"National Council of Nonprofits compliance challenges report",
            f"nonprofit grant compliance audit statistics",
        ]

        if state.additional_instructions:
            search_queries.append(
                f"{state.target_keyword} {state.additional_instructions}"
            )

        results = []
        for query in search_queries:
            self.progress(f"  Searching: {query}")
            results.extend(web_search(query))

        filtered = self._filter_results(results, fetched_urls)

        stats = []
        facts = []
        pages = []

        for r in filtered[:3]:
            url = r["url"]
            if url in fetched_urls:
                continue
            fetched_urls.add(url)

            self.progress(f"  Reading: {url[:70]}...")
            data = web_fetch(url)
            if not data.get("content") or data.get("error"):
                continue

            content = data["content"][:8000]
            title = r.get("title", "Unknown")

            pages.append({"url": url, "title": title, "keyword": "general"})

            page_stats = self._extract_statistics(content, url, title, topic_words)
            for s in page_stats:
                s["keyword"] = "general"
            stats.extend(page_stats)

            page_facts = self._extract_key_facts(content, url, topic_words)
            for f in page_facts:
                f["keyword"] = "general"
            facts.extend(page_facts)

        return stats, facts, pages

    # ══════════════════════════════════════════════════════════════
    # FILTERING AND EXTRACTION
    # ══════════════════════════════════════════════════════════════

    def _filter_results(self, results: list, already_fetched: set) -> list:
        """Filter search results: no competitors, no own site, no product pages."""
        seen_urls = set()
        filtered = []
        for r in results:
            url = r.get("url", "")
            if not url or url in seen_urls or url in already_fetched:
                continue
            seen_urls.add(url)
            if is_blocked(url) or is_internal(url):
                continue
            if any(p in url.lower() for p in self._SKIP_URL_PATTERNS):
                continue
            # Skip paywalled market research and data catalogs
            domain = url.split("/")[2].replace("www.", "") if "/" in url[8:] else ""
            if any(d in domain for d in self._SKIP_DOMAINS):
                continue
            filtered.append(r)

        # Sort by snippet relevance (basic — prefer results with stats/data signals)
        def data_signal(r):
            text = (r.get("snippet", "") + " " + r.get("title", "")).lower()
            score = 0
            if any(w in text for w in ["report", "survey", "study", "research", "found that"]):
                score += 3
            if re.search(r'\d+%|\$[\d,]+|\d+\s*(billion|million)', text):
                score += 2
            if any(w in text for w in [".gov", ".edu", ".org", "gartner", "forrester"]):
                score += 2
            return score

        filtered.sort(key=data_signal, reverse=True)
        return filtered

    def _extract_statistics(
        self, text: str, source_url: str, source_name: str,
        topic_words: set[str] = None
    ) -> list[dict]:
        """Extract statistics that are actually about our topic."""
        stats = []
        sentences = re.split(r"(?<=[.!?])\s+", text)

        for sentence in sentences:
            clean = sentence.strip()
            if not clean or clean.startswith("#") or len(clean) < 20:
                continue

            has_stat = bool(re.search(
                r"\d+(?:\.\d+)?%|\$[\d,.]+\s*(?:billion|million|trillion)?"
                r"|\d+\s*(?:billion|million|trillion|percent)",
                clean, re.IGNORECASE,
            ))

            if has_stat:
                stat_text = clean[:200].strip()
                if stat_text and len(stat_text.split()) >= 5:
                    stat_lower = stat_text.lower()

                    if any(s in stat_lower for s in [
                        "state-of-the-art", "comprehensive", "cutting-edge",
                        "industry-leading", "best-in-class", "download free",
                        "free sample", "get started", "sign up", "contact us",
                    ]):
                        continue

                    if topic_words:
                        topic_matches = sum(
                            1 for w in topic_words if w in stat_lower
                        )
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

    def _extract_key_facts(
        self, text: str, source_url: str, topic_words: set[str]
    ) -> list[dict]:
        """Extract verifiable factual statements, not marketing copy."""
        facts = []
        sentences = re.split(r"(?<=[.!?])\s+", text)

        for sentence in sentences:
            clean = sentence.strip()
            if not clean or len(clean) < 30:
                continue
            if clean.startswith("#") or clean.startswith("|") or clean.startswith("-"):
                continue

            lower = clean.lower()
            if any(signal in lower for signal in self._MARKETING_SIGNALS):
                continue
            if clean.endswith("?"):
                continue

            matches = sum(1 for w in topic_words if w in lower)
            if matches < 3:
                continue

            has_specifics = bool(re.search(
                r'\d{4}|\d+%|\$[\d,]+|according to|report|survey|study|found that',
                clean, re.IGNORECASE,
            ))

            facts.append({
                "fact": clean[:200],
                "source": source_url,
                "relevance": matches + (2 if has_specifics else 0),
            })

            if len(facts) >= 3:  # Fewer per source to encourage diversity
                break

        facts.sort(key=lambda f: f["relevance"], reverse=True)
        return facts

    # ══════════════════════════════════════════════════════════════
    # DEDUP AND HELPERS
    # ══════════════════════════════════════════════════════════════

    def _dedup_stats(self, stats: list[dict]) -> list[dict]:
        seen = set()
        deduped = []
        for s in stats:
            fingerprint = s.get("stat", "")[:40].lower()
            if fingerprint not in seen:
                seen.add(fingerprint)
                deduped.append(s)
        return deduped

    def _dedup_facts(self, facts: list[dict]) -> list[dict]:
        seen = set()
        deduped = []
        for f in facts:
            fingerprint = f.get("fact", "")[:40].lower()
            if fingerprint not in seen:
                seen.add(fingerprint)
                deduped.append(f)
        return deduped

    def _get_topic_words(self, state: PipelineState) -> set[str]:
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

    def _load_cited_stats(self) -> list[str]:
        from config import KNOWLEDGE_DIR
        ledger_path = KNOWLEDGE_DIR / "cited_stats.md"
        if not ledger_path.exists():
            return []
        try:
            content = ledger_path.read_text()
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

    def _build_research_summary(self, state, statistics, key_facts, pages_read):
        """Build human-readable research summary organized by keyword."""
        parts = [f"# Subject Research: {state.topic}\n"]

        # Organize by keyword
        kw_data = {}
        for s in statistics:
            kw = s.get("keyword", "general")
            kw_data.setdefault(kw, {"stats": [], "facts": [], "sources": []})
            kw_data[kw]["stats"].append(s)
        for f in key_facts:
            kw = f.get("keyword", "general")
            kw_data.setdefault(kw, {"stats": [], "facts": [], "sources": []})
            kw_data[kw]["facts"].append(f)
        for p in pages_read:
            kw = p.get("keyword", "general")
            kw_data.setdefault(kw, {"stats": [], "facts": [], "sources": []})
            kw_data[kw]["sources"].append(p)

        for kw, data in kw_data.items():
            parts.append(f"\n## Research: {kw}")
            if data["sources"]:
                parts.append("Sources read:")
                for p in data["sources"]:
                    parts.append(f"  - [{p['title'][:60]}]({p['url']})")
            if data["stats"]:
                parts.append("Statistics found:")
                for s in data["stats"]:
                    parts.append(f"  - {s['stat'][:120]} ({s.get('source_name', '')})")
            if data["facts"]:
                parts.append("Key facts:")
                for f in data["facts"]:
                    parts.append(f"  - {f['fact'][:120]}")
            if not data["stats"] and not data["facts"]:
                parts.append("  (No usable data found for this keyword)")
            parts.append("")

        return parts
