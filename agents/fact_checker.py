"""Agent 9: Fact Check Pass - verifies claims against research data.

Fully programmatic — no Claude call, no web fetching. Compares article
claims/statistics against state.statistics and state.key_facts using
fuzzy string matching. Removes unverified stats.
"""

from __future__ import annotations

import re
from agents.base import BaseAgent
from state import PipelineState


class FactCheckerAgent(BaseAgent):
    name = "Fact Checker"
    description = "Verify factual claims and statistics against research data"
    agent_number = 9
    emoji = "\u2705"

    def run(self, state: PipelineState) -> PipelineState:
        article = state.voice_pass_article or state.draft_article
        self.progress("Cross-referencing claims against research data...")

        # Extract claims (lines with numbers, percentages, or attribution)
        self.progress("Extracting statistics and attribution claims...")
        claims = self._extract_claims(article)
        self.progress(f"Found {len(claims)} factual claims to verify")

        # Build a reference corpus from research data
        reference_texts = self._build_reference_corpus(state)

        # Guard: if reference corpus is empty, skip verification entirely
        # (no data to verify against — removing content would be destructive)
        if not reference_texts:
            self.log("[yellow]Warning: No reference data available. Skipping fact verification.[/yellow]")
            state.fact_check_results = [{"claim": "N/A", "status": "SKIPPED", "note": "No reference data"}]
            state.fact_check_article = article
            return state

        # Verify each claim
        results = []
        unverified_claims = []
        for claim in claims:
            status, note = self._verify_claim(claim, reference_texts)
            results.append({
                "claim": claim[:200],
                "status": status,
                "note": note,
            })
            if status == "UNVERIFIED":
                unverified_claims.append(claim)

        # Apply removals: delete sentences with unverified stats
        # Circuit breaker: max 3 removals to prevent mass content destruction
        MAX_REMOVALS = 3
        revised_article = article
        removed_count = 0
        for claim in unverified_claims:
            if removed_count >= MAX_REMOVALS:
                self.log(
                    f"[yellow]Circuit breaker: stopping after {MAX_REMOVALS} removals. "
                    f"{len(unverified_claims) - removed_count} unverified claims left in article.[/yellow]"
                )
                break
            revised_article, did_remove = self._remove_unverified_claim(revised_article, claim)
            if did_remove:
                removed_count += 1

        state.fact_check_results = results
        state.fact_check_article = revised_article

        verified = sum(1 for r in results if r["status"] == "VERIFIED")
        self.log(
            f"Checked {len(results)} claims: {verified} verified, "
            f"{len(unverified_claims)} unverified, {removed_count} removed from article"
        )
        return state

    def _extract_claims(self, article: str) -> list[str]:
        """Extract factual claims from the article text.

        Strip markdown links before extraction so claims can be matched
        back to the article text during removal. Without this, a claim
        containing '[text](url)' can't be found for removal.
        """
        claims = []
        for line in article.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Strip markdown links to get plain text for matching
            plain = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', line)
            has_stat = bool(re.search(
                r'\d+%|\$[\d,]+|\d+\s*(billion|million|percent|times)',
                plain, re.IGNORECASE
            ))
            has_attribution = any(
                phrase in plain.lower()
                for phrase in ["according to", "study found", "report found",
                               "research shows", "data shows", "survey"]
            )
            if has_stat or has_attribution:
                claims.append(plain[:300])
        return claims[:20]

    def _build_reference_corpus(self, state: PipelineState) -> list[str]:
        """Build a list of reference strings from research data for matching."""
        refs = []

        # Add statistics
        for stat in state.statistics:
            if isinstance(stat, dict):
                refs.append(stat.get("stat", "").lower())
                refs.append(stat.get("source_name", "").lower())
                refs.append(stat.get("text", "").lower())
            else:
                refs.append(str(stat).lower())

        # Add key facts
        for fact in state.key_facts:
            if isinstance(fact, dict):
                refs.append(fact.get("fact", "").lower())
                refs.append(fact.get("text", "").lower())
            else:
                refs.append(str(fact).lower())

        # DO NOT add raw subject_research text — it contains unvetted search
        # snippets that may include unverified stats. Only state.statistics and
        # state.key_facts (which were extracted from pages Agent 2 actually read
        # and filtered for topic relevance) should be trusted.

        # Filter empty strings
        return [r for r in refs if r.strip()]

    def _normalize_number_text(self, text: str) -> str:
        """Normalize number representations: '60 percent' -> '60%', etc."""
        text = re.sub(r'(\d+)\s*percent', r'\1%', text, flags=re.IGNORECASE)
        text = re.sub(r'(\d+)\s*per\s*cent', r'\1%', text, flags=re.IGNORECASE)
        return text

    def _verify_claim(self, claim: str, reference_texts: list[str]) -> tuple[str, str]:
        """Verify a claim against reference corpus using fuzzy matching.

        Returns (status, note) where status is VERIFIED or UNVERIFIED.

        Rules:
        - If the claim has numbers/stats, at least one number MUST match in the reference.
          Word overlap alone cannot verify a stat (prevents hallucinated number acceptance).
        - Source name match is sufficient for attribution claims.
        """
        claim_lower = self._normalize_number_text(claim.lower())

        # Extract the key numbers/percentages from the claim
        numbers = re.findall(r'(\d+(?:\.\d+)?%?)', claim_lower)
        has_stats = bool(numbers)

        # Extract key proper nouns / source names (capitalized words 4+ chars)
        # Filter out common English words that happen to be capitalized
        _not_sources = {
            "That", "This", "Here", "When", "Then", "Most", "Some", "They",
            "There", "These", "Those", "Where", "What", "Which", "Every",
            "Many", "Much", "Such", "Very", "Just", "Even", "Only", "Over",
            "Under", "Before", "After", "Between", "Through", "About",
            "Your", "Their", "Could", "Would", "Should", "Does", "Have",
            "Been", "Were", "With", "From", "Into", "Also", "More",
            "Bigger", "Larger", "Small", "Smaller", "Companies", "Research",
            "According", "Meanwhile", "However", "Organizations", "Because",
            "Nonprofits", "Nonprofit", "Contracts", "Software", "Market",
            "Management", "Systems", "Platforms", "Tools", "Teams",
            "Mismanaged", "Mismanagement", "Revenue", "Annual", "Average",
        }
        raw_sources = re.findall(r'[A-Z][a-z]{3,}(?:\s+[A-Z][a-z]{3,})*', claim)
        sources = [s for s in raw_sources if s not in _not_sources and len(s.split()) >= 2]

        # Stopwords to exclude from overlap calculations
        stopwords = {
            "this", "that", "with", "from", "have", "will", "your", "their",
            "about", "which", "when", "what", "them", "been", "more", "also",
            "than", "into", "some", "could", "would", "should", "does", "most",
            "they", "there", "these", "those", "each", "every", "many", "much",
            "such", "very", "just", "even", "only", "over", "under", "before",
            "after", "between", "through",
        }

        for ref in reference_texts:
            if not ref:
                continue

            ref_normalized = self._normalize_number_text(ref)

            # Check if key numbers from the claim appear in the reference
            numbers_matched = sum(1 for n in numbers if n in ref_normalized)

            # Check word overlap (excluding stopwords)
            claim_words = set(re.split(r'\W+', claim_lower)) - stopwords
            ref_words = set(re.split(r'\W+', ref_normalized)) - stopwords
            claim_words = {w for w in claim_words if len(w) > 3}
            ref_words = {w for w in ref_words if len(w) > 3}

            if not claim_words:
                continue

            overlap = len(claim_words & ref_words)
            overlap_ratio = overlap / len(claim_words) if claim_words else 0

            # If claim has numbers, at least one number MUST match
            if has_stats:
                if numbers_matched >= 1 and overlap_ratio > 0.3:
                    return "VERIFIED", f"Matched {numbers_matched} numbers + {overlap} shared words in research data"
            else:
                # No stats — pure attribution claim, word overlap is fine
                if overlap_ratio > 0.5:
                    return "VERIFIED", f"High word overlap ({overlap_ratio:.0%}) with research data"

        # Check source names
        for source in sources:
            source_lower = source.lower()
            for ref in reference_texts:
                if source_lower in ref:
                    return "VERIFIED", f"Source '{source}' found in research data"

        return "UNVERIFIED", "No matching data found in research corpus"

    # Pattern matching numbered step lines: "**1. Text", "**Step 1: Text", "1. Text"
    _STEP_PATTERN = re.compile(r'^\*\*(?:Step\s*)?\d+[\.:]\s?|^\d+\.\s')

    def _is_only_body_of_step(self, lines: list[str], line_idx: int) -> bool:
        """Check if this line is the sole body paragraph of a numbered step.

        If removing it would leave a step heading with no body text, return True.
        Also returns True if the line IS a step heading itself (e.g., "**Step 3: ...").
        """
        stripped = lines[line_idx].strip()
        # The line itself is a step heading — don't remove it
        if self._STEP_PATTERN.match(stripped):
            return True

        # Find previous non-blank line
        prev_is_step = False
        for j in range(line_idx - 1, max(line_idx - 4, -1), -1):
            if lines[j].strip():
                prev_is_step = bool(self._STEP_PATTERN.match(lines[j].strip()))
                break
        if not prev_is_step:
            return False
        # Find next non-blank line
        for j in range(line_idx + 1, min(line_idx + 4, len(lines))):
            if lines[j].strip():
                next_is_step_or_heading = bool(
                    self._STEP_PATTERN.match(lines[j].strip()) or lines[j].strip().startswith('#')
                )
                return next_is_step_or_heading
        return False

    # Pattern for short commentary lines that are orphaned when an adjacent stat is removed
    _ORPHAN_PATTERN = re.compile(
        r"^(?:That's|That\u2019s|And yet|But this|So this|Remember |Here's|"
        r"Here\u2019s|This is why|The reason|The point|What this means|"
        r"Think about that|Let that sink|Sound familiar|Not exactly|"
        r"That should)",
        re.IGNORECASE,
    )

    def _clean_orphaned_neighbors(self, lines: list[str], removed_idx: int) -> None:
        """After removing a stat line, check adjacent lines for orphaned commentary."""
        for offset in (1, 2, -1, -2):
            j = removed_idx + offset
            if j < 0 or j >= len(lines):
                continue
            neighbor = lines[j].strip()
            if not neighbor or neighbor.startswith("#"):
                continue
            # Short commentary line with no numbers that matches orphan patterns
            if (len(neighbor.split()) < 25
                    and self._ORPHAN_PATTERN.match(neighbor)
                    and not re.search(r'\d+%|\$[\d,]+', neighbor)):
                lines[j] = ""
            # Lines starting with ". " or just "." — period fragment from
            # removing a sentence that was part of a larger paragraph
            elif neighbor.startswith(". ") or neighbor == ".":
                lines[j] = ""

    def _remove_unverified_claim(self, article: str, claim: str) -> tuple[str, bool]:
        """Remove an unverified claim from the article. Returns (revised_article, was_removed).

        Only removes if the claim contains a specific stat (number/percentage).
        Tries to remove just the sentence, not the whole paragraph.
        Also cleans up adjacent orphaned commentary (e.g., "That's not a rounding error.").
        """
        # Only remove stats, not general attribution claims
        if not re.search(r'\d+%|\$[\d,]+|\d+\s*(billion|million|percent|times)', claim, re.IGNORECASE):
            return article, False

        # Find the claim in the article (it might be a partial line match).
        # Claims are extracted as plain text (markdown links stripped),
        # so we also need to search a plain-text version of the article.
        claim_trimmed = claim.strip()[:150]
        article_plain = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', article)
        if claim_trimmed not in article and claim_trimmed not in article_plain:
            # Try matching just the stat portion
            stat_match = re.search(r'\d+(?:\.\d+)?%', claim)
            if not stat_match:
                return article, False
            # Find the sentence containing this stat in the article
            stat_text = stat_match.group()
            lines = article.split("\n")
            new_lines = []
            removed = False
            for li, line in enumerate(lines):
                if stat_text in line and not line.strip().startswith("#"):
                    # Guard: don't remove if it's the sole body of a numbered step
                    if self._is_only_body_of_step(lines, li):
                        new_lines.append(line)
                        continue
                    # Check this isn't a completely different stat
                    line_lower = line.lower()
                    claim_words = set(re.split(r'\W+', claim.lower()))
                    line_words = set(re.split(r'\W+', line_lower))
                    meaningful_claim = {w for w in claim_words if len(w) > 4}
                    if meaningful_claim:
                        overlap = len(meaningful_claim & line_words) / len(meaningful_claim)
                        if overlap > 0.6:  # Higher threshold to avoid removing wrong lines
                            removed = True
                            self._clean_orphaned_neighbors(lines, li)
                            continue  # skip this line
                new_lines.append(line)
            if removed:
                return "\n".join(new_lines), True
            return article, False

        # Direct match — remove the line (also check plain-text version)
        lines = article.split("\n")
        new_lines = []
        removed = False
        for li, line in enumerate(lines):
            line_plain = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', line)
            if claim_trimmed in line or claim_trimmed in line_plain:
                # Guard: don't remove if it's the sole body of a numbered step
                if self._is_only_body_of_step(lines, li):
                    new_lines.append(line)
                    continue
                removed = True
                self._clean_orphaned_neighbors(lines, li)
                continue
            new_lines.append(line)
        if removed:
            return "\n".join(new_lines), True
        return article, False
