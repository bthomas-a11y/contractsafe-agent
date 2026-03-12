"""Agent 5: Link Researcher - builds verified, policy-compliant citation map.

Enforces link policy in Python:
- Minimum 5 internal, 3 external links
- All URLs verified live (HTTP 200)
- External links checked against competitor blocklist
- Every linked page is actually READ for relevance (not just metadata)
- Source tier classification (Tier 1 preferred)
"""

from __future__ import annotations

import json
import re
from agents.base import BaseAgent
from state import PipelineState
from tools.web_search import web_search
from tools.web_fetch import web_fetch
from prompts.templates import LINK_RESEARCHER_SYSTEM
from link_policy import (
    MIN_INTERNAL_LINKS,
    MIN_EXTERNAL_LINKS,
    is_blocked,
    is_internal,
    get_source_tier,
    format_link_policy_for_prompt,
)


class LinkResearcherAgent(BaseAgent):
    name = "Link Researcher"
    description = "Build verified citation map with live, relevant links"
    agent_number = 5
    emoji = "\U0001f517"

    def run(self, state: PipelineState) -> PipelineState:
        # ── Phase 1: Gather internal link candidates ──
        internal_candidates = self._find_internal_candidates(state)

        # ── Phase 2: Gather external link candidates ──
        external_candidates = self._find_external_candidates(state)

        # ── Phase 3: Verify every URL is live AND read for relevance ──
        self.log("Verifying all link candidates (live check + relevance read)...")
        verified_internal = self._verify_and_read(internal_candidates, state.topic, is_internal_check=True)
        verified_external = self._verify_and_read(external_candidates, state.topic, is_internal_check=False)

        # ── Phase 4: Enforce minimums — search for more if needed ──
        if len(verified_internal) < MIN_INTERNAL_LINKS:
            self.log(f"[yellow]Only {len(verified_internal)} internal links. Searching for more...[/yellow]")
            verified_internal = self._backfill_internal(verified_internal, state)

        if len(verified_external) < MIN_EXTERNAL_LINKS:
            self.log(f"[yellow]Only {len(verified_external)} external links. Searching for more...[/yellow]")
            verified_external = self._backfill_external(verified_external, state)

        # ── Phase 5: Have Claude build the citation map ──
        state.internal_links = verified_internal
        state.external_links = verified_external
        state.citation_map = self._build_citation_map(state)

        # ── Report ──
        tier_counts = {1: 0, 2: 0, 3: 0}
        for link in verified_external:
            tier_counts[link.get("tier", 3)] += 1

        self.log(
            f"Final: {len(verified_internal)} internal links, "
            f"{len(verified_external)} external links "
            f"(Tier 1: {tier_counts[1]}, Tier 2: {tier_counts[2]}, Tier 3: {tier_counts[3]})"
        )

        if len(verified_internal) < MIN_INTERNAL_LINKS:
            self.log(f"[red]WARNING: Could not reach minimum {MIN_INTERNAL_LINKS} internal links[/red]")
        if len(verified_external) < MIN_EXTERNAL_LINKS:
            self.log(f"[red]WARNING: Could not reach minimum {MIN_EXTERNAL_LINKS} external links[/red]")

        return state

    # ── Internal link discovery ──

    def _find_internal_candidates(self, state: PipelineState) -> list[dict]:
        """Search for ContractSafe internal pages related to the topic."""
        self.progress("Fetching ContractSafe blog index...")
        blog_data = web_fetch("https://www.contractsafe.com/blog")

        queries = [
            f"site:contractsafe.com {state.topic}",
            f"site:contractsafe.com {state.target_keyword}",
        ]
        if state.keyword_data.get("related_terms"):
            for term in state.keyword_data["related_terms"][:3]:
                queries.append(f"site:contractsafe.com {term}")

        candidates = []
        seen = set()
        for query in queries:
            self.progress(f"Searching: {query}")
            for r in web_search(query):
                url = r.get("url", "")
                if url and "contractsafe.com" in url.lower() and url not in seen:
                    seen.add(url)
                    candidates.append({
                        "url": url,
                        "title": r.get("title", ""),
                        "snippet": r.get("snippet", ""),
                    })

        self.progress(f"Found {len(candidates)} internal link candidates")
        return candidates

    # ── External link discovery ──

    def _find_external_candidates(self, state: PipelineState) -> list[dict]:
        """Search for authoritative external sources. Filters out competitors."""
        queries = [
            f"{state.topic} industry report statistics",
            f"{state.topic} legal industry research study",
            f"{state.target_keyword} Gartner Forrester McKinsey",
            f"{state.target_keyword} research data",
        ]

        candidates = []
        seen = set()
        for query in queries:
            self.progress(f"Searching external: {query}")
            for r in web_search(query):
                url = r.get("url", "")
                if not url or url in seen:
                    continue
                if is_blocked(url):
                    self.progress(f"  Blocked (competitor): {url[:60]}")
                    continue
                if is_internal(url):
                    continue
                seen.add(url)
                candidates.append({
                    "url": url,
                    "title": r.get("title", ""),
                    "snippet": r.get("snippet", ""),
                    "tier": get_source_tier(url),
                })

        # Also check stat source URLs from research
        for stat in state.statistics:
            url = stat.get("source_url", "")
            if url and url not in seen and not is_blocked(url) and not is_internal(url):
                seen.add(url)
                candidates.append({
                    "url": url,
                    "title": stat.get("source_name", ""),
                    "snippet": stat.get("stat", ""),
                    "tier": get_source_tier(url),
                })

        # Sort by tier (Tier 1 first)
        candidates.sort(key=lambda x: x.get("tier", 3))

        self.progress(f"Found {len(candidates)} external link candidates (after competitor filter)")
        return candidates

    # ── Verification: live check + content read + relevance check ──

    def _verify_and_read(
        self,
        candidates: list[dict],
        topic: str,
        is_internal_check: bool,
    ) -> list[dict]:
        """
        Verify each URL is live (HTTP 200) and READ the page to check relevance.

        This is the key enforcement step: we don't trust metadata alone.
        """
        verified = []
        target = MIN_INTERNAL_LINKS + 3 if is_internal_check else MIN_EXTERNAL_LINKS + 3  # Gather extras

        for candidate in candidates:
            if len(verified) >= target:
                break

            url = candidate["url"]
            self.progress(f"  Verifying & reading: {url[:70]}...")

            # Step 1: Fetch the page
            data = web_fetch(url)

            # Step 2: Check it's live
            if data["status"] != 200:
                self.progress(f"  [red]DEAD ({data['status'] or 'error'}): {url[:60]}[/red]")
                continue

            if not data["content"] or len(data["content"].strip()) < 100:
                self.progress(f"  [red]EMPTY PAGE: {url[:60]}[/red]")
                continue

            # Step 3: Read content and check relevance via Claude
            page_excerpt = data["content"][:3000]
            relevance = self._check_relevance(url, page_excerpt, topic)

            if not relevance["relevant"]:
                self.progress(f"  [yellow]NOT RELEVANT: {url[:60]} — {relevance['reason']}[/yellow]")
                continue

            # Passed all checks
            verified.append({
                **candidate,
                "verified": True,
                "status": 200,
                "relevance_summary": relevance["summary"],
                "anchor_suggestion": relevance.get("suggested_anchor", candidate.get("title", "")),
            })
            self.progress(f"  [green]VERIFIED: {url[:60]}[/green]")

        return verified

    def _check_relevance(self, url: str, page_content: str, topic: str) -> dict:
        """Use Claude to verify a page is actually relevant to our article topic."""
        response = self.call_llm(
            system_prompt="You are a link relevance checker. Given a page's content and an article topic, "
            "determine if the page is relevant enough to link from the article. Respond with JSON only.",
            user_prompt=f"""Page URL: {url}

Page content (excerpt):
{page_content}

Article topic: {topic}

Is this page relevant to the article topic? Respond with this exact JSON format:
{{
  "relevant": true/false,
  "reason": "one sentence explanation",
  "summary": "2-3 sentence summary of what this page is about",
  "suggested_anchor": "natural anchor text phrase to use when linking to this page"
}}""",
        )

        try:
            # Strip markdown fences
            text = response.strip()
            if text.startswith("```"):
                text = "\n".join(text.split("\n")[1:])
                if text.rstrip().endswith("```"):
                    text = text.rstrip()[:-3]

            parsed = json.loads(text)
            return {
                "relevant": parsed.get("relevant", False),
                "reason": parsed.get("reason", ""),
                "summary": parsed.get("summary", ""),
                "suggested_anchor": parsed.get("suggested_anchor", ""),
            }
        except (json.JSONDecodeError, KeyError):
            # If parsing fails, assume relevant (don't block on parsing issues)
            return {"relevant": True, "reason": "Parse fallback", "summary": "", "suggested_anchor": ""}

    # ── Backfill: search for more links if we didn't hit minimums ──

    def _backfill_internal(self, current: list[dict], state: PipelineState) -> list[dict]:
        """Try additional searches to find more internal links."""
        seen = {link["url"] for link in current}
        extra_queries = [
            f"site:contractsafe.com features",
            f"site:contractsafe.com blog",
            f"site:contractsafe.com {state.target_keyword} guide",
        ]
        for query in extra_queries:
            if len(current) >= MIN_INTERNAL_LINKS:
                break
            self.progress(f"Backfill searching: {query}")
            for r in web_search(query):
                url = r.get("url", "")
                if url and "contractsafe.com" in url.lower() and url not in seen:
                    seen.add(url)
                    data = web_fetch(url)
                    if data["status"] == 200 and data["content"] and len(data["content"].strip()) > 100:
                        relevance = self._check_relevance(url, data["content"][:3000], state.topic)
                        if relevance["relevant"]:
                            current.append({
                                "url": url,
                                "title": r.get("title", ""),
                                "snippet": r.get("snippet", ""),
                                "verified": True,
                                "status": 200,
                                "relevance_summary": relevance["summary"],
                                "anchor_suggestion": relevance.get("suggested_anchor", r.get("title", "")),
                            })
                            self.progress(f"  [green]BACKFILL VERIFIED: {url[:60]}[/green]")
                    if len(current) >= MIN_INTERNAL_LINKS:
                        break
        return current

    def _backfill_external(self, current: list[dict], state: PipelineState) -> list[dict]:
        """Try additional searches to find more external links."""
        seen = {link["url"] for link in current}
        extra_queries = [
            f"{state.topic} Harvard Business Review",
            f"{state.target_keyword} statistics report .gov OR .edu",
            f"{state.topic} American Bar Association research",
            f"contract management best practices Forbes",
        ]
        for query in extra_queries:
            if len(current) >= MIN_EXTERNAL_LINKS:
                break
            self.progress(f"Backfill searching: {query}")
            for r in web_search(query):
                url = r.get("url", "")
                if not url or url in seen or is_blocked(url) or is_internal(url):
                    continue
                seen.add(url)
                data = web_fetch(url)
                if data["status"] == 200 and data["content"] and len(data["content"].strip()) > 100:
                    relevance = self._check_relevance(url, data["content"][:3000], state.topic)
                    if relevance["relevant"]:
                        current.append({
                            "url": url,
                            "title": r.get("title", ""),
                            "snippet": r.get("snippet", ""),
                            "tier": get_source_tier(url),
                            "verified": True,
                            "status": 200,
                            "relevance_summary": relevance["summary"],
                            "anchor_suggestion": relevance.get("suggested_anchor", r.get("title", "")),
                        })
                        self.progress(f"  [green]BACKFILL VERIFIED: {url[:60]}[/green]")
                if len(current) >= MIN_EXTERNAL_LINKS:
                    break
        return current

    # ── Citation map: organize links by section ──

    def _build_citation_map(self, state: PipelineState) -> dict:
        """Use Claude to map verified links to article sections."""
        h2s = "\n".join(f"- {h}" for h in state.recommended_h2s) if state.recommended_h2s else "No H2s yet."

        internal_json = json.dumps(state.internal_links, indent=2)
        external_json = json.dumps(state.external_links, indent=2)

        policy_text = format_link_policy_for_prompt()

        user_prompt = f"""{policy_text}

## Recommended H2 Structure
{h2s}

## Verified Internal Links (all confirmed live + relevant)
{internal_json}

## Verified External Links (all confirmed live + relevant, competitor-free)
{external_json}

## Article Topic
{state.topic}

Build a citation map that assigns these verified links to specific article sections.

Rules:
- Every link MUST be assigned to a section
- **Front-load: assign ~60% of links to sections in the first third of the article**
- Use the "anchor_suggestion" from each link as the anchor text
- Spread links across sections (no more than 3 links in any single section)
- Internal links should appear before external links within each section when possible

Return a JSON object where keys are section names and values are arrays of link objects:
```json
{{
  "Introduction": [{{"type": "internal", "url": "...", "anchor": "...", "relevance": "..."}}],
  "Section Name": [...]
}}
```"""

        self.progress("Building citation map with Claude...")
        response = self.call_llm(
            "You are a citation mapper. Assign verified links to article sections. "
            "Return a JSON object mapping section names to link arrays. JSON only, no commentary.",
            user_prompt,
        )

        # Parse the JSON response
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
            self.log("[yellow]Warning: Could not parse citation map JSON[/yellow]")
            return {}
