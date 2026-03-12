"""Agent 12: Meta Description + Social Post Copywriter."""

from agents.base import BaseAgent
from state import PipelineState
from prompts.templates import SOCIAL_COPY_SYSTEM


class SocialCopyAgent(BaseAgent):
    name = "Social Copywriter"
    description = "Write meta description and social media posts"
    agent_number = 12
    emoji = "\U0001f4f1"

    def run(self, state: PipelineState) -> PipelineState:
        article = (
            state.aeo_pass_article
            or state.seo_pass_article
            or state.fact_check_article
            or state.draft_article
        )

        user_prompt = f"""## Article
{article}

## Primary Keyword
{state.target_keyword}

## Content Type
{state.content_type}

## Topic
{state.topic}

## Article URL (placeholder)
https://www.contractsafe.com/blog/{state.get_topic_slug()}

Write the meta description, LinkedIn post, and X/Twitter post as specified in your instructions."""

        self.progress("Writing meta description and social posts...")
        response = self.call_llm(SOCIAL_COPY_SYSTEM, user_prompt)

        # Parse response sections
        self._parse_response(state, response)

        self.log("Meta description and social posts written")
        return state

    def _parse_response(self, state: PipelineState, response: str):
        """Parse the social copy response into individual components."""
        lines = response.split("\n")
        current_section = None
        sections = {"meta": [], "linkedin": [], "twitter": []}

        for line in lines:
            upper = line.strip().upper()
            if "META DESCRIPTION" in upper and ":" in upper:
                current_section = "meta"
                # Check if content is on the same line
                after_colon = line.split(":", 1)[1].strip() if ":" in line else ""
                if after_colon and "character" not in after_colon.lower():
                    sections["meta"].append(after_colon)
                continue
            elif "LINKEDIN POST" in upper:
                current_section = "linkedin"
                continue
            elif "X/TWITTER" in upper or "TWITTER POST" in upper or "X POST" in upper:
                current_section = "twitter"
                continue
            elif "CHARACTER COUNT" in upper or "CHAR COUNT" in upper:
                continue

            if current_section and line.strip():
                # Skip section headers that might appear
                if any(h in line.strip().upper() for h in ["LINKEDIN POST", "X/TWITTER", "META DESC"]):
                    continue
                sections[current_section].append(line)

        state.meta_description = "\n".join(sections["meta"]).strip()
        state.linkedin_post = "\n".join(sections["linkedin"]).strip()
        state.twitter_post = "\n".join(sections["twitter"]).strip()

        # If parsing failed, store raw response
        if not state.meta_description and not state.linkedin_post:
            state.meta_description = response[:160]
            state.linkedin_post = response
            state.twitter_post = response[:280]
