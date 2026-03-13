"""Agent 8: Brand Voice Pass - runs programmatic voice/style audit, then has Claude fix failures.

Uses delta mode: Claude returns find/replace pairs instead of the full article,
cutting output tokens by ~80%.
"""

from __future__ import annotations

import re
from agents.base import BaseAgent
from agents.knowledge_loader import load_brand_voice
from state import PipelineState
from prompts.templates import BRAND_VOICE_PASS_SYSTEM
from config import EDITING_MODEL


# Corporate phrases that signal B2B-speak instead of conversational voice
CORPORATE_PHRASES = [
    "leverage", "streamline", "drive efficiency", "optimize your",
    "maximize your", "empower your", "unlock the power", "best-in-class",
    "cutting-edge", "state-of-the-art", "synergy", "scalable solution",
    "robust platform", "seamless integration", "end-to-end", "holistic approach",
    "mission-critical", "paradigm shift", "value proposition", "pain point",
    "stakeholder", "actionable insights", "move the needle", "low-hanging fruit",
    "circle back", "deep dive", "take it to the next level",
    "digital transformation", "thought leader", "best practices",
    "key takeaway", "at the end of the day",
]

# Stiff transitions that should be conversational bridges
STIFF_TRANSITIONS = [
    "in conclusion", "furthermore", "additionally", "moreover",
    "consequently", "subsequently", "henceforth", "nevertheless",
    "notwithstanding", "in summary", "to summarize", "in essence",
    "it is important to note", "it should be noted", "it is worth noting",
    "as mentioned above", "as previously stated", "as discussed earlier",
    "in light of this", "with that being said", "that being said",
    "in today's", "in the modern",
]


# ── Module-level mechanical fix functions (used by Agent 8 AND Agent 13) ──


def _normalize_markdown(article: str) -> str:
    """Fix structurally broken markdown: collapsed tables, concatenated lists, broken links.

    This is a safety net that catches damage from any source — Claude's delta
    responses collapsing multi-line structures, or any other processing step.
    """
    article = _fix_single_line_tables(article)
    article = _fix_concatenated_bullets(article)
    article = _fix_concatenated_numbered_items(article)
    article = _fix_broken_links(article)
    article = _strip_trailing_social_copy(article)
    return article


def _fix_single_line_tables(text: str) -> str:
    """Split single-line markdown tables into proper multi-line format.

    When table rows are concatenated on one line, the boundary between rows
    is a space-only segment in split('|') output. Uses this to identify
    row boundaries and reconstruct multi-line tables.
    """
    lines = text.split('\n')
    result = []
    for line in lines:
        stripped = line.strip()
        # Only process lines that look like collapsed tables
        if '|---' not in stripped or stripped.count('|') <= 6:
            result.append(line)
            continue

        parts = stripped.split('|')
        # Group parts into rows — space-only parts are row boundaries
        rows = []
        current_cells = []
        for i, part in enumerate(parts):
            if part.strip() == '':
                # Row boundary (or leading/trailing empty from |)
                if current_cells:
                    rows.append('| ' + ' | '.join(c.strip() for c in current_cells) + ' |')
                    current_cells = []
            else:
                current_cells.append(part)
        if current_cells:
            rows.append('| ' + ' | '.join(c.strip() for c in current_cells) + ' |')

        if len(rows) > 1:
            result.extend(rows)
        else:
            result.append(line)
    return '\n'.join(result)


def _fix_concatenated_bullets(text: str) -> str:
    """Split concatenated bullet items onto separate lines.

    Detects patterns like '- item one - item two - item three' on a single line.
    """
    lines = text.split('\n')
    result = []
    for line in lines:
        stripped = line.strip()
        # Must start with - and contain at least one more - mid-line
        if not stripped.startswith('- ') or ' - ' not in stripped[2:]:
            result.append(line)
            continue
        # Don't split inside markdown links [text - with dash](url)
        # Temporarily protect link content
        protected = re.sub(r'\[([^\]]+)\]', lambda m: m.group(0).replace(' - ', ' \x00 '), stripped)
        if ' - ' not in protected[2:]:
            result.append(line)
            continue
        # Split at ' - ' boundaries (keeping the - prefix)
        parts = re.split(r'\s+(?=-\s)', protected)
        for part in parts:
            result.append(part.replace(' \x00 ', ' - '))
    return '\n'.join(result)


def _fix_concatenated_numbered_items(text: str) -> str:
    """Split concatenated numbered items onto separate lines.

    Detects patterns like '1. item 2. item 3. item' on a single line.
    """
    lines = text.split('\n')
    result = []
    for line in lines:
        stripped = line.strip()
        if not re.match(r'^\d+\.\s', stripped):
            result.append(line)
            continue
        # Check for multiple numbered items on same line
        # Pattern: digit(s) followed by . and space, appearing 2+ times
        items = re.split(r'\s+(?=\d+\.\s)', stripped)
        if len(items) <= 1:
            result.append(line)
            continue
        result.extend(items)
    return '\n'.join(result)


def _fix_broken_links(text: str) -> str:
    """Rejoin markdown links broken across lines.

    Detects [text at end of line with ](url) on a subsequent line,
    even across blank lines.
    """
    lines = text.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Check if line has an unclosed [ without matching ](
        if '[' in line:
            last_bracket = line.rfind('[')
            after_bracket = line[last_bracket:]
            if '](' not in after_bracket and ']' not in after_bracket:
                # Look ahead up to 3 lines (skipping blanks) for the closing ](url)
                for lookahead in range(1, 4):
                    if i + lookahead >= len(lines):
                        break
                    next_line = lines[i + lookahead].strip()
                    if not next_line:
                        continue  # skip blank lines
                    if re.match(r'^[^\[]*\]\(', next_line):
                        # Found the continuation — join all lines between
                        joined = line.rstrip()
                        for k in range(1, lookahead + 1):
                            part = lines[i + k].strip()
                            if part:
                                joined += ' ' + part
                        result.append(joined)
                        i += lookahead + 1
                        break
                else:
                    result.append(line)
                    i += 1
                continue
        result.append(line)
        i += 1
    return '\n'.join(result)


def _strip_trailing_social_copy(article: str) -> str:
    """Remove social copy / meta description appended after the article content.

    Looks for --- separators where the NEXT non-empty line contains social copy
    markers (LinkedIn, Twitter, Meta Description). Cuts at the earliest such separator.
    """
    lines = article.split('\n')
    social_markers = ['linkedin post', 'twitter post', 'x/twitter post',
                      'meta description', 'seo meta', 'social post']
    # Scan backward, cut at the earliest --- with social markers immediately after
    earliest_cut = None
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].strip() == '---':
            # Check next 3 non-empty lines for social markers
            next_content = ''
            for j in range(idx + 1, min(idx + 4, len(lines))):
                if lines[j].strip():
                    next_content = lines[j].strip().lower()
                    break
            if any(marker in next_content for marker in social_markers):
                earliest_cut = idx
    if earliest_cut is not None:
        return '\n'.join(lines[:earliest_cut]).strip()
    return article


def apply_mechanical_fixes(article: str) -> str:
    """Apply guaranteed-correct programmatic fixes that don't need Claude.

    This function is called by both Agent 8 (Brand Voice Pass) and Agent 13
    (Final Validator) to ensure dashes, long paragraphs, and quotes are always
    fixed, even if Agents 10/11 reintroduce them via FIND/REPLACE changes.
    """
    # ── 0. Normalize broken markdown structure FIRST ──
    article = _normalize_markdown(article)

    # ── 1. Replace em/en dashes ──
    article = article.replace("\u2014", ", ")
    article = article.replace("\u2013", ", ")
    article = re.sub(r'([.!?])\s*,\s*', r'\1 ', article)
    article = re.sub(r'^\s*,\s*', '', article, flags=re.MULTILINE)
    article = re.sub(r',\s*,', ',', article)

    # ── 2. Straight quotes → curly quotes ──
    article = _curl_quotes(article)

    # ── 3. Split long paragraphs at sentence boundaries ──
    article = _split_long_paragraphs(article)

    # ── 4. Clean up whitespace ──
    article = re.sub(r'  +', ' ', article)
    return article


def _curl_quotes(text: str) -> str:
    """Replace straight double quotes with curly quotes."""
    result = []
    in_code = False
    in_link = False
    open_quote = True  # next quote should be opening
    for i, ch in enumerate(text):
        if ch == '`':
            in_code = not in_code
            result.append(ch)
        elif ch == '[':
            in_link = True
            result.append(ch)
        elif ch == ')' and in_link:
            in_link = False
            result.append(ch)
        elif ch == '"' and not in_code and not in_link:
            if open_quote:
                result.append('\u201c')
            else:
                result.append('\u201d')
            open_quote = not open_quote
        else:
            result.append(ch)
    return ''.join(result)


def _chunk_by_limit(parts: list, limit: int, rejoin: str = " ") -> list:
    """Group parts into chunks that are each <= limit words."""
    chunks = []
    chunk = []
    chunk_words = 0
    for part in parts:
        part_words = len(part.split())
        if chunk_words + part_words > limit and chunk:
            chunks.append(rejoin.join(chunk))
            chunk = [part]
            chunk_words = part_words
        else:
            chunk.append(part)
            chunk_words += part_words
    if chunk:
        chunks.append(rejoin.join(chunk))
    return chunks


def _split_long_paragraphs(article: str) -> str:
    """Split prose paragraphs over 42 words at sentence boundaries.
    Skips tables, bullet lists, and numbered lists — those are structural."""
    lines = article.split("\n")
    result = []
    current_para = []
    current_para_lines = []

    def _is_structural(line: str) -> bool:
        """Lines that shouldn't be merged into paragraphs."""
        s = line.strip()
        return (s.startswith("|") or s.startswith("- ") or s.startswith("* ")
                or bool(re.match(r'^\d+[\.\)]\s', s)))

    def flush_para():
        if not current_para:
            return
        para_text = " ".join(current_para)
        word_count = len(para_text.split())
        if word_count <= 42:
            result.extend(current_para_lines)
        else:
            # First pass: split at sentence boundaries
            # Protect .!? inside markdown links [...] from triggering splits
            protected = re.sub(
                r'\[([^\]]+)\]',
                lambda m: m.group(0).replace('.', '\x00').replace('!', '\x01').replace('?', '\x02'),
                para_text
            )
            sentences = re.split(r'(?<=[.!?])\s+', protected)
            sentences = [s.replace('\x00', '.').replace('\x01', '!').replace('\x02', '?') for s in sentences]
            chunks = _chunk_by_limit(sentences, 42)
            # Second pass: if any chunk is still >42 words, split at commas
            final_chunks = []
            for chunk_text in chunks:
                if len(chunk_text.split()) > 42:
                    parts = re.split(r',\s+', chunk_text)
                    final_chunks.extend(_chunk_by_limit(parts, 42, rejoin=", "))
                else:
                    final_chunks.append(chunk_text)
            for i, c in enumerate(final_chunks):
                result.append(c)
                if i < len(final_chunks) - 1:
                    result.append("")  # blank line between splits

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped or _is_structural(stripped):
            flush_para()
            current_para = []
            current_para_lines = []
            result.append(line)
        else:
            current_para.append(stripped)
            current_para_lines.append(line)

    flush_para()
    return "\n".join(result)


class BrandVoicePassAgent(BaseAgent):
    name = "Brand Voice Pass"
    description = "Audit article for voice/style issues and fix them"
    agent_number = 8
    model = EDITING_MODEL      # Sonnet — this is fix-specific-issues work, not creative writing
    timeout = 120              # 2 min — mechanical fixes are instant, Claude only handles creative issues
    emoji = "\U0001f3a4"

    def run(self, state: PipelineState) -> PipelineState:
        article = state.draft_article

        # ── Step 1: Apply all mechanical fixes FIRST (instant, no Claude) ──
        self.progress("Applying mechanical fixes (dashes, quotes, paragraphs)...")
        article = apply_mechanical_fixes(article)
        mechanical_count = self._count_mechanical_fixes(state.draft_article, article)
        self.progress(f"Applied {mechanical_count} mechanical fixes programmatically")

        # ── Step 2: Audit what's left (only creative issues remain) ──
        self.progress("Auditing for creative voice issues...")
        audit = self._audit(article)
        issues = audit["issues"]

        self.progress(f"Found {len(issues)} creative issues for Claude to fix")
        for issue in issues:
            self.progress(f"  - {issue}")

        # ── Step 3: If creative issues remain, send to Claude (Sonnet, small prompt) ──
        if issues:
            issue_list = "\n".join(f"{i+1}. {issue}" for i, issue in enumerate(issues))
            brand_voice = load_brand_voice()

            user_prompt = f"""## BRAND VOICE REFERENCE
{brand_voice}

## ISSUES TO FIX
{issue_list}

## ARTICLE
===ARTICLE_START===
{article}
===ARTICLE_END==="""

            self.progress("Having Claude fix creative voice issues (delta mode)...")
            response = self.call_llm(BRAND_VOICE_PASS_SYSTEM, user_prompt)

            changes = self.parse_delta_response(response)
            article = self.apply_delta_changes(article, changes)
            state.voice_issues_found = [{"change": c["find"][:50], "fix": c["replace"][:50]} for c in changes]
            self.log(f"Applied {mechanical_count} mechanical + {len(changes)} creative fixes.")
        else:
            state.voice_issues_found = []
            self.log(f"Applied {mechanical_count} mechanical fixes. No creative issues found.")

        state.voice_pass_article = article
        return state

    def _count_mechanical_fixes(self, before: str, after: str) -> int:
        """Count approximate number of mechanical changes made."""
        count = 0
        count += before.count("\u2014") + before.count("\u2013")  # dashes removed
        count += before.count('"') - after.count('"')  # straight quotes replaced
        # Rough paragraph split count
        count += after.count("\n\n") - before.count("\n\n")
        return max(0, count)

    # ── Programmatic voice/style audit ──

    def _audit(self, article: str) -> dict:
        """Audit for creative voice issues only. Mechanical issues (dashes, quotes,
        paragraph length) are already handled by _apply_mechanical_fixes()."""
        issues = []
        report_lines = []
        lines = article.split("\n")
        text_lower = article.lower()

        # ── 1. Corporate/B2B phrases ──
        found_corporate = []
        for phrase in CORPORATE_PHRASES:
            if phrase in text_lower:
                count = text_lower.count(phrase)
                found_corporate.append(f"'{phrase}' x{count}")

        if found_corporate:
            issues.append(
                f"CORPORATE/B2B PHRASES FOUND: {', '.join(found_corporate)}. "
                f"Replace each with specific, conversational language. "
                f"Don't say 'leverage our platform', say what it actually does. "
                f"Don't say 'streamline your workflow', describe the actual experience."
            )
        report_lines.append(f"Corporate phrases: {len(found_corporate)} types found (target: 0)")

        # ── 5. Stiff transitions ──
        found_transitions = []
        for trans in STIFF_TRANSITIONS:
            matches = [(i + 1, line.strip()[:60]) for i, line in enumerate(lines)
                       if trans in line.lower()]
            if matches:
                found_transitions.append((trans, matches[0]))

        if found_transitions:
            examples = [f"'{t}' (line {m[0]})" for t, m in found_transitions[:5]]
            issues.append(
                f"STIFF TRANSITIONS FOUND: {', '.join(examples)}. "
                f"Replace with conversational bridges: 'Here\u2019s the thing though,' 'But wait,' "
                f"'Which brings us to,' 'And honestly,' etc."
            )
        report_lines.append(f"Stiff transitions: {len(found_transitions)} found (target: 0)")

        # ── 6. "Definitions at a Glance" sections (FORBIDDEN) ──
        has_definitions_section = bool(re.search(
            r'definitions?\s+at\s+a\s+glance', text_lower
        ))
        if has_definitions_section:
            issues.append(
                "FORBIDDEN 'DEFINITIONS AT A GLANCE' SECTION FOUND. "
                "Remove it entirely. Definitions should be woven into the narrative, "
                "not presented as a glossary block."
            )
        report_lines.append(f"Definitions at a Glance: {'FOUND (forbidden)' if has_definitions_section else 'none'}")

        # ── 7. Robotic link formatting ──
        according_to_links = re.findall(r'[Aa]ccording to \[', article)
        paren_links = re.findall(r'\(\[.+?\]\(.+?\)\)', article)
        generic_anchors = re.findall(
            r'\[(click here|learn more|read more|check out|here|this link|this article|this resource)\]',
            article, re.IGNORECASE
        )
        naked_urls = []
        for line in lines:
            found = re.findall(r'(?<!\()(https?://\S+)(?!\))', line)
            for url in found:
                if f"]({url}" not in article:
                    naked_urls.append(url)

        link_issues = []
        if according_to_links:
            link_issues.append(f"'According to [Source]' pattern: {len(according_to_links)} instances")
        if paren_links:
            link_issues.append(f"Parenthetical links: {len(paren_links)} instances")
        if generic_anchors:
            link_issues.append(f"Generic anchor text: {[m for m in generic_anchors]}")
        if naked_urls:
            link_issues.append(f"Naked URLs: {naked_urls[:3]}")

        if link_issues:
            issues.append(
                f"ROBOTIC LINK FORMATTING: {'; '.join(link_issues)}. "
                f"Links must be woven organically into sentences. "
                f"WRONG: 'According to [Source](url)...' "
                f"RIGHT: 'The [World Commerce & Contracting report](url) found that...'"
            )
        report_lines.append(f"Link formatting issues: {len(link_issues)} types found")

        # ── 8. Sentence variety check ──
        sentences = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for sent in re.split(r'[.!?]+', stripped):
                sent = sent.strip()
                if len(sent) > 10:
                    sentences.append(sent)

        if len(sentences) >= 3:
            repetitive_starts = []
            for i in range(len(sentences) - 2):
                words_a = sentences[i].split()[:2]
                words_b = sentences[i + 1].split()[:2]
                words_c = sentences[i + 2].split()[:2]
                if words_a and words_b and words_c:
                    start_a = words_a[0].lower()
                    start_b = words_b[0].lower()
                    start_c = words_c[0].lower()
                    if start_a == start_b == start_c:
                        repetitive_starts.append(f"3+ sentences starting with '{start_a}' near: '{sentences[i][:40]}...'")

            if repetitive_starts:
                issues.append(
                    f"REPETITIVE SENTENCE STARTS: {len(repetitive_starts)} clusters found. "
                    f"Vary sentence openings for a natural, conversational rhythm. "
                    f"Examples: {repetitive_starts[:2]}"
                )
            report_lines.append(f"Repetitive sentence starts: {len(repetitive_starts)} clusters")
        else:
            report_lines.append("Repetitive sentence starts: not enough sentences to check")

        # ── 9. Passive voice density ──
        passive_patterns = [
            r'\b(?:is|are|was|were|been|being)\s+(?:\w+ed|written|done|made|given|taken|found|known|seen|shown)\b',
        ]
        passive_count = 0
        for pattern in passive_patterns:
            passive_count += len(re.findall(pattern, article, re.IGNORECASE))

        total_sentences = len(sentences) if sentences else 1
        passive_pct = (passive_count / total_sentences * 100) if total_sentences else 0
        if passive_pct > 20:
            issues.append(
                f"HIGH PASSIVE VOICE: ~{passive_count} passive constructions in ~{total_sentences} sentences ({passive_pct:.0f}%). "
                f"Target: under 20%. Rewrite passive sentences to active voice for a more direct, conversational tone."
            )
        report_lines.append(f"Passive voice: ~{passive_count}/{total_sentences} sentences ({passive_pct:.0f}%, target: <20%)")

        # ── 10. Exclamation mark overuse ──
        exclamation_count = article.count("!")
        if exclamation_count > 2:
            issues.append(
                f"EXCLAMATION MARK OVERUSE: {exclamation_count} found. "
                f"Use at most 1-2 in the entire article. The voice is calm and conversational, "
                f"not enthusiastic or salesy."
            )
        report_lines.append(f"Exclamation marks: {exclamation_count} (target: 0-2)")

        report = "\n".join(report_lines)
        return {"issues": issues, "report": report}
