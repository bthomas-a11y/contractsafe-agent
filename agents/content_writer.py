"""Agent 7: Content Writer - writes the full article from the brief."""

from agents.base import BaseAgent
from agents.knowledge_loader import load_full_knowledge_pack
from state import PipelineState
from prompts.templates import CONTENT_WRITER_SYSTEM
from config import WRITER_MODEL


class ContentWriterAgent(BaseAgent):
    name = "Content Writer"
    description = "Write the full article from the consolidated brief"
    agent_number = 7
    model = WRITER_MODEL
    emoji = "\u270d\ufe0f"

    def run(self, state: PipelineState) -> PipelineState:
        # Force-load ALL knowledge (brand voice + style rules + North Star articles)
        knowledge_pack = load_full_knowledge_pack()

        user_prompt = f"""Here is your content brief:
{state.consolidated_brief}

{knowledge_pack}

Write a {state.content_type} of approximately {state.target_word_count} words on the topic: {state.topic}

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

    def run_with_revisions(self, state: PipelineState, notes: str) -> PipelineState:
        """Re-run the writer with revision notes."""
        knowledge_pack = load_full_knowledge_pack()

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
        """Parse the writer's response to extract metaphor and article."""
        lines = response.split("\n")
        metaphor_lines = []
        article_start = 0

        for i, line in enumerate(lines):
            lower = line.lower().strip()
            if lower.startswith("# ") or lower.startswith("## "):
                article_start = i
                break
            elif "metaphor" in lower or "mapping" in lower:
                metaphor_lines.append(line)
            elif metaphor_lines and line.strip():
                metaphor_lines.append(line)
            elif metaphor_lines and not line.strip():
                article_start = i + 1
                for j in range(i + 1, len(lines)):
                    if lines[j].strip():
                        article_start = j
                        break
                break

        if metaphor_lines:
            state.extended_metaphor = "\n".join(metaphor_lines)
            state.draft_article = "\n".join(lines[article_start:])
        else:
            state.extended_metaphor = ""
            state.draft_article = response
