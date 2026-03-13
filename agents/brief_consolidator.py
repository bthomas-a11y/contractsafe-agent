"""Agent 6: Brief Consolidator - combines all research into a unified writer's brief.

Fully programmatic — no Claude call. Uses a structured template to concatenate
research data into an actionable brief.
"""

import json
from agents.base import BaseAgent
from state import PipelineState


class BriefConsolidatorAgent(BaseAgent):
    name = "Brief Consolidator"
    description = "Synthesize all research into a single content brief"
    agent_number = 6
    emoji = "\U0001f4cb"

    def run(self, state: PipelineState) -> PipelineState:
        self.progress("Consolidating research into brief (template-based)...")
        state.consolidated_brief = self._build_brief(state)
        self.log(f"Brief generated ({len(state.consolidated_brief)} chars)")
        return state

    def _build_brief(self, state: PipelineState) -> str:
        """Build the content brief from a structured template."""
        sections = []

        # ── Header ──
        sections.append("# Content Brief\n")

        # ── Content Specifications ──
        sections.append("## Content Specifications")
        sections.append(f"- **Topic:** {state.topic}")
        sections.append(f"- **Content Type:** {state.content_type}")
        sections.append(f"- **Primary Keyword:** {state.target_keyword}")
        if state.secondary_keywords:
            sections.append(f"- **Secondary Keywords:** {', '.join(state.secondary_keywords)}")
        sections.append(f"- **Target Word Count:** {state.target_word_count}")
        if state.additional_instructions:
            sections.append(f"- **Additional Instructions:** {state.additional_instructions}")
        sections.append("")

        # ── Recommended H2 Structure (scoped to word budget) ──
        if state.recommended_h2s:
            # Budget: ~200 words for intro+conclusion, ~250 words per H2 section
            max_h2s = max(4, (state.target_word_count - 200) // 250)
            h2s = state.recommended_h2s[:max_h2s]
            words_per_section = (state.target_word_count - 200) // len(h2s)

            sections.append("## Recommended H2 Structure")
            sections.append(f"**Word cap: {state.target_word_count} words max. "
                          f"Cover ONLY these {len(h2s)} sections. Do NOT add extra sections.**")
            for i, h2 in enumerate(h2s, 1):
                sections.append(f"{i}. {h2}")
            if len(state.recommended_h2s) > max_h2s:
                skipped = len(state.recommended_h2s) - max_h2s
                sections.append(f"\n*({skipped} lower-priority H2s omitted to fit word count)*")
            sections.append("")

        # ── Key Facts & Statistics ──
        if state.key_facts:
            sections.append("## Key Facts to Include")
            for fact in state.key_facts:
                if isinstance(fact, dict):
                    f_text = fact.get("fact", fact.get("text", str(fact)))
                    f_source = fact.get("source", "")
                    sections.append(f"- {f_text}" + (f" (Source: {f_source})" if f_source else ""))
                else:
                    sections.append(f"- {fact}")
            sections.append("")

        if state.statistics:
            sections.append("## Statistics to Reference")
            for stat in state.statistics:
                if isinstance(stat, dict):
                    s_text = stat.get("stat", stat.get("text", str(stat)))
                    s_source = stat.get("source_name", stat.get("source", ""))
                    s_url = stat.get("source_url", "")
                    line = f"- {s_text}"
                    if s_source:
                        line += f" — {s_source}"
                    if s_url:
                        line += f" ({s_url})"
                    sections.append(line)
                else:
                    sections.append(f"- {stat}")
            sections.append("")

        # ── Target Audience ──
        sections.append("## Target Audience")
        sections.append("- In-house legal teams and contract managers")
        sections.append("- Operations and procurement professionals who manage contracts")
        sections.append("- Small-to-mid-size business leaders evaluating CLM solutions")
        sections.append("")

        # ── CTA Strategy ──
        sections.append("## CTA Strategy")
        sections.append("- Primary CTA: Link to relevant ContractSafe features/product pages")
        sections.append("- Secondary CTA: Link to related blog posts for further reading")
        sections.append("- Tone: Helpful, not pushy. Mention ContractSafe as a solution where natural.")
        sections.append("")

        # ── Product Hooks ──
        if state.product_knowledge:
            sections.append("## Product Knowledge & Hooks")
            # Truncate at sentence boundary if very long
            pk = state.product_knowledge
            if len(pk) > 3000:
                # Find the last sentence end before 3000 chars
                truncated = pk[:3000]
                last_period = truncated.rfind(". ")
                if last_period > 2000:
                    pk = truncated[:last_period + 1]
                else:
                    pk = truncated
                pk += "\n\n[...truncated for brevity]"
            sections.append(pk)
            sections.append("")

        # ── Citation Map ──
        if state.citation_map:
            sections.append("## Citation Map (Links by Section)")
            for section_name, links in state.citation_map.items():
                sections.append(f"\n### {section_name}")
                for link in links:
                    link_type = link.get("type", "unknown")
                    url = link.get("url", "")
                    anchor = link.get("anchor", link.get("anchor_suggestion", ""))
                    sections.append(f"- [{anchor}]({url}) ({link_type})")
            sections.append("")

        # ── Competitor Gaps ──
        if state.competitor_pages:
            sections.append("## Competitor Gaps to Exploit")
            for cp in state.competitor_pages:
                gaps = cp.get("gaps", "")
                if gaps and gaps != "N/A":
                    title = cp.get("title", "Unknown")
                    sections.append(f"- **{title}:** {gaps}")
            sections.append("")

        # ── Questions to Answer ──
        questions = state.keyword_data.get("questions_people_ask", []) if state.keyword_data else []
        if questions:
            sections.append("## Questions to Answer in the Article")
            for q in questions[:10]:
                sections.append(f"- {q}")
            sections.append("")

        # ── SERP Features ──
        if state.serp_features:
            sections.append("## SERP Features Detected")
            for f in state.serp_features:
                sections.append(f"- {f}")
            sections.append("")

        return "\n".join(sections)

    def run_with_feedback(self, state: PipelineState, feedback: str) -> PipelineState:
        """Re-run consolidation with user feedback appended."""
        self.progress("Revising brief based on feedback...")
        # Rebuild the brief and append feedback as a note
        base_brief = self._build_brief(state)
        state.consolidated_brief = (
            base_brief
            + "\n\n## User Feedback\n"
            + feedback
            + "\n\n*Please incorporate the above feedback when writing the article.*\n"
        )
        return state
