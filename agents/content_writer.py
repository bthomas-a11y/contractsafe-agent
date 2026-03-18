"""Agent 7: Content Writer - writes the full article from the brief.

Uses section-by-section generation with minimal prompts.
Each call writes 2-3 sections (~300-600 words).
Links are NOT included — they are added by Agent 10 (SEO Pass) programmatically.
"""

from agents.base import BaseAgent
from state import PipelineState
from prompts.templates import CONTENT_WRITER_SYSTEM
from config import WRITER_MODEL


class ContentWriterAgent(BaseAgent):
    name = "Content Writer"
    description = "Write the full article from the consolidated brief"
    agent_number = 7
    model = WRITER_MODEL
    timeout = 180  # Per-call timeout (each call writes a portion, not the full article)
    emoji = "\u270d\ufe0f"

    def run(self, state: PipelineState) -> PipelineState:
        self.progress("Writing article section-by-section with Opus...")
        h2s = state.recommended_h2s or []
        # Cap H2s based on target word count (~300 words/section + 200 for intro/TL;DR)
        max_sections = max(3, (state.target_word_count - 200) // 300)
        if len(h2s) > max_sections:
            self.progress(f"Capping H2s from {len(h2s)} to {max_sections} (for {state.target_word_count}-word target)")
            h2s = h2s[:max_sections]
        chunks = self._split_h2s(h2s)

        # --- Call 1: Intro + TL;DR + first chunk of H2s ---
        self.progress(f"Writing intro + first {len(chunks[0])} sections...")
        first_sections = ", ".join(f'"{h}"' for h in chunks[0])
        intro_prompt = f"""Topic: {state.topic}
Keyword: {state.target_keyword}
Word cap: {state.target_word_count} words (full article).
H2 outline: {', '.join(h2s)}
Key facts: {self._condense_facts(state)}

Write: metaphor declaration (one sentence), H1, intro (<150 words), TL;DR (3-5 bullets), then sections: {first_sections}.
~250-350 words per section. Stop after the last section listed.
{f'Note: {state.additional_instructions}' if state.additional_instructions else ''}"""

        part1 = self.call_llm(CONTENT_WRITER_SYSTEM, intro_prompt)

        if len(part1) < 1000:
            self.log(f"[yellow]SHORT RESPONSE part1 ({len(part1)} chars): {part1[:500]}[/yellow]")

        # Extract metaphor for voice continuity in later calls
        metaphor = self._extract_metaphor(part1)

        # --- Call 2+: Continue with remaining chunks ---
        parts = [part1]
        for i, chunk in enumerate(chunks[1:], 2):
            section_names = ", ".join(f'"{h}"' for h in chunk)
            is_last = (i == len(chunks))
            self.progress(f"Writing sections {section_names[:60]}...")

            continue_prompt = f"""Continue the article. Write sections: {section_names}
Keyword: {state.target_keyword}
Metaphor: {metaphor}
{f'Product info: {state.product_knowledge[:500]}' if is_last and state.product_knowledge else ''}
~250-350 words per section. Same voice. Start with the H2 heading."""

            part = self.call_llm(CONTENT_WRITER_SYSTEM, continue_prompt)
            if len(part) < 500:
                self.log(f"[yellow]SHORT RESPONSE part{i} ({len(part)} chars): {part[:500]}[/yellow]")
            parts.append(part)

        # --- Assemble full article ---
        full_response = "\n\n".join(parts)
        self._parse_response(state, full_response)

        word_count = len(state.draft_article.split())
        self.log(f"Draft complete: ~{word_count} words ({len(chunks)} calls)")
        return state

    def _split_h2s(self, h2s: list[str]) -> list[list[str]]:
        """Split H2s into chunks of 2-3 for multi-call generation."""
        if not h2s:
            return [["Introduction"]]

        chunks = []
        chunk_size = 3 if len(h2s) <= 6 else 2
        for i in range(0, len(h2s), chunk_size):
            chunks.append(h2s[i:i + chunk_size])

        # Ensure at least 2 chunks (so first call isn't the whole article)
        if len(chunks) == 1:
            mid = max(1, len(chunks[0]) // 2)
            chunks = [chunks[0][:mid], chunks[0][mid:]]

        return chunks

    def _extract_metaphor(self, text: str) -> str:
        """Extract the metaphor declaration (first non-heading line) for continuity."""
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and len(stripped) > 10:
                return stripped[:200]
        return ""

    def _condense_facts(self, state: PipelineState) -> str:
        """Condense key facts and statistics into a brief string."""
        facts = []
        for f in (state.key_facts or [])[:5]:
            if isinstance(f, dict):
                facts.append(f.get("fact", str(f))[:150])
            else:
                facts.append(str(f)[:150])
        for s in (state.statistics or [])[:3]:
            if isinstance(s, dict):
                facts.append(s.get("stat", str(s))[:150])
        return "; ".join(facts) if facts else "N/A"

    def run_with_revisions(self, state: PipelineState, notes: str) -> PipelineState:
        """Re-run the writer with revision notes."""
        user_prompt = f"""Here is the current draft:

{state.draft_article}

The user provided these revision notes:
{notes}

Revise the article to address this feedback. Maintain the same extended metaphor
and conversational voice. Return the complete revised article."""

        self.progress("Revising article based on feedback...")
        response = self.call_llm(CONTENT_WRITER_SYSTEM, user_prompt)
        self._parse_response(state, response)

        word_count = len(state.draft_article.split())
        self.log(f"Revision complete: ~{word_count} words")
        return state

    def _parse_response(self, state: PipelineState, response: str):
        """Parse the writer's response to extract metaphor preamble and article."""
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

        # Strip trailing social copy that Claude sometimes appends
        state.draft_article = self._strip_trailing_social(state.draft_article)

    @staticmethod
    def _strip_trailing_social(article: str) -> str:
        """Remove social copy that Claude appends after the article."""
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
