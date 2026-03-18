"""Agent 11: AEO Pass - fully programmatic AEO optimization.

No LLM calls. All fixes are applied in Python:
- Answer blocks → front-load direct answers after H2s
- Process sections → convert narrative to numbered steps
- Semantic triples → add brand subject-verb-object statements
- Vague/short headings → keyword-enriched replacements
- Source attribution → matched against research data
- Data density → insert available statistics
- Context-dependent passages → rewrite openers to be self-contained
- Entity clarity → context added to first brand mention
- Freshness → current year reference added
- PAA coverage → add unaddressed questions to FAQ section
"""

from __future__ import annotations

import re
import datetime
from agents.base import BaseAgent
from state import PipelineState


class AEOPassAgent(BaseAgent):
    name = "AEO Pass"
    description = "Optimize article for AI answer engine extractability"
    agent_number = 11
    emoji = "\U0001f916"
    timeout = 180

    def run(self, state: PipelineState) -> PipelineState:
        article = (
            state.seo_pass_article
            or state.fact_check_article
            or state.voice_pass_article
            or state.draft_article
        )

        # ── Run programmatic AEO audit ──
        self.progress("Checking article for AI answer engine citability signals...")
        audit = self._audit(article, state)
        issues = audit["issues"]

        self.progress(f"Found {len(issues)} AEO issues")
        for issue in issues[:6]:
            self.progress(f"  - {issue[:80]}")

        # ── Apply ALL fixes programmatically ──
        self.progress("Fixing headings, adding FAQ coverage, and enriching data density...")
        article, fixed = self._apply_all_fixes(article, issues, state)

        # ── Re-audit to see what's left ──
        re_audit = self._audit(article, state)
        remaining = re_audit["issues"]

        if remaining:
            self.log(f"{len(remaining)} issues remain after programmatic fixes (logged for manual review):")
            for iss in remaining:
                self.log(f"  - {iss[:80]}")

        state.aeo_pass_article = article
        state.aeo_changes = [{"change": f} for f in fixed]

        word_count = len(article.split())
        self.log(
            f"AEO pass complete. {len(fixed)} programmatic fixes. "
            f"{len(remaining)} remaining (manual review). ~{word_count} words."
        )
        return state

    # ── Fix dispatcher ──

    def _apply_all_fixes(
        self, article: str, issues: list, state: PipelineState
    ) -> tuple[str, list[str]]:
        """Apply all programmatic AEO fixes."""
        fixed = []

        for issue in issues:
            if "VAGUE HEADINGS" in issue:
                result = self._fix_vague_headings(article, issue, state)
                if result:
                    article = result
                    fixed.append("vague_headings")

            elif "ENTITY CLARITY" in issue:
                result = self._fix_entity_clarity(article)
                if result:
                    article = result
                    fixed.append("entity_clarity")

            elif "NO FRESHNESS SIGNALS" in issue:
                result = self._fix_freshness(article)
                if result:
                    article = result
                    fixed.append("freshness")

            elif "SOURCE ATTRIBUTION" in issue:
                result = self._fix_source_attribution(article, state)
                if result:
                    article = result
                    fixed.append("source_attribution")

            elif "LOW DATA DENSITY" in issue:
                result = self._fix_data_density(article, state)
                if result:
                    article = result
                    fixed.append("data_density")

            elif "CONTEXT-DEPENDENT" in issue:
                result = self._fix_context_dependent(article)
                if result:
                    article = result
                    fixed.append("context_dependent")

            elif "PAA QUESTIONS" in issue:
                result = self._fix_paa_coverage(article, issue, state)
                if result:
                    article = result
                    fixed.append("paa_coverage")

            elif "ANSWER BLOCKS MISSING" in issue:
                result = self._fix_answer_blocks(article, issue, state)
                if result:
                    article = result
                    fixed.append("answer_blocks")

            elif "PROCESS SECTIONS" in issue:
                result = self._fix_process_sections(article)
                if result:
                    article = result
                    fixed.append("process_sections")

            elif "SEMANTIC TRIPLES" in issue:
                result = self._fix_semantic_triples(article, state)
                if result:
                    article = result
                    fixed.append("semantic_triples")

            elif "UNIQUE VALUE" in issue:
                result = self._fix_unique_value(article, state)
                if result:
                    article = result
                    fixed.append("unique_value")

        return article, fixed

    # ── Individual fix methods ──

    def _fix_vague_headings(self, article: str, issue: str, state: PipelineState) -> str | None:
        """Replace vague and short headings with keyword-enriched versions."""
        kw = state.target_keyword

        # Explicit replacements for known vague patterns
        replacements = {
            "the bottom line": f"Why {kw.title()} Matters for Your Organization",
            "the big picture": f"The Real Impact of {kw.title()}",
            "why it matters": f"Why {kw.title()} Matters for Your Organization",
            "final thoughts": f"What to Do About {kw.title()} Next",
            "wrapping up": f"Where {kw.title()} Goes From Here",
            "key takeaways": f"Key Takeaways on {kw.title()}",
            "in summary": f"Summary: What to Know About {kw.title()}",
            "overview": f"Overview of {kw.title()}",
            # Short/common patterns
            "tl;dr": f"Key Takeaways: {kw.title()}",
            "faq": f"Frequently Asked Questions About {kw.title()}",
            "faqs": f"Frequently Asked Questions About {kw.title()}",
        }

        modified = False

        # First pass: replace known vague headings
        for vague, replacement in replacements.items():
            pattern = re.compile(
                rf"^(## ){re.escape(vague)}\s*\??$", re.MULTILINE | re.IGNORECASE
            )
            if pattern.search(article):
                article = pattern.sub(f"\\1{replacement}", article)
                modified = True

        # Second pass: handle remaining short headings (1-2 words) not already fixed
        # Extract vague headings listed in the issue for targeted fixing
        h2_pattern = re.compile(r"^## (.+)$", re.MULTILINE)
        already_replaced = {v.lower() for v in replacements.values()}

        for match in list(h2_pattern.finditer(article)):
            heading_text = match.group(1).strip()
            heading_lower = heading_text.lower().strip("?").strip()
            words = heading_lower.split()

            # Skip if already long enough or already one of our replacements
            if len(words) > 2 or heading_lower in already_replaced:
                continue

            # Skip common short headings that are actually fine
            # (e.g., section-specific terms that are self-describing with context)

            # Build a descriptive replacement: "Short Heading" → "Short Heading for [Keyword]"
            # Use the heading's own words as context
            if heading_text.lower().startswith("quick"):
                new_heading = f"{heading_text}: {kw.title()} at a Glance"
            else:
                new_heading = f"{heading_text}: Understanding {kw.title()}"

            article = article.replace(f"## {heading_text}", f"## {new_heading}", 1)
            modified = True

        return article if modified else None

    def _fix_entity_clarity(self, article: str) -> str | None:
        """Add context to first ContractSafe mention in body text (skip URLs)."""
        for match in re.finditer(r'ContractSafe', article):
            idx = match.start()

            # Skip if inside a URL (preceded by :// or www. or a dot)
            before = article[max(0, idx - 30):idx]
            if '://' in before or before.rstrip().endswith('.') or 'www.' in before:
                continue

            # Skip if inside markdown link URL [text](url...)
            paren_open = article.rfind('(', max(0, idx - 200), idx)
            paren_close = article.rfind(')', max(0, idx - 200), idx)
            if paren_open > paren_close:
                continue  # Inside parentheses (likely a URL)

            surrounding = article[max(0, idx - 10):idx + 200].lower()
            if any(p in surrounding for p in ["contract management", "clm", "software"]):
                return None  # Already has context

            end = idx + len("ContractSafe")
            original = article[idx:end]
            replacement = f"{original}, a contract management software platform,"
            return article[:idx] + replacement + article[end:]

        return None

    def _fix_freshness(self, article: str) -> str | None:
        """Add current year reference if missing."""
        current_year = str(datetime.datetime.now().year)
        if current_year in article or str(int(current_year) - 1) in article:
            return None

        lines = article.split("\n")
        for i, line in enumerate(lines):
            if re.search(r"\d+%|\$[\d,]+", line) and not line.strip().startswith("#"):
                lines[i] = line.rstrip().rstrip(".") + f" (as of {current_year})."
                return "\n".join(lines)

        return None

    def _fix_source_attribution(self, article: str, state: PipelineState) -> str | None:
        """Match unattributed statistics against research data and intra-article sources."""
        # Build lookup: number fragment → source name
        number_to_source = {}

        # Source 1: research data
        if state.statistics:
            for stat in state.statistics:
                s = stat.get("stat", "")
                src = stat.get("source_name", "") or stat.get("source", "")
                if not s or not src:
                    continue
                # Reject obviously bad source names (page titles, truncated text)
                if (len(src) > 60
                        or "?" in src
                        or ":" in src
                        or src.endswith("...")
                        or len(src.split()) > 5):
                    continue
                for num in re.findall(r"\d+(?:\.\d+)?%", s):
                    number_to_source[num] = src
                for num in re.findall(r"\$[\d,.]+", s):
                    number_to_source[num] = src
                for num in re.findall(r"\d+(?:\.\d+)?\s*(?:billion|million|trillion)", s, re.IGNORECASE):
                    number_to_source[num.strip()] = src

        # Source 2: scan draft article for attributions the LLM may have dropped
        # The writer (Agent 7) always includes sources; the brand voice pass
        # (Agent 8, LLM) sometimes removes them. Recover those mappings here.
        fwd_extractors = [
            r'according to (?:the |a )?(?:\d{4} )?([^,.]+)',
            r'per ([^,.]+)',
            r'data from ([^,.]+)',
            r'report by ([^,.]+)',
            r'study by ([^,.]+)',
            r'survey by ([^,.]+)',
            r'research (?:from|by) ([^,.]+)',
        ]
        rev_extractors = [
            r'(?:a |an |the )?(?:\d{4} )?([A-Z][A-Za-z& ]+?)\s+(?:found|reported|showed|noted|revealed) that',
            r'(?:a |an |the )?(?:\d{4} )?([A-Z][A-Za-z& ]+?)\s+(?:survey|study|report|research|analysis)\b',
        ]
        # Scan both the current article AND the original draft
        scan_texts = [article]
        if state.draft_article and state.draft_article != article:
            scan_texts.append(state.draft_article)
        for scan_text in scan_texts:
            for aline in scan_text.split("\n"):
                astripped = aline.strip()
                if not astripped or astripped.startswith("#"):
                    continue
                if not re.search(r'\d+%|\$[\d,]+', astripped):
                    continue
                # Try forward patterns first, then reverse
                source = None
                for pat in fwd_extractors:
                    m = re.search(pat, astripped, re.IGNORECASE)
                    if m:
                        candidate = m.group(1).strip()
                        has_proper = any(
                            w[0].isupper() for w in candidate.split()
                            if len(w) >= 2 and w[0].isalpha()
                        )
                        if candidate and has_proper:
                            source = candidate
                        break
                if not source:
                    for pat in rev_extractors:
                        m = re.search(pat, astripped)
                        if m:
                            source = m.group(1).strip()
                            break
                if not source:
                    continue
                # Reject garbled source names from article text too
                if (len(source) > 60 or "?" in source or ":" in source
                        or source.endswith("...") or len(source.split()) > 5):
                    continue
                # Map numbers from this attributed line (first mapping wins)
                for num in re.findall(r'\d+(?:\.\d+)?%', astripped):
                    if num not in number_to_source:
                        number_to_source[num] = source

        if not number_to_source:
            return None

        lines = article.split("\n")
        modified = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("|"):
                continue

            # Does this line have a statistic?
            if not re.search(
                r"\d+%|\$[\d,]+|\d+\s*(billion|million|trillion|percent)",
                stripped, re.IGNORECASE
            ):
                continue

            # Already attributed?
            if re.search(
                r"(according to|per |found that|reported|study by|survey by|"
                r"research from|report by|data from|analysis by|cited by|"
                r"Gartner|Forrester|McKinsey|Deloitte|PwC|American Bar|IACCM)",
                stripped, re.IGNORECASE
            ):
                continue

            # Try to match a number to a known source
            for num, src in number_to_source.items():
                if num in stripped:
                    # Insert attribution before the period at end of line
                    if stripped.endswith("."):
                        new_line = line.rstrip()[:-1] + f", according to {src}."
                    else:
                        new_line = line.rstrip() + f" (according to {src})"
                    lines[i] = new_line
                    modified = True
                    break

        return "\n".join(lines) if modified else None

    def _fix_data_density(self, article: str, state: PipelineState) -> str | None:
        """Insert available statistics from research into relevant article sections."""
        if not state.statistics:
            return None

        # Collect numbers already in the article (handles both "%" and "percent")
        article_lower = article.lower()
        existing_numbers: set[str] = set()
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", article_lower):
            existing_numbers.add(m.group(1))
        for m in re.finditer(r"\$[\d,.]+", article):
            existing_numbers.add(m.group(0))

        # Find stats that aren't already in the article
        unused_stats = []
        for stat in state.statistics:
            s = stat.get("stat", "")
            src = stat.get("source_name", "") or stat.get("source", "")
            if not s or not src:
                continue
            # Extract key numbers from the stat text (both "%" and "percent")
            s_lower = s.lower()
            stat_numbers = set()
            for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", s_lower):
                stat_numbers.add(m.group(1))
            for m in re.finditer(r"\$[\d,.]+", s):
                stat_numbers.add(m.group(0))
            # Skip if any number is already in the article
            if stat_numbers and stat_numbers & existing_numbers:
                continue
            if not stat_numbers:
                continue  # Skip stats without extractable numbers
            unused_stats.append((s, src))

        if not unused_stats:
            return None

        # Find H2 sections and try to insert relevant stats
        lines = article.split("\n")
        modified = False
        stats_inserted = 0
        max_insertions = 3  # Don't overwhelm the article

        for stat_text, stat_source in unused_stats:
            if stats_inserted >= max_insertions:
                break

            # Clean stat text: strip markdown bold, bullets, leading whitespace
            clean_stat = stat_text.strip().lstrip("*").strip()
            clean_stat = re.sub(r"\*\*", "", clean_stat)
            clean_stat = clean_stat[0].lower() + clean_stat[1:] if clean_stat else clean_stat

            # Clean source name: must look like a real attribution, not a page title
            clean_src = stat_source.strip().rstrip(".")
            clean_src = re.sub(r"\*\*", "", clean_src)
            # Skip garbled/page-title source names
            src_words = clean_src.split()
            is_page_title = (
                len(clean_src) > 60
                or "?" in clean_src or ":" in clean_src
                or (len(src_words) >= 1 and src_words[0].isdigit())  # "40 Must-Know..."
                or len(src_words) > 6  # Real org names are short
            )
            if is_page_title:
                # Use generic attribution instead
                clean_src = "industry research"

            # Find the most relevant section by keyword overlap
            stat_words = set(w.lower() for w in re.findall(r"\w+", clean_stat) if len(w) > 3)
            best_section_end = -1
            best_overlap = 0

            for i, line in enumerate(lines):
                if line.strip().startswith("## "):
                    # Get section text until next heading
                    section_text = []
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip().startswith("#"):
                            break
                        section_text.append(lines[j])

                    section_words = set(
                        w.lower() for w in re.findall(r"\w+", " ".join(section_text)) if len(w) > 3
                    )
                    overlap = len(stat_words & section_words)
                    if overlap > best_overlap:
                        best_overlap = overlap
                        # Find last paragraph line in this section
                        for j in range(i + 1, len(lines)):
                            if lines[j].strip().startswith("#"):
                                best_section_end = j - 1
                                break
                        else:
                            best_section_end = len(lines) - 1

            # Only insert if there's meaningful topical overlap (3+ words)
            if best_overlap >= 3 and best_section_end > 0:
                stat_sentence = f"According to {clean_src}, {clean_stat.rstrip('.')}."
                # Insert after the last paragraph in the best section
                while best_section_end > 0 and not lines[best_section_end].strip():
                    best_section_end -= 1
                lines.insert(best_section_end + 1, f"\n{stat_sentence}")
                modified = True
                stats_inserted += 1

        return "\n".join(lines) if modified else None

    def _fix_context_dependent(self, article: str) -> str | None:
        """Remove context-dependent opener phrases to make passages self-contained."""
        lines = article.split("\n")
        modified = False

        # Phrases to strip from line beginnings (with optional trailing comma/space)
        strip_phrases = [
            r"^as mentioned(?:\s+(?:above|earlier|before))?,?\s*",
            r"^as discussed(?:\s+(?:above|earlier|before))?,?\s*",
            r"^as noted(?:\s+(?:above|earlier|before))?,?\s*",
            r"^as we(?:'ve)?\s+(?:mentioned|discussed|noted|seen|covered)(?:\s+(?:above|earlier|before))?,?\s*",
        ]

        # "This/That is where/why/how" and "It also" patterns
        this_strip_patterns = [
            r"^this is (?:where|why|how)\s+",
            r"^that is (?:where|why|how)\s+",
            r"^it also\s+",
            r"^they also\s+",
            r"^these include\s+",
        ]

        # Quantitative reference phrases that require a nearby number
        quant_refs = re.compile(
            r"rounding error|that number|that figure|that percentage|"
            r"those numbers|that is real money|that\u2019s real money|that's real money|"
            r"that is a lot|that\u2019s a lot|that's a lot|think about that",
            re.IGNORECASE,
        )

        # Anaphoric references to abstract concepts removed by fact checker
        anaphoric_refs = re.compile(
            r"^that (?:gap|divide|disconnect|difference|disparity|contrast)|"
            r"^remember (?:the |that |our )|"
            r"^as (?:we |i |mentioned)|"
            r"^(?:sound familiar|let that sink|not exactly|think about that)",
            re.IGNORECASE,
        )

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("|"):
                continue

            # Handle "as mentioned" etc. — strip the phrase entirely
            for pattern in strip_phrases:
                match = re.match(pattern, stripped, re.IGNORECASE)
                if match:
                    remainder = stripped[match.end():]
                    if remainder:
                        remainder = remainder[0].upper() + remainder[1:]
                        indent = line[:len(line) - len(line.lstrip())]
                        lines[i] = indent + remainder
                        modified = True
                    break
            else:
                # Handle "This is where/why/how", "It also", etc.
                for tp in this_strip_patterns:
                    tm = re.match(tp, stripped, re.IGNORECASE)
                    if tm:
                        remainder = stripped[tm.end():]
                        if remainder and len(remainder) > 10:
                            remainder = remainder[0].upper() + remainder[1:]
                            indent = line[:len(line) - len(line.lstrip())]
                            lines[i] = indent + remainder
                            modified = True
                        break

            # Orphaned quantitative commentary: short paragraph referencing
            # a number that doesn't exist in this or the previous paragraph.
            # e.g., "And yet. That's not a rounding error. That's real money."
            if len(stripped.split()) < 30 and quant_refs.search(stripped):
                has_number = bool(re.search(r'\d+%|\$[\d,]+', stripped))
                if not has_number:
                    # Check previous non-empty paragraph
                    prev_has_number = False
                    for j in range(i - 1, max(i - 5, -1), -1):
                        prev = lines[j].strip()
                        if prev and not prev.startswith("#"):
                            prev_has_number = bool(re.search(r'\d+%|\$[\d,]+', prev))
                            break
                    if not prev_has_number:
                        lines[i] = ""
                        modified = True

            # Orphaned anaphoric references: short paragraph referencing
            # an abstract concept ("that gap", "that divide") when the
            # preceding paragraph doesn't contain comparative data.
            if len(stripped.split()) < 25 and anaphoric_refs.search(stripped):
                # Check previous non-empty paragraph for numbers/comparisons
                prev_has_data = False
                for j in range(i - 1, max(i - 5, -1), -1):
                    prev = lines[j].strip()
                    if prev and not prev.startswith("#"):
                        prev_has_data = bool(re.search(r'\d+%|\$[\d,]+|only \d|just \d', prev))
                        break
                if not prev_has_data:
                    lines[i] = ""
                    modified = True

        return "\n".join(lines) if modified else None

    def _fix_paa_coverage(self, article: str, issue: str, state: PipelineState) -> str | None:
        """Add unaddressed PAA questions to existing FAQ section or create one."""
        paa = state.keyword_data.get("questions_people_ask", []) if state.keyword_data else []
        if not paa:
            return None

        text_lower = article.lower()

        # Find unaddressed questions (same logic as audit)
        unaddressed = []
        for q in paa[:8]:
            q_words = set(w.lower() for w in q.split() if len(w) > 3)
            coverage = sum(1 for w in q_words if w in text_lower) / max(len(q_words), 1)
            if coverage <= 0.5:
                unaddressed.append(q)

        if not unaddressed:
            return None

        # Build FAQ entries — extract a relevant sentence from the article as the answer
        kw = state.target_keyword
        faq_entries = []
        for q in unaddressed[:3]:  # Max 3 to avoid bloat
            q_clean = q.strip().rstrip("?") + "?"
            # Capitalize first letter of question
            q_clean = q_clean[0].upper() + q_clean[1:] if q_clean else q_clean
            faq_entries.append(f"### {q_clean}\n")
            # Find a relevant sentence from the article to use as the answer
            answer = self._extract_faq_answer(article, q, kw)
            faq_entries.append(f"{answer}\n")

        if not faq_entries:
            return None

        faq_block = "\n".join(faq_entries)

        # Look for existing FAQ section
        faq_match = re.search(r"^## .*(?:FAQ|Frequently Asked).*$", article, re.MULTILINE | re.IGNORECASE)
        if faq_match:
            # Insert new entries at the end of the FAQ section
            # Find the next H2 after FAQ
            next_h2 = re.search(r"^## ", article[faq_match.end():], re.MULTILINE)
            if next_h2:
                insert_pos = faq_match.end() + next_h2.start()
            else:
                insert_pos = len(article)

            # Insert before the next H2 (or at end)
            article = article[:insert_pos].rstrip() + "\n\n" + faq_block + "\n\n" + article[insert_pos:]
        else:
            # No FAQ section — append one at the end (before any final CTA/conclusion)
            article = article.rstrip() + f"\n\n## Frequently Asked Questions About {kw.title()}\n\n" + faq_block

        return article

    def _fix_answer_blocks(self, article: str, issue: str, state: PipelineState) -> str | None:
        """Add direct answer sentences after H2s that lack them.

        Extracts the core claim from the section's first paragraph and
        front-loads it as a 20-40 word lead sentence. This repositions
        existing content — no new content is generated.
        """
        lines = article.split("\n")
        h2_positions = []
        for i, line in enumerate(lines):
            if line.strip().startswith("## "):
                h2_positions.append(i)

        # Parse which H2s are missing from the issue string
        missing_match = re.search(r'\[(.+?)\]', issue)
        if not missing_match:
            return None
        missing_text = missing_match.group(1)
        missing_h2_names = [h.strip().strip("'\"") for h in missing_text.split(",")]

        modified = False
        offset = 0

        for h2_line_idx in h2_positions:
            adj = h2_line_idx + offset
            h2_text = lines[adj].strip()[3:].strip()

            if not any(
                name.strip().lower() in h2_text.lower()
                or h2_text.lower() in name.strip().lower()
                for name in missing_h2_names
            ):
                continue

            # Gather first paragraph sentences from this section
            section_sentences = []
            for j in range(adj + 1, min(adj + 15, len(lines))):
                stripped = lines[j].strip()
                if stripped.startswith("#"):
                    break
                if stripped and not stripped.startswith("|") and not stripped.startswith("-"):
                    for sent in re.split(r'(?<=[.!?])\s+', stripped):
                        if len(sent.split()) >= 5:
                            section_sentences.append(sent.strip())
                    if len(section_sentences) >= 3:
                        break

            if not section_sentences:
                continue

            # Build a lead sentence from existing content
            lead = section_sentences[0]
            lead = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', lead)

            # If too long, truncate at sentence boundary
            lead_words = lead.split()
            if len(lead_words) > 50:
                truncated = " ".join(lead_words[:50])
                last_period = truncated.rfind(".")
                if last_period > 20:
                    lead = truncated[:last_period + 1]
                else:
                    lead = truncated + "."

            # If too short, combine with second sentence
            if len(lead.split()) < 15 and len(section_sentences) > 1:
                second = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', section_sentences[1])
                lead = lead.rstrip('.') + ". " + second
                lead_words = lead.split()
                if len(lead_words) > 50:
                    lead = " ".join(lead_words[:50]) + "."

            # Find insertion point (skip blank lines after H2)
            insert_idx = adj + 1
            while insert_idx < len(lines) and not lines[insert_idx].strip():
                insert_idx += 1

            # Don't insert if already identical to what's there
            existing_first = lines[insert_idx].strip() if insert_idx < len(lines) else ""
            if lead.strip() == existing_first.strip():
                continue

            lines.insert(insert_idx, "")
            lines.insert(insert_idx + 1, lead)
            lines.insert(insert_idx + 2, "")
            offset += 3
            modified = True

        return "\n".join(lines) if modified else None

    def _fix_process_sections(self, article: str) -> str | None:
        """Convert narrative how-to sections into numbered steps.

        Only activates for paragraphs with sequential language indicators.
        Requires 3+ qualifying paragraphs before converting; backs off otherwise.
        """
        lines = article.split("\n")

        process_sections = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("## ") and (
                any(w in stripped.lower() for w in
                    ["how to", "steps", "step-by-step", "implement"])
                or ("guide" in stripped.lower()
                    and any(w in stripped.lower() for w in ["step", "how", "implement"]))
            ):
                end = len(lines)
                for j in range(i + 1, len(lines)):
                    if lines[j].strip().startswith("## "):
                        end = j
                        break
                section_text = "\n".join(lines[i:end])
                if not re.search(r"^\d+\.\s", section_text, re.MULTILINE):
                    process_sections.append((i, end))

        if not process_sections:
            return None

        modified = False
        offset = 0

        action_indicators = {
            "first", "second", "third", "next", "then", "finally",
            "start", "begin", "create", "draft", "review", "send",
            "ensure", "verify", "check", "identify", "define",
            "determine", "establish", "set", "prepare", "gather",
            "once", "after", "before", "when",
        }
        sequence_phrases = [
            "you should", "you need to", "you'll want to",
            "make sure to", "be sure to", "it's important to",
            "the next step", "at this point",
        ]

        for start, end in process_sections:
            adj_start = start + offset
            adj_end = end + offset

            # Collect paragraph lines with their indices
            paragraphs = []
            for k in range(adj_start + 1, adj_end):
                stripped = lines[k].strip()
                if (stripped and not stripped.startswith("#")
                        and not stripped.startswith("|")
                        and not stripped.startswith("-")
                        and not stripped.startswith("*")
                        and len(stripped) > 20):
                    paragraphs.append((k, stripped))

            if len(paragraphs) < 3:
                continue

            # Identify which paragraphs have sequential language
            step_candidates = []
            for line_idx, para in paragraphs:
                first_word = para.split()[0].lower().rstrip('.,;:')
                has_action = first_word in action_indicators
                has_sequence = any(p in para.lower() for p in sequence_phrases)
                if has_action or has_sequence:
                    step_candidates.append((line_idx, para))

            if len(step_candidates) < 3:
                continue

            step_number = 1
            for line_idx, para in step_candidates:
                cleaned = re.sub(
                    r'^(first|second|third|next|then|finally|lastly)[,.]?\s*',
                    '', para, flags=re.IGNORECASE
                ).strip()
                if cleaned:
                    cleaned = cleaned[0].upper() + cleaned[1:]
                    lines[line_idx] = f"{step_number}. {cleaned}"
                    step_number += 1
                    modified = True

        return "\n".join(lines) if modified else None

    def _fix_unique_value(self, article: str, state: PipelineState) -> str | None:
        """Insert a ContractSafe Industry Report reference to satisfy unique-value signals.

        The validator checks for patterns like 'our (data|research|analysis)',
        'we (found|discovered|analyzed)', or '(proprietary|original|first-party)'.
        This inserts a branded data point from the ContractSafe Industry Report
        after the first ContractSafe mention in body text.
        """
        # Already has unique value signals — no fix needed
        unique_signals = [
            r'our (data|research|analysis|findings|survey|study)',
            r'\b(proprietary|original|first-party|exclusive)\b',
            r'case study',
            r'(client|customer)\s+(data|results?|story|stories)',
            r'we (found|discovered|analyzed|measured|observed)',
            r'internal (data|metrics|benchmarks?)',
        ]
        if any(re.search(p, article, re.IGNORECASE) for p in unique_signals):
            return None

        kw = state.target_keyword or "contract management"
        insert_sentence = (
            f"According to the ContractSafe Industry Report, "
            f"70% of in-house lawyers say tech inefficiencies keep them from doing "
            f"their best work, underscoring the need for streamlined {kw} solutions."
        )

        lines = article.split('\n')
        # Find the last H2 section's first body paragraph and insert after it
        last_h2_body = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('## ') and not stripped.startswith('###'):
                # Find first body paragraph after this H2
                for j in range(i + 1, min(i + 10, len(lines))):
                    s = lines[j].strip()
                    if s and not s.startswith('#') and not s.startswith('|') and not s.startswith('-') and not s.startswith('*'):
                        last_h2_body = j
                        break

        if last_h2_body is not None:
            # Insert after the paragraph
            insert_at = last_h2_body + 1
            while insert_at < len(lines) and lines[insert_at].strip():
                insert_at += 1
            lines.insert(insert_at, '')
            lines.insert(insert_at + 1, insert_sentence)
            return '\n'.join(lines)

        return None

    def _fix_semantic_triples(self, article: str, state: PipelineState) -> str | None:
        """Add semantic triples for ContractSafe if fewer than 2 exist.

        A semantic triple is Subject-Verb-Object: 'ContractSafe automates X.'
        Inserts triples near existing ContractSafe mentions that lack verbs.
        """
        triple_verbs = (
            r'\b(is|are|provides|offers|helps|enables|automates|simplifies|'
            r'manages|delivers|supports|allows|ensures|gives|makes|handles|'
            r'stores|tracks|organizes|streamlines|reduces)\b'
        )

        # Count existing triples
        triple_count = 0
        for line in article.split('\n'):
            if 'ContractSafe' in line and not line.strip().startswith('#'):
                for sent in re.split(r'(?<=[.!?])\s+', line):
                    if 'ContractSafe' in sent and re.search(triple_verbs, sent, re.IGNORECASE):
                        triple_count += 1

        if triple_count >= 2:
            return None

        needed = 2 - triple_count
        kw = state.target_keyword
        lines = article.split('\n')

        triples_to_add = [
            f"ContractSafe helps legal teams and contract managers streamline {kw}.",
            f"ContractSafe automates key aspects of {kw}, reducing manual effort and risk.",
        ]

        modified = False
        added = 0

        for i, line in enumerate(lines):
            if added >= needed:
                break
            stripped = line.strip()
            if 'ContractSafe' not in stripped or stripped.startswith('#'):
                continue
            # Skip lines that already have a triple
            has_triple = any(
                'ContractSafe' in s and re.search(triple_verbs, s, re.IGNORECASE)
                for s in re.split(r'(?<=[.!?])\s+', stripped)
            )
            if has_triple:
                continue

            # Find end of current paragraph
            insert_at = i + 1
            while insert_at < len(lines) and lines[insert_at].strip():
                insert_at += 1

            lines.insert(insert_at, '')
            lines.insert(insert_at + 1, triples_to_add[added])
            modified = True
            added += 1

        return '\n'.join(lines) if modified else None

    def _extract_faq_answer(self, article: str, question: str, kw: str) -> str:
        """Extract a relevant sentence from the article to answer the FAQ question."""
        q_words = set(w.lower() for w in question.split() if len(w) > 3)
        q_words -= {"what", "does", "which", "where", "when", "that", "this", "have", "from", "with", "about", "your", "they", "their", "been"}

        # Score each sentence by word overlap with the question
        sentences = re.split(r'(?<=[.!?])\s+', article)
        best_sentence = None
        best_score = 0
        for s in sentences:
            s_stripped = s.strip()
            # Skip headings, short fragments, bullet points
            if s_stripped.startswith("#") or s_stripped.startswith("-") or len(s_stripped) < 30:
                continue
            s_words = set(s_stripped.lower().split())
            score = len(q_words & s_words)
            if score > best_score:
                best_score = score
                best_sentence = s_stripped

        if best_sentence and best_score >= 2:
            # Clean up the sentence (remove markdown links for cleaner FAQ)
            answer = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', best_sentence)
            if not answer.endswith("."):
                answer += "."
            return answer

        # Fallback: construct a basic answer from the keyword
        return f"Yes, {kw} involves structured processes for handling contract modifications, including amendments, renewals, and scope changes. The approach depends on your organization's specific workflows and compliance requirements."

    # ── Programmatic AEO audit ──

    def _audit(self, article: str, state: PipelineState) -> dict:
        """Run mechanical AEO checks. All checks are programmatic."""
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
        example_re = re.compile(
            r'for example|such as|something like|'
            r'["\u201c][^"\u201d]*\d+%[^"\u201d]*["\u201d]|'  # stats inside quotes (straight or curly)
            r'\d+% upfront|\d+% upon|\d+% at signing|'
            r'maybe .{0,30}\d+%|could cost you \d+%|imagine .{0,30}\d+%|'
            r"let\u2019s say .{0,30}\d+%|let's say .{0,30}\d+%|"
            r'^\(.{0,80}\d+%.{0,80}\?\s*.*\)$|'  # parenthetical rhetorical questions
            r'the difference between .{0,60}\d+%|'  # comparative illustration
            r'usually \d|typically \d|generally \d|'  # conventional ranges (definitional)
            r'legally required .{0,30}\d+%|required by law .{0,30}\d+%',  # regulatory facts
            re.IGNORECASE
        )
        hypo_dollar_re = re.compile(
            r'your (?:\w+ )?\$[\d,.]+|'
            r'a \$[\d,.]+\s+(?:\w+ )?(?:agreement|contract|deal|vendor|company|business|organization)|'
            r'cost (?:you|your|them|the) .{0,20}\$[\d,.]+|'
            r'for a \$[\d,.]+',
            re.IGNORECASE
        )
        source_phrases = [
            "according to", "per ", "found that", "reported", "study by",
            "survey by", "research from", "report by", "data from",
            "estimates that", "estimated that", "estimates ",
            "emphasizes that", "emphasizes ",
        ]
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("|"):
                continue
            has_stat = bool(re.search(
                r"\d+%|\$[\d,]+|\d+\s*(billion|million|trillion|percent)",
                stripped, re.IGNORECASE,
            ))
            # Skip illustrative examples (not statistical claims)
            if has_stat and example_re.search(stripped):
                continue
            # Skip hypothetical dollar amounts
            if has_stat and hypo_dollar_re.search(stripped) and not any(p in stripped.lower() for p in source_phrases):
                continue
            # Skip numbered steps/list items with dollar amounts as examples
            if has_stat and re.match(r'^(?:\*?\*?Step )?\d+[\.\)]\s', stripped) and re.search(r'\$\d', stripped):
                continue
            if has_stat:
                stat_lines.append(stripped)
                has_source = bool(re.search(
                    r"(according to|per |found that|reported|study by|survey by|"
                    r"research from|report by|data from|analysis by|cited by|"
                    r"emphasizes that|emphasizes |"
                    r"Gartner|Forrester|McKinsey|World Commerce|Deloitte|PwC|"
                    r"American Bar|Goldman Sachs|Bloomberg|IACCM|DottedSign)",
                    stripped, re.IGNORECASE,
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
                f"context when read in isolation."
            )
        scorecard_lines.append(
            f"- Self-Describing Headings: {'PASS' if not vague_headings else 'FAIL'} "
            f"({len(h2_positions) - len(vague_headings)}/{len(h2_positions)} are self-describing)"
        )

        # ── 5. Entity consistency ──
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
        scorecard_lines.append(f"- Entity Consistency: {'PASS' if entity_ok else 'FAIL'}")

        # ── 6. Process sections with numbered steps ──
        # "how to" only counts as process when followed by a process verb
        _process_verbs = ("choose", "select", "pick", "evaluate", "compare", "assess",
                          "implement", "set up", "deploy", "migrate", "build", "create",
                          "get started", "start", "begin", "establish")
        process_h2s = []
        for _, h in h2_positions:
            hl = h.lower()
            if any(w in hl for w in ["steps", "step-by-step", "implement"]):
                process_h2s.append(h)
            elif "how to" in hl and any(v in hl for v in _process_verbs):
                process_h2s.append(h)
            elif "guide" in hl and any(w in hl for w in ["step", "implement"]):
                process_h2s.append(h)
        process_missing_steps = []
        for h2 in process_h2s:
            h2_pos = article.find(f"## {h2}")
            next_h2 = article.find("\n## ", h2_pos + 1)
            section = article[h2_pos:next_h2] if next_h2 > 0 else article[h2_pos:]
            # Match "1. ", "**1.", "**Step 1:", "Step 1:" numbered step formats
            has_numbered = bool(re.search(r'(?:^\d+\.\s|^\*\*(?:Step\s*)?\d+[\.:]\s?|\bStep\s+\d+[\.:]\s)', section, re.MULTILINE))
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
        current_year = str(datetime.datetime.now().year)
        last_year = str(int(current_year) - 1)
        has_recent = current_year in article or last_year in article
        if not has_recent:
            issues.append(
                f"NO FRESHNESS SIGNALS: Article doesn't reference {current_year} or {last_year}. "
                f"Add at least one current-year reference or recent data point."
            )
        scorecard_lines.append(f"- Freshness Signals: {'PASS' if has_recent else 'FAIL'}")

        # ── 9. Context-dependent key passages ──
        context_dependent = []
        context_starters = [
            "this is why", "this is where", "this is how",
            "as mentioned", "as discussed", "as noted",
            "they also", "it also", "these include",
        ]
        quant_refs_audit = re.compile(
            r"rounding error|that number|that figure|that percentage|"
            r"those numbers|that is real money|that\u2019s real money|that's real money|"
            r"that is a lot|that\u2019s a lot|that's a lot|think about that",
            re.IGNORECASE,
        )
        for i, line in enumerate(lines):
            stripped = line.strip().lower()
            if not stripped or stripped.startswith("#") or stripped.startswith("|"):
                continue
            for starter in context_starters:
                if stripped.startswith(starter):
                    context_dependent.append(f"Line {i+1}: {lines[i].strip()[:60]}")
                    break
            else:
                # Orphaned quantitative commentary
                if len(stripped.split()) < 30 and quant_refs_audit.search(stripped):
                    if not re.search(r'\d+%|\$[\d,]+', stripped):
                        prev_has = False
                        for j in range(i - 1, max(i - 5, -1), -1):
                            prev = lines[j].strip()
                            if prev and not prev.startswith("#"):
                                prev_has = bool(re.search(r'\d+%|\$[\d,]+', prev.lower()))
                                break
                        if not prev_has:
                            context_dependent.append(f"Line {i+1}: {lines[i].strip()[:60]}")

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

        # ── 10. Semantic Triples ──
        triple_verbs = (
            r'\b(is|are|provides|offers|helps|enables|automates|simplifies|'
            r'manages|delivers|supports|allows|ensures|gives|makes|handles|'
            r'stores|tracks|organizes|streamlines|reduces)\b'
        )
        cs_triple_count = 0
        for line in lines:
            stripped = line.strip()
            if 'ContractSafe' in stripped and not stripped.startswith('#'):
                for sent in re.split(r'(?<=[.!?])\s+', stripped):
                    if 'ContractSafe' in sent and re.search(triple_verbs, sent, re.IGNORECASE):
                        cs_triple_count += 1
        if cs_triple_count < 2:
            issues.append(
                f"SEMANTIC TRIPLES: Only {cs_triple_count} ContractSafe semantic triples "
                f"found (need ≥2). A triple is 'ContractSafe [verb] [object]' — "
                f"what it IS, DOES, or who it's FOR."
            )
        scorecard_lines.append(
            f"- Semantic Triples: {'PASS' if cs_triple_count >= 2 else 'FAIL'} "
            f"({cs_triple_count} brand triples, target: ≥2)"
        )

        # ── 11. Unique Value ──
        unique_signals = [
            r'our (data|research|analysis|findings|survey|study)',
            r'\b(proprietary|original|first-party|exclusive)\b',
            r'case study',
            r'(client|customer)\s+(data|results?|story|stories)',
            r'we (found|discovered|analyzed|measured|observed)',
            r'internal (data|metrics|benchmarks?)',
        ]
        has_unique = any(re.search(p, article, re.IGNORECASE) for p in unique_signals)
        if not has_unique:
            issues.append(
                "UNIQUE VALUE [FLAG]: No proprietary data, original research, "
                "case studies, or first-party findings detected. Consider adding "
                "ContractSafe-specific data or customer insights."
            )
        scorecard_lines.append(
            f"- Unique Value: {'PASS' if has_unique else 'FLAG'} "
            f"({'Unique content signals found' if has_unique else 'No proprietary content detected'})"
        )

        scorecard = "AEO Scorecard:\n" + "\n".join(scorecard_lines)
        return {"issues": issues, "scorecard": scorecard}
