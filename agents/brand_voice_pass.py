"""Agent 8: Brand Voice Pass - runs programmatic voice/style audit, then has Claude fix failures.

Uses delta mode: Claude returns find/replace pairs instead of the full article,
cutting output tokens by ~80%.
"""

from __future__ import annotations

import re
from agents.base import BaseAgent
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
    "digital transformation", "thought leader",
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


def _split_list_item_trailing_prose(text: str) -> str:
    """Split list items that have a prose paragraph appended on the same line.

    Detects patterns like:
      8. **Item text** (parenthetical) Next paragraph starts here.
      - Bullet item content. Next paragraph starts here.
    And splits them into separate lines.
    """
    lines = text.split('\n')
    result = []
    for line in lines:
        stripped = line.strip()
        # Match numbered or bullet list items
        if not re.match(r'^(?:\d+[\.\)]\s|[-*]\s)', stripped):
            result.append(line)
            continue
        # Look for a closing pattern followed by a capital letter starting a new sentence
        # Patterns: ") Capital", ".) Capital", ".**) Capital", ".** Capital"
        m = re.search(r'(\)|\*\*\)?|\.)\s{1,3}([A-Z][a-z])', stripped)
        if not m:
            result.append(line)
            continue
        # Make sure the split point is past the first 30 chars (not splitting a short item)
        if m.start() < 30:
            result.append(line)
            continue
        # Check that the part before looks like a complete list item
        # and the part after looks like a prose sentence (not another list marker)
        after = stripped[m.start() + len(m.group(1)):].strip()
        if after and not re.match(r'^[-*]\s|^\d+[\.\)]\s', after):
            before = stripped[:m.start() + len(m.group(1))]
            result.append(before)
            result.append('')
            result.append(after)
            continue
        result.append(line)
    return '\n'.join(result)


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
    article = _split_list_item_trailing_prose(article)
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
    # Handle " — " (with surrounding spaces) first to avoid double-space artifacts
    # Protect numeric ranges (e.g. "55–70%") before replacing dashes
    article = re.sub(r'(\d)\s*\u2014\s*(\d)', r'\1-\2', article)  # em-dash between digits → hyphen
    article = re.sub(r'(\d)\s*\u2013\s*(\d)', r'\1-\2', article)  # en-dash between digits → hyphen
    article = re.sub(r'\s*\u2014\s*', ', ', article)
    article = re.sub(r'\s*\u2013\s*', ', ', article)
    article = re.sub(r'([.!?])\s*,\s*', r'\1 ', article)
    article = re.sub(r'^\s*,\s*', '', article, flags=re.MULTILINE)
    article = re.sub(r',\s*,', ',', article)

    # ── 2. Strip "according to [truncated source]" artifacts ──
    # The writer sometimes generates raw source citations like
    # ", according to Contract Change Management Tactics That Actually W."
    # These are truncated research titles that leaked into the output.
    article = _strip_source_artifacts(article)

    # ── 2b. Strip lines with truncated words from garbled research data ──
    article = _strip_truncated_lines(article)

    # ── 2c. Remove empty parentheses left by citation removal ──
    article = re.sub(r'\s*\(\s*\)', '', article)

    # ── 3. Normalize Unicode to ASCII ──
    # Fix literal \uXXXX escape sequences the LLM sometimes outputs
    article = re.sub(
        r'\\u([0-9a-fA-F]{4})',
        lambda m: chr(int(m.group(1), 16)),
        article,
    )
    # Replace curly/smart quotes with straight equivalents
    article = article.replace('\u2018', "'")   # left single quote
    article = article.replace('\u2019', "'")   # right single quote / apostrophe
    article = article.replace('\u201c', '"')   # left double quote
    article = article.replace('\u201d', '"')   # right double quote
    article = article.replace('\u2713', '*')   # checkmark → asterisk
    article = article.replace('\u2026', '...')  # ellipsis
    article = article.replace('\u00a0', ' ')   # non-breaking space

    # ── 3c. Close unmatched parentheses ──
    # Writer sometimes produces "(text without closing paren."
    fixed_paren_lines = []
    for pline in article.split("\n"):
        open_parens = pline.count('(')
        close_parens = pline.count(')')
        if open_parens > close_parens:
            # Find the last unmatched opener, close before the period at line end
            stripped = pline.rstrip()
            if stripped.endswith('.'):
                pline = stripped[:-1] + ').'
            elif stripped.endswith('?') or stripped.endswith('!'):
                pline = stripped[:-1] + ')' + stripped[-1]
            else:
                pline = stripped + ')'
        fixed_paren_lines.append(pline)
    article = "\n".join(fixed_paren_lines)

    # ── 4. Split long paragraphs at sentence boundaries ──
    article = _split_long_paragraphs(article)

    # ── 5. Fix broken markdown links ──
    # Remove orphaned [ that have no matching ] on the same line
    # e.g., "World Commerce & [Contracting." → "World Commerce & Contracting."
    # Must not touch valid links like [text, more text](url)
    fixed_lines = []
    for mline in article.split("\n"):
        result = list(mline)
        opens = [i for i, c in enumerate(mline) if c == '[']
        for start in opens:
            close = mline.find(']', start + 1)
            if close == -1:
                result[start] = ''  # Remove orphaned [
        fixed_lines.append(''.join(result))
    article = "\n".join(fixed_lines)

    # ── 5b. Rejoin sentences broken across paragraph boundaries ──
    # Pattern: line A ends mid-sentence, blank line, line B starts lowercase
    # e.g., "...latest redline"?\n\nis another hour..."
    rejoin_lines = article.split("\n")
    i = 0
    while i < len(rejoin_lines) - 2:
        cur = rejoin_lines[i].strip()
        # Check for blank line followed by lowercase continuation
        if cur and rejoin_lines[i + 1].strip() == "":
            next_content = rejoin_lines[i + 2].strip() if i + 2 < len(rejoin_lines) else ""
            if next_content and next_content[0].islower():
                # Rejoin: merge next_content onto current line
                rejoin_lines[i] = rejoin_lines[i].rstrip() + " " + next_content
                rejoin_lines[i + 1] = ""
                rejoin_lines[i + 2] = ""
        i += 1
    article = "\n".join(rejoin_lines)

    # ── 6. Fix punctuation artifacts ──
    # ":. " → colon followed by period (from content removal)
    article = article.replace(':. ', ': ')
    article = article.replace(':.\n', ':\n')
    # ".." → double period (from content insertion/removal)
    article = re.sub(r'(?<!\.)\.\.(?!\.)' , '.', article)  # ".." → "." but not "..."
    # "?.\n" or "!.\n" → remove trailing period after question/exclamation
    article = re.sub(r'([?!])\.\s', r'\1 ', article)
    # ". But." → sentence fragment from content removal (". But [removed stat]. That's")
    article = re.sub(r'\.\s*But\.\s*', '. ', article)

    # ── 6b. Capitalize bold openers at start of lines ──
    # "**decision-maker..." → "**Decision-maker..."
    article = re.sub(
        r'^(\*\*)([a-z])',
        lambda m: m.group(1) + m.group(2).upper(),
        article,
        flags=re.MULTILINE
    )

    # ── 7. Fix numbered step gaps ──
    # If steps go 1, 3, 4, 5 (missing 2), renumber to 1, 2, 3, 4
    step_lines = article.split("\n")
    step_indices = []
    for si, sl in enumerate(step_lines):
        m = re.match(r'^(\d+)\.\s', sl.strip())
        if m:
            step_indices.append((si, int(m.group(1))))
    if step_indices:
        # Find contiguous runs of numbered items
        runs = []
        current_run = [step_indices[0]]
        for j in range(1, len(step_indices)):
            prev_idx, prev_num = step_indices[j - 1]
            cur_idx, cur_num = step_indices[j]
            # Same run if indices are close and numbers are sequential-ish
            if cur_idx - prev_idx <= 5 and cur_num > prev_num:
                current_run.append(step_indices[j])
            else:
                runs.append(current_run)
                current_run = [step_indices[j]]
        runs.append(current_run)
        for run in runs:
            if len(run) >= 2:
                expected = 1  # Always start at 1
                for si_idx, (line_idx, actual_num) in enumerate(run):
                    if actual_num != expected:
                        old = step_lines[line_idx].strip()
                        step_lines[line_idx] = step_lines[line_idx].replace(
                            f"{actual_num}.", f"{expected}.", 1
                        )
                    expected += 1
    article = "\n".join(step_lines)

    # Also fix **Step word:** patterns (e.g., "Step one" → "Step three" gap)
    word_to_num = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }
    num_to_word = {v: k for k, v in word_to_num.items()}
    step_word_lines = article.split("\n")
    word_step_indices = []
    for si, sl in enumerate(step_word_lines):
        m = re.match(r'^\*?\*?[Ss]tep\s+(\w+)', sl.strip())
        if m and m.group(1).lower() in word_to_num:
            word_step_indices.append((si, word_to_num[m.group(1).lower()], m.group(1)))
    if len(word_step_indices) >= 2:
        # Always start at 1 — if steps go 2,3,4,5 that means step 1 was dropped
        expected = 1
        for line_idx, actual_num, original_word in word_step_indices:
            if actual_num != expected and expected in num_to_word:
                new_word = num_to_word[expected]
                # Match capitalization of original
                if original_word[0].isupper():
                    new_word = new_word.capitalize()
                step_word_lines[line_idx] = step_word_lines[line_idx].replace(
                    original_word, new_word, 1
                )
            expected += 1
    article = "\n".join(step_word_lines)

    # ── 8. Remove AI hallmark phrases ──
    # Known AI writing patterns that signal machine-generated content.
    # These are removed or replaced to make the writing sound human.
    _ai_hallmarks = [
        # Filler openers — just delete (the sentence works without them)
        (r"(?i)Here's the thing[.:]\s*", ""),
        (r"(?i)Here's the thing about ([^.:]+)[.:]\s*", r"About \1: "),
        (r"(?i)Here's the thing (\w+)", lambda m: m.group(1).capitalize()),  # "Here's the thing most" → "Most"
        (r"(?i)Here's what (?:actually )?matters[.:]\s*", ""),
        (r"(?i)And here's the (?:part|thing) (?:nobody|no one) talks about(?: enough)?[.:]\s*", ""),
        (r"(?i)Let's (?:dive in|break it down|unpack this|take a closer look)[.:]\s*", ""),
        (r"(?i)Let's be honest[.:]\s*", ""),
        (r"(?i)It's worth noting that\s+", ""),
        (r"(?i)It's important to (?:note|remember|understand) that\s+", ""),
        (r"(?i)At the end of the day,\s*", ""),
        (r"(?i)The bottom line is[.:]\s*", ""),
        (r"(?i)In today's (?:landscape|world|environment|climate),?\s*", ""),
        (r"(?i)In an era (?:where|of)\s+", ""),
        (r"(?i)When it comes to\s+", "For "),
        (r"(?i)The reality is(?:,| that)\s*", ""),
        # "Spend less time X, more time Y" → just keep the benefit
        (r"(?i)spend(?:s|ing)? less time (?:on |doing )?[^,]+(?:,| and) (?:more|spend(?:ing)?) (?:more )?time (?:on |doing )?", "focus on "),
        # Hedging / AI caution phrases
        (r"(?i)This (?:matters|is important) more (?:for [^.]+)?than you might think\.\s*", ""),
        # "Navigate the complexities/challenges" — AI corporate
        (r"(?i)navigate the (?:complexities|challenges|landscape) of\s+", "handle "),
        # "Game-changer" / "robust" / "comprehensive" — AI vocabulary
        (r"(?i)\bgame[- ]changer\b", "significant improvement"),
        (r"(?i)\brobust\b", "strong"),
        # More AI clichés
        (r"(?i)\ba different animal\b", "different"),
        (r"(?i)That's the whole game\.\s*", ""),
        (r"(?i)That's the whole point\.\s*", ""),
        (r"(?i)(?:And )?that's (?:exactly )?(?:where|why|how) things get interesting\.\s*", ""),
        (r"(?i)Spoiler:\s*", ""),
        (r"(?i)(?:And )?here's (?:the )?(?:kicker|catch|twist)[.:]\s*", ""),
        (r"(?i)(?:But )?wait,? (?:it gets|there's) (?:better|worse|more)[.:]\s*", ""),
        (r"(?i)Sound familiar\?\s*", ""),
        (r"(?i)You already knew that\b", ""),
        (r"(?i)If you're like most (?:organizations|teams|nonprofits|companies),?\s*", ""),
    ]
    for pattern, replacement in _ai_hallmarks:
        article = re.sub(pattern, replacement, article)

    # Capitalize the first letter after a deletion left a lowercase sentence start
    article = re.sub(
        r'(?<=\n)([a-z])',
        lambda m: m.group(1).upper(),
        article,
    )

    # Fix concatenated words ("mismanagementMismanagement" → "mismanagement. Mismanagement")
    # Protect known camelCase brand names first
    _brand_names = {'ContractSafe', 'JavaScript', 'LinkedIn', 'GitHub', 'NetSuite',
                    'DocuSign', 'PandaDoc', 'LinkSquares', 'QuickBooks', 'NonprofitPro',
                    'HubSpot', 'DataForSEO', 'WorldCC', 'GetApp', 'SoftwareAdvice'}
    _brand_pattern = '|'.join(re.escape(b) for b in _brand_names)
    # Only fix concatenation that ISN'T inside a known brand name
    def _fix_concat(m):
        full = m.group(0)
        # Check if this match is part of a brand name in the surrounding context
        start = max(0, m.start() - 15)
        end = min(len(article), m.end() + 15)
        context = article[start:end]
        for brand in _brand_names:
            if brand in context:
                return full  # Don't touch — it's a brand name
        return f'{m.group(1)}. {m.group(2)}'
    article = re.sub(r'([a-z])([A-Z][a-z]{3,})', _fix_concat, article)

    # ── 9. Collapse double blank lines ──
    while '\n\n\n' in article:
        article = article.replace('\n\n\n', '\n\n')

    # ── 10. Title-case H2 headings ──
    # e.g., "## main points for Contract Risk" → "## Main Points for Contract Risk"
    _tc_small = {'a', 'an', 'and', 'as', 'at', 'but', 'by', 'for', 'from',
                 'if', 'in', 'nor', 'of', 'on', 'or', 'so', 'the', 'to',
                 'up', 'vs', 'yet'}
    h2_lines = article.split("\n")
    for hi, hl in enumerate(h2_lines):
        if hl.startswith("## ") and not hl.startswith("### "):
            heading_text = hl[3:]
            words = heading_text.split()
            new_words = []
            for wi, word in enumerate(words):
                if not word:
                    new_words.append(word)
                    continue
                # Preserve already-capitalized words (ContractSafe, CLM, etc.)
                if word[0].isupper() or word.isupper():
                    new_words.append(word)
                elif wi == 0 or word.lower().rstrip(':?!.,') not in _tc_small:
                    new_words.append(word[0].upper() + word[1:])
                else:
                    new_words.append(word)
            h2_lines[hi] = "## " + " ".join(new_words)
    article = "\n".join(h2_lines)

    # ── 11. Clean up whitespace ──
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


def _strip_source_artifacts(article: str) -> str:
    """Remove 'according to [Truncated Source Title]' artifacts from writer output.

    The writer sometimes leaks truncated research source titles into the text,
    e.g., 'according to Contract Change Management Tactics That Actually W.'
    These aren't markdown links — just raw text that reads as broken citations.
    """
    lines = article.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip lines that are entirely a source citation or garbled data
        if stripped and re.match(r'^According to \d+\s', stripped):
            continue  # "According to 16 Contract Management Statistics..." — pure citation artifact
        if stripped and '|' in stripped and stripped.count('|') >= 3 and not stripped.startswith('|'):
            continue  # garbled table data leaked from sources

        # Remove entire lines starting with "According to [page title]"
        # Page titles contain ?, :, &, or are truncated (>50 chars)
        at_match = re.match(r'^According to ([^,\n]+)', stripped)
        at_is_legitimate = False
        if at_match:
            src_name = at_match.group(1).strip().rstrip('.')
            # Check if last word looks truncated (no vowels, or single letter)
            last_word = src_name.split()[-1].lower() if src_name.split() else ""
            looks_truncated = (
                len(last_word) >= 2
                and not last_word.isdigit()  # "2026" is a year, not truncated
                and not re.search(r'[aeiouy]', last_word)  # no vowels = likely truncated
                and last_word not in ('by', 'my', 'gym', 'lynx', 'myth', 'sync', 'hymn')
            ) or (
                len(last_word) == 1 and last_word.isalpha()  # single letter = truncated
            )
            is_garbled = (
                '?' in src_name          # Page title with question mark
                or ':' in src_name       # Page title with colon
                or '&' in src_name       # Truncated title with ampersand
                or len(src_name) > 50    # Too long for a real org name
                or looks_truncated       # Ends with truncated word (no vowels)
            )
            if is_garbled:
                continue  # Skip the entire line
            else:
                at_is_legitimate = True  # Real source — don't strip this line

        # Strip parenthetical attributions: "(according to ...)"
        line = re.sub(r'\s*\([Aa]ccording to [^)]+\)', '', line)

        # Strip ", according to [Truncated Source Title]" at end of sentences
        # Only applies to MID-SENTENCE "according to" — skip if the line starts
        # with a legitimate "According to [Source]" that passed the check above.
        if not at_is_legitimate:
            line = re.sub(
                r',?\s*[Aa]ccording to [A-Z0-9][^[\n]{35,}?\.(?!\d)(?=\s|$)',
                lambda m: '.' if m.group(0).rstrip().endswith('.') else '',
                line
            )
            # Also strip article titles used as sources (contain ?)
            line = re.sub(
                r',?\s*[Aa]ccording to [A-Z0-9][^[\n]*?\?[.\s]',
                lambda m: '.' if m.group(0).rstrip().endswith('.') else '',
                line
            )
        cleaned.append(line)
    return "\n".join(cleaned)


def _strip_truncated_lines(article: str) -> str:
    """Remove lines ending with truncated words from garbled research data.

    Detects lines ending with clearly truncated words: consonant-only words
    or words ending in rare double consonants that aren't real English words.
    e.g., "...require a writt." or "...contract value thr."
    """
    _ABBREVS = frozenset({
        'etc', 'inc', 'ltd', 'corp', 'avg', 'min', 'max', 'dept', 'govt',
        'intl', 'mgmt', 'natl', 'org', 'prof', 'est', 'fig', 'ref', 'sec',
        'vol', 'assoc', 'bros', 'co', 'dr', 'jr', 'sr', 'vs', 'pt', 'ed',
        'rev', 'st', 'no', 'mr', 'mrs', 'ms',
    })
    # Real English words ending in rare double consonants (tt, dd, gg, nn, pp, rr, zz)
    _DOUBLE_CONSONANT_WORDS = frozenset({
        'butt', 'mutt', 'putt', 'watt', 'mitt', 'matt',
        'add', 'odd',
        'egg',
        'inn', 'ann',
        'app',
        'err', 'burr', 'purr',
        'buzz', 'fizz', 'fuzz', 'jazz', 'razz',
    })

    lines = article.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Only check prose lines (skip headings, tables, bullets, blank lines)
        if not stripped or stripped.startswith('#') or stripped.startswith('|') or stripped.startswith('- '):
            cleaned.append(line)
            continue
        # Skip lines with markdown links (likely intentional content)
        if '](http' in stripped:
            cleaned.append(line)
            continue

        # Check for truncated word at end of line
        trunc_match = re.search(r'\b([a-z]{2,6})\.\s*$', stripped)
        if trunc_match:
            word = trunc_match.group(1).lower()
            if word not in _ABBREVS:
                # No vowels → clearly truncated (e.g., "thr", "str")
                if not re.search(r'[aeiouy]', word):
                    continue  # Remove the line
                # Ends with rare double consonant → likely truncated (e.g., "writt")
                if (len(word) >= 3
                        and word[-1] == word[-2]
                        and word[-1] not in 'lsf'  # ll, ss, ff are common endings
                        and word not in _DOUBLE_CONSONANT_WORDS):
                    continue  # Remove the line

        cleaned.append(line)
    return "\n".join(cleaned)


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
            # Protect abbreviation periods from being treated as sentence boundaries
            protected = re.sub(
                r'\b([A-Z])\.([A-Z])\.',
                lambda m: m.group(1) + '\x03' + m.group(2) + '\x03',
                protected
            )
            protected = re.sub(
                r'\b(e\.g|i\.e|etc|vs|Dr|Mr|Mrs|Ms|Jr|Sr|St|No)\.',
                lambda m: m.group(0).replace('.', '\x03'),
                protected
            )
            sentences = re.split(r'(?<=[.!?])[)\]"\'"\u201d\u2019*]*\s+', protected)
            sentences = [s.replace('\x00', '.').replace('\x01', '!').replace('\x02', '?').replace('\x03', '.') for s in sentences]
            chunks = _chunk_by_limit(sentences, 42)
            # If a chunk is still over 42 words (single long sentence),
            # try splitting at colons or semicolons as a fallback.
            final_chunks = []
            for c in chunks:
                if len(c.split()) > 42 and re.search(r'[:;]', c):
                    parts = re.split(r'(?<=[:;])\s+', c, maxsplit=1)
                    if len(parts) == 2 and all(len(p.split()) >= 5 for p in parts):
                        parts[1] = parts[1][0].upper() + parts[1][1:]
                        final_chunks.extend(parts)
                    else:
                        final_chunks.append(c)
                # Fallback: split at comma + coordinating conjunction
                elif len(c.split()) > 42 and re.search(r',\s+(?:and|but|or|so|yet)\s+', c):
                    parts = re.split(r',\s+(?=(?:and|but|or|so|yet)\s+)', c, maxsplit=1)
                    if len(parts) == 2 and all(len(p.split()) >= 5 for p in parts):
                        parts[0] = parts[0] + '.'
                        parts[1] = parts[1][0].upper() + parts[1][1:]
                        final_chunks.extend(parts)
                    else:
                        final_chunks.append(c)
                else:
                    final_chunks.append(c)
            for i, c in enumerate(final_chunks):
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
        self.progress("Scanning for corporate language and applying brand voice fixes...")
        article = state.draft_article

        # ── Step 1: Apply all mechanical fixes FIRST (instant, no Claude) ──
        self.progress("Replacing dashes, stiff transitions, and corporate jargon...")
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

        # ── Step 3: Fix creative issues programmatically ──
        creative_fixes = 0
        if issues:
            for issue in issues:
                if "CORPORATE" in issue or "B2B" in issue:
                    article, count = self._fix_corporate_phrases(article, state)
                    creative_fixes += count
                elif "REPETITIVE" in issue:
                    article, count = self._fix_repetitive_starts(article)
                    creative_fixes += count
                elif "STIFF TRANSITION" in issue:
                    article, count = self._fix_stiff_transitions(article)
                    creative_fixes += count
                else:
                    # Log issues that can't be fixed programmatically
                    self.log(f"[yellow]Cannot fix programmatically (manual review): {issue[:100]}[/yellow]")

            state.voice_issues_found = [{"issue": i[:80], "fix": "programmatic"} for i in issues]
            # Enrich with detail: count corporate, transition, and repetitive fixes
            corp_count = sum(1 for i in issues if "CORPORATE" in i or "B2B" in i)
            trans_count = sum(1 for i in issues if "STIFF TRANSITION" in i)
            rep_count = sum(1 for i in issues if "REPETITIVE" in i)
            for entry in state.voice_issues_found:
                iss = entry["issue"].upper()
                if "CORPORATE" in iss or "B2B" in iss:
                    entry["detail"] = f"Replaced corporate/B2B phrases ({corp_count} found)"
                elif "STIFF TRANSITION" in iss:
                    entry["detail"] = f"Replaced stiff transitions ({trans_count} found)"
                elif "REPETITIVE" in iss:
                    entry["detail"] = f"Fixed repetitive sentence starts ({rep_count} found)"
                else:
                    entry["detail"] = "Flagged for review"
            self.log(f"Applied {mechanical_count} mechanical + {creative_fixes} creative fixes (all programmatic).")
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

    def _fix_corporate_phrases(self, article: str, state: PipelineState) -> tuple[str, int]:
        """Replace corporate phrases with conversational alternatives."""
        # Don't replace terms that are in the article topic/title
        title_words = set(state.topic.lower().split()) if hasattr(state, 'topic') else set()

        replacements = {
            "leverage": "use",
            "leveraging": "using",
            "leveraged": "used",
            "streamline": "simplify",
            "streamlined": "simplified",
            "streamlines": "simplifies",
            "streamlining": "simplifying",
            "drive efficiency": "save time",
            "optimize your": "improve your",
            "maximize your": "get the most from your",
            "empower your": "help your",
            "unlock the power": "get the benefit",
            "best-in-class": "excellent",
            "cutting-edge": "modern",
            "state-of-the-art": "modern",
            "synergy": "collaboration",
            "scalable solution": "flexible tool",
            "robust platform": "solid tool",
            "seamless integration": "easy connection",
            "end-to-end": "complete",
            "holistic approach": "complete approach",
            "mission-critical": "essential",
            "paradigm shift": "major change",
            "value proposition": "benefit",
            "pain point": "problem",
            "pain points": "problems",
            "stakeholder": "decision-maker",
            "stakeholders": "decision-makers",
            "key stakeholders": "the people who sign off",
            "actionable insights": "useful information",
            "move the needle": "make a real difference",
            "low-hanging fruit": "easy wins",
            "circle back": "revisit",
            "deep dive": "closer look",
            "take it to the next level": "improve it",
            "digital transformation": "technology upgrade",
            "thought leader": "expert",
            "key takeaway": "main point",
            "key takeaways": "main points",
            "at the end of the day": "ultimately",
            "in today's": "in the current",
        }

        count = 0
        for phrase, replacement in replacements.items():
            # Skip if the phrase is part of the topic/title
            if phrase.lower() in title_words or all(w in title_words for w in phrase.lower().split()):
                continue
            if phrase.lower() in article.lower():
                # Case-preserving replacement
                pattern = re.compile(re.escape(phrase), re.IGNORECASE)
                new_article = pattern.sub(replacement, article)
                if new_article != article:
                    occurrences = len(pattern.findall(article))
                    count += occurrences
                    article = new_article
                    self.progress(f"  Replaced '{phrase}' x{occurrences} → '{replacement}'")

        return article, count

    def _fix_repetitive_starts(self, article: str) -> tuple[str, int]:
        """Vary sentence openings in clusters of 3+ same-start sentences."""
        lines = article.split("\n")
        count = 0
        bridge_words = ["And ", "But ", "Which is why ", "That said, ", "Still, "]
        bridge_idx = 0

        for i in range(len(lines) - 2):
            # Only process body paragraphs (not headings, lists, tables)
            s1 = lines[i].strip()
            s2 = lines[i + 1].strip()
            s3 = lines[i + 2].strip()

            if not all(s and not s.startswith("#") and not s.startswith("|")
                       and not s.startswith("-") and not s.startswith("*")
                       and len(s) > 20 for s in [s1, s2, s3]):
                continue

            w1 = s1.split()[0].lower() if s1.split() else ""
            w2 = s2.split()[0].lower() if s2.split() else ""
            w3 = s3.split()[0].lower() if s3.split() else ""

            if w1 == w2 == w3 and w1:
                # Vary the 2nd and 3rd sentence starts
                bridge = bridge_words[bridge_idx % len(bridge_words)]
                bridge_idx += 1
                # Lowercase the first word of the original sentence
                words = lines[i + 1].strip().split()
                if len(words) > 1:
                    words[0] = words[0].lower()
                    lines[i + 1] = bridge + " ".join(words)
                    count += 1

        return "\n".join(lines), count

    def _fix_stiff_transitions(self, article: str) -> tuple[str, int]:
        """Replace stiff transitions with conversational bridges."""
        bridges = {
            "furthermore": "And here's the thing:",
            "additionally": "On top of that,",
            "moreover": "What's more,",
            "consequently": "So naturally,",
            "subsequently": "After that,",
            "nevertheless": "But still,",
            "in conclusion": "Which brings us back to the big question.",
            "to summarize": "So, the short version:",
            "in summary": "The short version:",
            "in essence": "Basically,",
            "it is important to note": "Worth knowing:",
            "it should be noted": "Here's the thing:",
            "it is worth noting": "Here's the thing:",
            "as mentioned above": "As we covered,",
            "as previously stated": "Like we said,",
            "as discussed earlier": "As we talked about,",
            "with that being said": "That said,",
            "that being said": "That said,",
            "in today's": "In the current",
            "in the modern": "In today's",
        }

        count = 0
        for stiff, bridge in bridges.items():
            pattern = re.compile(re.escape(stiff), re.IGNORECASE)
            if pattern.search(article):
                article = pattern.sub(bridge, article)
                count += 1

        return article, count

    def _extract_issue_excerpts(self, article: str, issues: list[str]) -> str:
        """Extract only paragraphs containing issues, with ±1 paragraph context.

        Reduces prompt from ~19k (full article) to ~3-5k (relevant excerpts only).
        """
        paragraphs = article.split("\n\n")
        relevant = set()

        for issue in issues:
            if "CORPORATE" in issue or "B2B" in issue:
                for i, para in enumerate(paragraphs):
                    for phrase in CORPORATE_PHRASES:
                        if phrase in para.lower():
                            relevant.add(i)
                            break

            if "STIFF TRANSITION" in issue:
                for i, para in enumerate(paragraphs):
                    for trans in STIFF_TRANSITIONS:
                        if trans in para.lower():
                            relevant.add(i)
                            break

            if "REPETITIVE" in issue:
                for i, para in enumerate(paragraphs):
                    sentences = re.split(r'(?<=[.!?])\s+', para.strip())
                    if len(sentences) >= 3:
                        for j in range(len(sentences) - 2):
                            w = [s.split() for s in sentences[j:j+3]]
                            if all(w_s for w_s in w) and w[0][0].lower() == w[1][0].lower() == w[2][0].lower():
                                relevant.add(i)
                                break

            if "CONVERSATIONAL MARKER" in issue or "PARENTHETICAL" in issue or "READER ENGAGEMENT" in issue:
                # For personality issues, include body paragraphs that are flat/explanatory
                for i, para in enumerate(paragraphs):
                    stripped = para.strip()
                    if (stripped and not stripped.startswith("#") and not stripped.startswith("|")
                            and not stripped.startswith("-") and len(stripped) > 80):
                        relevant.add(i)
                        if len(relevant) > 15:
                            break

        if not relevant:
            # Fallback: send first ~3k chars
            return article[:3000]

        # Expand ±1 paragraph for context
        expanded = set()
        for idx in relevant:
            for j in range(max(0, idx - 1), min(len(paragraphs), idx + 2)):
                expanded.add(j)

        result = []
        prev_idx = -2
        for i in sorted(expanded):
            if i > prev_idx + 1:
                result.append("...")
            result.append(paragraphs[i])
            prev_idx = i

        return "\n\n".join(result)

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
                f"Replace with conversational bridges: 'Here's the thing though,' 'But wait,' "
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
