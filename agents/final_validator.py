"""Agent 13: Final Validator - comprehensive quality checklist."""

from agents.base import BaseAgent
from agents.knowledge_loader import load_full_knowledge_pack
from state import PipelineState
from prompts.templates import FINAL_VALIDATOR_SYSTEM


class FinalValidatorAgent(BaseAgent):
    name = "Final Validator"
    description = "Run comprehensive quality checklist on the complete package"
    agent_number = 13
    emoji = "\U0001f3c1"

    def run(self, state: PipelineState) -> PipelineState:
        article = (
            state.aeo_pass_article
            or state.seo_pass_article
            or state.fact_check_article
            or state.draft_article
        )

        # Force-load ALL knowledge for voice comparison
        knowledge_pack = load_full_knowledge_pack()

        user_prompt = f"""{knowledge_pack}

## Final Article to Validate
{article}

## Extended Metaphor
{state.extended_metaphor or 'Not specified'}

## Primary Keyword
{state.target_keyword}

## Secondary Keywords
{', '.join(state.secondary_keywords)}

## Content Type
{state.content_type}

## Target Word Count
{state.target_word_count}

## Meta Description
{state.meta_description}

## LinkedIn Post
{state.linkedin_post}

## X/Twitter Post
{state.twitter_post}

Run the complete validation checklist as specified in your instructions.
Compare the article's voice against the North Star articles above.
Produce a detailed pass/fail report."""

        self.progress("Running final validation...")
        response = self.call_llm(FINAL_VALIDATOR_SYSTEM, user_prompt)

        state.validation_report = response
        state.final_article = article
        state.pass_fail = self._check_overall(response)

        status = "PASS" if state.pass_fail else "FAIL"
        self.log(f"Validation result: {status}")
        return state

    def _check_overall(self, report: str) -> bool:
        """Check if the overall validation passed."""
        for line in report.split("\n"):
            stripped = line.strip().upper()
            if "OVERALL:" in stripped:
                return "PASS" in stripped and "FAIL" not in stripped.split("PASS")[0]
        return False
