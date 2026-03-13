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
        # Load voice, style rules, and North Star voice excerpts
        knowledge_pack = self._load_knowledge()

        # Trim citation map to just URL + anchor
        citation_summary = self._trim_citation_map(state.citation_map)

        user_prompt = f"""## VOICE IS THE #1 PRIORITY

Before anything else: this article must sound like a smart friend explaining something over coffee. Every paragraph. Not just the intro. If a paragraph reads like a textbook or corporate blog, rewrite it until it sounds like conversation.

Specific voice requirements:
- Start the opening with a universal observation or analogy, NOT the topic. Keep the intro under 150 words.
- State your chosen metaphor in one sentence before the article, then introduce it naturally in the opening and thread it through sections with brief callbacks. Do NOT front-load the metaphor as a mapping.
- Every section needs personality: parenthetical asides, rhetorical questions, direct address ("you"), conversational bridges ("Here's the thing," "The part most people get wrong"), observations that feel like genuine realizations.
- Tell at least one COMPLETE story (setup, context, buildup, payoff). Don't summarize stories.
- No flat informational paragraphs. If you catch yourself writing "X is a document that does Y," stop and rewrite it as "X is basically Y (which is the key distinction, and the one that trips people up)."

{knowledge_pack}

## Content Brief
{state.consolidated_brief}

## Citation Map (URL + anchor text only)
{citation_summary}

## Specifications
- Content type: {state.content_type}
- Topic: {state.topic}
- **Word cap: {state.target_word_count} words maximum.** Shorter is fine if the topic is fully covered. Do not pad.
- Primary keyword: {state.target_keyword}
- Secondary keywords: {', '.join(state.secondary_keywords)}

{f'Additional instructions from the user: {state.additional_instructions}' if state.additional_instructions else ''}

## Style Rules (apply on first draft)
- No em dashes (use commas, periods, or restructure)
- All paragraphs under 42 words
- Curly quotes only
- Integrate all links from the citation map naturally
- Format bullet lists and numbered lists with each item on its own line
- **Section ordering: The FAQ section must always be the LAST section of the article.** Any product/software CTA section goes before FAQs, not after."""

        self.progress("Writing article (this may take a minute)...")
        response = self.call_llm(CONTENT_WRITER_SYSTEM, user_prompt)

        # Parse metaphor and article
        self._parse_response(state, response)

        word_count = len(state.draft_article.split())
        self.log(f"Draft complete: ~{word_count} words")
        return state

    def _load_knowledge(self) -> str:
        """Load brand voice, style rules, and North Star voice excerpts.

        The North Star article demonstrates the target voice. We extract KEY
        excerpts that show patterns Claude wouldn't naturally produce:
        - Metaphor callbacks at section openings
        - Complete stories told fully
        - Casual product integration with personality
        - Short punchy sentences mixed with longer ones
        """
        brand_voice = load_brand_voice()
        style_rules = load_style_rules()

        # Load North Star voice excerpts
        voice_excerpts = self._extract_voice_excerpts()

        return f"""## BRAND VOICE GUIDE (MANDATORY)
{brand_voice}

## STYLE RULES (MANDATORY)
{style_rules}

{voice_excerpts}"""

    def _extract_voice_excerpts(self) -> str:
        """Extract voice-teaching excerpts from North Star articles.

        These excerpts demonstrate patterns Claude wouldn't naturally produce.
        We load selected passages, not the full article, to keep context focused.
        """
        if not NORTH_STAR_DIR.exists():
            return ""

        articles = list(NORTH_STAR_DIR.glob("*.md"))
        if not articles:
            return ""

        # Read the first article
        content = articles[0].read_text()

        # Extract specific passages that demonstrate key voice patterns
        excerpts = []

        # 1. How the metaphor is introduced (casual, not announced)
        excerpts.append("""PATTERN: Introducing the metaphor casually, not as a declaration
"Think of it like planning a hike. This is that initial text you send to figure out which friend you can rope in to go with you."
NOTE: The metaphor isn't announced ("My metaphor is hiking"). It's just used naturally.""")

        # 2. How the metaphor is called back in each section
        excerpts.append("""PATTERN: Brief metaphor callbacks at section openings (different angle each time)
Section 2: "This is like talking over your hiking route with all your friends before you set off into the woods together."
Section 3: "After wrangling your hiking buddies into an outdoor adventure and planning the details, you'll get their final approval on which trail you're taking and who's providing the snacks."
Section 5: "The moment of truth comes once you've finished your hike. You'll likely be in your car with your friend, debriefing about the experience. Was the walk too steep? Was the scenery worth it?"
NOTE: Each callback advances the metaphor's story, not just repeats it.""")

        # 3. Complete story (George Lucas) — told fully, not summarized
        excerpts.append("""PATTERN: Complete story with setup, buildup, and ironic payoff
"Take, for example, the contract between 20th Century Fox and George Lucas over merchandising rights for Star Wars back in 1977. George Lucas said, 'Sure, I'll take a lower directors fee... if you give me all the licenses for the characters in the movie.' The studio execs happily agreed, laughing all the way to the bank. I mean, it's not like anyone would ever create an entire Disney theme park based on this Star Wars flick, right? Right?!"
NOTE: The irony IS the payoff. The story is told, not summarized.""")

        # 4. Casual product integration — personality, not feature list
        excerpts.append("""PATTERN: Product mentions with personality (not feature lists)
"That's a lot to write out from scratch every time! But luckily, you don't have to."
"That way, your contract language will always stay consistent and rock-solid, and your business will be protected from a lot of unnecessary risks. (We can hear your lawyer breathing a sigh of relief!)"
"Instead of emailing 'Agreement_version_2_update_5.docx' back and forth, everyone clicks straight into the same live version of the same document"
NOTE: Specific scenarios ("Agreement_version_2_update_5.docx") beat abstract claims ("streamline collaboration").""")

        # 5. Transitions that are conversational, not formal
        excerpts.append("""PATTERN: Conversational transitions (not "Furthermore" or "Additionally")
"But we'll be the first to admit this all sounds a little jargony, which is why we're breaking it down into human terms."
"If this all sounds like a lot of work, well, that's what we're here to help with."
"You can probably see where we're going with this."
NOTE: These transitions feel like someone TALKING, not writing a paper.""")

        return "## NORTH STAR VOICE EXAMPLES (MANDATORY REFERENCE)\n\nThese excerpts from a published ContractSafe article demonstrate the EXACT voice you must use. Study the patterns, not the topic.\n\n" + "\n\n".join(excerpts)

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
        knowledge_pack = self._load_knowledge()

        user_prompt = f"""Here is the current draft:

{state.draft_article}

{knowledge_pack}

The user provided these revision notes:
{notes}

Please revise the article to address this feedback. Maintain the same extended metaphor
and conversational voice throughout. Every paragraph should sound like you're explaining
to a friend, not writing a textbook. Return the complete revised article.

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
