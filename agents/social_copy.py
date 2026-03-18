"""Agent 12: Meta Description + Social Post Copywriter.

Uses Haiku model for speed — short-form copy doesn't need Opus.
Sends only H1, first 300 words, and H2 list instead of full article.
"""

import re
from agents.base import BaseAgent
from state import PipelineState
from prompts.templates import SOCIAL_COPY_SYSTEM
from config import HAIKU_MODEL


class SocialCopyAgent(BaseAgent):
    name = "Social Copywriter"
    description = "Write meta description and social media posts"
    agent_number = 12
    model = HAIKU_MODEL
    timeout = 90  # 1.5 min — Haiku is fast but CLI startup adds overhead
    emoji = "\U0001f4f1"

    def run(self, state: PipelineState) -> PipelineState:
        article = (
            state.aeo_pass_article
            or state.seo_pass_article
            or state.fact_check_article
            or state.voice_pass_article
            or state.draft_article
        )

        # Extract just what we need: H1, first 300 words, H2 list
        h1, h2_list, first_300 = self._extract_article_summary(article)

        user_prompt = f"""## Article H1
{h1}

## H2s
{chr(10).join(f'- {h}' for h in h2_list) if h2_list else 'No H2s found'}

## First 300 Words
{first_300}

## Keyword: {state.target_keyword}
## Type: {state.content_type}
## Topic: {state.topic}
## URL: https://www.contractsafe.com/blog/{state.get_topic_slug()}

Write the meta description, LinkedIn post, and X/Twitter post."""

        self.progress("Generating meta description, LinkedIn post, and X/Twitter post...")
        response = self.call_llm(SOCIAL_COPY_SYSTEM, user_prompt)

        # Parse response sections
        self._parse_response(state, response)

        self.log("Meta description and social posts written")
        return state

    def _extract_article_summary(self, article: str) -> tuple[str, list[str], str]:
        """Extract H1, H2 list, and first 300 words from article."""
        # H1
        h1_match = re.search(r"^# (.+)$", article, re.MULTILINE)
        h1 = h1_match.group(1) if h1_match else "No H1 found"

        # H2s
        h2_list = re.findall(r"^## (.+)$", article, re.MULTILINE)

        # First 300 words (skip H1 line) — enough context for social copy
        lines = article.split("\n")
        body_lines = []
        word_count = 0
        for line in lines:
            if line.strip().startswith("# ") and not line.strip().startswith("## "):
                continue  # skip H1
            body_lines.append(line)
            word_count += len(line.split())
            if word_count >= 300:
                break
        first_300 = "\n".join(body_lines)

        return h1, h2_list, first_300

    def _parse_response(self, state: PipelineState, response: str):
        """Parse the social copy response into individual components.

        Flexible header detection — matches variations like:
        "META DESCRIPTION:", "Meta Description", "**Meta Description**",
        "## Meta Description", "1. Meta Description", etc.
        """
        lines = response.split("\n")
        current_section = None
        sections = {"meta": [], "linkedin": [], "twitter": []}

        # Flexible section header patterns (order matters — check most specific first)
        meta_pat = re.compile(r"meta\s*desc", re.IGNORECASE)
        linkedin_pat = re.compile(r"linkedin", re.IGNORECASE)
        twitter_pat = re.compile(r"x[/\s]twitter|twitter|x\s+post", re.IGNORECASE)
        skip_pat = re.compile(r"character\s*count|char\s*count|word\s*count|\d+\s*char", re.IGNORECASE)

        for line in lines:
            stripped = line.strip()
            # Strip markdown formatting for header detection
            clean = re.sub(r'^[#*\-\d.)\s]+', '', stripped).strip()
            clean_upper = clean.upper()

            # Detect section headers
            if meta_pat.search(clean):
                current_section = "meta"
                # Extract inline content after colon if present
                if ":" in stripped:
                    after_colon = stripped.split(":", 1)[1].strip()
                    if after_colon and not skip_pat.search(after_colon):
                        sections["meta"].append(after_colon)
                continue
            elif linkedin_pat.search(clean) and len(clean.split()) <= 5:
                current_section = "linkedin"
                continue
            elif twitter_pat.search(clean) and len(clean.split()) <= 5:
                current_section = "twitter"
                continue
            elif skip_pat.search(clean):
                continue

            if current_section and stripped:
                # Skip lines that are actually section headers for other sections
                if any(pat.search(stripped) for pat in [meta_pat, linkedin_pat, twitter_pat]):
                    continue
                sections[current_section].append(line)

        state.meta_description = "\n".join(sections["meta"]).strip()
        state.linkedin_post = "\n".join(sections["linkedin"]).strip()
        state.twitter_post = "\n".join(sections["twitter"]).strip()

        # If parsing failed, extract from raw response
        if not state.meta_description and not state.linkedin_post:
            state.meta_description = response[:160]
            state.linkedin_post = response
            state.twitter_post = response[:280]
