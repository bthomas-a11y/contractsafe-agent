"""Agent 3: Competitor / Keyword Researcher."""

import json
import re
from agents.base import BaseAgent
from state import PipelineState
from tools.web_search import web_search
from tools.web_fetch import web_fetch
from tools.keyword_research import full_keyword_research
from tools.semrush import (
    full_keyword_analysis as semrush_analysis,
    domain_organic_keywords as semrush_domain_kws,
    competitor_keyword_gap as semrush_gap,
)
from config import SEMRUSH_API_KEY
from prompts.templates import COMPETITOR_KW_SYSTEM


class CompetitorKWAgent(BaseAgent):
    name = "Competitor/KW Research"
    description = "Analyze competitor content and build keyword intelligence"
    agent_number = 3
    emoji = "\U0001f3f7\ufe0f"

    def run(self, state: PipelineState) -> PipelineState:
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

        # --- Competitor Search (Tavily: ~3 credits) ---
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

        # Fetch top 5 competitor pages for content analysis
        competitor_content = []
        fetch_urls = [r["url"] for r in unique if r["url"]][:5]
        for url in fetch_urls:
            self.progress(f"Fetching competitor: {url[:80]}...")
            data = web_fetch(url)
            if data["content"]:
                title = next((r["title"] for r in unique if r["url"] == url), "Unknown")
                competitor_content.append(
                    f"### URL: {url}\n### Title: {title}\n{data['content'][:8000]}"
                )

        # --- SEMrush competitor keyword gap (if available) ---
        semrush_gap_data = {}
        if SEMRUSH_API_KEY and fetch_urls:
            # Pick the top competitor domain for gap analysis
            from urllib.parse import urlparse
            top_competitor = urlparse(fetch_urls[0]).netloc
            self.progress(f"SEMrush keyword gap: contractsafe.com vs {top_competitor}")
            semrush_gap_data = semrush_gap("contractsafe.com", top_competitor)

        # --- Build user prompt ---
        search_summary = "\n".join(
            f"- [{r['title']}]({r['url']}): {r['snippet']}"
            for r in unique[:15]
        )

        # Format free keyword research
        kw_summary = f"**Base autocomplete suggestions:** {', '.join(keyword_research['autocomplete_suggestions'][:10])}\n\n"
        kw_summary += f"**Total expanded keywords:** {len(keyword_research['all_keywords'])}\n"
        kw_summary += f"**Sample expanded keywords:** {', '.join(keyword_research['all_keywords'][:30])}\n\n"

        if keyword_research.get("people_also_ask"):
            kw_summary += "**People Also Ask (KeywordsPeopleUse):**\n"
            for item in keyword_research["people_also_ask"][:10]:
                if isinstance(item, dict):
                    kw_summary += f"  - {item.get('question', item)}\n"
                else:
                    kw_summary += f"  - {item}\n"
            kw_summary += "\n"

        if keyword_research.get("semantic_keywords"):
            kw_summary += f"**Semantic keywords:** {', '.join(str(k) for k in keyword_research['semantic_keywords'][:15])}\n\n"

        questions_summary = "\n".join(f"- {q}" for q in keyword_research["all_questions"][:25])

        # Format SEMrush data
        semrush_section = ""
        if semrush_data.get("available"):
            overview = semrush_data.get("overview", {})
            semrush_section = f"""
## SEMrush Keyword Data
**Search Volume:** {overview.get('Search Volume', overview.get('Nq', 'N/A'))}
**CPC:** ${overview.get('CPC', overview.get('Cp', 'N/A'))}
**Competition:** {overview.get('Competition', overview.get('Co', 'N/A'))}
**Keyword Difficulty:** {semrush_data.get('keyword_difficulty', 'N/A')}/100

**SEMrush Related Keywords:**
{chr(10).join(f"- {r.get('Keyword', r.get('Ph', ''))}: vol={r.get('Search Volume', r.get('Nq', ''))}" for r in semrush_data.get('related_keywords', [])[:15])}

**SEMrush Questions:**
{chr(10).join(f"- {r.get('Keyword', r.get('Ph', ''))}: vol={r.get('Search Volume', r.get('Nq', ''))}" for r in semrush_data.get('questions', [])[:10])}

**SEMrush Broad Match Keywords:**
{chr(10).join(f"- {r.get('Keyword', r.get('Ph', ''))}: vol={r.get('Search Volume', r.get('Nq', ''))}" for r in semrush_data.get('broad_match', [])[:10])}
"""
            if semrush_gap_data.get("available"):
                gap_kws = semrush_gap_data.get("gap_keywords", [])[:10]
                semrush_section += f"""
**Keyword Gap (vs top competitor):**
{chr(10).join(f"- {r.get('Keyword', r.get('Ph', ''))}: vol={r.get('Search Volume', r.get('Nq', ''))}, pos={r.get('Position', r.get('Po', ''))}" for r in gap_kws)}
"""

        user_prompt = f"""## Target Keyword
{state.target_keyword}

## Topic
{state.topic}

## Free Keyword Research (Google Autocomplete + KeywordsPeopleUse)
{kw_summary}

## All Questions People Ask
{questions_summary}
{semrush_section}

## Search Results
{search_summary}

## Competitor Page Content
{"---".join(competitor_content) if competitor_content else "No competitor pages could be fetched."}

Analyze these competitors and build keyword intelligence as specified in your instructions.
Incorporate ALL keyword data (free tools + SEMrush if present) into your analysis.
{
    "Prioritize SEMrush volume and difficulty data for keyword selection when available."
    if semrush_data.get("available") else ""
}"""

        self.progress("Analyzing competitors with Claude...")
        response = self.call_llm(COMPETITOR_KW_SYSTEM, user_prompt)

        # Parse structured data from response
        state.competitor_pages = self._extract_json_block(response, list)

        # Build keyword_data from Claude's analysis + our research
        claude_kw_data = self._extract_json_block(response, dict)
        state.keyword_data = {
            "primary_kw": state.target_keyword,
            "secondary_kws": claude_kw_data.get("secondary_kws", state.secondary_keywords),
            "questions_people_ask": claude_kw_data.get(
                "questions_people_ask", keyword_research["all_questions"][:15]
            ),
            "related_terms": claude_kw_data.get(
                "related_terms", keyword_research["all_keywords"][:20]
            ),
            "autocomplete_suggestions": keyword_research["autocomplete_suggestions"],
            "semantic_keywords": keyword_research.get("semantic_keywords", []),
            "people_also_ask": keyword_research.get("people_also_ask", []),
        }

        # Enrich with SEMrush data if available
        if semrush_data.get("available"):
            state.keyword_data["semrush"] = {
                "search_volume": semrush_data.get("search_volume"),
                "keyword_difficulty": semrush_data.get("keyword_difficulty"),
                "overview": semrush_data.get("overview", {}),
                "related_count": len(semrush_data.get("related_keywords", [])),
                "questions_count": len(semrush_data.get("questions", [])),
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
            + (f", SEMrush vol={semrush_data.get('search_volume', 'N/A')}" if semrush_data.get("available") else "")
        )
        return state

    def _extract_json_block(self, text: str, target_type: type):
        """Extract first JSON block of the target type from text."""
        pattern = r'```json\s*\n(.*?)\n\s*```'
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            try:
                parsed = json.loads(match)
                if isinstance(parsed, target_type):
                    return parsed
            except json.JSONDecodeError:
                continue
        return [] if target_type == list else {}
