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
    keyword_difficulty as semrush_kd,
    batch_keyword_overview as semrush_batch,
    broad_match_keywords as semrush_broad,
)
from config import SEMRUSH_API_KEY


class SEOResearcherAgent(BaseAgent):
    name = "SEO Researcher"
    description = "Analyze SERP features and recommend content structure"
    agent_number = 4
    emoji = "\U0001f4ca"

    def run(self, state: PipelineState) -> PipelineState:
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

        # --- SEMrush data ---
        semrush_section = ""
        if SEMRUSH_API_KEY:
            self.progress("Pulling SEMrush difficulty and keyword data...")

            all_kws = [state.target_keyword] + state.secondary_keywords
            related = state.keyword_data.get("related_terms", [])[:10]
            all_kws.extend(related)
            difficulty_data = semrush_kd(all_kws)

            batch_data = semrush_batch(state.secondary_keywords[:20]) if state.secondary_keywords else []
            broad_data = semrush_broad(state.target_keyword, limit=15)

            if difficulty_data:
                semrush_section += "\n## SEMrush Keyword Difficulty Scores\n"
                for kd in difficulty_data:
                    kw = kd.get("Keyword", kd.get("Ph", ""))
                    diff = kd.get("Keyword Difficulty Index", kd.get("Kd", ""))
                    semrush_section += f"- {kw}: difficulty={diff}/100\n"

            if batch_data:
                semrush_section += "\n## SEMrush Secondary Keyword Volumes\n"
                for b in batch_data:
                    kw = b.get("Keyword", b.get("Ph", ""))
                    vol = b.get("Search Volume", b.get("Nq", ""))
                    cpc = b.get("CPC", b.get("Cp", ""))
                    semrush_section += f"- {kw}: vol={vol}, CPC=${cpc}\n"

            if broad_data:
                semrush_section += "\n## SEMrush Broad Match / Long-Tail Opportunities\n"
                for b in broad_data:
                    kw = b.get("Keyword", b.get("Ph", ""))
                    vol = b.get("Search Volume", b.get("Nq", ""))
                    diff = b.get("Keyword Difficulty Index", b.get("Kd", ""))
                    semrush_section += f"- {kw}: vol={vol}, difficulty={diff}\n"

            if semrush_section:
                self.log("SEMrush data enriched SEO analysis")
        else:
            self.progress("SEMrush not configured, proceeding with SERP analysis only")

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
            # Fallback: generate basic structure when no competitor data
            kw = state.target_keyword
            add_if_unique(f"What Is {kw.title()}?")
            add_if_unique(f"Why {kw.title()} Matters")
            add_if_unique(f"Key Benefits of {kw.title()}")
            add_if_unique(f"How to Get Started with {kw.title()}")
            add_if_unique(f"Common {kw.title()} Mistakes to Avoid")

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
