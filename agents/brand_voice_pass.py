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
    # Step 0: Rejoin lines broken mid-sentence BEFORE splitting lists
    article = _rejoin_broken_continuations(article)
    article = _fix_single_line_tables(article)
    article = _fix_concatenated_bullets(article)
    article = _fix_concatenated_numbered_items(article)
    article = _fix_broken_links(article)
    article = _merge_bullet_orphans(article)
    article = _strip_trailing_social_copy(article)
    return article


def _rejoin_broken_continuations(text: str) -> str:
    """Rejoin lines that were broken mid-sentence.

    Detects when a line doesn't end with sentence-ending punctuation and the
    next non-blank line starts with a lowercase word (indicating it was split
    from the previous line). Joins them back so downstream list/link fixers
    see complete text.
    """
    lines = text.split('\n')
    result = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Skip empty, structural, or very short lines
        if (not stripped or stripped.startswith('#') or stripped == '---'
                or stripped.startswith('|') or stripped.startswith('>')):
            result.append(lines[i])
            i += 1
            continue

        # Check if line doesn't end with sentence-ending punctuation
        if stripped[-1] not in '.!?:':
            # Look for continuation on next non-blank line
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1

            if j < len(lines):
                next_s = lines[j].strip()
                # Continuation: starts with lowercase, isn't structural
                if (next_s and next_s[0].islower()
                        and not next_s.startswith('---')):
                    # Join, skip blank lines in between
                    result.append(stripped + ' ' + next_s)
                    i = j + 1
                    continue

        result.append(lines[i])
        i += 1
    return '\n'.join(result)


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

    Handles two patterns:
    1. '- item one - item two - item three' on a single line
    2. '**Heading:** - item one - item two' (inline bullets after bold heading)
    """
    lines = text.split('\n')
    result = []
    for line in lines:
        stripped = line.strip()

        # Pattern 1: Line starts with '- ' and has more items
        if stripped.startswith('- ') and ' - ' in stripped[2:]:
            # Protect link content from splitting
            protected = re.sub(r'\[([^\]]+)\]', lambda m: m.group(0).replace(' - ', ' \x00 '), stripped)
            if ' - ' in protected[2:]:
                parts = re.split(r'\s+(?=-\s)', protected)
                for part in parts:
                    result.append(part.replace(' \x00 ', ' - '))
                continue

        # Pattern 2: Line has inline bullets after other content (e.g., bold heading)
        # Detect: text followed by ' - item - item' pattern
        if not stripped.startswith('- ') and ' - ' in stripped:
            # Protect link content
            protected = re.sub(r'\[([^\]]+)\]', lambda m: m.group(0).replace(' - ', ' \x00 '), stripped)
            # Find first ' - ' that starts a bullet list pattern
            # Must have at least 2 ' - ' patterns to indicate a list
            dash_positions = [m.start() for m in re.finditer(r' - \S', protected)]

            if len(dash_positions) >= 2:
                # Multiple dashes — clearly a concatenated list
                first_dash = dash_positions[0]
                heading = stripped[:first_dash].rstrip()
                bullet_text = stripped[first_dash:].strip()
                bullet_text = bullet_text.replace(' \x00 ', ' - ')
                result.append(heading)
                bullet_protected = re.sub(r'\[([^\]]+)\]', lambda m: m.group(0).replace(' - ', ' \x00 '), bullet_text)
                parts = re.split(r'\s+(?=-\s)', bullet_protected)
                for part in parts:
                    result.append(part.replace(' \x00 ', ' - '))
                continue

            if len(dash_positions) == 1:
                # Single dash — check if it's a bullet after end-of-sentence
                # Pattern: "...sentence end. - **Bold text" or "...end. - Text"
                dash_pos = dash_positions[0]
                before = protected[:dash_pos].rstrip()
                if before and before[-1] in '.!?':
                    result.append(stripped[:dash_pos].rstrip())
                    result.append(stripped[dash_pos:].strip())
                    continue

        result.append(line)
    return '\n'.join(result)


def _fix_concatenated_numbered_items(text: str) -> str:
    """Reconstruct broken numbered lists onto one-item-per-line format.

    Claude sometimes generates numbered lists as flowing prose that gets
    broken across lines by downstream processing. This function:
    1. Detects numbered list blocks (starting from '1.')
    2. Collects all lines belonging to the block
    3. Joins them into one string
    4. Splits at sequential number boundaries (1., 2., 3., ...)
    5. Outputs each item on its own line
    """
    lines = text.split('\n')
    result = []
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        # Only trigger on the start of a numbered list (must begin with "1.")
        if not re.match(r'^1[\.\)]\s', stripped):
            result.append(lines[i])
            i += 1
            continue

        # Collect all lines belonging to this numbered list block
        block_lines = [stripped]
        j = i + 1

        while j < len(lines):
            s = lines[j].strip()

            # Clear terminators: headings, horizontal rules, tables, bullets
            if s.startswith('#') or s == '---':
                break
            if s.startswith('- ') or s.startswith('* '):
                break
            if s.startswith('|') and '|' in s[1:]:
                break

            # Blank line: check if list continues after it
            if not s:
                peek = j + 1
                while peek < len(lines) and not lines[peek].strip():
                    peek += 1
                if peek >= len(lines):
                    break
                peek_s = lines[peek].strip()
                has_nums = bool(re.search(r'(?:^|\s)\d+[\.\)]\s', peek_s))
                prev_orphaned = bool(
                    block_lines and re.search(r'(?:^|\s)\d+[\.\)]\s*$', block_lines[-1]))
                if has_nums or prev_orphaned:
                    j += 1
                    continue
                # Bold text after orphaned number
                if re.match(r'^\*\*', peek_s) and prev_orphaned:
                    j += 1
                    continue
                break

            # Content line: check if it belongs to the list
            has_nums = bool(re.search(r'(?:^|\s)\d+[\.\)]\s', s))
            prev_orphaned = bool(
                block_lines and re.search(r'(?:^|\s)\d+[\.\)]\s*$', block_lines[-1]))

            if has_nums:
                block_lines.append(s)
                j += 1
            elif prev_orphaned:
                # Content for an orphaned number at end of previous line
                block_lines.append(s)
                j += 1
            elif re.match(r'^\*\*', s) and prev_orphaned:
                block_lines.append(s)
                j += 1
            elif re.match(r'^["\u201c]', s) and block_lines:
                # Quoted text (e.g., example in a step) — include if in list context
                block_lines.append(s)
                j += 1
            else:
                break

        # Join block into one string
        block_text = ' '.join(block_lines)

        # Find sequential number boundaries and split there
        num_matches = list(re.finditer(r'(?:^|(?<=\s))(\d+)[\.\)]\s', block_text))
        expected = 1
        split_positions = []
        for m in num_matches:
            num = int(m.group(1))
            if num == expected:
                split_positions.append(m.start())
                expected += 1

        if split_positions:
            for k, pos in enumerate(split_positions):
                end = split_positions[k + 1] if k + 1 < len(split_positions) else len(block_text)
                item = block_text[pos:end].strip()
                if item:
                    result.append(item)
        else:
            # Couldn't find sequential numbers — keep original lines
            result.extend(block_lines)

        i = j

    return '\n'.join(result)


def _merge_bullet_orphans(text: str) -> str:
    """Merge standalone text lines sandwiched between bullet items into the previous bullet.

    When a bullet item's text gets split across lines, the continuation can
    appear as a standalone paragraph between two bullets. This breaks list
    rendering in the DOCX. Detect and merge these orphans.
    """
    lines = text.split('\n')
    result = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Skip blank, structural, bullets, numbered items, and bold headings
        if (not stripped or stripped.startswith('- ') or stripped.startswith('* ')
                or stripped.startswith('#') or stripped == '---'
                or stripped.startswith('|') or stripped.startswith('>')
                or re.match(r'^\d+[\.\)]\s', stripped)
                or stripped.startswith('**')):
            result.append(line)
            continue

        # This is a non-structural text line. Check if it's between bullets.
        prev_is_bullet = False
        prev_idx = None
        for k in range(len(result) - 1, -1, -1):
            if result[k].strip():
                if result[k].strip().startswith('- ') or result[k].strip().startswith('* '):
                    prev_is_bullet = True
                    prev_idx = k
                break

        next_is_bullet = False
        for j in range(i + 1, min(i + 4, len(lines))):
            ns = lines[j].strip()
            if ns:
                next_is_bullet = ns.startswith('- ') or ns.startswith('* ')
                break

        if prev_is_bullet and next_is_bullet and prev_idx is not None:
            # Merge with previous bullet, removing any blank lines between
            result[prev_idx] = result[prev_idx].rstrip() + ' ' + stripped
            while len(result) > prev_idx + 1 and not result[-1].strip():
                result.pop()
            continue

        result.append(line)

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
            # Split at sentence boundaries only
            # Protect .!? inside markdown links [...] from triggering splits
            protected = re.sub(
                r'\[([^\]]+)\]',
                lambda m: m.group(0).replace('.', '\x00').replace('!', '\x01').replace('?', '\x02'),
                para_text
            )
            sentences = re.split(r'(?<=[.!?])\s+', protected)
            sentences = [s.replace('\x00', '.').replace('\x01', '!').replace('\x02', '?') for s in sentences]
            chunks = _chunk_by_limit(sentences, 42)
            # If a single sentence is over 42 words, leave it intact.
            # Splitting at commas creates broken fragments that look worse
            # than a slightly long paragraph.
            for i, c in enumerate(chunks):
                result.append(c)
                if i < len(chunks) - 1:
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

        # ── 8. Intro length check ──
        # Intro is everything before the first H2 (excluding H1 and TL;DR)
        intro_words = self._count_intro_words(article)
        if intro_words > 200:
            issues.append(
                f"INTRO TOO LONG: {intro_words} words before first H2 (target: under 150). "
                f"The opening should be 3-5 short paragraphs that set up the metaphor and bridge "
                f"to the topic. Move detailed content into the body sections."
            )
        report_lines.append(f"Intro length: {intro_words} words (target: <150)")

        # ── 9. Conversational markers (positive check) ──
        conversational_markers = [
            "here's the thing", "the part most people", "which brings us",
            "and honestly", "but wait", "here's where", "here's what",
            "the interesting part", "worth knowing", "the short version",
            "look,", "the thing is", "turns out", "funny enough",
            "not quite", "kind of", "sort of", "basically",
            "the real question", "what most people", "nobody talks about",
        ]
        marker_count = sum(1 for m in conversational_markers if m in text_lower)
        if marker_count < 3:
            issues.append(
                f"LOW CONVERSATIONAL MARKER DENSITY: Only {marker_count} conversational bridges "
                f"found (target: 3+). Add phrases like 'Here's the thing,' 'The part most people "
                f"get wrong,' 'Which brings us to,' 'And honestly,' etc. These make the voice "
                f"feel like conversation, not a report."
            )
        report_lines.append(f"Conversational markers: {marker_count} found (target: 3+)")

        # ── 10. Parenthetical asides (positive check) ──
        paren_asides = re.findall(r'\([^)]{10,}[^)]*\)', article)
        # Filter out markdown links and pure citations
        paren_asides = [p for p in paren_asides if not p.startswith('(http') and '](http' not in p]
        if len(paren_asides) < 2:
            issues.append(
                f"LOW PARENTHETICAL ASIDE COUNT: Only {len(paren_asides)} found (target: 2+). "
                f"Parenthetical asides add personality. Examples: '(which is the key distinction, "
                f"and the one that trips people up)', '(spoiler: it does)', '(cycle?)'."
            )
        report_lines.append(f"Parenthetical asides: {len(paren_asides)} found (target: 2+)")

        # ── 11. Reader questions (positive check) ──
        question_count = len(re.findall(r'[^#\|]\?', article))  # exclude heading ? and table ?
        if question_count < 2:
            issues.append(
                f"LOW READER ENGAGEMENT: Only {question_count} questions to the reader (target: 2+). "
                f"Ask the reader questions to create dialogue. Examples: 'Ever tried to...?', "
                f"'Why does that matter?', 'What happens when...?'"
            )
        report_lines.append(f"Reader questions: {question_count} found (target: 2+)")

        # ── 13. Sentence variety check ──
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

        # ── 14. Passive voice density ──
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

        # ── 15. Exclamation mark overuse ──
        exclamation_count = article.count("!")
        if exclamation_count > 5:
            issues.append(
                f"EXCLAMATION MARK OVERUSE: {exclamation_count} found. "
                f"Use at most 3-5 in the entire article. A few enthusiastic moments are "
                f"fine, but too many feels salesy."
            )
        report_lines.append(f"Exclamation marks: {exclamation_count} (target: 0-5)")

        report = "\n".join(report_lines)
        return {"issues": issues, "report": report}

    @staticmethod
    def _count_intro_words(article: str) -> int:
        """Count words in the intro (before the first H2, excluding H1 and TL;DR header)."""
        lines = article.split("\n")
        intro_words = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## "):
                break  # First H2 = end of intro
            if stripped.startswith("# "):
                continue  # Skip H1
            if stripped.startswith("**TL;DR"):
                continue  # Skip TL;DR header
            if stripped.startswith("---"):
                continue  # Skip horizontal rules
            if stripped:
                intro_words.extend(stripped.split())
        return len(intro_words)
