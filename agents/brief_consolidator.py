"""Agent 6: Brief Consolidator - combines all research into a unified writer's brief."""

import json
from agents.base import BaseAgent
from state import PipelineState
from prompts.templates import BRIEF_CONSOLIDATOR_SYSTEM


class BriefConsolidatorAgent(BaseAgent):
    name = "Brief Consolidator"
    description = "Synthesize all research into a single content brief"
    agent_number = 6
    emoji = "\U0001f4cb"

    def run(self, state: PipelineState) -> PipelineState:
        # Build comprehensive user prompt with all research
        user_prompt = f"""## CONTENT SPECIFICATIONS
- Content type: {state.content_type}
- Target word count: {state.target_word_count}
- Primary keyword: {state.target_keyword}
- Secondary keywords: {', '.join(state.secondary_keywords)}
- Additional instructions: {state.additional_instructions or 'None'}

## PRODUCT KNOWLEDGE (Agent 1)
{state.product_knowledge}

## SUBJECT RESEARCH (Agent 2)
{state.subject_research[:6000]}

## KEY FACTS
{json.dumps(state.key_facts, indent=2) if state.key_facts else 'No key facts extracted.'}

## STATISTICS
{json.dumps(state.statistics, indent=2) if state.statistics else 'No statistics extracted.'}

## COMPETITOR ANALYSIS (Agent 3)
{json.dumps(state.competitor_pages, indent=2) if state.competitor_pages else 'No competitor data.'}

## KEYWORD DATA
{json.dumps(state.keyword_data, indent=2) if state.keyword_data else 'No keyword data.'}

## SEO BRIEF (Agent 4)
{state.seo_brief[:4000]}

## SERP FEATURES
{json.dumps(state.serp_features, indent=2) if state.serp_features else 'None identified.'}

## RECOMMENDED H2 STRUCTURE
{json.dumps(state.recommended_h2s, indent=2) if state.recommended_h2s else 'No recommendations.'}

## INTERNAL LINKS
{json.dumps(state.internal_links, indent=2) if state.internal_links else 'No internal links found.'}

## EXTERNAL LINKS
{json.dumps(state.external_links, indent=2) if state.external_links else 'No external links found.'}

## CITATION MAP
{json.dumps(state.citation_map, indent=2) if state.citation_map else 'No citation map built.'}

Consolidate all of this research into a single, actionable content brief for the writer.
Follow the format specified in your instructions."""

        self.progress("Consolidating research into brief...")
        state.consolidated_brief = self.call_llm(BRIEF_CONSOLIDATOR_SYSTEM, user_prompt)
        return state

    def run_with_feedback(self, state: PipelineState, feedback: str) -> PipelineState:
        """Re-run consolidation with user feedback incorporated."""
        user_prompt = f"""The previous brief was:

{state.consolidated_brief}

The user provided this feedback:
{feedback}

Please revise the brief to address this feedback while maintaining all the research data.
Return the complete revised brief."""

        self.progress("Revising brief based on feedback...")
        state.consolidated_brief = self.call_llm(BRIEF_CONSOLIDATOR_SYSTEM, user_prompt)
        return state
