"""Agent 11: AEO Pass - runs programmatic AEO audit, then has Claude optimize.

Full-article mode: Claude returns the complete revised article with an AEO
scorecard, because AEO changes are pervasive (answer blocks in every section,
extractability fixes across many paragraphs) and too nuanced for delta mode.
"""

from __future__ import annotations

import re
from agents.base import BaseAgent
from state import PipelineState
from prompts.templates import AEO_PASS_SYSTEM


class AEOPassAgent(BaseAgent):
    name = "AEO Pass"
    description = "Optimize article for AI answer engine extractability"
    agent_number = 11
    emoji = "\U0001f916"
    timeout = 180  # 3 min — full-article output takes longer than delta

    def run(self, state: PipelineState) -> PipelineState:
        article = (
            state.seo_pass_article
            or state.fact_check_article
            or state.voice_pass_article
            or state.draft_article
        )

        # ── Run programmatic AEO pre-screening ──
        self.progress("Running programmatic AEO pre-screening...")
        audit = self._audit(article, state)
        issues = audit["issues"]
        scorecard = audit["scorecard"]

        self.progress(f"Pre-screening found {len(issues)} issues")
        for issue in issues[:5]:
            self.progress(f"  - {issue[:80]}")

        # ── Build user prompt with pre-screened results ──
        issue_list = "\n".join(f"{i+1}. {iss}" for i, iss in enumerate(issues))

        paa = state.keyword_data.get("questions_people_ask", []) if state.keyword_data else []
        paa_text = "\n".join(f"- {q}" for q in paa[:10]) if paa else "None available."

        # Include research data for Claude to reference when adding sources/stats
        stats_text = ""
        if state.statistics:
            stats_text = "\n## Available Statistics (from research)\n"
            for stat in state.statistics[:15]:
                s = stat.get("stat", "")
                src = stat.get("source_name", "")
                url = stat.get("source_url", "")
                stats_text += f"- {s} (Source: {src}, URL: {url})\n"

        user_prompt = f"""## PRE-SCREENED AUDIT RESULTS

These checks were run programmatically. Use them to focus your work.

{scorecard}

### Issues Identified:
{issue_list if issues else "No programmatic issues found. Run your own nuanced checks (semantic triples, passage extractability, unique value)."}

## People Also Ask Questions (for follow-up query coverage)
{paa_text}
{stats_text}
## Target Keyword: {state.target_keyword}

## ARTICLE
===ARTICLE_START===
{article}
===ARTICLE_END===

Apply all 11 AEO checks from your instructions. The pre-screened results above cover the mechanical checks. You must also evaluate: semantic triples, passage extractability, unique value, and any nuanced issues the programmatic audit can't catch.

Return the AEO scorecard followed by --- then the full revised article."""

        self.progress("Claude is optimizing for AEO (full-article mode)...")
        response = self.call_llm(AEO_PASS_SYSTEM, user_prompt)

        # ── Parse response: scorecard + article ──
        revised_article = self._parse_full_response(response, article)
        state.aeo_pass_article = revised_article
        state.aeo_changes = self._extract_changes_list(response)

        word_count = len(revised_article.split())
        self.log(f"AEO pass complete. Article: ~{word_count} words. {len(state.aeo_changes)} changes logged.")
        return state

    def _parse_full_response(self, response: str, fallback_article: str) -> str:
        """Extract the revised article from Claude's response.

        Expected format:
        AEO CHANGES MADE: ...
        AEO SCORECARD: ...
        VOICE INTEGRITY: ...
        ---
        [Full revised article]
        """
        # Look for the article after the --- separator
        parts = response.split("\n---\n")
        if len(parts) >= 2:
            # The article is everything after the last ---
            article = parts[-1].strip()
            # Verify it looks like an article (has heading)
            if article and ("# " in article[:200] or len(article) > 500):
                return article

        # Fallback: look for # heading and take everything from there
        lines = response.split("\n")
        for i, line in enumerate(lines):
            if line.strip().startswith("# ") and not line.strip().startswith("## "):
                article = "\n".join(lines[i:]).strip()
                if len(article) > 500:
                    return article

        # Last resort: if response is long enough, it might be the article itself
        if len(response) > 1000 and "# " in response[:500]:
            self.log("[yellow]Warning: Could not find clear scorecard/article separator. Using full response.[/yellow]")
            return response.strip()

        # Final fallback
        self.log("[red]Warning: Could not parse AEO response. Using input article unchanged.[/red]")
        return fallback_article

    def _extract_changes_list(self, response: str) -> list[dict]:
        """Extract the changes list from the scorecard portion of the response."""
        changes = []
        in_changes = False
        for line in response.split("\n"):
            stripped = line.strip()
            if "AEO CHANGES MADE" in stripped or "CHANGES MADE" in stripped:
                in_changes = True
                continue
            if in_changes:
                if stripped.startswith("AEO SCORECARD") or stripped == "---":
                    break
                if re.match(r'^\d+\.', stripped):
                    changes.append({"change": stripped[:100]})
        return changes

    # ── Programmatic AEO pre-screening ──

    def _audit(self, article: str, state: PipelineState) -> dict:
        """Run mechanical AEO checks to pre-screen for Claude.

        These checks handle what's programmatically verifiable. Claude handles
        nuanced checks like semantic triples, extractability, and unique value.
        """
        issues = []
        scorecard_lines = []
        lines = article.split("\n")
        text_lower = article.lower()
        words = article.split()
        word_count = len(words)

        # ── 1. Answer Blocks: H2 sections starting with direct answers ──
        h2_positions = []
        for i, line in enumerate(lines):
            if line.strip().startswith("## "):
                h2_positions.append((i, line.strip()[3:].strip()))

        h2s_missing_answer = []
        for idx, (line_num, h2_text) in enumerate(h2_positions):
            # Get first 2 sentences after the H2
            following_text = []
            for j in range(line_num + 1, min(line_num + 8, len(lines))):
                stripped = lines[j].strip()
                if stripped.startswith("#"):
                    break
                if stripped and not stripped.startswith("|") and not stripped.startswith("-"):
                    following_text.append(stripped)
                    if len(" ".join(following_text).split()) >= 30:
                        break

            first_text = " ".join(following_text)
            first_words = first_text.split()[:50]
            first_50 = " ".join(first_words)

            # Check: does the first 50 words contain a declarative statement?
            # A question or vague opener doesn't count as an answer block
            has_question_only = first_50.count("?") > 0 and len(first_words) < 15
            too_short = len(first_words) < 10
            if has_question_only or too_short:
                h2s_missing_answer.append(h2_text)

        if h2s_missing_answer:
            issues.append(
                f"ANSWER BLOCKS MISSING: These H2 sections don't start with a clear, "
                f"direct answer in the first 1-2 sentences: {h2s_missing_answer}. "
                f"Add a 20-50 word direct answer BEFORE the conversational explanation."
            )
        scorecard_lines.append(
            f"- Answer Blocks: {'PASS' if not h2s_missing_answer else 'FAIL'} "
            f"({len(h2_positions) - len(h2s_missing_answer)}/{len(h2_positions)} H2s have answer blocks)"
        )

        # ── 2. Statistics without inline source attribution ──
        stat_lines = []
        stat_without_source = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("|"):
                continue
            has_stat = bool(re.search(
                r'\d+%|\$[\d,]+|\d+\s*(billion|million|trillion|percent)',
                stripped, re.IGNORECASE
            ))
            if has_stat:
                stat_lines.append(stripped)
                has_source = bool(re.search(
                    r'(according to|per |found that|reported|study by|survey by|'
                    r'research from|report by|data from|analysis by|cited by|'
                    r'Gartner|Forrester|McKinsey|World Commerce|Deloitte|PwC|'
                    r'American Bar|Goldman Sachs|Bloomberg|IACCM|DottedSign)',
                    stripped, re.IGNORECASE
                ))
                if not has_source:
                    stat_without_source.append(stripped[:80])

        if stat_without_source:
            issues.append(
                f"SOURCE ATTRIBUTION MISSING: {len(stat_without_source)} statistics lack "
                f"inline source names. Examples: {stat_without_source[:2]}. "
                f"Name the source in the text: 'according to [Source]' or 'a [Source] study found'."
            )
        scorecard_lines.append(
            f"- Source Attribution: {'PASS' if not stat_without_source else 'FAIL'} "
            f"({len(stat_lines) - len(stat_without_source)}/{len(stat_lines)} stats have inline sources)"
        )

        # ── 3. Quantifiable claims density ──
        data_points = len(stat_lines)
        data_per_1000 = (data_points / max(word_count, 1)) * 1000
        low_data = data_per_1000 < 3
        if low_data:
            issues.append(
                f"LOW DATA DENSITY: {data_points} quantifiable claims in {word_count} words "
                f"({data_per_1000:.1f} per 1,000 words, target: 3+). Replace vague claims with "
                f"specific numbers from the research data provided."
            )
        scorecard_lines.append(
            f"- Quantifiable Claims: {'PASS' if not low_data else 'FAIL'} "
            f"({data_per_1000:.1f} per 1,000 words)"
        )

        # ── 4. Self-describing headings ──
        vague_headings = []
        vague_patterns = [
            "the bottom line", "the big picture", "why it matters",
            "how we're different", "key takeaways", "final thoughts",
            "wrapping up", "in summary", "overview",
        ]
        for _, h2_text in h2_positions:
            h2_lower = h2_text.lower().strip("?").strip()
            if h2_lower in vague_patterns or len(h2_lower.split()) <= 2:
                vague_headings.append(h2_text)

        if vague_headings:
            issues.append(
                f"VAGUE HEADINGS: {vague_headings}. Headings must communicate complete "
                f"context when read in isolation. 'The Bottom Line' → "
                f"'Why Waiting to Upgrade Contract Management Costs More Than Acting'."
            )
        scorecard_lines.append(
            f"- Self-Describing Headings: {'PASS' if not vague_headings else 'FAIL'} "
            f"({len(h2_positions) - len(vague_headings)}/{len(h2_positions)} are self-describing)"
        )

        # ── 5. Entity consistency (ContractSafe identification) ──
        entity_ok = True
        if "contractsafe" in text_lower:
            first_mention = text_lower.find("contractsafe")
            surrounding = article[max(0, first_mention - 10):first_mention + 200]
            if not any(phrase in surrounding.lower() for phrase in [
                "contract management", "contract lifecycle", "clm", "software"
            ]):
                entity_ok = False
                issues.append(
                    "ENTITY CLARITY: ContractSafe not identified on first mention. "
                    "Add 'ContractSafe, a contract management software company,' or similar."
                )
        scorecard_lines.append(
            f"- Entity Consistency: {'PASS' if entity_ok else 'FAIL'}"
        )

        # ── 6. Process sections with numbered steps ──
        process_h2s = [h for _, h in h2_positions
                       if any(w in h.lower() for w in ["how to", "steps", "step-by-step", "process", "guide", "write"])]
        process_missing_steps = []
        for h2 in process_h2s:
            h2_pos = article.find(f"## {h2}")
            next_h2 = article.find("\n## ", h2_pos + 1)
            section = article[h2_pos:next_h2] if next_h2 > 0 else article[h2_pos:]
            has_numbered = bool(re.search(r"^\d+\.\s", section, re.MULTILINE))
            if not has_numbered:
                process_missing_steps.append(h2)

        if process_missing_steps:
            issues.append(
                f"PROCESS SECTIONS WITHOUT NUMBERED STEPS: {process_missing_steps}. "
                f"How-to sections should use numbered steps for AI extractability."
            )
        scorecard_lines.append(
            f"- Structured Formats: {'PASS' if not process_missing_steps else 'FAIL'} "
            f"({len(process_h2s) - len(process_missing_steps)}/{len(process_h2s)} process sections have numbered steps)"
        )

        # ── 7. People Also Ask coverage ──
        paa = state.keyword_data.get("questions_people_ask", []) if state.keyword_data else []
        addressed = 0
        unaddressed = []
        for q in paa[:8]:
            q_words = set(w.lower() for w in q.split() if len(w) > 3)
            coverage = sum(1 for w in q_words if w in text_lower) / max(len(q_words), 1)
            if coverage > 0.5:
                addressed += 1
            else:
                unaddressed.append(q)

        if unaddressed:
            issues.append(
                f"PAA QUESTIONS NOT ADDRESSED: {unaddressed[:4]}. "
                f"Weave answers into existing sections or add as new content."
            )
        scorecard_lines.append(
            f"- Follow-Up Coverage: {'PASS' if not unaddressed else 'FAIL'} "
            f"({addressed}/{len(paa[:8])} PAA questions addressed)"
        )

        # ── 8. Content freshness ──
        import datetime
        current_year = str(datetime.datetime.now().year)
        last_year = str(int(current_year) - 1)
        has_recent = current_year in article or last_year in article
        if not has_recent:
            issues.append(
                f"NO FRESHNESS SIGNALS: Article doesn't reference {current_year} or {last_year}. "
                f"Add at least one current-year reference or recent data point."
            )
        scorecard_lines.append(
            f"- Freshness Signals: {'PASS' if has_recent else 'FAIL'}"
        )

        # ── 9. Context-dependent key passages (extractability pre-check) ──
        context_dependent = []
        context_starters = [
            "this is why", "this is where", "this is how",
            "as mentioned", "as discussed", "as noted",
            "they also", "it also", "these include",
        ]
        for i, line in enumerate(lines):
            stripped = line.strip().lower()
            if not stripped or stripped.startswith("#") or stripped.startswith("|"):
                continue
            for starter in context_starters:
                if stripped.startswith(starter):
                    context_dependent.append(f"Line {i+1}: {lines[i].strip()[:60]}")
                    break

        if context_dependent:
            issues.append(
                f"CONTEXT-DEPENDENT PASSAGES: {len(context_dependent)} key passages start with "
                f"context-dependent references ('{context_dependent[0]}'). "
                f"These break when extracted by AI. Rewrite to be self-contained."
            )
        scorecard_lines.append(
            f"- Passage Extractability: {'PASS' if not context_dependent else 'NEEDS REVIEW'} "
            f"({len(context_dependent)} context-dependent passages found)"
        )

        # ── Checks that require Claude (not pre-screened) ──
        scorecard_lines.append("- Semantic Triples: NEEDS CLAUDE REVIEW")
        scorecard_lines.append("- Unique Value: NEEDS CLAUDE REVIEW")

        scorecard = "Pre-Screened AEO Scorecard:\n" + "\n".join(scorecard_lines)
        return {"issues": issues, "scorecard": scorecard}
