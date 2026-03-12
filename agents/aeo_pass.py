"""Agent 11: AEO (Answer Engine Optimization) Pass."""

import json
from agents.base import BaseAgent
from state import PipelineState
from prompts.templates import AEO_PASS_SYSTEM


class AEOPassAgent(BaseAgent):
    name = "AEO Pass"
    description = "Optimize article for AI answer engines while preserving voice"
    agent_number = 11
    emoji = "\U0001f916"

    def run(self, state: PipelineState) -> PipelineState:
        article = state.seo_pass_article or state.fact_check_article or state.draft_article

        questions = []
        if state.keyword_data:
            questions = state.keyword_data.get("questions_people_ask", [])

        user_prompt = f"""## Article to Optimize
{article}

## Questions People Ask About This Topic
{json.dumps(questions, indent=2) if questions else 'No questions data available.'}

## Keyword Data
{json.dumps(state.keyword_data, indent=2) if state.keyword_data else 'No keyword data.'}

## Primary Keyword
{state.target_keyword}

Optimize this article for AI answer engines as specified in your instructions.
Return the change log, scorecard, then the full revised article."""

        self.progress("Running AEO optimization pass...")
        response = self.call_llm(AEO_PASS_SYSTEM, user_prompt)

        # Parse response
        state.aeo_pass_article = self._extract_article(response)
        state.aeo_changes = self._parse_changes(response)

        self.log(f"Made {len(state.aeo_changes)} AEO changes")
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
        """Parse AEO changes from the response."""
        changes = []
        in_changes = False
        for line in text.split("\n"):
            stripped = line.strip()
            if "AEO CHANGES" in stripped.upper():
                in_changes = True
                continue
            if "AEO SCORECARD" in stripped.upper() or stripped == "---":
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
