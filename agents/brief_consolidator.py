"""Agent 6: Brief Consolidator - combines all research into a unified writer's brief.

Fully programmatic — no Claude call. Uses a structured template to concatenate
research data into an actionable brief.
"""

import json
import re
from agents.base import BaseAgent
from state import PipelineState


class BriefConsolidatorAgent(BaseAgent):
    name = "Brief Consolidator"
    description = "Synthesize all research into a single content brief"
    agent_number = 6
    emoji = "\U0001f4cb"

    def run(self, state: PipelineState) -> PipelineState:
        self.progress("Assembling writer brief from research, SEO data, and citations...")
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
        max_h2s = max(4, (state.target_word_count - 200) // 250) if state.target_word_count else 6
        if state.recommended_h2s:
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

        # ── Research Data Organized by H2 Section ──
        # A blog writer organizes their research notes by section, not in one pile.
        # Each H2 gets the facts, stats, and source URLs relevant to it.
        sections.append("## Research for Each Section")
        sections.append("**USE ONLY THESE STATS AND FACTS. DO NOT INVENT OTHERS.**")
        sections.append("**When you use a stat, name the source in the text (e.g., 'according to World Commerce & Contracting').**\n")

        h2s_for_brief = state.recommended_h2s[:max_h2s] if state.recommended_h2s else []
        all_research = list(state.statistics or []) + list(state.key_facts or [])

        if h2s_for_brief and all_research:
            # Map each research item to its best-matching H2 by keyword tag + word overlap
            h2_research = {h2: [] for h2 in h2s_for_brief}
            unmatched = []

            for item in all_research:
                item_kw = item.get("keyword", "")
                item_text = item.get("stat", item.get("fact", "")).lower()
                is_stat = "stat" in item

                best_h2 = None
                best_score = 0
                for h2 in h2s_for_brief:
                    h2_lower = h2.lower()
                    score = 0
                    # Keyword tag matches H2 words
                    if item_kw:
                        kw_words = set(item_kw.lower().split())
                        h2_words = set(re.findall(r'[a-z]+', h2_lower))
                        score = len(kw_words & h2_words)
                    # Item text matches H2 words
                    h2_content_words = set(re.findall(r'[a-z]{4,}', h2_lower))
                    text_overlap = sum(1 for w in h2_content_words if w in item_text)
                    score += text_overlap
                    if score > best_score:
                        best_score = score
                        best_h2 = h2

                if best_h2 and best_score >= 2:
                    h2_research[best_h2].append(item)
                else:
                    unmatched.append(item)

            for h2 in h2s_for_brief:
                items = h2_research[h2]
                sections.append(f"\n### For: {h2}")
                if items:
                    for item in items[:4]:
                        if "stat" in item:
                            s_text = item.get("stat", "")[:150]
                            s_source = item.get("source_name", "")
                            s_url = item.get("source_url", "")
                            sections.append(f"- STAT: {s_text}")
                            if s_source:
                                sections.append(f"  Source name: {s_source}")
                            if s_url:
                                sections.append(f"  Source URL: {s_url}")
                        else:
                            f_text = item.get("fact", "")[:150]
                            f_source = item.get("source", "")
                            sections.append(f"- FACT: {f_text}")
                            if f_source:
                                sections.append(f"  Source: {f_source}")
                else:
                    sections.append("- (No specific research data for this section — write from general knowledge without inventing statistics)")
            sections.append("")

            if unmatched:
                sections.append("### General research (use where appropriate)")
                for item in unmatched[:5]:
                    if "stat" in item:
                        sections.append(f"- STAT: {item.get('stat', '')[:150]} — {item.get('source_name', '')} ({item.get('source_url', '')})")
                    else:
                        sections.append(f"- FACT: {item.get('fact', '')[:150]} (Source: {item.get('source', '')})")
                sections.append("")
        elif all_research:
            # No H2s to map to — list all research generically
            sections.append("### Available Research Data")
            for item in all_research[:10]:
                if "stat" in item:
                    sections.append(f"- STAT: {item.get('stat', '')[:150]} — {item.get('source_name', '')} ({item.get('source_url', '')})")
                else:
                    sections.append(f"- FACT: {item.get('fact', '')[:150]} (Source: {item.get('source', '')})")
            sections.append("")
        else:
            sections.append("- No research data available. Write without statistics. DO NOT INVENT ANY.")
            sections.append("")

        # ── Keyword Cluster Strategy (from Agent 0) ──
        cluster = state.keyword_cluster
        if cluster and not cluster.get("synthesis_failed"):
            sections.append("## Article Strategy (from Keyword Research)")
            if cluster.get("article_angle"):
                sections.append(f"**Article Angle:** {cluster['article_angle']}")
            if cluster.get("vocabulary_note"):
                sections.append(f"**Vocabulary:** {cluster['vocabulary_note']}")
            if cluster.get("cannibalization_notes"):
                sections.append(f"**Cannibalization Warning:** {cluster['cannibalization_notes']}")
            sections.append("")

            if cluster.get("content_gaps"):
                sections.append("## Content Gaps to Cover")
                sections.append("*These topics are NOT covered by any top-ranking competitor. "
                              "Covering them differentiates this article:*")
                for gap in cluster["content_gaps"]:
                    sections.append(f"- **{gap.get('topic', '')}**: {gap.get('explanation', '')}")
                sections.append("")

            if cluster.get("strategic_notes"):
                sections.append("## Strategic Notes")
                for note in cluster["strategic_notes"]:
                    sections.append(f"- {note}")
                sections.append("")

            if cluster.get("ai_overview_strategy"):
                sections.append("## AI Overview Citability Strategy")
                sections.append(cluster["ai_overview_strategy"])
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

        # ── AI Citability Intelligence ──
        ca = state.citability_analysis
        if ca and ca.get("queries_with_ai_overview", 0) > 0:
            sections.append("## AI Citability Intelligence")
            sections.append("*Google AI Overviews exist for this topic. Structure content to be citable:*\n")

            cp = ca.get("citation_patterns", {})
            if cp.get("definition_blocks", 0) >= 2:
                sections.append("- **Start key sections with definitions:** Use 'X is a [concise definition].' format in the first sentence after each H2.")
            if cp.get("bold_label_lists", 0) >= 2:
                sections.append("- **Use bold-label lists:** Format feature/benefit sections as '**Feature:** Description' bullets.")
            if cp.get("numbered_steps", 0) >= 2:
                sections.append("- **Use numbered steps** for process/how-to sections.")
            if cp.get("data_backed_claims", 0) >= 1:
                sections.append("- **Include sourced statistics** inline with named sources (e.g., 'According to Deloitte, ...').")

            if not ca.get("our_domain_cited"):
                top = ca.get("top_cited_domains", [])[:3]
                if top:
                    domains = ", ".join(d["domain"] for d in top)
                    sections.append(f"\n**Competitive gap:** ContractSafe is NOT cited in AI Overviews. Currently cited: {domains}.")
                    sections.append("Write definitive, extractable answers that are more specific and better sourced than these competitors.")
            sections.append("")

        # ── Keyword Clusters (from SEMrush) ──
        if state.keyword_clusters:
            sections.append("## Keyword Clusters to Target")
            for cluster in state.keyword_clusters:
                name = cluster.get("name", "")
                keywords = cluster.get("keywords", [])
                if keywords:
                    kw_list = ", ".join(
                        f"{k['keyword']} (vol: {k.get('volume', 'N/A')})"
                        for k in keywords[:5]
                    )
                    sections.append(f"- **{name}:** {kw_list}")
            sections.append("")

        # ── Keyword Gaps (from SEMrush) ──
        if state.keyword_gaps:
            sections.append("## Keyword Gap Opportunities")
            sections.append("*Keywords competitors rank for that we don't — weave into content where natural:*")
            for gap in state.keyword_gaps[:8]:
                kw = gap.get("keyword", "")
                vol = gap.get("volume", "N/A")
                sections.append(f"- {kw} (vol: {vol})")
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
