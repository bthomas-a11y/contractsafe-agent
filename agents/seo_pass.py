"""Agent 10: SEO Pass - runs programmatic SEO audit, then has Claude fix failures.

Uses delta mode: Claude returns find/replace pairs instead of the full article.
"""

from __future__ import annotations

import re
from agents.base import BaseAgent
from state import PipelineState
from prompts.templates import SEO_PASS_SYSTEM


class SEOPassAgent(BaseAgent):
    name = "SEO Pass"
    description = "Audit article for SEO issues and fix them"
    agent_number = 10
    emoji = "\U0001f50e"
    timeout = 120  # 2 min — delta mode is faster

    def run(self, state: PipelineState) -> PipelineState:
        article = state.fact_check_article or state.voice_pass_article or state.draft_article
        input_article = article

        # ── Run programmatic SEO audit FIRST ──
        self.progress("Running programmatic SEO audit...")
        audit = self._audit(article, state)
        issues = audit["issues"]
        report = audit["report"]

        self.progress(f"Found {len(issues)} SEO issues to fix")
        for issue in issues:
            self.progress(f"  - {issue}")

        # ── Give Claude the specific issues to fix (delta mode) ──
        issue_list = "\n".join(f"{i+1}. {issue}" for i, issue in enumerate(issues))

        user_prompt = f"""## ISSUES TO FIX
{issue_list if issues else "No issues found."}

## SEO Parameters
- Primary keyword: {state.target_keyword}
- Secondary keywords: {', '.join(state.secondary_keywords) if state.secondary_keywords else 'None specified'}

## Available Links (use when adding links)
Internal: {self._format_links(state.internal_links)}
External: {self._format_links(state.external_links)}

## ARTICLE
===ARTICLE_START===
{article}
===ARTICLE_END==="""

        self.progress("Having Claude fix identified issues (delta mode)...")
        response = self.call_llm(SEO_PASS_SYSTEM, user_prompt)

        # Parse and apply find/replace pairs (using shared parser from BaseAgent)
        changes = self.parse_delta_response(response)
        state.seo_pass_article = self.apply_delta_changes(input_article, changes)
        state.seo_changes = [{"change": c["find"][:50], "reason": c["replace"][:50]} for c in changes]

        if issues and not changes:
            self.log(f"[yellow]Warning: {len(issues)} issues identified but no changes parsed from response.[/yellow]")

        self.log(f"Audit found {len(issues)} issues. Applied {len(changes)} changes via delta mode.")
        return state

    # ── Programmatic SEO audit ──

    def _audit(self, article: str, state: PipelineState) -> dict:
        """Run concrete, measurable SEO checks in Python."""
        issues = []
        report_lines = []
        kw = state.target_keyword.lower()
        text_lower = article.lower()
        words = article.split()
        word_count = len(words)

        # ── 1. Keyword in title/H1 ──
        h1_match = re.search(r"^# (.+)$", article, re.MULTILINE)
        h1_text = h1_match.group(1).lower() if h1_match else ""
        if kw not in h1_text:
            issues.append(f"KEYWORD MISSING FROM H1/TITLE. Current H1: '{h1_match.group(1) if h1_match else 'NO H1 FOUND'}'. Must contain '{state.target_keyword}'.")
        report_lines.append(f"H1/Title keyword: {'PASS' if kw in h1_text else 'FAIL'}")

        # ── 2. Keyword in first 100 words ──
        first_100 = " ".join(words[:100]).lower()
        if kw not in first_100:
            issues.append(f"KEYWORD MISSING FROM FIRST 100 WORDS. Naturally include '{state.target_keyword}' in the opening paragraph.")
        report_lines.append(f"Keyword in first 100 words: {'PASS' if kw in first_100 else 'FAIL'}")

        # ── 3. Keyword in at least one H2 ──
        h2s = re.findall(r"^## (.+)$", article, re.MULTILINE)
        h2_has_kw = any(kw in h.lower() for h in h2s)
        if not h2_has_kw and h2s:
            issues.append(f"KEYWORD NOT IN ANY H2. Reword one H2 to include '{state.target_keyword}' or a close variant.")
        report_lines.append(f"Keyword in H2: {'PASS' if h2_has_kw else 'FAIL'} (H2s: {h2s})")

        # ── 4. Keyword density (scaled by keyword length) ──
        kw_count = text_lower.count(kw)
        kw_word_count = len(kw.split())
        # Long keywords (4+ words) need fewer occurrences to avoid sounding forced
        if kw_word_count >= 4:
            ideal_min = 2
            ideal_max = max(4, word_count // 500)
        else:
            ideal_min = max(3, word_count // 500)
            ideal_max = max(7, word_count // 200)
        if kw_count < ideal_min:
            issues.append(f"KEYWORD UNDERUSED. '{state.target_keyword}' appears {kw_count} times in {word_count} words. Target: {ideal_min}-{ideal_max} occurrences. Add it naturally in body paragraphs.")
        elif kw_count > ideal_max:
            issues.append(f"KEYWORD STUFFING RISK. '{state.target_keyword}' appears {kw_count} times. Target: {ideal_min}-{ideal_max}. Remove some to sound natural.")
        report_lines.append(f"Keyword density: {kw_count} occurrences in {word_count} words (target: {ideal_min}-{ideal_max})")

        # ── 5. Secondary keywords ──
        missing_secondary = []
        for sk in state.secondary_keywords:
            if sk.lower() not in text_lower:
                missing_secondary.append(sk)
        if missing_secondary:
            issues.append(f"MISSING SECONDARY KEYWORDS: {missing_secondary}. Weave each into the article naturally at least once.")
        report_lines.append(f"Secondary keywords present: {len(state.secondary_keywords) - len(missing_secondary)}/{len(state.secondary_keywords)}")

        # ── 6. Internal links ──
        internal_links = re.findall(r'\[([^\]]+)\]\((https?://[^)]*contractsafe\.com[^)]*)\)', article)
        if len(internal_links) < 5:
            issues.append(f"ONLY {len(internal_links)} INTERNAL LINKS (minimum 5). Add {5 - len(internal_links)} more using the available internal links provided. Use organic keyword anchor text.")
        report_lines.append(f"Internal links: {len(internal_links)} (minimum: 5)")

        # ── 7. External links ──
        all_links = re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', article)
        external_links = [(t, u) for t, u in all_links if "contractsafe.com" not in u.lower()]
        if len(external_links) < 3:
            issues.append(f"ONLY {len(external_links)} EXTERNAL LINKS (minimum 3). Add {3 - len(external_links)} more using the available external links provided.")
        report_lines.append(f"External links: {len(external_links)} (minimum: 3)")

        # ── 8. Link distribution / front-loading ──
        total_links = len(all_links)
        if total_links > 0:
            first_third_end = word_count // 3
            first_third_text = " ".join(words[:first_third_end])
            links_in_first_third = len(re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', first_third_text))
            front_pct = (links_in_first_third / total_links * 100) if total_links else 0
            if front_pct < 50:
                issues.append(f"LINKS NOT FRONT-LOADED. Only {links_in_first_third}/{total_links} links ({front_pct:.0f}%) are in the first third. Target: ~60%. Move some links earlier in the article.")
            report_lines.append(f"Link front-loading: {links_in_first_third}/{total_links} in first third ({front_pct:.0f}%, target: ~60%)")

        # ── 9. Forbidden link patterns ──
        naked_urls = re.findall(r'(?<!\()https?://\S+(?!\))', article)
        naked_urls = [u for u in naked_urls if f"]({u}" not in article]
        if naked_urls:
            issues.append(f"NAKED URLs FOUND (forbidden): {naked_urls[:3]}. Convert each to [anchor text](url) format.")

        paren_links = re.findall(r'\(\[.+?\]\(.+?\)\)', article)
        if paren_links:
            issues.append(f"LINKS IN PARENTHESES FOUND (forbidden): {len(paren_links)} instances. Integrate links into the sentence flow.")

        according_to = re.findall(r'[Aa]ccording to \[', article)
        if according_to:
            issues.append(f"'ACCORDING TO [Source]' PATTERN FOUND (forbidden): {len(according_to)} instances. Rewrite to integrate links organically.")

        click_here = re.findall(r'\[(click here|learn more|read more|check out|here)\]', article, re.IGNORECASE)
        if click_here:
            issues.append(f"GENERIC ANCHOR TEXT FOUND (forbidden): {[m for m in click_here]}. Replace with descriptive keyword anchor text.")

        # ── 10. Heading structure ──
        h1_count = len(re.findall(r"^# (?!#)", article, re.MULTILINE))
        if h1_count != 1:
            issues.append(f"HEADING STRUCTURE: Found {h1_count} H1 headings (should be exactly 1).")

        h3s = re.findall(r"^### (.+)$", article, re.MULTILINE)
        h4s = re.findall(r"^#### (.+)$", article, re.MULTILINE)
        if h4s and not h3s:
            issues.append("HEADING HIERARCHY: H4 headings found without H3s. Don't skip heading levels.")

        # ── 11. Word count (report only — not sent to Claude as an issue) ──
        # Word count is the writer's job, not the SEO editor's. Asking Claude to
        # cut/add 40% of an article via find/replace pairs causes timeouts.
        target = state.target_word_count
        report_lines.append(f"Word count: {word_count} (target: {target})")

        report = "\n".join(report_lines)
        return {"issues": issues, "report": report}

    def _format_links(self, links: list[dict]) -> str:
        if not links:
            return "None available."
        return "\n".join(
            f"- {l.get('url', '')} | anchor: \"{l.get('anchor_suggestion', '')}\""
            for l in links[:15]
        )
