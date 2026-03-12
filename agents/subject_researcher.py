"""Agent 2: Subject Matter Researcher - deep research on the article topic."""

import json
from agents.base import BaseAgent
from state import PipelineState
from tools.web_search import web_search
from tools.web_fetch import web_fetch
from prompts.templates import SUBJECT_RESEARCHER_SYSTEM


class SubjectResearcherAgent(BaseAgent):
    name = "Subject Researcher"
    description = "Deep-dive research on the article's subject matter"
    agent_number = 2
    emoji = "\U0001f4da"

    def run(self, state: PipelineState) -> PipelineState:
        # Perform multiple web searches
        search_queries = [
            f"{state.topic} definition overview",
            f"{state.topic} statistics data {self._current_year()}",
            f"{state.topic} recent developments trends",
            f"{state.topic} case study examples",
            f"{state.topic} common mistakes misconceptions",
            f"{state.topic} expert insights best practices",
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

        # Fetch top pages for full content
        fetch_urls = [r["url"] for r in unique_results if r["url"]][:5]
        fetched_content = []
        for url in fetch_urls:
            self.progress(f"Fetching: {url[:80]}...")
            data = web_fetch(url)
            if data["content"]:
                fetched_content.append(f"### Source: {url}\n{data['content'][:6000]}")

        # Build research context
        search_summary = "\n".join(
            f"- [{r['title']}]({r['url']}): {r['snippet']}"
            for r in unique_results[:20]
        )

        user_prompt = f"""## Topic
{state.topic}

## Content Type
{state.content_type}

## Additional Instructions
{state.additional_instructions or 'None'}

## Search Results Summary
{search_summary}

## Full Page Content (top sources)
{"".join(fetched_content) if fetched_content else "No pages could be fetched."}

Synthesize this research into the structured format specified in your instructions."""

        self.progress("Synthesizing research with Claude...")
        response = self.call_llm(SUBJECT_RESEARCHER_SYSTEM, user_prompt)

        # Store the full research
        state.subject_research = response

        # Try to parse out structured data
        state.key_facts = self._extract_json_block(response, "KEY FACTS", [])
        state.statistics = self._extract_json_block(response, "STATISTICS", [])

        fact_count = len(state.key_facts)
        stat_count = len(state.statistics)
        self.log(f"Found {fact_count} key facts and {stat_count} statistics")

        return state

    def _extract_json_block(self, text: str, label: str, default):
        """Extract a JSON block from the response text."""
        import re
        # Look for ```json blocks
        pattern = r'```json\s*\n(.*?)\n\s*```'
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            try:
                parsed = json.loads(match)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                continue
        return default

    def _current_year(self) -> str:
        from datetime import datetime
        return str(datetime.now().year)
