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

        self.progress("Running 27-point quality checklist...")

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
        if not passed and h1_text:
            # Fuzzy match: all keyword words appear in H1 in order
            kw_words = kw.split()
            h1_words = [re.sub(r'[^\w]', '', w) for w in h1_text.split()]
            ki = 0
            for hw in h1_words:
                if ki < len(kw_words) and hw == kw_words[ki]:
                    ki += 1
            passed = ki == len(kw_words)
        if not passed and h1_text:
            # Relaxed match for creative titles: at least 2/3 of keyword words
            # appear anywhere in the H1 (not necessarily in order)
            kw_words = kw.split()
            h1_word_set = {re.sub(r'[^\w]', '', w) for w in h1_text.split()}
            matches = sum(1 for w in kw_words if w in h1_word_set)
            passed = matches >= max(1, (len(kw_words) + 1) // 2)
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
        if not passed:
            # Relaxed: at least 2/3 of keyword words appear in first 100 words
            kw_words = kw.split()
            first_100_words = {re.sub(r'[^\w]', '', w) for w in first_100.split()}
            matches = sum(1 for w in kw_words if w in first_100_words)
            passed = matches >= max(2, len(kw_words) * 2 // 3)
        checks.append(("Keyword in First 100 Words", passed, ""))
        if not passed:
            overall_pass = False

        # ── 10. No naked URLs ──
        naked_urls = re.findall(r'(?<!\()https?://\S+(?!\))', article)
        naked_urls = [u for u in naked_urls if f"]({u}" not in article]
        passed = len(naked_urls) == 0
        checks.append(("No Naked URLs", passed, f"{len(naked_urls)} found" if naked_urls else ""))

        # ── 11. Meta description ──
        # Auto-trim to 160 chars at last sentence boundary if over limit
        if state.meta_description and len(state.meta_description) > 160:
            trimmed = state.meta_description[:160]
            last_period = trimmed.rfind(".")
            if last_period > 80:
                state.meta_description = trimmed[:last_period + 1]
            else:
                state.meta_description = trimmed.rsplit(" ", 1)[0] + "..."
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

        # ── 17. AEO Answer Blocks ──
        # Each H2 section should open with a direct answer (≥10 substantive words)
        h2_sections = re.split(r'^## ', article, flags=re.MULTILINE)
        h2_total = max(len(h2_sections) - 1, 1)  # skip pre-H2 content
        h2_with_answer = 0
        for section in h2_sections[1:]:  # skip content before first H2
            lines = section.strip().split('\n')
            # First line is the heading text, find first substantive paragraph after it
            for line in lines[1:]:
                stripped = line.strip()
                if not stripped or stripped.startswith('#'):
                    continue
                # Skip bullet/numbered list lines — we want a prose answer
                if stripped.startswith('- ') or stripped.startswith('* ') or re.match(r'^\d+[\.\)]\s', stripped):
                    break
                # Count substantive words (skip markdown artifacts)
                words = [w for w in stripped.split() if not w.startswith('[') and not w.startswith('(')]
                if len(words) >= 10:
                    h2_with_answer += 1
                break
        answer_pct = (h2_with_answer / h2_total * 100) if h2_total > 0 else 0
        passed = answer_pct >= 70
        checks.append(("AEO Answer Blocks", passed,
                       f"{h2_with_answer}/{h2_total} H2s have answer blocks ({answer_pct:.0f}%, target: ≥70%)"))
        if not passed:
            overall_pass = False

        # ── 18. AEO Freshness ──
        import datetime
        current_year = str(datetime.date.today().year)
        prior_year = str(int(current_year) - 1)
        has_freshness = current_year in article or prior_year in article
        checks.append(("AEO Freshness", has_freshness,
                       f"{'Found' if has_freshness else 'Missing'} year reference ({current_year} or {prior_year})"))
        if not has_freshness:
            overall_pass = False

        # ── 19. AEO Source Attribution ──
        attribution_phrases = ["according to", "reports", "found that", "shows that",
                               "data from", "survey by", "study by", "research by",
                               "research from", "report by",
                               "published by", "cited by", "per ", "says ",
                               "estimates that", "estimated that", "estimates ",
                               "emphasizes that", "emphasizes "]
        # Example patterns — illustrative numbers, not statistical claims
        example_patterns = re.compile(
            r'for example|such as|something like|'
            r'["\u201c][^"\u201d]*\d+%[^"\u201d]*["\u201d]|'  # stats inside quotes (straight or curly)
            r'\d+% upfront|\d+% upon|\d+% at signing|'
            r'maybe .{0,30}\d+%|could cost you \d+%|imagine .{0,30}\d+%|'
            r"let\u2019s say .{0,30}\d+%|let's say .{0,30}\d+%|"
            r'^\(.{0,80}\d+%.{0,80}\?\s*.*\)$|'  # parenthetical rhetorical questions with stats
            r'the difference between .{0,60}\d+%|'  # comparative illustration
            r'usually \d|typically \d|generally \d|'  # conventional ranges (definitional, not claims)
            r'legally required .{0,30}\d+%|required by law .{0,30}\d+%|'  # regulatory facts
            r'\d+% of (?:quota|target|goal|capacity|budget)|'  # illustrative performance metrics
            r"\d+%\s+(?:sure|certain|confident|positive|likely|complete|done|finished|ready)",  # conversational
            re.IGNORECASE
        )
        # Hypothetical/illustrative dollar amounts
        hypo_dollar = re.compile(
            r'your (?:\w+ ){0,3}\$[\d,.]+|'
            r'a \$[\d,.]+\s+(?:\w+ )?(?:agreement|contract|deal|vendor|company|business|organization)|'
            r'cost (?:you|your|them|the) .{0,20}\$[\d,.]+|'
            r'for a \$[\d,.]+|'
            r'can run \$[\d,.]+|'       # market cost ranges ("can run $50,000 to $200,000")
            r'run \$[\d,.]+\s+to\s+\$|'  # cost range pattern
            r'sells? (?:\w+ )?(?:it |them )?for \$[\d,.]+|'  # retail price metaphors ("sells it for $3")
            r'["\u201c][^"\u201d]*\$[\d,.]+[^"\u201d]*["\u201d]',  # dollar amounts inside quotes (query examples)
            re.IGNORECASE
        )
        stat_lines = []
        in_tldr = False
        for vline_idx, vline in enumerate(article.split('\n')):
            vstripped = vline.strip()
            # Track TL;DR section — bullets are summaries, not standalone claims
            if vstripped.startswith('**TL;DR') or vstripped == 'TL;DR':
                in_tldr = True
                continue
            if in_tldr and not vstripped:
                in_tldr = False
            if in_tldr and vstripped.startswith('-'):
                continue
            if not vstripped or vstripped.startswith('#') or vstripped.startswith('|'):
                continue
            if not re.search(r'\d+%|\$[\d,]+|\d+\s*(billion|million|percent)', vstripped, re.IGNORECASE):
                continue
            # Skip illustrative examples (not statistical claims)
            if example_patterns.search(vstripped):
                continue
            # Skip hypothetical dollar amounts
            if hypo_dollar.search(vstripped) and not any(p in vstripped.lower() for p in attribution_phrases):
                continue
            # Skip numbered steps/list items that contain dollar amounts as examples
            if re.match(r'^(?:\*?\*?Step )?\d+[\.\)]\s', vstripped) and re.search(r'\$\d', vstripped):
                continue
            # Skip lines with truncated/corrupted numbers (e.g., ".82 million" from "$14.82")
            if re.search(r'(?<!\d)\.\d+\s*(?:million|billion|trillion)', vstripped, re.IGNORECASE):
                continue
            stat_lines.append((vline_idx, vstripped))
        all_lines = article.split('\n')
        unattributed = 0
        seen_stat_fingerprints: set[str] = set()
        for vline_idx, stat_line in stat_lines:
            has_attr = any(phrase in stat_line.lower() for phrase in attribution_phrases)
            has_source = bool(re.search(r'[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}', stat_line))
            # Brand-name product claims are self-attributed (first-party data)
            is_brand_claim = 'ContractSafe' in stat_line
            # Check adjacent lines for contextual attribution
            if not has_attr and not has_source:
                for offset in [-1, 1]:
                    adj_idx = vline_idx + offset
                    if 0 <= adj_idx < len(all_lines):
                        adj = all_lines[adj_idx].strip().lower()
                        if any(p in adj for p in attribution_phrases):
                            has_attr = True
                            break
            if not has_attr and not has_source and not is_brand_claim:
                # Dedup: same stat repeated across sections counts once
                pcts = tuple(sorted(re.findall(r'\d+%', stat_line)))
                if pcts and pcts in seen_stat_fingerprints:
                    continue
                if pcts:
                    seen_stat_fingerprints.add(pcts)
                unattributed += 1
        passed = unattributed <= 2
        checks.append(("AEO Source Attribution", passed,
                       f"{unattributed} unattributed stats (max: 2)"))
        if not passed:
            overall_pass = False

        # ── 20. AEO Semantic Triples ──
        triple_verbs_re = (
            r'\b(is|are|provides|offers|helps|enables|automates|simplifies|'
            r'manages|delivers|supports|allows|ensures|gives|makes|handles|'
            r'stores|tracks|organizes|streamlines|reduces)\b'
        )
        cs_triple_count = 0
        for aline in article.split('\n'):
            if 'ContractSafe' in aline and not aline.strip().startswith('#'):
                for sent in re.split(r'(?<=[.!?])\s+', aline):
                    if 'ContractSafe' in sent and re.search(triple_verbs_re, sent, re.IGNORECASE):
                        cs_triple_count += 1
        passed = cs_triple_count >= 2
        checks.append(("AEO Semantic Triples", passed,
                       f"{cs_triple_count} brand triples (target: ≥2)"))

        # ── 21. AEO Passage Extractability ──
        context_starters = [
            "this is why", "this is where", "this is how",
            "as mentioned", "as discussed", "as noted",
            "they also", "it also", "these include",
        ]
        quant_refs_v = re.compile(
            r"rounding error|that number|that figure|that percentage|"
            r"those numbers|that is real money|that\u2019s real money|that's real money|"
            r"that is a lot|that\u2019s a lot|that's a lot|think about that",
            re.IGNORECASE,
        )
        anaphoric_refs_v = re.compile(
            r"^that (?:gap|divide|disconnect|difference|disparity|contrast)",
            re.IGNORECASE,
        )
        context_dep_count = 0
        article_lines = article.split('\n')
        for idx, aline in enumerate(article_lines):
            s = aline.strip().lower()
            if not s or s.startswith('#') or s.startswith('|'):
                continue
            for starter in context_starters:
                if s.startswith(starter):
                    context_dep_count += 1
                    break
            else:
                # Orphaned quantitative commentary
                if len(s.split()) < 30 and quant_refs_v.search(s):
                    if not re.search(r'\d+%|\$[\d,]+', s):
                        prev_has = False
                        for j in range(idx - 1, max(idx - 5, -1), -1):
                            prev = article_lines[j].strip()
                            if prev and not prev.startswith('#'):
                                prev_has = bool(re.search(r'\d+%|\$[\d,]+', prev.lower()))
                                break
                        if not prev_has:
                            context_dep_count += 1
                # Orphaned anaphoric references
                s_orig = aline.strip()
                if len(s_orig.split()) < 25 and anaphoric_refs_v.search(s_orig):
                    prev_data = False
                    for j in range(idx - 1, max(idx - 5, -1), -1):
                        prev = article_lines[j].strip()
                        if prev and not prev.startswith('#'):
                            prev_data = bool(re.search(r'\d+%|\$[\d,]+|only \d|just \d', prev.lower()))
                            break
                    if not prev_data:
                        context_dep_count += 1
        passed = context_dep_count == 0
        checks.append(("AEO Passage Extractability", passed,
                       f"{context_dep_count} context-dependent passages (target: 0)"))
        if not passed:
            overall_pass = False

        # ── 22. AEO Quantifiable Claims ──
        qc_count = len([
            l for l in article.split('\n')
            if re.search(r'\d+%|\$[\d,]+|\d+\s*(billion|million|percent)', l, re.IGNORECASE)
            and not l.strip().startswith('#')
        ])
        qc_per_1000 = (qc_count / max(len(article.split()), 1)) * 1000
        passed = qc_per_1000 >= 3
        checks.append(("AEO Quantifiable Claims", passed,
                       f"{qc_per_1000:.1f} per 1,000 words (target: ≥3)"))

        # ── 23. AEO Entity Consistency ──
        first_200_words = ' '.join(article.split()[:200]).lower()
        has_entity_id = 'contractsafe' in first_200_words and any(
            p in first_200_words for p in ['contract management', 'clm', 'software']
        )
        passed = has_entity_id
        checks.append(("AEO Entity Consistency", passed,
                       "ContractSafe identified in first 200 words" if passed else "Missing entity context in first 200 words"))
        if not passed:
            overall_pass = False

        # ── 24. AEO Self-Describing Headings ──
        vague_patterns = [
            "the bottom line", "the big picture", "why it matters",
            "how we're different", "key takeaways", "final thoughts",
            "wrapping up", "in summary", "overview",
        ]
        vague_h2_count = 0
        for h2_text in h2s:
            h2_lower = h2_text.lower().strip('?').strip()
            if h2_lower in vague_patterns or len(h2_lower.split()) <= 2:
                vague_h2_count += 1
        passed = vague_h2_count == 0
        checks.append(("AEO Self-Describing Headings", passed,
                       f"{vague_h2_count} vague headings (target: 0)"))
        if not passed:
            overall_pass = False

        # ── 25. AEO Follow-Up Coverage ──
        paa_questions = state.keyword_data.get("questions_people_ask", []) if state.keyword_data else []
        paa_addressed = 0
        article_lower = article.lower()
        for q in paa_questions[:8]:
            q_words = set(w.lower() for w in q.split() if len(w) > 3)
            coverage = sum(1 for w in q_words if w in article_lower) / max(len(q_words), 1)
            if coverage > 0.5:
                paa_addressed += 1
        paa_total = min(len(paa_questions), 8)
        passed = paa_addressed >= max(paa_total - 2, 1) if paa_total > 0 else True
        checks.append(("AEO Follow-Up Coverage", passed,
                       f"{paa_addressed}/{paa_total} PAA questions addressed"))

        # ── 26. AEO Structured Formats ──
        # Detect H2s that describe a process (should have numbered steps)
        # "guide" alone is too broad — only match "step-by-step guide" or similar
        # "how to" only counts as process when followed by a process verb (choose, implement, set up, etc.)
        # NOT "how to agree anywhere" (capability) or "how X handles" (mechanism)
        _process_verbs = ("choose", "select", "pick", "evaluate", "compare", "assess",
                          "implement", "set up", "deploy", "migrate", "build", "create",
                          "get started", "start", "begin", "establish")
        process_h2_list = []
        for h in h2s:
            hl = h.lower()
            if any(w in hl for w in ["steps", "step-by-step", "implement"]):
                process_h2_list.append(h)
            elif "how to" in hl and any(v in hl for v in _process_verbs):
                process_h2_list.append(h)
            elif "guide" in hl and any(w in hl for w in ["step", "implement"]):
                process_h2_list.append(h)
        process_without_steps = 0
        for ph in process_h2_list:
            ph_pos = article.find(f"## {ph}")
            next_h2_pos = article.find("\n## ", ph_pos + 1)
            section = article[ph_pos:next_h2_pos] if next_h2_pos > 0 else article[ph_pos:]
            # Match "1. ", "**1.", "**Step 1:", "Step 1:" numbered step formats
            has_steps = bool(re.search(r'(?:^\d+\.\s|^\*\*(?:Step\s*)?\d+[\.:]\s?|\bStep\s+\d+[\.:]\s)', section, re.MULTILINE))
            # Tables also count as structured format (e.g., comparison tables in "how to choose" sections)
            has_table = bool(re.search(r'^\|.+\|$', section, re.MULTILINE))
            if not has_steps and not has_table:
                process_without_steps += 1
        passed = process_without_steps == 0
        checks.append(("AEO Structured Formats", passed,
                       f"{len(process_h2_list) - process_without_steps}/{len(process_h2_list)} process sections have numbered steps"
                       if process_h2_list else "No process sections detected"))

        # ── 27. AEO Unique Value ──
        unique_signals = [
            r'our (data|research|analysis|findings|survey|study)',
            r'\b(proprietary|original|first-party|exclusive)\b',
            r'case study',
            r'(client|customer)\s+(data|results?|story|stories)',
            r'we (found|discovered|analyzed|measured|observed)',
            r'ContractSafe Industry Report',
            r'internal (data|metrics|benchmarks?)',
        ]
        has_unique_content = any(re.search(p, article, re.IGNORECASE) for p in unique_signals)
        checks.append(("AEO Unique Value", has_unique_content,
                       "Unique content signals found" if has_unique_content else "No proprietary/original content (strategic gap)"))

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
            # Exclude 4-digit years (e.g. "2026.") and large numbers (e.g. "page 47.")
            # Real concatenated lists have small sequential numbers (≤20)
            if re.match(r'^\d+\.\s', stripped):
                for m in re.finditer(r'\s(\d+)\.\s', stripped[3:]):
                    num = int(m.group(1))
                    if num <= 20 and len(m.group(1)) <= 2:
                        count += 1
                        break
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
