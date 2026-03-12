"""Agent 1: Product Knowledge Base - pulls relevant ContractSafe product info."""

from agents.base import BaseAgent
from state import PipelineState
from tools.web_search import web_search
from tools.web_fetch import web_fetch
from prompts.templates import PRODUCT_KNOWLEDGE_SYSTEM
from config import KNOWLEDGE_DIR


class ProductKnowledgeAgent(BaseAgent):
    name = "Product Knowledge"
    description = "Pull relevant ContractSafe product information for the topic"
    agent_number = 1
    emoji = "\U0001f50d"

    def run(self, state: PipelineState) -> PipelineState:
        self.log("Loading static product knowledge...")
        product_info = (KNOWLEDGE_DIR / "product_info.md").read_text()

        # Fetch live data from contractsafe.com
        self.progress("Fetching contractsafe.com/features...")
        features_data = web_fetch("https://www.contractsafe.com/features")

        self.progress("Fetching AI features page...")
        ai_data = web_fetch("https://www.contractsafe.com/features/ai-contract-management")

        self.progress("Fetching blog index...")
        blog_data = web_fetch("https://www.contractsafe.com/blog")

        self.progress("Fetching pricing page...")
        pricing_data = web_fetch("https://www.contractsafe.com/pricing")

        # Build context from fetched pages
        live_data_parts = []
        for label, data in [
            ("Features Page", features_data),
            ("AI Features Page", ai_data),
            ("Blog Index", blog_data),
            ("Pricing Page", pricing_data),
        ]:
            if data["content"]:
                live_data_parts.append(f"### {label}\n{data['content'][:8000]}")
            elif data["error"]:
                live_data_parts.append(f"### {label}\n[Could not fetch: {data['error']}]")

        live_data = "\n\n".join(live_data_parts)

        user_prompt = f"""## Static Product Knowledge Base
{product_info}

## Live Data from contractsafe.com
{live_data}

## Article Topic
{state.topic}

## Content Type
{state.content_type}

Given this topic and content type, identify:
1. Which ContractSafe features, benefits, and differentiators are most relevant?
2. What product claims can we make?
3. What links to product pages should we reference?
4. How does ContractSafe solve the problems discussed in this topic?"""

        self.progress("Analyzing product relevance with Claude...")
        state.product_knowledge = self.call_llm(PRODUCT_KNOWLEDGE_SYSTEM, user_prompt)
        return state
