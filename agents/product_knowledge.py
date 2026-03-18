"""Agent 1: Product Knowledge Base - fully programmatic.

No LLM calls. Matches product features and pages to the article topic
using keyword overlap. Outputs a structured summary of relevant features,
claims, and page URLs for the brief consolidator.
"""

import re
from agents.base import BaseAgent
from state import PipelineState
from tools.web_search import web_search
from tools.web_fetch import web_fetch
from config import KNOWLEDGE_DIR


class ProductKnowledgeAgent(BaseAgent):
    name = "Product Knowledge"
    description = "Pull relevant ContractSafe product information for the topic"
    agent_number = 1
    emoji = "\U0001f50d"

    def run(self, state: PipelineState) -> PipelineState:
        self.progress("Matching ContractSafe features to topic...")
        self.log("Loading static product knowledge...")
        product_info = (KNOWLEDGE_DIR / "product_info.md").read_text()

        # ── Build topic word set for relevance matching ──
        topic_words = self._get_topic_words(state)
        self.progress(f"Topic words: {', '.join(sorted(topic_words)[:10])}")

        # ── Extract relevant sections from product_info.md ──
        relevant_sections = self._extract_relevant_sections(product_info, topic_words)

        # ── Search contractsafe.com for topic-relevant pages ──
        self.progress(f"Searching contractsafe.com for pages about: {state.topic}")
        topic_results = web_search(
            f"site:contractsafe.com {state.topic}", num_results=5
        )

        keyword = state.target_keyword or state.topic
        if keyword.lower() != state.topic.lower():
            self.progress(f"Searching contractsafe.com for: {keyword}")
            kw_results = web_search(
                f"site:contractsafe.com {keyword}", num_results=5
            )
        else:
            kw_results = []

        # Deduplicate
        seen_urls = set()
        all_results = []
        for r in topic_results + kw_results:
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(r)

        # ── Fetch and extract relevant content from top pages ──
        relevant_pages = []
        for r in all_results[:5]:
            url = r.get("url", "")
            title = r.get("title", "Unknown")
            self.progress(f"Fetching: {url[:80]}...")
            data = web_fetch(url)
            if data["content"]:
                # Extract sentences relevant to the topic
                relevant_sentences = self._extract_relevant_sentences(
                    data["content"][:6000], topic_words
                )
                if relevant_sentences:
                    relevant_pages.append({
                        "url": url,
                        "title": title,
                        "relevant_content": relevant_sentences,
                    })

        # ── Build structured product knowledge summary ──
        output_parts = []

        if relevant_sections:
            output_parts.append("## Relevant Product Features & Info")
            for section in relevant_sections:
                output_parts.append(section)

        if relevant_pages:
            output_parts.append("\n## Relevant ContractSafe Pages")
            for page in relevant_pages:
                output_parts.append(f"\n### [{page['title']}]({page['url']})")
                output_parts.append(page["relevant_content"])

        # Always include key differentiators and claims
        output_parts.append("\n## Key Differentiators")
        output_parts.append("- Unlimited users on all plans (no per-seat pricing)")
        output_parts.append("- Fast setup: most teams live in under 30 minutes")
        output_parts.append("- Transparent, affordable pricing")
        output_parts.append("- Top-rated for ease of use")
        output_parts.append("- Real human support included in every plan")

        state.product_knowledge = "\n".join(output_parts)
        self.log(
            f"Product knowledge: {len(relevant_sections)} relevant sections, "
            f"{len(relevant_pages)} relevant pages"
        )
        return state

    def _get_topic_words(self, state: PipelineState) -> set[str]:
        """Extract meaningful words from topic and keyword."""
        stopwords = {
            "a", "an", "the", "and", "or", "but", "for", "of", "to", "in",
            "on", "at", "by", "is", "are", "was", "were", "be", "been",
            "your", "our", "their", "this", "that", "with", "how", "what",
            "why", "when", "best", "top", "guide", "tips", "practices",
        }
        words = set()
        for text in [state.topic, state.target_keyword]:
            for w in re.findall(r"[a-z]+", text.lower()):
                if len(w) > 3 and w not in stopwords:
                    words.add(w)
        return words

    def _extract_relevant_sections(self, product_info: str, topic_words: set[str]) -> list[str]:
        """Extract sections from product_info.md that match topic keywords."""
        # Split by ## headings
        sections = re.split(r"\n(?=## )", product_info)
        relevant = []

        for section in sections:
            section_lower = section.lower()
            matches = sum(1 for w in topic_words if w in section_lower)
            if matches >= 2:  # At least 2 topic words match
                # Trim to key content (skip very long sections)
                lines = section.strip().split("\n")
                trimmed = "\n".join(lines[:15])
                relevant.append(trimmed)

        return relevant

    def _extract_relevant_sentences(self, content: str, topic_words: set[str]) -> str:
        """Extract sentences from page content that are relevant to the topic."""
        # Split into sentences
        sentences = re.split(r"(?<=[.!?])\s+", content)
        relevant = []

        for sentence in sentences:
            sentence_lower = sentence.lower()
            matches = sum(1 for w in topic_words if w in sentence_lower)
            if matches >= 2 and len(sentence.split()) >= 5:
                # Clean up the sentence
                clean = sentence.strip()
                if len(clean) > 20 and not clean.startswith("#"):
                    relevant.append(f"- {clean[:200]}")

            if len(relevant) >= 5:  # Cap at 5 relevant sentences per page
                break

        return "\n".join(relevant) if relevant else ""
