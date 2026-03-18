"""Agent 4: SEO Researcher - SERP analysis and content structure recommendation.

Fully programmatic — no Claude call. Builds recommended H2s from competitor
pages, PAA data, and keyword targeting.
"""

import json
import re
from agents.base import BaseAgent
from state import PipelineState
from tools.web_search import web_search
from tools.keyword_research import google_autocomplete
from tools.semrush import (
    batch_keyword_overview as semrush_batch,
    domain_organic_keywords as semrush_domain_kws,
)
from config import SEMRUSH_API_KEY


class SEOResearcherAgent(BaseAgent):
    name = "SEO Researcher"
    description = "Analyze SERP features and recommend content structure"
    agent_number = 4
    emoji = "\U0001f4ca"

    def run(self, state: PipelineState) -> PipelineState:
        self.progress("Analyzing search results and building content structure...")
        # --- SERP analysis (Tavily: ~2 credits) ---
        self.progress(f"Analyzing SERP for: {state.target_keyword}")
        serp_results = web_search(state.target_keyword, num_results=10)

        self.progress("Searching keyword variations...")
        variation_results = web_search(f"how to {state.target_keyword}", num_results=5)

        # --- Free autocomplete for question data ---
        self.progress("Getting autocomplete suggestions...")
        extra_questions = []
        for prefix in ["best", "how to", "why use", "what is the difference between"]:
            suggestions = google_autocomplete(f"{prefix} {state.target_keyword}")
            extra_questions.extend(suggestions)

        # Combine with existing question data
        existing_questions = state.keyword_data.get("questions_people_ask", [])
        all_questions = list(dict.fromkeys(existing_questions + extra_questions))

        # --- SEMrush data (read from Agent 3's state, not re-fetched) ---
        semrush_section = ""
        semrush_raw = state.keyword_data.get("semrush", {})
        has_semrush = bool(semrush_raw.get("search_volume"))

        if has_semrush:
            self.progress("Using SEMrush data from Agent 3 (no duplicate API calls)...")

            # Read pre-fetched data from Agent 3
            difficulty_data = semrush_raw.get("difficulty", [])
            related_data = semrush_raw.get("related_keywords", [])
            broad_data = semrush_raw.get("broad_match", [])

            if difficulty_data:
                semrush_section += "\n## SEMrush Keyword Difficulty Scores\n"
                for kd in difficulty_data:
                    kw = kd.get("Keyword", kd.get("Ph", ""))
                    diff = kd.get("Keyword Difficulty Index", kd.get("Kd", ""))
                    semrush_section += f"- {kw}: difficulty={diff}/100\n"

            # Only API call Agent 4 makes: batch volumes for secondary keywords
            # (Agent 3 doesn't cover these)
            if SEMRUSH_API_KEY and state.secondary_keywords:
                self.progress("Checking secondary keyword volumes via SEMRush...")
                batch_data = semrush_batch(state.secondary_keywords[:20])
                if batch_data:
                    semrush_section += "\n## SEMrush Secondary Keyword Volumes\n"
                    for b in batch_data:
                        kw = b.get("Keyword", b.get("Ph", ""))
                        vol = b.get("Search Volume", b.get("Nq", ""))
                        cpc = b.get("CPC", b.get("Cp", ""))
                        semrush_section += f"- {kw}: vol={vol}, CPC=${cpc}\n"

            # --- Keyword Clusters (built from Agent 3's data) ---
            self.progress("Building keyword clusters from SEMrush data...")
            all_semrush_kws = related_data + broad_data
            clusters = self._build_keyword_clusters(state.target_keyword, all_semrush_kws)
            state.keyword_clusters = clusters

            if clusters:
                semrush_section += "\n## Keyword Clusters (by intent/topic)\n"
                for cluster in clusters:
                    semrush_section += f"\n**{cluster['name']}** ({len(cluster['keywords'])} keywords)\n"
                    for kw_info in cluster["keywords"][:5]:
                        semrush_section += f"- {kw_info['keyword']}: vol={kw_info.get('volume', 'N/A')}, diff={kw_info.get('difficulty', 'N/A')}\n"
                self.log(f"Built {len(clusters)} keyword clusters")

            # --- Keyword Gaps (vs top competitors from SERP) ---
            if SEMRUSH_API_KEY:
                self.progress("Running keyword gap analysis vs top competitors...")
                gap_keywords = self._find_keyword_gaps(serp_results)
                state.keyword_gaps = gap_keywords

                if gap_keywords:
                    semrush_section += "\n## Keyword Gaps (competitors rank, we don't)\n"
                    for gap in gap_keywords[:15]:
                        kw = gap.get("keyword", "")
                        vol = gap.get("volume", "N/A")
                        competitor = gap.get("competitor", "")
                        semrush_section += f"- {kw}: vol={vol} (from {competitor})\n"
                    self.log(f"Found {len(gap_keywords)} keyword gap opportunities")

            if semrush_section:
                self.log("SEMrush data enriched SEO analysis")
        else:
            self.progress("No SEMrush data from Agent 3, proceeding with SERP analysis only")

        # --- Build recommended H2s programmatically ---
        self.progress("Building H2 recommendations programmatically...")
        recommended_h2s = self._build_recommended_h2s(state, all_questions)

        state.recommended_h2s = recommended_h2s
        state.serp_features = self._detect_serp_features(serp_results)

        # --- Build SEO brief as formatted data (no Claude synthesis) ---
        state.seo_brief = self._build_seo_brief(
            state, serp_results, variation_results, all_questions, semrush_section
        )

        self.log(f"Recommended {len(state.recommended_h2s)} H2 headings")
        return state

    def _build_recommended_h2s(self, state: PipelineState, all_questions: list[str]) -> list[str]:
        """Build recommended H2s programmatically from competitor data + PAA + keywords."""
        h2_candidates = []
        seen_normalized = set()

        # Stopwords to exclude from overlap calculations (prevents merging
        # distinct concepts like "Contract Addendum" vs "Contract Amendment")
        dedup_stopwords = {
            "what", "how", "why", "when", "where", "who", "which", "does",
            "your", "their", "this", "that", "with", "from", "about",
            "best", "guide", "tips", "ways", "steps", "need", "know",
            "understanding", "complete",
        }

        def normalize(text: str) -> str:
            """Normalize H2 text for deduplication."""
            return re.sub(r'[^a-z0-9\s]', '', text.lower()).strip()

        def content_words(text: str) -> set:
            """Get meaningful content words, excluding stopwords and short words."""
            words = set(normalize(text).split())
            # Remove stopwords and words shorter than 3 chars (a, is, an, to, etc.)
            return {w for w in words if len(w) >= 3} - dedup_stopwords

        def add_if_unique(h2: str):
            norm = normalize(h2)
            norm_content = content_words(h2)
            if len(norm_content) < 1:
                return
            for existing_norm in seen_normalized:
                existing_content = content_words(existing_norm)
                if not norm_content or not existing_content:
                    continue
                # Use max() denominator so both sides must overlap significantly
                overlap = len(norm_content & existing_content) / max(len(norm_content), len(existing_content))
                if overlap > 0.6:
                    return
            seen_normalized.add(norm)
            h2_candidates.append(h2)

        # 1. Start with H2s from competitor pages
        if state.competitor_pages:
            for cp in state.competitor_pages:
                for h2 in cp.get("h2s", []):
                    if h2 and len(h2) > 5:
                        add_if_unique(h2)
        else:
            # Fallback: generate basic structure when no competitor data.
            # Use varied phrasing so long keywords (4+ words) don't trigger
            # dedup overlap. The writer will still use the keyword naturally.
            kw = state.target_keyword
            kw_title = kw.title()
            fallback_h2s = [
                f"What Is {kw_title}?",
                f"Common {kw_title} Mistakes to Avoid",
                f"Why {kw_title} Matters for Your Organization",
                f"How to Choose the Right Solution",
                f"Key Features to Look For",
            ]
            for h in fallback_h2s:
                add_if_unique(h)

        # 2. Add question-based H2s from PAA data (top 3-4)
        question_count = 0
        for q in all_questions:
            if question_count >= 4:
                break
            q = q.strip().rstrip("?").strip()
            if len(q) > 10:
                # Make it a proper H2 question
                if not q.endswith("?"):
                    q += "?"
                # Capitalize first letter
                q = q[0].upper() + q[1:]
                add_if_unique(q)
                question_count += 1

        # 3. Add keyword-targeted H2 if not already covered
        kw = state.target_keyword
        kw_lower = kw.lower()
        kw_covered = any(kw_lower in normalize(h) for h in h2_candidates)
        if not kw_covered and kw:
            # Create a "What Is [Keyword]?" or "Understanding [Keyword]" H2
            add_if_unique(f"What Is {kw.title()}?")

        # 4. Ensure we have a conclusion/takeaway
        has_conclusion = any(
            w in normalize(h) for h in h2_candidates
            for w in ["conclusion", "takeaway", "bottom line", "final", "summary"]
        )
        if not has_conclusion:
            add_if_unique("The Bottom Line")

        # Limit to a reasonable number
        return h2_candidates[:10]

    def _build_keyword_clusters(self, target_keyword: str, semrush_keywords: list[dict]) -> list[dict]:
        """Group SEMrush keywords into topical clusters by shared content words."""
        if not semrush_keywords:
            return []

        # Normalize keywords and extract metadata
        kw_items = []
        seen = set()
        for item in semrush_keywords:
            phrase = item.get("Keyword", item.get("Ph", "")).lower().strip()
            if not phrase or phrase in seen:
                continue
            seen.add(phrase)
            kw_items.append({
                "keyword": phrase,
                "volume": item.get("Search Volume", item.get("Nq", "N/A")),
                "difficulty": item.get("Keyword Difficulty Index", item.get("Kd", "N/A")),
                "words": set(w for w in phrase.split() if len(w) > 2),
            })

        if not kw_items:
            return []

        # Define cluster seeds based on common intent patterns
        target_words = set(w.lower() for w in target_keyword.split() if len(w) > 2)
        cluster_seeds = {
            "Definitions & Basics": {"what", "definition", "meaning", "difference", "vs", "versus"},
            "How-To & Process": {"how", "write", "draft", "create", "steps", "process", "template"},
            "Examples & Templates": {"example", "sample", "template", "format", "letter"},
            "Legal & Compliance": {"legal", "law", "enforce", "binding", "clause", "provision", "court"},
            "Cost & Business Impact": {"cost", "price", "fee", "business", "risk", "benefit", "value"},
        }

        clusters = {name: [] for name in cluster_seeds}
        unclustered = []

        for item in kw_items:
            placed = False
            best_cluster = None
            best_overlap = 0

            for cluster_name, seed_words in cluster_seeds.items():
                overlap = len(item["words"] & seed_words)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_cluster = cluster_name

            if best_overlap > 0 and best_cluster:
                clusters[best_cluster].append({
                    "keyword": item["keyword"],
                    "volume": item["volume"],
                    "difficulty": item["difficulty"],
                })
                placed = True

            if not placed:
                unclustered.append({
                    "keyword": item["keyword"],
                    "volume": item["volume"],
                    "difficulty": item["difficulty"],
                })

        # Build result — only include non-empty clusters, sorted by size
        result = []
        for name, keywords in sorted(clusters.items(), key=lambda x: len(x[1]), reverse=True):
            if keywords:
                # Sort by volume (descending) within each cluster
                keywords.sort(key=lambda x: int(x["volume"]) if str(x["volume"]).isdigit() else 0, reverse=True)
                result.append({"name": name, "keywords": keywords})

        if unclustered:
            unclustered.sort(key=lambda x: int(x["volume"]) if str(x["volume"]).isdigit() else 0, reverse=True)
            result.append({"name": "Other Related Keywords", "keywords": unclustered})

        return result

    def _find_keyword_gaps(self, serp_results: list[dict]) -> list[dict]:
        """Find keywords that top SERP competitors rank for that contractsafe.com doesn't."""
        if not serp_results:
            return []

        from urllib.parse import urlparse

        # Get top 3 competitor domains from SERP results
        competitor_domains = []
        seen_domains = set()
        for r in serp_results:
            url = r.get("url", "")
            if not url:
                continue
            domain = urlparse(url).netloc.replace("www.", "")
            # Skip our own domain and common non-competitor sites
            if domain in seen_domains or "contractsafe" in domain:
                continue
            skip = {"youtube.com", "wikipedia.org", "reddit.com", "quora.com", "linkedin.com"}
            if domain in skip:
                continue
            seen_domains.add(domain)
            competitor_domains.append(domain)
            if len(competitor_domains) >= 3:
                break

        if not competitor_domains:
            return []

        # Get our keywords
        self.progress(f"SEMrush: fetching contractsafe.com organic keywords...")
        our_kws = semrush_domain_kws("contractsafe.com", limit=100)
        our_phrases = {r.get("Keyword", r.get("Ph", "")).lower() for r in our_kws}

        gap_keywords = []
        seen_gap = set()

        for domain in competitor_domains:
            self.progress(f"SEMrush: keyword gap vs {domain}...")
            their_kws = semrush_domain_kws(domain, limit=50)
            for r in their_kws:
                phrase = r.get("Keyword", r.get("Ph", "")).lower()
                vol = r.get("Search Volume", r.get("Nq", "0"))
                if phrase and phrase not in our_phrases and phrase not in seen_gap:
                    seen_gap.add(phrase)
                    gap_keywords.append({
                        "keyword": phrase,
                        "volume": vol,
                        "position": r.get("Position", r.get("Po", "")),
                        "competitor": domain,
                    })

        # Sort by volume descending
        gap_keywords.sort(key=lambda x: int(x["volume"]) if str(x["volume"]).isdigit() else 0, reverse=True)
        return gap_keywords[:30]

    def _detect_serp_features(self, serp_results: list[dict]) -> list[str]:
        """Detect SERP features from search results."""
        features = []
        for r in serp_results:
            snippet = r.get("snippet", "").lower()
            title = r.get("title", "").lower()
            if "featured snippet" in snippet or "featured snippet" in title:
                features.append("Featured Snippet")
            if "people also ask" in snippet:
                features.append("People Also Ask")
        # Always note organic results
        if serp_results:
            features.append(f"{len(serp_results)} organic results analyzed")
        return list(dict.fromkeys(features))  # deduplicate

    def _build_seo_brief(
        self, state, serp_results, variation_results, all_questions, semrush_section
    ) -> str:
        """Build a formatted SEO brief from all collected data."""
        sections = []

        sections.append(f"# SEO Analysis: {state.target_keyword}\n")

        # SERP overview
        sections.append("## SERP Overview")
        for i, r in enumerate(serp_results[:10]):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            snippet = r.get("snippet", "")
            sections.append(f"{i+1}. [{title}]({url})")
            if snippet:
                sections.append(f"   {snippet}")
        sections.append("")

        # Variation results
        if variation_results:
            sections.append("## Keyword Variation Results")
            for r in variation_results[:5]:
                title = r.get("title", "Untitled")
                url = r.get("url", "")
                snippet = r.get("snippet", "")
                sections.append(f"- [{title}]({url}): {snippet}")
            sections.append("")

        # Questions
        if all_questions:
            sections.append("## Questions People Ask")
            for q in all_questions[:20]:
                sections.append(f"- {q}")
            sections.append("")

        # Recommended H2s
        if state.recommended_h2s:
            sections.append("## Recommended H2 Structure")
            for h2 in state.recommended_h2s:
                sections.append(f"- {h2}")
            sections.append("")

        # SEMrush data
        if semrush_section:
            sections.append(semrush_section)

        # Keyword clusters
        if state.keyword_clusters:
            sections.append("## Keyword Clusters")
            for cluster in state.keyword_clusters:
                sections.append(f"### {cluster['name']}")
                for kw_info in cluster["keywords"][:5]:
                    sections.append(f"- {kw_info['keyword']} (vol: {kw_info.get('volume', 'N/A')})")
            sections.append("")

        # Keyword gaps
        if state.keyword_gaps:
            sections.append("## Keyword Gap Opportunities")
            for gap in state.keyword_gaps[:10]:
                sections.append(f"- {gap['keyword']} (vol: {gap.get('volume', 'N/A')}, competitor: {gap.get('competitor', '')})")
            sections.append("")

        # Competitor analysis
        if state.competitor_pages:
            sections.append("## Competitor Analysis")
            for cp in state.competitor_pages:
                h2s = ", ".join(cp.get("h2s", [])) if cp.get("h2s") else "N/A"
                sections.append(f"- {cp.get('title', 'Unknown')} ({cp.get('url', '')})")
                sections.append(f"  Word count: ~{cp.get('word_count', 'unknown')}")
                sections.append(f"  H2s: {h2s}")
                sections.append(f"  Gaps: {cp.get('gaps', 'N/A')}")
            sections.append("")

        return "\n".join(sections)
