"""Agent 10: SEO Pass - fully programmatic SEO audit and fixes.

Every issue is fixed in Python. No LLM call. If an issue can't be fixed
programmatically, it's logged for manual review — not sent to Claude to
burn 2 minutes on a 16k prompt.
"""

from __future__ import annotations

import re
from agents.base import BaseAgent
from state import PipelineState


class SEOPassAgent(BaseAgent):
    name = "SEO Pass"
    description = "Audit article for SEO issues and fix them programmatically"
    agent_number = 10
    emoji = "\U0001f50e"
    timeout = 120  # 2 min — but should complete in <1s since it's fully programmatic

    def run(self, state: PipelineState) -> PipelineState:
        article = state.fact_check_article or state.voice_pass_article or state.draft_article

        # ── Dedup existing link URLs (writer may have used same URL multiple times) ──
        article = self._dedup_existing_links(article)

        # ── Audit ──
        self.progress("Auditing keyword density, links, and heading structure...")
        audit = self._audit(article, state)
        issues = audit["issues"]

        self.progress(f"Found {len(issues)} SEO issues")
        for issue in issues:
            self.progress(f"  - {issue[:100]}")

        # ── Fix everything programmatically ──
        self.progress("Inserting internal and external links programmatically...")
        article, fixed = self._apply_all_fixes(article, issues, state)

        # ── Dedup again after link insertion (may have added duplicate URLs) ──
        article = self._dedup_existing_links(article)

        # ── Re-audit to see what remains ──
        re_audit = self._audit(article, state)
        remaining = re_audit["issues"]

        if remaining:
            self.log(f"[yellow]{len(remaining)} issues remain after programmatic fixes (logged, not sent to Claude):[/yellow]")
            for issue in remaining:
                self.log(f"  [yellow]- {issue[:100]}[/yellow]")

        state.seo_pass_article = article
        state.seo_changes = fixed  # already list of dicts with change + detail
        self.log(f"SEO pass complete. {len(fixed)} programmatic fixes. {len(remaining)} remaining (manual review).")
        return state

    # ══════════════════════════════════════════════════════════════
    # PROGRAMMATIC FIXES — one method per issue type
    # ══════════════════════════════════════════════════════════════

    def _apply_all_fixes(self, article: str, issues: list, state: PipelineState) -> tuple[str, list[dict]]:
        """Apply every possible programmatic fix. Returns (article, list_of_fix_dicts)."""
        fixed = []
        self._global_modified_lines = set()  # Track lines modified across all link-insertion calls
        self._anchor_phrases = self._build_anchor_phrases(state)  # Cluster-informed phrases

        # Count links before fixes for detail reporting
        internal_before = len(re.findall(r'\[.*?\]\(https?://(?:www\.)?contractsafe\.com[^)]*\)', article))
        external_before = len(re.findall(r'\[.*?\]\(https?://[^)]+\)', article)) - internal_before

        # Process link insertion FIRST (before keyword destuffing, which removes anchor points)
        link_issues = [i for i in issues if ("INTERNAL LINKS" in i or "EXTERNAL LINKS" in i) and "ONLY" in i]
        other_issues = [i for i in issues if i not in link_issues]
        for issue in link_issues + other_issues:
            if "KEYWORD NOT IN ANY H2" in issue:
                result = self._fix_keyword_in_h2(article, state.target_keyword)
                if result:
                    article = result
                    fixed.append({"change": "keyword_in_h2", "detail": f"Inserted '{state.target_keyword}' into an H2 heading"})

            elif "KEYWORD UNDERUSED" in issue:
                result = self._fix_keyword_underuse(article, state.target_keyword)
                if result:
                    article = result
                    fixed.append({"change": "keyword_density", "detail": f"Added natural keyword mentions for '{state.target_keyword}'"})

            elif "KEYWORD MISSING FROM FIRST 100" in issue:
                result = self._fix_keyword_in_intro(article, state.target_keyword)
                if result:
                    article = result
                    fixed.append({"change": "keyword_in_intro", "detail": f"Added '{state.target_keyword}' to the introduction"})

            elif "MISSING SECONDARY KEYWORDS" in issue:
                result = self._fix_secondary_keywords(article, state.secondary_keywords)
                if result:
                    article = result
                    fixed.append({"change": "secondary_keywords", "detail": f"Inserted secondary keywords: {', '.join(state.secondary_keywords[:5])}"})

            elif "INTERNAL LINKS" in issue and "ONLY" in issue:
                result = self._fix_add_links(article, state.internal_links, "internal")
                if result:
                    article = result
                    new_count = len(re.findall(r'\[.*?\]\(https?://(?:www\.)?contractsafe\.com[^)]*\)', result))
                    added = new_count - internal_before
                    fixed.append({"change": "add_internal_links", "detail": f"Inserted {added} internal links to contractsafe.com pages"})

            elif "EXTERNAL LINKS" in issue and "ONLY" in issue:
                result = self._fix_add_links(article, state.external_links, "external")
                if result:
                    article = result
                    new_ext = len(re.findall(r'\[.*?\]\(https?://[^)]+\)', result)) - len(re.findall(r'\[.*?\]\(https?://(?:www\.)?contractsafe\.com[^)]*\)', result))
                    added = new_ext - external_before
                    fixed.append({"change": "add_external_links", "detail": f"Inserted {added} external links to authoritative sources"})

            elif "LINKS NOT FRONT-LOADED" in issue:
                result = self._fix_front_loading(article, state)
                if result:
                    article = result
                    fixed.append({"change": "link_front_loading", "detail": "Redistributed links toward the first third of the article"})

            elif "NAKED URLs" in issue:
                result = self._fix_naked_urls(article)
                if result:
                    article = result
                    fixed.append({"change": "naked_urls", "detail": "Wrapped bare URLs in markdown link syntax"})

            elif "GENERIC ANCHOR TEXT" in issue:
                result = self._fix_generic_anchors(article)
                if result:
                    article = result
                    fixed.append({"change": "generic_anchors", "detail": "Replaced generic anchor text ('click here', 'learn more') with descriptive text"})

            elif "KEYWORD STUFFING" in issue:
                result = self._fix_keyword_overuse(article, state.target_keyword)
                if result:
                    article = result
                    fixed.append({"change": "keyword_destuffing", "detail": f"Reduced overuse of '{state.target_keyword}'"})

        return article, fixed

    def _fix_keyword_in_h2(self, article: str, keyword: str) -> str | None:
        """Insert keyword into the most relevant H2 heading."""
        # Long keywords (4+ words) can't be naturally inserted into H2s.
        # They'll appear in H1, intro, and body text — that's sufficient.
        if len(keyword.split()) > 3:
            return None

        h2s = re.findall(r"^(## .+)$", article, re.MULTILINE)
        if not h2s:
            return None

        kw_lower = keyword.lower()
        kw_words = set(kw_lower.split())

        # Check if any H2 already contains keyword
        for h2 in h2s:
            if kw_lower in h2.lower():
                return None

        # Find H2 with most keyword-word overlap
        best_h2 = None
        best_overlap = 0
        for h2 in h2s:
            h2_words = set(h2.lower().split())
            overlap = len(kw_words & h2_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best_h2 = h2

        if not best_h2:
            best_h2 = h2s[0]

        h2_text = best_h2[3:].strip()
        kw_title = keyword.title() if keyword == keyword.lower() else keyword
        new_h2 = f"## {kw_title}: {h2_text}"

        return article.replace(best_h2, new_h2, 1)

    def _fix_keyword_underuse(self, article: str, keyword: str) -> str | None:
        """Add keyword naturally to topically relevant body paragraphs."""
        kw_lower = keyword.lower()
        current_count = article.lower().count(kw_lower)
        kw_word_count = len(keyword.split())

        # Long keywords (4+ words) are too awkward to inject into body paragraphs
        # with templates like ", a key aspect of {keyword}." — they produce
        # ungrammatical output. H1 + intro fixes handle minimum placement.
        if kw_word_count >= 4:
            return None

        target = max(3, len(article.split()) // 500)
        if current_count >= target:
            return None

        needed = target - current_count
        lines = article.split("\n")
        modified = False
        topic_words = set(kw_lower.split())

        for i, line in enumerate(lines):
            if needed <= 0:
                break
            stripped = line.strip()
            if (not stripped or stripped.startswith("#") or stripped.startswith("|")
                    or stripped.startswith("-") or len(stripped) < 50
                    or kw_lower in stripped.lower()
                    or ("[" in stripped and "](" in stripped)):
                continue

            line_words = set(stripped.lower().split())
            if not (topic_words & line_words):
                continue

            if stripped.endswith("."):
                new_line = stripped[:-1] + f", a key aspect of {keyword}."
                lines[i] = line.replace(stripped, new_line)
                needed -= 1
                modified = True

        return "\n".join(lines) if modified else None

    def _fix_keyword_overuse(self, article: str, keyword: str) -> str | None:
        """Remove excess keyword occurrences by replacing them with pronouns or synonyms.

        Targets non-essential occurrences: those not in H1, H2, first paragraph,
        key takeaways, or inside markdown links. Replaces with contextual pronouns
        like "this process", "it", or just drops the keyword from appositives.
        """
        kw_lower = keyword.lower()
        kw_word_count = len(keyword.split())

        # Calculate target
        word_count = len(article.split())
        if kw_word_count >= 4:
            ideal_max = max(4, word_count // 500)
        else:
            ideal_max = max(7, word_count // 200)

        current_count = article.lower().count(kw_lower)
        if current_count <= ideal_max:
            return None

        excess = current_count - ideal_max
        lines = article.split("\n")
        modified = False
        removed = 0

        # Identify protected lines (H1, H2, first body paragraph, key takeaways bullet, links)
        first_body_idx = None
        in_takeaways = False
        protected_lines = set()

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("# ") or stripped.startswith("## "):
                protected_lines.add(i)
                if "takeaway" in stripped.lower() or "key " in stripped.lower():
                    in_takeaways = True
                else:
                    in_takeaways = False
                continue
            if in_takeaways and stripped.startswith("- "):
                protected_lines.add(i)
                continue
            if first_body_idx is None and stripped and not stripped.startswith("#"):
                first_body_idx = i
                protected_lines.add(i)
                in_takeaways = False

        # Remove excess from unprotected lines (bottom-up to preserve earlier occurrences)
        for i in range(len(lines) - 1, -1, -1):
            if removed >= excess:
                break
            if i in protected_lines:
                continue
            stripped = lines[i].strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Don't touch lines where keyword is inside a markdown link anchor
            if f"[{kw_lower}" in stripped.lower() or f" {kw_lower}](" in stripped.lower():
                continue

            low = stripped.lower()
            if kw_lower not in low:
                continue

            # Strategy 1: Remove ", a key aspect of <keyword>." appositives (added by _fix_keyword_underuse)
            appositive = f", a key aspect of {keyword}"
            if appositive.lower() in low:
                new_stripped = stripped.replace(appositive, "").replace(appositive.lower(), "")
                lines[i] = lines[i].replace(stripped, new_stripped)
                modified = True
                removed += 1
                continue

            # Strategy 2: Replace standalone keyword with "this process"
            # Only if the keyword appears as a standalone phrase (not part of a link)
            # and the replacement would be grammatically correct
            idx = low.index(kw_lower)
            # Ensure it's not inside a markdown link
            before = stripped[:idx]
            if before.count('[') > before.count(']'):
                continue
            # Don't replace if keyword is preceded by an article, adjective, or modifier
            before_text = stripped[:idx].rstrip()
            before_word = before_text.split()[-1].lower() if before_text else ""
            if before_word in ("the", "a", "an", "your", "their", "our", "this", "that",
                               "right", "is", "modern", "effective", "good", "better",
                               "best", "poor", "strong", "robust", "solid", "proper",
                               "basic", "simple", "advanced", "automated", "manual"):
                continue  # "Modern this process" is ungrammatical
            # Don't replace if followed by a noun that would make "this process X" awkward
            after_kw = stripped[idx + len(keyword):].lstrip()
            after_word = after_kw.split()[0].lower().rstrip(".,;:!?") if after_kw.split() else ""
            if after_word in ("software", "system", "tool", "tools", "platform", "solution",
                              "solutions", "program", "programs", "framework", "strategy"):
                continue  # "this process software" is ungrammatical
            # Capitalize at start of sentence, bullet, or numbered item
            at_sentence_start = (
                idx == 0
                or (before_text and before_text[-1] in '.!?')
                or (before_text and before_text.rstrip() in ('-', '*', '- ', '* '))
                or bool(re.match(r'^(?:- |\* |\d+\.\s)', stripped))
            )
            replacement = "This process" if at_sentence_start else "this process"
            new_stripped = stripped[:idx] + replacement + stripped[idx + len(keyword):]
            lines[i] = lines[i].replace(stripped, new_stripped)
            modified = True
            removed += 1

        return "\n".join(lines) if modified else None

    @staticmethod
    def _build_anchor_phrases(state: PipelineState) -> list[str]:
        """Build link anchor phrases from the keyword cluster.

        Extracts meaningful 2-3 word noun phrases from supporting keywords.
        Result: diverse anchors like "grant compliance," "donor agreements,"
        "volunteer waivers" instead of every link using "contract management."
        """
        # Words that don't add meaning to an anchor phrase
        skip_words = {
            "for", "the", "and", "best", "software", "small", "with",
            "from", "that", "this", "about", "how", "what", "when",
            "companies", "tools", "systems", "platforms", "solutions",
        }

        cluster_phrases = []
        seen = set()

        def add(phrase):
            p = phrase.lower().strip()
            if p and p not in seen and 2 <= len(p.split()) <= 3:
                # Must have at least 2 meaningful words
                meaningful = [w for w in p.split() if w not in skip_words and len(w) > 2]
                if len(meaningful) >= 2:
                    seen.add(p)
                    cluster_phrases.append(p)

        # Extract from secondary keywords
        for sk in (state.secondary_keywords or []):
            words = sk.lower().split()
            # Try the full keyword first (if short enough)
            add(sk)
            # Extract 2-3 word subphrases
            for length in (3, 2):
                for i in range(len(words) - length + 1):
                    add(" ".join(words[i:i + length]))

        # Fallback generic phrases
        base_phrases = [
            "contract lifecycle management",
            "contract compliance", "contract renewal",
            "contract obligations", "contract deadlines",
            "contracts", "agreements", "renewals",
        ]

        return (cluster_phrases + base_phrases)[:30]

    @staticmethod
    def _find_whole_word(phrase: str, text: str) -> int:
        """Find phrase in text at word boundaries. Returns index or -1."""
        start = 0
        while True:
            idx = text.find(phrase, start)
            if idx == -1:
                return -1
            # Check word boundary before
            if idx > 0 and text[idx - 1].isalpha():
                start = idx + 1
                continue
            # Check word boundary after
            end = idx + len(phrase)
            if end < len(text) and text[end].isalpha():
                start = idx + 1
                continue
            return idx

    def _dedup_existing_links(self, article: str) -> str:
        """Remove duplicate link URLs already in the article from the writer.

        Keeps the first occurrence of each URL; subsequent occurrences are
        replaced with just the anchor text (link removed).
        """
        seen_urls: set[str] = set()
        lines = article.split("\n")
        any_changed = False
        link_pattern = re.compile(r'\[([^\]]+)\]\((https?://[^)]+)\)')

        for i, line in enumerate(lines):
            new_line = line
            offset = 0
            line_changed = False

            for m in link_pattern.finditer(line):
                url = m.group(2).lower().rstrip("/")
                if url in seen_urls:
                    anchor = m.group(1)
                    start = m.start() + offset
                    end = m.end() + offset
                    new_line = new_line[:start] + anchor + new_line[end:]
                    offset += len(anchor) - (m.end() - m.start())
                    line_changed = True
                else:
                    seen_urls.add(url)

            if line_changed:
                lines[i] = new_line
                any_changed = True

        return "\n".join(lines) if any_changed else article

    def _fix_keyword_in_intro(self, article: str, keyword: str) -> str | None:
        """Add keyword to the first paragraph if missing from first 100 words."""
        kw_lower = keyword.lower()
        lines = article.split("\n")

        # Find first body paragraph (not heading, not empty)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and len(stripped) > 30:
                if kw_lower not in stripped.lower():
                    # Append keyword mention to first paragraph
                    if stripped.endswith("."):
                        new_line = stripped[:-1] + f", particularly when it comes to {keyword}."
                        lines[i] = line.replace(stripped, new_line)
                        return "\n".join(lines)
                break
        return None

    def _fix_secondary_keywords(self, article: str, secondary_kws: list[str]) -> str | None:
        """Secondary keywords are strategic guidance from the keyword cluster,
        not phrases to mechanically inject. The writer received them in the brief.
        If the article doesn't contain them as exact phrases, that's acceptable
        as long as the topics are covered (validated by Agent 13 with word-overlap).
        Mechanical injection (e.g., '(including X)') always produces bad output."""
        return None

    def _fix_add_links(self, article: str, available_links: list[dict], link_type: str) -> str | None:
        """Add links from the available pool to topically relevant sentences."""
        from link_policy import is_blocked

        if not available_links:
            return None

        # Find which URLs are already in the article
        existing_urls = set(u.lower() for _, u in re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', article))

        # Get unused available links, filtering out blocked competitors and deduplicating by URL
        seen_urls: set[str] = set()
        unused = []
        for l in available_links:
            url_lower = l.get("url", "").lower().rstrip("/")
            if url_lower not in existing_urls and url_lower not in seen_urls and not is_blocked(l.get("url", "")):
                seen_urls.add(url_lower)
                unused.append(l)
        if not unused:
            return None

        lines = article.split("\n")
        modified = False
        added = 0

        for link in unused[:7]:  # try up to 7 to meet minimum of 5
            url = link.get("url", "")
            anchor = link.get("anchor_suggestion", "") or link.get("anchor", "")
            if not url or not anchor:
                continue

            # Use anchor + title words for broader matching
            title = link.get("title", "")
            match_text = f"{anchor} {title}".lower()
            stopwords = {"the", "a", "an", "of", "for", "and", "in", "to", "with", "is", "on", "by", "at", "how", "why", "what"}
            anchor_words = set(match_text.split()) - stopwords

            # Try with 2+ word overlap first, then fall back to 1
            placed = False
            for min_overlap in (2, 1):
                if placed:
                    break
                for i, line in enumerate(lines):
                    if i in self._global_modified_lines:
                        continue  # already had a link added by any fix call
                    stripped = line.strip()
                    link_count = stripped.count("](")
                    if (not stripped or stripped.startswith("#") or stripped.startswith("|")
                            or stripped.startswith("- ") or stripped.startswith("* ")
                            or re.match(r'^\d+[\.\)]\s', stripped)
                            or len(stripped) < 40 or url.lower() in line.lower()
                            or link_count >= 2):
                        continue

                    line_words = set(stripped.lower().split())
                    overlap = anchor_words & line_words
                    if len(overlap) >= min_overlap and stripped.endswith("."):
                        new_line = self._insert_link_naturally(stripped, anchor, url)
                        if new_line is None:
                            continue  # would exceed 42 words
                        lines[i] = line.replace(stripped, new_line)
                        modified = True
                        added += 1
                        self._global_modified_lines.add(i)
                        placed = True
                        break

        # Fallback: if we still haven't reached 5, wrap keyword phrases directly
        if link_type == "internal":
            # Count current internal links (including any just placed above)
            current_internal = len(re.findall(
                r'\[([^\]]+)\]\((https?://[^)]*contractsafe\.com[^)]*)\)',
                "\n".join(lines)
            ))
            if current_internal < 5:
                remaining_unused = [
                    l for l in unused
                    if l.get("url", "").lower() not in set(
                        u.lower() for _, u in re.findall(
                            r'\[([^\]]+)\]\((https?://[^)]+)\)', "\n".join(lines)
                        )
                    )
                ]
                for link in remaining_unused:
                    if current_internal >= 5:
                        break
                    url = link.get("url", "")
                    if not url:
                        continue
                    all_internal_phrases = self._anchor_phrases
                    placed = False
                    for i, line in enumerate(lines):
                        if i in self._global_modified_lines:
                            continue
                        stripped = line.strip()
                        if (not stripped or stripped.startswith("#")
                                or stripped.startswith("|")
                                or len(stripped) < 30):
                            continue
                        low = stripped.lower()
                        for phrase in all_internal_phrases:
                            idx = self._find_whole_word(phrase, low)
                            if idx >= 0 and f"[{phrase}" not in low:
                                before = stripped[:idx]
                                if before.count('[') > before.count(']'):
                                    continue
                                original = stripped[idx:idx + len(phrase)]
                                new_stripped = (
                                    stripped[:idx] + f"[{original}]({url})"
                                    + stripped[idx + len(phrase):]
                                )
                                lines[i] = line.replace(stripped, new_stripped)
                                modified = True
                                current_internal += 1
                                self._global_modified_lines.add(i)
                                placed = True
                                break
                        if placed:
                            break
                        if current_internal >= 5:
                            break

        # Fallback for external links: wrap topic-relevant phrases
        if link_type == "external":
            all_links_now = re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', "\n".join(lines))
            current_external = len([(t, u) for t, u in all_links_now if "contractsafe.com" not in u.lower()])
            if current_external < 3:
                remaining_unused = [
                    l for l in unused
                    if l.get("url", "").lower() not in set(
                        u.lower() for _, u in all_links_now
                    )
                ]
                for link in remaining_unused:
                    if current_external >= 3:
                        break
                    url = link.get("url", "")
                    if not url:
                        continue
                    # Find an unlinked sentence containing topic-related terms
                    for i, line in enumerate(lines):
                        if i in self._global_modified_lines:
                            continue
                        stripped = line.strip()
                        if (not stripped or stripped.startswith("#")
                                or stripped.startswith("|") or "](http" in stripped
                                or len(stripped) < 30):
                            continue
                        low = stripped.lower()
                        all_phrases = self._anchor_phrases
                        placed = False
                        for phrase in all_phrases:
                            idx = self._find_whole_word(phrase, low)
                            if idx >= 0 and f"[{phrase}" not in low:
                                # Don't wrap if already inside a link
                                before = stripped[:idx]
                                if before.count('[') > before.count(']'):
                                    continue
                                original = stripped[idx:idx + len(phrase)]
                                new_stripped = (
                                    stripped[:idx] + f"[{original}]({url})"
                                    + stripped[idx + len(phrase):]
                                )
                                lines[i] = line.replace(stripped, new_stripped)
                                modified = True
                                current_external += 1
                                self._global_modified_lines.add(i)
                                placed = True
                                break
                        if placed:
                            break
                    if current_external >= 3:
                        break

            # Last resort: if still short, try single-word anchors on any body line
            if current_external < 3:
                single_words = [
                    "contracts", "agreements", "compliance", "procurement",
                    "obligations", "renewals", "negotiations", "stakeholders",
                ]
                remaining_unused2 = [
                    l for l in unused
                    if l.get("url", "").lower() not in set(
                        u.lower() for _, u in re.findall(
                            r'\[([^\]]+)\]\((https?://[^)]+)\)', "\n".join(lines)
                        )
                    )
                ]
                for link in remaining_unused2:
                    if current_external >= 3:
                        break
                    url = link.get("url", "")
                    if not url:
                        continue
                    for i, line in enumerate(lines):
                        if i in self._global_modified_lines:
                            continue
                        stripped = line.strip()
                        if (not stripped or stripped.startswith("#")
                                or stripped.startswith("|") or "](http" in stripped
                                or len(stripped) < 30):
                            continue
                        low = stripped.lower()
                        for word in single_words:
                            if word in low and f"[{word}" not in low:
                                idx = low.index(word)
                                before = stripped[:idx]
                                if before.count('[') > before.count(']'):
                                    continue
                                original = stripped[idx:idx + len(word)]
                                new_stripped = (
                                    stripped[:idx] + f"[{original}]({url})"
                                    + stripped[idx + len(word):]
                                )
                                lines[i] = line.replace(stripped, new_stripped)
                                modified = True
                                current_external += 1
                                self._global_modified_lines.add(i)
                                break
                        if current_external >= 3:
                            break

        return "\n".join(lines) if modified else None

    def _insert_link_naturally(self, sentence: str, anchor: str, url: str) -> str | None:
        """Insert a link by wrapping the best matching keyword span in the sentence.

        Path 1: Exact anchor text found in sentence → wrap it.
        Path 2: Find longest contiguous 2-4 word span matching anchor topic words → wrap it.
        Path 3: No match → return None (caller tries next sentence).

        Returns None if no suitable insertion point or result exceeds 42 words.
        """
        anchor_lower = anchor.lower()
        sentence_lower = sentence.lower()

        # Path 1: Exact anchor text in sentence — best case
        if anchor_lower in sentence_lower:
            idx = sentence_lower.index(anchor_lower)
            # Don't wrap if already inside a markdown link
            before = sentence[:idx]
            if before.count('[') > before.count(']'):
                return None
            original = sentence[idx:idx + len(anchor)]
            result = sentence[:idx] + f"[{original}]({url})" + sentence[idx + len(anchor):]
            if len(result.split()) > 42:
                return None
            return result

        # Path 2: Find the best multi-word span matching anchor topic words
        stopwords = {
            "the", "a", "an", "of", "for", "and", "in", "to", "with",
            "is", "on", "by", "at", "how", "why", "what", "are", "this",
            "that", "your", "their", "its", "can", "may", "will", "has",
        }
        # Exclude brand name so links don't anchor to "ContractSafe is..." sentences
        anchor_words = set(anchor_lower.split()) - stopwords - {'contractsafe'}

        if not anchor_words:
            return None

        words = sentence.split()
        best_span = None
        best_density = 0.0
        best_span_len = 0
        best_stopword_count = float('inf')

        for span_len in range(4, 1, -1):  # try all span sizes
            for start in range(len(words) - span_len + 1):
                span = words[start:start + span_len]
                span_text = " ".join(span)

                # Skip if span is inside an existing markdown link
                prefix_text = " ".join(words[:start])
                if prefix_text.count('[') > prefix_text.count(']'):
                    continue
                if '[' in span_text or '](' in span_text:
                    continue

                # Reject spans that cross clause or sentence boundaries
                if any(',' in w or ';' in w or '.' in w or '!' in w or '?' in w for w in span):
                    continue

                span_lower_words = set(w.lower().strip('.,;:!?') for w in span)
                matches = span_lower_words & anchor_words
                if len(matches) >= 2:
                    density = len(matches) / span_len
                    # Count stopwords in span (fewer = cleaner anchor text)
                    sw_count = sum(1 for w in span if w.lower().strip('.,;:!?') in stopwords)
                    # Prefer: higher density → fewer stopwords → longer span
                    if (density > best_density or
                            (density == best_density and sw_count < best_stopword_count) or
                            (density == best_density and sw_count == best_stopword_count and span_len > best_span_len)):
                        best_density = density
                        best_span = (start, start + span_len)
                        best_span_len = span_len
                        best_stopword_count = sw_count

        if best_span is None:
            return None

        start_idx, end_idx = best_span
        matched = " ".join(words[start_idx:end_idx])
        before_words = " ".join(words[:start_idx])
        after_words = " ".join(words[end_idx:])

        parts = []
        if before_words:
            parts.append(before_words)
        parts.append(f"[{matched}]({url})")
        if after_words:
            parts.append(after_words)

        result = " ".join(parts)
        if len(result.split()) > 42:
            return None
        return result

    def _fix_front_loading(self, article: str, state: PipelineState) -> str | None:
        """Improve link front-loading by adding unused links to the first third.

        Strategy: find available links not yet in the article, then add them
        to topically relevant sentences in the first third of the article.
        This doesn't move existing links (which would damage the writer's intent),
        it adds new ones where they naturally fit.
        """
        words = article.split()
        word_count = len(words)
        first_third_end = word_count // 3

        all_links = re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', article)
        total_links = len(all_links)
        if total_links == 0:
            return None

        first_third_text = " ".join(words[:first_third_end])
        links_in_first_third = len(re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', first_third_text))
        front_pct = links_in_first_third / total_links * 100

        if front_pct >= 50:
            return None  # Close enough

        # Find unused available links (filter competitors)
        from link_policy import is_blocked
        existing_urls = set(u.lower() for _, u in all_links)
        all_available = (state.internal_links or []) + (state.external_links or [])
        unused = [
            l for l in all_available
            if l.get("url", "").lower() not in existing_urls
            and not is_blocked(l.get("url", ""))
        ]

        if not unused:
            # No unused links to add. Accept the current distribution.
            return None

        # Find the line number boundary for the first third
        lines = article.split("\n")
        char_count = 0
        first_third_line_end = len(lines)
        target_chars = len(" ".join(words[:first_third_end]))
        for idx, line in enumerate(lines):
            char_count += len(line) + 1
            if char_count >= target_chars:
                first_third_line_end = idx
                break

        modified = False
        added = 0
        needed = max(1, int(total_links * 0.6) - links_in_first_third)  # how many to add to reach ~60%

        for link in unused[:needed + 1]:
            url = link.get("url", "")
            anchor = link.get("anchor_suggestion", "") or link.get("anchor", "")
            if not url or not anchor:
                continue

            anchor_words = set(anchor.lower().split()) - {"the", "a", "an", "of", "for", "and", "in", "to", "with", "is", "on", "by", "at"}

            # Only look at first third of article
            for i in range(min(first_third_line_end, len(lines))):
                if i in self._global_modified_lines:
                    continue
                line = lines[i]
                stripped = line.strip()
                if (not stripped or stripped.startswith("#") or stripped.startswith("|")
                        or stripped.startswith("- ") or stripped.startswith("* ")
                        or re.match(r'^\d+[\.\)]\s', stripped)
                        or len(stripped) < 40 or url.lower() in line.lower()
                        or "](" in stripped):  # skip lines with existing markdown links
                    continue

                line_words = set(stripped.lower().split())
                if len(anchor_words & line_words) >= 1 and stripped.endswith("."):
                    new_line = self._insert_link_naturally(stripped, anchor, url)
                    if new_line is None:
                        continue  # would exceed 42 words
                    lines[i] = line.replace(stripped, new_line)
                    self._global_modified_lines.add(i)
                    modified = True
                    added += 1
                    break

            if added >= needed:
                break

        return "\n".join(lines) if modified else None

    def _fix_naked_urls(self, article: str) -> str | None:
        """Convert naked URLs to markdown links."""
        # Find URLs not already in markdown link format
        naked = re.findall(r'(?<!\()(https?://\S+)(?!\))', article)
        naked = [u for u in naked if f"]({u}" not in article]

        if not naked:
            return None

        modified = article
        for url in naked:
            # Extract domain as anchor text
            domain = re.sub(r'https?://(www\.)?', '', url).split('/')[0]
            modified = modified.replace(url, f"[{domain}]({url})", 1)

        return modified if modified != article else None

    def _fix_generic_anchors(self, article: str) -> str | None:
        """Replace generic anchor text like 'click here' with the URL domain."""
        generic_pattern = r'\[(click here|learn more|read more|check out|here)\]\((https?://[^)]+)\)'
        matches = list(re.finditer(generic_pattern, article, re.IGNORECASE))

        if not matches:
            return None

        modified = article
        for match in reversed(matches):  # reverse to preserve positions
            url = match.group(2)
            domain = re.sub(r'https?://(www\.)?', '', url).split('/')[0]
            replacement = f"[{domain}]({url})"
            modified = modified[:match.start()] + replacement + modified[match.end():]

        return modified if modified != article else None

    # ══════════════════════════════════════════════════════════════
    # AUDIT — detects issues (unchanged from before)
    # ══════════════════════════════════════════════════════════════

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
        kw_word_count = len(kw.split())
        h2s = re.findall(r"^## (.+)$", article, re.MULTILINE)
        h2_has_kw = any(kw in h.lower() for h in h2s)
        # Long keywords (4+ words) can't be naturally inserted into H2s — skip the check
        if not h2_has_kw and h2s and kw_word_count <= 3:
            issues.append(f"KEYWORD NOT IN ANY H2. Reword one H2 to include '{state.target_keyword}' or a close variant.")
        report_lines.append(f"Keyword in H2: {'PASS' if h2_has_kw else 'FAIL'} (H2s: {h2s})")

        # ── 4. Keyword density (scaled by keyword length) ──
        kw_count = text_lower.count(kw)
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

        # ── 11. Word count (report only) ──
        target = state.target_word_count
        report_lines.append(f"Word count: {word_count} (target: {target})")

        report = "\n".join(report_lines)
        return {"issues": issues, "report": report}
