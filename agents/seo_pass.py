"""Agent 10: SEO Pass - optimizes article for search without breaking voice."""

import json
from agents.base import BaseAgent
from state import PipelineState
from prompts.templates import SEO_PASS_SYSTEM


class SEOPassAgent(BaseAgent):
    name = "SEO Pass"
    description = "Optimize article for search engines while preserving voice"
    agent_number = 10
    emoji = "\U0001f50e"

    def run(self, state: PipelineState) -> PipelineState:
        article = state.fact_check_article or state.voice_pass_article or state.draft_article

        user_prompt = f"""## Article to Optimize
{article}

## SEO Parameters
- Primary keyword: {state.target_keyword}
- Secondary keywords: {', '.join(state.secondary_keywords)}
- Target word count: {state.target_word_count}

## Keyword Data
{json.dumps(state.keyword_data, indent=2) if state.keyword_data else 'No keyword data.'}

## SEO Brief
{state.seo_brief[:3000]}

## Recommended H2s
{json.dumps(state.recommended_h2s, indent=2) if state.recommended_h2s else 'No H2 recommendations.'}

## Internal Links Available
{json.dumps(state.internal_links, indent=2) if state.internal_links else 'No internal links.'}

## Citation Map
{json.dumps(state.citation_map, indent=2) if state.citation_map else 'No citation map.'}

Optimize this article for SEO as specified in your instructions.
Return the change log, scorecard, then the full revised article."""

        self.progress("Running SEO optimization pass...")
        response = self.call_llm(SEO_PASS_SYSTEM, user_prompt)

        # Parse response
        state.seo_pass_article = self._extract_article(response)
        state.seo_changes = self._parse_changes(response)

        self.log(f"Made {len(state.seo_changes)} SEO changes")
        return state

    def _extract_article(self, text: str) -> str:
        """Extract the article from the response."""
        if "---" in text:
            parts = text.split("---")
            article_parts = [p for p in parts if len(p.strip()) > 500]
            if article_parts:
                return article_parts[-1].strip()

        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line.strip().startswith("# "):
                return "\n".join(lines[i:])
        return text

    def _parse_changes(self, text: str) -> list[dict]:
        """Parse SEO changes from the response."""
        changes = []
        in_changes = False
        for line in text.split("\n"):
            stripped = line.strip()
            if "SEO CHANGES" in stripped.upper():
                in_changes = True
                continue
            if "SEO SCORECARD" in stripped.upper() or stripped == "---":
                in_changes = False
                continue
            if in_changes and stripped and (stripped[0].isdigit() or stripped.startswith("-")):
                text_part = stripped.lstrip("0123456789.-) ").strip()
                if ":" in text_part:
                    change, reason = text_part.split(":", 1)
                    changes.append({"change": change.strip(), "reason": reason.strip()})
                else:
                    changes.append({"change": text_part, "reason": ""})
        return changes
