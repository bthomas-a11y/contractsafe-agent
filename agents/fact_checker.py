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
        self.progress("Extracting claims from article...")

        # Extract claims (lines with numbers, percentages, or attribution)
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
        """Extract factual claims from the article text."""
        claims = []
        for line in article.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Lines with numbers, percentages, dollar amounts, or attribution phrases
            has_stat = bool(re.search(
                r'\d+%|\$[\d,]+|\d+\s*(billion|million|percent|times)',
                line, re.IGNORECASE
            ))
            has_attribution = any(
                phrase in line.lower()
                for phrase in ["according to", "study found", "report found",
                               "research shows", "data shows", "survey"]
            )
            if has_stat or has_attribution:
                claims.append(line[:300])
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

        # Add subject research snippets
        if state.subject_research:
            # Extract sentences with numbers from research
            for sentence in re.split(r'[.!?\n]', state.subject_research):
                sentence = sentence.strip()
                if any(c.isdigit() for c in sentence) and len(sentence) > 20:
                    refs.append(sentence.lower())

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
        sources = re.findall(r'[A-Z][a-z]{3,}(?:\s+[A-Z][a-z]{3,})*', claim)

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

    def _remove_unverified_claim(self, article: str, claim: str) -> tuple[str, bool]:
        """Remove an unverified claim from the article. Returns (revised_article, was_removed).

        Only removes if the claim contains a specific stat (number/percentage).
        Tries to remove just the sentence, not the whole paragraph.
        """
        # Only remove stats, not general attribution claims
        if not re.search(r'\d+%|\$[\d,]+|\d+\s*(billion|million|percent|times)', claim, re.IGNORECASE):
            return article, False

        # Find the claim in the article (it might be a partial line match)
        claim_trimmed = claim.strip()[:150]
        if claim_trimmed not in article:
            # Try matching just the stat portion
            stat_match = re.search(r'\d+(?:\.\d+)?%', claim)
            if not stat_match:
                return article, False
            # Find the sentence containing this stat in the article
            stat_text = stat_match.group()
            lines = article.split("\n")
            new_lines = []
            removed = False
            for line in lines:
                if stat_text in line and not line.strip().startswith("#"):
                    # Check this isn't a completely different stat
                    line_lower = line.lower()
                    claim_words = set(re.split(r'\W+', claim.lower()))
                    line_words = set(re.split(r'\W+', line_lower))
                    meaningful_claim = {w for w in claim_words if len(w) > 4}
                    if meaningful_claim:
                        overlap = len(meaningful_claim & line_words) / len(meaningful_claim)
                        if overlap > 0.6:  # Higher threshold to avoid removing wrong lines
                            removed = True
                            continue  # skip this line
                new_lines.append(line)
            if removed:
                return "\n".join(new_lines), True
            return article, False

        # Direct match — remove the line
        lines = article.split("\n")
        new_lines = [line for line in lines if claim_trimmed not in line]
        if len(new_lines) < len(lines):
            return "\n".join(new_lines), True
        return article, False
