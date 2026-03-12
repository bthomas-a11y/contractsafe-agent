"""Agent 9: Fact Check Pass - verifies claims, statistics, and source URLs."""

import json
import re
from agents.base import BaseAgent
from state import PipelineState
from tools.web_search import web_search
from tools.web_fetch import web_fetch
from prompts.templates import FACT_CHECKER_SYSTEM


class FactCheckerAgent(BaseAgent):
    name = "Fact Checker"
    description = "Verify factual claims, statistics, and source URLs"
    agent_number = 9
    emoji = "\u2705"

    def run(self, state: PipelineState) -> PipelineState:
        article = state.voice_pass_article or state.draft_article

        # First pass: extract claims from the article
        self.progress("Extracting factual claims from article...")
        claims = self._extract_claims(article)

        # Verify source URLs from statistics
        verified_sources = []
        for stat in state.statistics[:10]:
            url = stat.get("source_url", "")
            if url:
                self.progress(f"Verifying: {url[:60]}...")
                data = web_fetch(url)
                verified_sources.append({
                    "stat": stat.get("stat", ""),
                    "url": url,
                    "accessible": data["status"] == 200,
                    "error": data.get("error"),
                })

        # Search for verification of key claims
        for claim in claims[:5]:
            self.progress(f"Verifying claim: {claim[:50]}...")
            results = web_search(claim[:100])
            verified_sources.append({
                "claim": claim,
                "search_results": [
                    {"title": r["title"], "url": r["url"], "snippet": r["snippet"]}
                    for r in results[:3]
                ],
            })

        user_prompt = f"""## Article to Fact-Check
{article}

## Original Research Statistics
{json.dumps(state.statistics, indent=2) if state.statistics else 'No statistics provided.'}

## Original Key Facts
{json.dumps(state.key_facts, indent=2) if state.key_facts else 'No key facts provided.'}

## Source Verification Results
{json.dumps(verified_sources, indent=2)}

Fact-check this article. For every claim and statistic:
1. Cross-reference against the provided research data
2. Check verification results
3. Assign status: VERIFIED, UNVERIFIED, DISPUTED, or NEEDS_SOURCE

Be CONSERVATIVE. If a stat can't be verified, recommend removing it.

Return the fact check results as JSON, then the full revised article."""

        self.progress("Running fact check with Claude...")
        response = self.call_llm(FACT_CHECKER_SYSTEM, user_prompt)

        # Parse response
        state.fact_check_results = self._extract_json_array(response)
        state.fact_check_article = self._extract_article(response)

        verified = sum(1 for r in state.fact_check_results if r.get("status") == "VERIFIED")
        total = len(state.fact_check_results)
        self.log(f"Checked {total} claims: {verified} verified")
        return state

    def _extract_claims(self, article: str) -> list[str]:
        """Extract factual claims from the article text."""
        claims = []
        for line in article.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Look for lines with numbers, percentages, or strong claims
            if any(c.isdigit() for c in line) or "%" in line or "according to" in line.lower():
                claims.append(line[:200])
        return claims[:15]

    def _extract_json_array(self, text: str) -> list[dict]:
        """Extract JSON array from response."""
        pattern = r'```json\s*\n(.*?)\n\s*```'
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            try:
                parsed = json.loads(match)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                continue
        return []

    def _extract_article(self, text: str) -> str:
        """Extract the article portion from the response."""
        if "---" in text:
            parts = text.split("---")
            # Find the longest part (likely the article)
            article_parts = [p for p in parts if len(p.strip()) > 500]
            if article_parts:
                return article_parts[-1].strip()

        # Fallback: find first markdown heading
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line.strip().startswith("# "):
                return "\n".join(lines[i:])

        return text
