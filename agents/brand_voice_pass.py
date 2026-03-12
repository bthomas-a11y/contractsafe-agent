"""Agent 8: Brand Voice Pass - checks and fixes voice consistency."""

from agents.base import BaseAgent
from agents.knowledge_loader import load_full_knowledge_pack
from state import PipelineState
from prompts.templates import BRAND_VOICE_PASS_SYSTEM


class BrandVoicePassAgent(BaseAgent):
    name = "Brand Voice Pass"
    description = "Check and fix brand voice consistency"
    agent_number = 8
    emoji = "\U0001f3a4"

    def run(self, state: PipelineState) -> PipelineState:
        article = state.draft_article

        # Force-load ALL knowledge (brand voice + style rules + North Star articles)
        knowledge_pack = load_full_knowledge_pack()

        user_prompt = f"""{knowledge_pack}

## Extended Metaphor Used
{state.extended_metaphor or 'Not specified'}

## Article to Review
{article}

Review this article for voice consistency against the North Star articles above.
The North Star articles define the target voice. Fix any issues found.
Return the change log followed by the full revised article."""

        self.progress("Running voice consistency check...")
        response = self.call_llm(BRAND_VOICE_PASS_SYSTEM, user_prompt)

        # Parse response: change log + revised article
        state.voice_pass_article, state.voice_issues_found = self._parse_response(response)

        self.log(f"Found and fixed {len(state.voice_issues_found)} voice issues")
        return state

    def _parse_response(self, response: str) -> tuple[str, list[dict]]:
        """Parse the change log and revised article from the response."""
        issues = []

        # Split on the separator
        if "---" in response:
            parts = response.split("---", 1)
            change_log = parts[0]
            article = parts[1].strip()
        else:
            lines = response.split("\n")
            article_start = 0
            change_log = ""
            for i, line in enumerate(lines):
                if line.strip().startswith("# ") and i > 5:
                    article_start = i
                    change_log = "\n".join(lines[:i])
                    break
            article = "\n".join(lines[article_start:]) if article_start > 0 else response

        # Parse change log into structured issues
        if change_log:
            for line in change_log.split("\n"):
                line = line.strip()
                if line and (line[0].isdigit() or line.startswith("-")):
                    text = line.lstrip("0123456789.-) ").strip()
                    if ":" in text:
                        location, rest = text.split(":", 1)
                        if "->" in rest or "\u2192" in rest:
                            sep = "->" if "->" in rest else "\u2192"
                            parts = rest.split(sep)
                            issues.append({
                                "location": location.strip(),
                                "issue": parts[0].strip(),
                                "fix": parts[1].strip() if len(parts) > 1 else "",
                            })
                        else:
                            issues.append({
                                "location": location.strip(),
                                "issue": rest.strip(),
                                "fix": "",
                            })

        return article, issues
