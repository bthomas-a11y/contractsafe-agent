"""Agent 7: Content Writer - writes the full article from the brief."""

import json
from agents.base import BaseAgent, WRITER_TIMEOUT
from agents.knowledge_loader import load_brand_voice, load_style_rules, load_north_star_articles
from state import PipelineState
from prompts.templates import CONTENT_WRITER_SYSTEM
from config import WRITER_MODEL, NORTH_STAR_DIR


class ContentWriterAgent(BaseAgent):
    name = "Content Writer"
    description = "Write the full article from the consolidated brief"
    agent_number = 7
    model = WRITER_MODEL
    timeout = WRITER_TIMEOUT  # 5 min — writing a full article takes longer
    emoji = "\u270d\ufe0f"

    def run(self, state: PipelineState) -> PipelineState:
        # Load trimmed knowledge pack (1 North Star, no redundancy)
        knowledge_pack = self._load_trimmed_knowledge()

        # Trim citation map to just URL + anchor
        citation_summary = self._trim_citation_map(state.citation_map)

        user_prompt = f"""Here is your content brief:
{state.consolidated_brief}

{knowledge_pack}

## Citation Map (URL + anchor text only)
{citation_summary}

Write a {state.content_type} on the topic: {state.topic}
**Word cap: {state.target_word_count} words maximum.** Shorter is fine if the topic is fully covered. Do not pad.

Primary keyword: {state.target_keyword}
Secondary keywords: {', '.join(state.secondary_keywords)}

{f'Additional instructions from the user: {state.additional_instructions}' if state.additional_instructions else ''}

Remember:
- Choose your OWN extended metaphor (state it before the article)
- Tell COMPLETE stories with narrative arcs
- Meander and digress, take the scenic route
- Same voice as casual conversation, no mode switching
- Apply ALL style rules on this first draft (no em dashes, under 42 words per paragraph, curly quotes)
- Integrate all links from the citation map naturally"""

        self.progress("Writing article (this may take a minute)...")
        response = self.call_llm(CONTENT_WRITER_SYSTEM, user_prompt)

        # Parse metaphor and article
        self._parse_response(state, response)

        word_count = len(state.draft_article.split())
        self.log(f"Draft complete: ~{word_count} words")
        return state

    def _load_trimmed_knowledge(self) -> str:
        """Load knowledge pack with only 1 North Star article (the shorter one)."""
        brand_voice = load_brand_voice()
        style_rules = load_style_rules()

        # Pick the shorter North Star article
        north_star_text = ""
        if NORTH_STAR_DIR.exists():
            articles = []
            for f in sorted(NORTH_STAR_DIR.glob("*.md")):
                content = f.read_text()
                articles.append((f.stem, content, len(content)))

            if articles:
                # Sort by length, pick shortest
                articles.sort(key=lambda x: x[2])
                stem, content, _ = articles[0]
                north_star_text = f"### NORTH STAR: {stem.replace('_', ' ').title()}\n\n{content}"

        return f"""## BRAND VOICE GUIDE (MANDATORY)
{brand_voice}

## STYLE RULES (MANDATORY)
{style_rules}

## NORTH STAR ARTICLE (MANDATORY REFERENCE)
Read this for HOW it thinks and writes, not WHAT it references.

{north_star_text or '[No North Star articles found]'}"""

    def _trim_citation_map(self, citation_map: dict) -> str:
        """Trim citation map to just URL + anchor (no full metadata)."""
        if not citation_map:
            return "No citation map available."
        lines = []
        for section, links in citation_map.items():
            lines.append(f"\n### {section}")
            for link in links:
                url = link.get("url", "")
                anchor = link.get("anchor", link.get("anchor_suggestion", ""))
                link_type = link.get("type", "")
                lines.append(f"- [{anchor}]({url}) ({link_type})")
        return "\n".join(lines)

    def run_with_revisions(self, state: PipelineState, notes: str) -> PipelineState:
        """Re-run the writer with revision notes."""
        knowledge_pack = self._load_trimmed_knowledge()

        user_prompt = f"""Here is the current draft:

{state.draft_article}

{knowledge_pack}

The user provided these revision notes:
{notes}

Please revise the article to address this feedback. Maintain the same extended metaphor
and voice. Return the complete revised article.

Remember all style rules: no em dashes, paragraphs under 42 words, curly quotes."""

        self.progress("Revising article based on feedback...")
        response = self.call_llm(CONTENT_WRITER_SYSTEM, user_prompt)
        self._parse_response(state, response)

        word_count = len(state.draft_article.split())
        self.log(f"Revision complete: ~{word_count} words")
        return state

    def _parse_response(self, state: PipelineState, response: str):
        """Parse the writer's response to extract metaphor preamble and article.

        Everything before the first H1 heading is preamble (metaphor framework).
        Everything from the H1 onward is the article.
        """
        lines = response.split("\n")
        article_start = 0

        for i, line in enumerate(lines):
            if line.strip().startswith("# ") and not line.strip().startswith("## "):
                article_start = i
                break

        if article_start > 0:
            state.extended_metaphor = "\n".join(lines[:article_start]).strip()
            state.draft_article = "\n".join(lines[article_start:]).strip()
        else:
            state.extended_metaphor = ""
            state.draft_article = response.strip()

        # Strip trailing social copy that Claude sometimes appends unbidden
        state.draft_article = self._strip_trailing_social(state.draft_article)

    @staticmethod
    def _strip_trailing_social(article: str) -> str:
        """Remove social copy / meta description that Claude appends after the article."""
        lines = article.split('\n')
        social_markers = ['linkedin post', 'twitter post', 'x/twitter post',
                          'meta description', 'seo meta', 'social post']
        earliest_cut = None
        for idx in range(len(lines) - 1, -1, -1):
            if lines[idx].strip() == '---':
                next_content = ''
                for j in range(idx + 1, min(idx + 4, len(lines))):
                    if lines[j].strip():
                        next_content = lines[j].strip().lower()
                        break
                if any(marker in next_content for marker in social_markers):
                    earliest_cut = idx
        if earliest_cut is not None:
            return '\n'.join(lines[:earliest_cut]).strip()
        return article
