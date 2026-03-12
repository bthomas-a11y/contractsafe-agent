"""Agent 4: SEO Researcher - SERP analysis and content structure recommendation."""

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
from prompts.templates import SEO_RESEARCHER_SYSTEM


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

        # --- SEMrush: difficulty scores + broad match for secondary keywords ---
        semrush_section = ""
        if SEMRUSH_API_KEY:
            self.progress("Pulling SEMrush difficulty and keyword data...")

            # Get difficulty for all target keywords
            all_kws = [state.target_keyword] + state.secondary_keywords
            related = state.keyword_data.get("related_terms", [])[:10]
            all_kws.extend(related)
            difficulty_data = semrush_kd(all_kws)

            # Get batch overview for secondary keywords
            batch_data = semrush_batch(state.secondary_keywords[:20]) if state.secondary_keywords else []

            # Get broad match for additional long-tail opportunities
            broad_data = semrush_broad(state.target_keyword, limit=15)

            # Format for the prompt
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

        # --- Build the user prompt ---
        serp_summary = "\n".join(
            f"{i+1}. [{r['title']}]({r['url']})\n   {r['snippet']}"
            for i, r in enumerate(serp_results[:10])
        )

        variation_summary = "\n".join(
            f"- [{r['title']}]({r['url']}): {r['snippet']}"
            for r in variation_results[:5]
        )

        questions_summary = "\n".join(f"- {q}" for q in all_questions[:25])

        # Format competitor data
        competitor_summary = ""
        if state.competitor_pages:
            for cp in state.competitor_pages:
                h2s = ", ".join(cp.get("h2s", [])) if cp.get("h2s") else "N/A"
                competitor_summary += (
                    f"- {cp.get('title', 'Unknown')} ({cp.get('url', '')})\n"
                    f"  Word count: ~{cp.get('word_count', 'unknown')}\n"
                    f"  H2s: {h2s}\n"
                    f"  Strengths: {cp.get('strengths', 'N/A')}\n"
                    f"  Gaps: {cp.get('gaps', 'N/A')}\n\n"
                )

        keyword_data_str = json.dumps(state.keyword_data, indent=2) if state.keyword_data else "No keyword data available"

        user_prompt = f"""## Target Keyword
{state.target_keyword}

## Topic
{state.topic}

## Target Word Count
{state.target_word_count}

## SERP Results for "{state.target_keyword}"
{serp_summary}

## Variation Search Results ("how to {state.target_keyword}")
{variation_summary}

## Questions People Ask (autocomplete + research)
{questions_summary}
{semrush_section}

## Competitor Analysis
{competitor_summary or "No competitor data available."}

## Keyword Data
{keyword_data_str}

Analyze the SERP and provide SEO-informed content structure recommendations.
{"Use SEMrush difficulty scores to prioritize which keywords to target in H2s (prefer lower difficulty + higher volume)." if semrush_section else ""}"""

        self.progress("Building SEO recommendations with Claude...")
        response = self.call_llm(SEO_RESEARCHER_SYSTEM, user_prompt)

        state.seo_brief = response

        # Extract structured data
        state.recommended_h2s = self._extract_labeled_json(response, "RECOMMENDED_H2S", [])
        state.serp_features = self._extract_labeled_json(response, "SERP_FEATURES", [])

        if not state.recommended_h2s:
            state.recommended_h2s = self._extract_first_json_array(response) or []

        self.log(f"Recommended {len(state.recommended_h2s)} H2 headings")
        return state

    def _extract_labeled_json(self, text: str, label: str, default):
        pattern = rf'{label}\s*```json\s*\n(.*?)\n\s*```'
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return default

    def _extract_first_json_array(self, text: str):
        pattern = r'```json\s*\n(\[.*?\])\n\s*```'
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return None
