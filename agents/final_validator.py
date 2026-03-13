"""Agent 13: Final Validator - comprehensive quality checklist.

Fully programmatic — no Claude call. Re-runs programmatic audits from
agents 8, 10, 11 on the final article and compiles a pass/fail report.
"""

from __future__ import annotations

import re
from agents.base import BaseAgent
from agents.brand_voice_pass import apply_mechanical_fixes
from state import PipelineState


class FinalValidatorAgent(BaseAgent):
    name = "Final Validator"
    description = "Run comprehensive quality checklist on the complete package"
    agent_number = 13
    emoji = "\U0001f3c1"

    def run(self, state: PipelineState) -> PipelineState:
        article = (
            state.aeo_pass_article
            or state.seo_pass_article
            or state.fact_check_article
            or state.voice_pass_article
            or state.draft_article
        )

        # Sanity check: the article should be substantial
        if len(article.strip()) < 1000:
            self.log("[red]ERROR: Article is suspiciously short. Falling back to draft.[/red]")
            article = state.draft_article or ""
            if len(article.strip()) < 1000:
                self.log("[red]ERROR: Draft article is also too short. Validation will fail.[/red]")

        # ── Apply mechanical fixes as a final pass ──
        # Agents 10 (SEO) and 11 (AEO) use Claude FIND/REPLACE which can
        # reintroduce em/en dashes and long paragraphs. Clean them up here
        # so the validator doesn't flag issues that are trivially fixable.
        self.progress("Applying final mechanical cleanup (dashes, paragraphs)...")
        article = apply_mechanical_fixes(article)

        self.progress("Running final validation checks programmatically...")

        checks = []
        overall_pass = True

        # ── 1. Em dashes (FORBIDDEN) ──
        em_count = article.count("\u2014") + article.count("\u2013")
        passed = em_count == 0
        checks.append(("Em/En Dashes", passed, f"{em_count} found (target: 0)"))
        if not passed:
            overall_pass = False

        # ── 2. Paragraphs over 42 words ──
        long_paras = self._count_long_paragraphs(article)
        passed = long_paras == 0
        checks.append(("Paragraphs ≤42 Words", passed, f"{long_paras} long paragraphs found (target: 0)"))
        if not passed:
            overall_pass = False

        # ── 3. Keyword in H1 ──
        kw = state.target_keyword.lower()
        h1_match = re.search(r"^# (.+)$", article, re.MULTILINE)
        h1_text = h1_match.group(1).lower() if h1_match else ""
        passed = kw in h1_text
        checks.append(("Keyword in H1", passed, f"H1: '{h1_match.group(1) if h1_match else 'NONE'}'"))
        if not passed:
            overall_pass = False

        # ── 4. Internal links ≥5 ──
        internal_links = re.findall(r'\[([^\]]+)\]\((https?://[^)]*contractsafe\.com[^)]*)\)', article)
        passed = len(internal_links) >= 5
        checks.append(("Internal Links ≥5", passed, f"{len(internal_links)} found"))
        if not passed:
            overall_pass = False

        # ── 5. External links ≥3 ──
        all_links = re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', article)
        external_links = [(t, u) for t, u in all_links if "contractsafe.com" not in u.lower()]
        passed = len(external_links) >= 3
        checks.append(("External Links ≥3", passed, f"{len(external_links)} found"))
        if not passed:
            overall_pass = False

        # ── 6. Word count within 25% of target ──
        # Using 25% because the brief, metaphor framework, and SEO/AEO passes
        # naturally add length beyond the raw word count target.
        word_count = len(article.split())
        target = state.target_word_count
        lower_bound = int(target * 0.75)
        upper_bound = int(target * 1.25)
        passed = lower_bound <= word_count <= upper_bound
        checks.append(("Word Count", passed, f"{word_count} words (target: {target}, range: {lower_bound}-{upper_bound})"))
        # Word count doesn't fail the whole pipeline — it's a soft check

        # ── 7. Has H1 ──
        h1_count = len(re.findall(r"^# (?!#)", article, re.MULTILINE))
        passed = h1_count == 1
        checks.append(("Single H1", passed, f"{h1_count} H1 headings found"))
        if not passed:
            overall_pass = False

        # ── 8. Has H2s ──
        h2s = re.findall(r"^## (.+)$", article, re.MULTILINE)
        passed = len(h2s) >= 3
        checks.append(("H2 Structure", passed, f"{len(h2s)} H2 headings"))
        if not passed:
            overall_pass = False

        # ── 9. Keyword in first 100 words ──
        first_100 = " ".join(article.split()[:100]).lower()
        passed = kw in first_100
        checks.append(("Keyword in First 100 Words", passed, ""))
        if not passed:
            overall_pass = False

        # ── 10. No naked URLs ──
        naked_urls = re.findall(r'(?<!\()https?://\S+(?!\))', article)
        naked_urls = [u for u in naked_urls if f"]({u}" not in article]
        passed = len(naked_urls) == 0
        checks.append(("No Naked URLs", passed, f"{len(naked_urls)} found" if naked_urls else ""))

        # ── 11. Meta description ──
        has_meta = bool(state.meta_description and len(state.meta_description) > 20)
        meta_len = len(state.meta_description) if state.meta_description else 0
        passed = has_meta and meta_len <= 160
        checks.append(("Meta Description", passed, f"{meta_len} chars" if has_meta else "Missing"))

        # ── 12. Social posts ──
        has_linkedin = bool(state.linkedin_post and len(state.linkedin_post) > 20)
        has_twitter = bool(state.twitter_post and len(state.twitter_post) > 20)
        passed = has_linkedin and has_twitter
        checks.append(("Social Posts", passed,
                       f"LinkedIn: {'Yes' if has_linkedin else 'No'}, X/Twitter: {'Yes' if has_twitter else 'No'}"))

        # ── 13. Well-formed tables ──
        collapsed_tables = self._count_collapsed_tables(article)
        passed = collapsed_tables == 0
        checks.append(("Well-Formed Tables", passed, f"{collapsed_tables} collapsed tables" if collapsed_tables else ""))
        if not passed:
            overall_pass = False

        # ── 14. Well-formed lists ──
        concat_lists = self._count_concatenated_lists(article)
        passed = concat_lists == 0
        checks.append(("Well-Formed Lists", passed, f"{concat_lists} concatenated list lines" if concat_lists else ""))
        if not passed:
            overall_pass = False

        # ── 15. No broken links ──
        broken_links = self._count_broken_links(article)
        passed = broken_links == 0
        checks.append(("No Broken Links", passed, f"{broken_links} broken" if broken_links else ""))
        if not passed:
            overall_pass = False

        # ── 16. No social copy in body ──
        has_social_in_body = bool(re.search(
            r'(?:^|\n)\*\*(?:LinkedIn|Twitter|X/Twitter|SEO Meta)\s*(?:Post|Description)',
            article, re.IGNORECASE
        ))
        passed = not has_social_in_body
        checks.append(("No Social Copy in Body", passed, "Social copy found in article" if has_social_in_body else ""))
        if not passed:
            overall_pass = False

        # ── Build report ──
        report_lines = ["# Final Validation Report\n"]
        pass_count = sum(1 for _, p, _ in checks if p)
        total = len(checks)

        for check_name, passed, detail in checks:
            status = "PASS ✓" if passed else "FAIL ✗"
            detail_str = f" — {detail}" if detail else ""
            report_lines.append(f"- **{check_name}**: {status}{detail_str}")

        report_lines.append(f"\n**Score: {pass_count}/{total}**")
        report_lines.append(f"\n**OVERALL: {'PASS' if overall_pass else 'FAIL'}**")

        if not overall_pass:
            failures = [name for name, p, _ in checks if not p]
            report_lines.append(f"\nFailed checks: {', '.join(failures)}")

        state.validation_report = "\n".join(report_lines)
        state.final_article = article
        state.pass_fail = overall_pass

        status = "PASS" if overall_pass else "FAIL"
        self.log(f"Validation result: {status} ({pass_count}/{total} checks passed)")
        return state

    def _count_long_paragraphs(self, article: str) -> int:
        """Count prose paragraphs over 42 words. Skips tables, bullet/numbered lists."""
        lines = article.split("\n")
        count = 0
        current_para = []
        for line in lines:
            stripped = line.strip()
            is_structural = (
                stripped.startswith("#") or not stripped
                or stripped.startswith("|") or stripped.startswith("- ")
                or stripped.startswith("* ") or bool(re.match(r'^\d+[\.\)]\s', stripped))
            )
            if is_structural:
                if current_para:
                    para_text = " ".join(current_para)
                    if len(para_text.split()) > 42:
                        count += 1
                    current_para = []
            else:
                current_para.append(stripped)
        if current_para:
            if len(" ".join(current_para).split()) > 42:
                count += 1
        return count

    def _count_collapsed_tables(self, article: str) -> int:
        """Count lines that contain a full table collapsed onto one line."""
        count = 0
        for line in article.split('\n'):
            stripped = line.strip()
            # A collapsed table has |---| AND many pipes (>6)
            if '|---' in stripped and stripped.count('|') > 6:
                count += 1
        return count

    def _count_concatenated_lists(self, article: str) -> int:
        """Count lines with multiple list items concatenated."""
        count = 0
        for line in article.split('\n'):
            stripped = line.strip()
            # Concatenated bullets: starts with - and has more - items mid-line
            if stripped.startswith('- ') and re.search(r'\s-\s\S', stripped[2:]):
                # Exclude lines where - is inside a link
                no_links = re.sub(r'\[([^\]]+)\]', '', stripped)
                if re.search(r'\s-\s\S', no_links[2:]):
                    count += 1
            # Concatenated numbered items: 1. text 2. text
            if re.match(r'^\d+\.\s', stripped) and re.search(r'\s\d+\.\s', stripped[3:]):
                count += 1
        return count

    def _count_broken_links(self, article: str) -> int:
        """Count markdown links broken across lines."""
        lines = article.split('\n')
        count = 0
        for i, line in enumerate(lines):
            # Find [ without matching ]( — but only if the content between [ and ]
            # is substantial (>5 chars) and doesn't close on the same line.
            # Short brackets like [1], [Party A] are not markdown links.
            for match in re.finditer(r'\[([^\]]{6,})', line):
                after = line[match.start():]
                if '](' not in after and ']' not in after[1:match.end()-match.start()+10]:
                    count += 1
        return count
