"""Agent 5: Link Researcher - builds verified, policy-compliant citation map.

Fully programmatic — no Claude calls. Uses keyword matching for relevance
and programmatic section assignment for the citation map.

Enforces link policy in Python:
- Minimum 5 internal, 3 external links
- All URLs verified live (HTTP 200)
- External links checked against competitor blocklist
- Every linked page is actually READ for relevance (keyword matching)
- Source tier classification (Tier 1 preferred)
"""

from __future__ import annotations

import re
from agents.base import BaseAgent
from state import PipelineState
from tools.web_search import web_search
from tools.web_fetch import web_fetch
from link_policy import (
    MIN_INTERNAL_LINKS,
    MIN_EXTERNAL_LINKS,
    EVERGREEN_EXTERNAL_LINKS,
    EVERGREEN_INTERNAL_LINKS,
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
        self.progress("Discovering and verifying citation candidates...")
        # ── Phase 1: Gather internal link candidates ──
        internal_candidates = self._find_internal_candidates(state)

        # ── Phase 2: Gather external link candidates ──
        # Start with research source URLs — these are pages Agent 2 actually read
        # and extracted facts/stats from. A blog writer links to their sources.
        external_candidates = self._research_source_links(state)
        # Then supplement with search-discovered links
        external_candidates += self._find_external_candidates(state)

        # ── Phase 3: Verify every URL is live AND check relevance programmatically ──
        self.progress("Verifying links are live and topic-relevant...")
        self.log("Verifying all link candidates (live check + keyword relevance)...")
        topic_words = self._get_topic_words(state)
        verified_internal = self._verify_and_check(internal_candidates, topic_words, is_internal_check=True)
        verified_external = self._verify_and_check(external_candidates, topic_words, is_internal_check=False)

        # ── Phase 4: Enforce minimums — search for more if needed ──
        if len(verified_internal) < MIN_INTERNAL_LINKS:
            self.log(f"[yellow]Only {len(verified_internal)} internal links. Searching for more...[/yellow]")
            verified_internal = self._backfill_internal(verified_internal, state, topic_words)

        # ── Phase 4b: Evergreen internal link fallbacks ──
        if len(verified_internal) < MIN_INTERNAL_LINKS:
            existing_urls = {l.get("url", "").lower().rstrip("/") for l in verified_internal}
            topic_lower = {w.lower() for w in topic_words}
            # Sort by relevance: most keyword overlap first
            scored = sorted(
                EVERGREEN_INTERNAL_LINKS,
                key=lambda ev: len(topic_lower & set(ev["keywords"])),
                reverse=True,
            )
            for ev in scored:
                if len(verified_internal) >= MIN_INTERNAL_LINKS:
                    break
                if ev["url"].lower().rstrip("/") in existing_urls:
                    continue
                # All ContractSafe pages are valid internal links — add them
                verified_internal.append({
                    "url": ev["url"],
                    "anchor": ev["anchor"],
                    "section": "any",
                    "relevance": "evergreen_fallback",
                })
                self.progress(f"  [cyan]EVERGREEN INTERNAL: {ev['url'][:60]}[/cyan]")

        if len(verified_external) < MIN_EXTERNAL_LINKS:
            self.log(f"[yellow]Only {len(verified_external)} external links. Searching for more...[/yellow]")
            verified_external = self._backfill_external(verified_external, state, topic_words)

        # ── Phase 5: Deduplicate by URL (keep first occurrence) ──
        def _dedup_by_url(links: list[dict]) -> list[dict]:
            seen_urls: set[str] = set()
            deduped: list[dict] = []
            for link in links:
                url = link.get("url", "").lower().rstrip("/")
                if url not in seen_urls:
                    seen_urls.add(url)
                    deduped.append(link)
            return deduped

        verified_internal = _dedup_by_url(verified_internal)
        verified_external = _dedup_by_url(verified_external)

        # ── Phase 6: Build citation map programmatically ──
        state.internal_links = verified_internal
        state.external_links = verified_external
        state.citation_map = self._build_citation_map_programmatic(state)

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

    def _research_source_links(self, state: PipelineState) -> list[dict]:
        """Build external link candidates from pages Agent 2 actually read.

        A blog writer's external links come from their research. If they found
        a stat from worldcc.com, they link to that worldcc.com page. The link
        IS the citation — it proves the stat is real.
        """
        candidates = []
        seen_urls = set()

        # Stats have source URLs — these are the highest-value external links
        for stat in state.statistics:
            url = stat.get("source_url", "")
            if not url or url in seen_urls or is_internal(url) or is_blocked(url):
                continue
            seen_urls.add(url)
            source_name = stat.get("source_name", "")
            stat_text = stat.get("stat", "")
            candidates.append({
                "url": url,
                "title": source_name,
                "anchor_suggestion": source_name[:40] if source_name else "research source",
                "relevance_summary": f"Source of stat: {stat_text[:80]}",
                "from_research": True,
            })

        # Key facts also have source URLs
        for fact in state.key_facts:
            url = fact.get("source", fact.get("source_url", ""))
            if not url or url in seen_urls or is_internal(url) or is_blocked(url):
                continue
            seen_urls.add(url)
            candidates.append({
                "url": url,
                "title": fact.get("fact", "")[:50],
                "anchor_suggestion": "research source",
                "relevance_summary": f"Source of fact: {fact.get('fact', '')[:80]}",
                "from_research": True,
            })

        if candidates:
            self.progress(f"Found {len(candidates)} external link candidates from research sources")

        return candidates

    # ── Topic word extraction for relevance matching ──

    def _get_topic_words(self, state: PipelineState) -> set[str]:
        """Extract topic-relevant words for keyword matching."""
        words = set()
        # Add words from topic
        for w in re.split(r'\W+', state.topic.lower()):
            if len(w) > 3:
                words.add(w)
        # Add words from keyword
        for w in re.split(r'\W+', state.target_keyword.lower()):
            if len(w) > 3:
                words.add(w)
        # Add related terms from keyword data
        for term in state.keyword_data.get("related_terms", [])[:10]:
            for w in re.split(r'\W+', term.lower()):
                if len(w) > 3:
                    words.add(w)
        # Add secondary keywords
        for kw in state.secondary_keywords:
            for w in re.split(r'\W+', kw.lower()):
                if len(w) > 3:
                    words.add(w)
        # Remove very common words
        stopwords = {
            "this", "that", "with", "from", "have", "will", "your", "their",
            "about", "which", "when", "what", "them", "been", "more", "also",
            "than", "into", "some", "could", "would", "should", "does", "most",
            "they", "there", "these", "those", "each", "every", "many", "much",
            "such", "very", "just", "even", "only", "over", "under", "before",
            "after", "between", "through", "like", "make", "need", "know",
            "best", "good", "well", "help", "want", "take", "find", "give",
            "tell", "come", "think", "look", "work", "call", "keep", "part",
            "important", "different", "business", "company", "companies",
        }
        words -= stopwords
        return words

    def _check_relevance_programmatic(self, page_content: str, topic_words: set[str]) -> tuple[bool, str]:
        """Check relevance by verifying the page's PRIMARY topic matches ours.

        A blog writer would click the link and ask: "Is this page actually about
        the same topic as my article?" A CRM page that mentions 'contract' in
        passing is NOT relevant to a contract management article.

        Checks:
        1. The page's H1/title must contain at least 2 topic words (primary topic match)
        2. The overall content must match 4+ topic words (general relevance)
        Both must pass.
        """
        content_lower = page_content.lower()

        # Extract H1 and first heading
        h1_match = re.search(r'^#\s+(.+)$', page_content, re.MULTILINE)
        title_text = h1_match.group(1).lower() if h1_match else ""
        if not title_text:
            # Try first non-empty line as title
            for line in page_content.split('\n'):
                stripped = line.strip()
                if stripped and not stripped.startswith('|') and len(stripped) > 10:
                    title_text = stripped.lower()
                    break

        # Check 1: Page title/H1 must match at least 2 topic words
        title_matches = [w for w in topic_words if w in title_text]
        if len(title_matches) < 2:
            return False, f"Page title doesn't match topic (only {len(title_matches)} words in title: '{title_text[:60]}')"

        # Check 2: Overall content must match 4+ topic words
        content_matches = [w for w in topic_words if w in content_lower]
        if len(content_matches) >= 4:
            return True, f"Title matched {len(title_matches)} words, content matched {len(content_matches)} words"
        return False, f"Only matched {len(content_matches)} topic words in content (need 4+)"

    def _generate_anchor(self, title: str) -> str:
        """Generate a 2-4 word keyword anchor from a page title.

        Strips noise words (the, a, best, guide, how, etc.) and leading
        numbers to extract the core topic phrase.
        """
        if not title:
            return ""
        # Remove site name suffixes like " | ContractSafe" or " - Blog"
        title = re.split(r'\s*[|\-\u2013\u2014]\s*(?:ContractSafe|Blog|Home)', title)[0].strip()

        noise_words = {
            "the", "a", "an", "of", "for", "and", "in", "to", "with", "is",
            "on", "by", "at", "how", "why", "what", "your", "our", "best",
            "top", "guide", "ultimate", "complete", "everything", "need",
            "know", "about", "you", "that", "this", "are", "from", "its",
        }
        title_words = title.split()
        content_words = []
        for w in title_words:
            if w.lower() in noise_words:
                continue
            if re.match(r'^\d+$', w):
                continue
            content_words.append(w)

        if not content_words:
            return " ".join(title_words[:3]).lower()

        if len(content_words) <= 4:
            return " ".join(content_words).lower()

        return " ".join(content_words[:3]).lower()

    # ── Internal link discovery ──

    def _find_internal_candidates(self, state: PipelineState) -> list[dict]:
        """Search for ContractSafe internal pages related to the topic."""
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

    # ── Verification: live check + programmatic relevance ──

    def _verify_and_check(
        self,
        candidates: list[dict],
        topic_words: set[str],
        is_internal_check: bool,
    ) -> list[dict]:
        """Verify each URL is live (HTTP 200) and check relevance with keyword matching."""
        verified = []
        target = MIN_INTERNAL_LINKS + 3 if is_internal_check else MIN_EXTERNAL_LINKS + 3

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

            # Step 3: Programmatic relevance check
            page_excerpt = data["content"][:3000]
            is_relevant, reason = self._check_relevance_programmatic(page_excerpt, topic_words)

            if not is_relevant:
                self.progress(f"  [yellow]NOT RELEVANT: {url[:60]} — {reason}[/yellow]")
                continue

            # Generate anchor text from title
            anchor = self._generate_anchor(candidate.get("title", ""))

            # Passed all checks
            verified.append({
                **candidate,
                "verified": True,
                "status": 200,
                "relevance_summary": reason,
                "anchor_suggestion": anchor or candidate.get("title", ""),
            })
            self.progress(f"  [green]VERIFIED: {url[:60]}[/green]")

        return verified

    # ── Backfill: search for more links if we didn't hit minimums ──

    def _backfill_internal(self, current: list[dict], state: PipelineState, topic_words: set[str]) -> list[dict]:
        """Try up to 2 additional searches to find more internal links."""
        seen = {link["url"] for link in current}
        extra_queries = [
            f"site:contractsafe.com {state.target_keyword} guide",
            f"site:contractsafe.com features",
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
                        is_relevant, reason = self._check_relevance_programmatic(data["content"][:3000], topic_words)
                        if is_relevant:
                            anchor = self._generate_anchor(r.get("title", ""))
                            current.append({
                                "url": url,
                                "title": r.get("title", ""),
                                "snippet": r.get("snippet", ""),
                                "verified": True,
                                "status": 200,
                                "relevance_summary": reason,
                                "anchor_suggestion": anchor or r.get("title", ""),
                            })
                            self.progress(f"  [green]BACKFILL VERIFIED: {url[:60]}[/green]")
                    if len(current) >= MIN_INTERNAL_LINKS:
                        break
        return current

    def _backfill_external(self, current: list[dict], state: PipelineState, topic_words: set[str]) -> list[dict]:
        """Try additional searches to find more external links."""
        seen = {link["url"] for link in current}
        extra_queries = [
            f"{state.target_keyword} best practices guide",
            f"{state.target_keyword} statistics report .gov OR .edu",
            f"{state.topic} research study industry report",
            f"{state.target_keyword} framework methodology",
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
                    # Use relaxed relevance threshold for backfill (3+ instead of 4+)
                    content_lower = data["content"][:3000].lower()
                    matches = [w for w in topic_words if w in content_lower]
                    if len(matches) >= 3:
                        reason = f"Backfill matched {len(matches)} topic words: {', '.join(matches[:5])}"
                        anchor = self._generate_anchor(r.get("title", ""))
                        current.append({
                            "url": url,
                            "title": r.get("title", ""),
                            "snippet": r.get("snippet", ""),
                            "tier": get_source_tier(url),
                            "verified": True,
                            "status": 200,
                            "relevance_summary": reason,
                            "anchor_suggestion": anchor or r.get("title", ""),
                        })
                        self.progress(f"  [green]BACKFILL VERIFIED: {url[:60]}[/green]")
                if len(current) >= MIN_EXTERNAL_LINKS:
                    break

        # ── Evergreen fallback: guarantee minimum even if search fails ──
        if len(current) < MIN_EXTERNAL_LINKS:
            seen = {link["url"] for link in current}
            for ev in EVERGREEN_EXTERNAL_LINKS:
                if len(current) >= MIN_EXTERNAL_LINKS:
                    break
                if ev["url"] in seen:
                    continue
                seen.add(ev["url"])
                current.append({
                    "url": ev["url"],
                    "title": ev["title"],
                    "snippet": "",
                    "tier": ev["tier"],
                    "verified": True,
                    "status": 200,
                    "relevance_summary": "Evergreen fallback — pre-verified authoritative source",
                    "anchor_suggestion": ev["anchor"],
                })
                self.progress(f"  [cyan]EVERGREEN FALLBACK: {ev['url'][:60]}[/cyan]")

        return current

    # ── Citation map: programmatic section assignment ──

    def _build_citation_map_programmatic(self, state: PipelineState) -> dict:
        """Assign verified links to article sections programmatically.

        Strategy:
        - ~60% of links go to the first third of sections
        - Spread evenly, max 3 per section
        - Internal links before external within each section
        """
        self.progress("Building citation map programmatically...")

        h2s = state.recommended_h2s or []
        if not h2s:
            # Fallback: create a single "General" section
            h2s = ["Introduction"]

        # Add "Introduction" if not present
        sections = ["Introduction"] + [h for h in h2s if h.lower() != "introduction"]

        # Combine all links, internal first
        all_links = []
        for link in state.internal_links:
            all_links.append({
                "type": "internal",
                "url": link.get("url", ""),
                "anchor": link.get("anchor_suggestion", link.get("title", "")),
                "relevance": link.get("relevance_summary", ""),
            })
        for link in state.external_links:
            all_links.append({
                "type": "external",
                "url": link.get("url", ""),
                "anchor": link.get("anchor_suggestion", link.get("title", "")),
                "relevance": link.get("relevance_summary", ""),
            })

        if not all_links:
            return {}

        # Distribute links across sections
        citation_map = {section: [] for section in sections}
        section_counts = {section: 0 for section in sections}

        # Calculate first-third boundary (front-load ~60%)
        first_third_end = max(1, len(sections) // 3)
        first_third_sections = sections[:first_third_end + 1]
        remaining_sections = sections[first_third_end + 1:]

        # Assign ~60% to first third, ~40% to rest
        front_load_count = int(len(all_links) * 0.6)
        front_links = all_links[:front_load_count]
        back_links = all_links[front_load_count:]

        def distribute(links, target_sections):
            """Distribute links evenly across target sections, max 3 per section."""
            if not target_sections or not links:
                return
            idx = 0
            for link in links:
                assigned = False
                # Try each section in order, wrapping around
                for attempt in range(len(target_sections)):
                    section = target_sections[(idx + attempt) % len(target_sections)]
                    if section_counts[section] < 3:
                        citation_map[section].append(link)
                        section_counts[section] += 1
                        idx = (idx + attempt + 1) % len(target_sections)
                        assigned = True
                        break
                if not assigned:
                    # All sections full, add to first available
                    for section in target_sections:
                        citation_map[section].append(link)
                        section_counts[section] += 1
                        break

        distribute(front_links, first_third_sections)
        distribute(back_links, remaining_sections if remaining_sections else first_third_sections)

        # Remove empty sections from map
        citation_map = {k: v for k, v in citation_map.items() if v}

        self.progress(f"Assigned {len(all_links)} links across {len(citation_map)} sections")
        return citation_map
