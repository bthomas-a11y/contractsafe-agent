"""Agent 11: AEO Pass - runs programmatic AEO audit, then has Claude fix failures.

Uses delta mode: Claude returns find/replace pairs instead of the full article.
"""

from __future__ import annotations

import re
from agents.base import BaseAgent
from state import PipelineState
from prompts.templates import AEO_PASS_SYSTEM


class AEOPassAgent(BaseAgent):
    name = "AEO Pass"
    description = "Audit article for AEO issues and fix them"
    agent_number = 11
    emoji = "\U0001f916"
    timeout = 120  # 2 min — delta mode is faster

    def run(self, state: PipelineState) -> PipelineState:
        article = (
            state.seo_pass_article
            or state.fact_check_article
            or state.voice_pass_article
            or state.draft_article
        )
        input_article = article

        # ── Run programmatic AEO audit FIRST ──
        self.progress("Running programmatic AEO audit...")
        audit = self._audit(article, state)
        issues = audit["issues"]
        report = audit["report"]

        self.progress(f"Found {len(issues)} AEO issues to fix")
        for issue in issues:
            self.progress(f"  - {issue}")

        issue_list = "\n".join(f"{i+1}. {issue}" for i, issue in enumerate(issues))

        paa = state.keyword_data.get("questions_people_ask", []) if state.keyword_data else []
        paa_text = "\n".join(f"- {q}" for q in paa[:10]) if paa else "None available."

        user_prompt = f"""## ISSUES TO FIX
{issue_list if issues else "No issues found."}

## People Also Ask Questions
{paa_text}

## ARTICLE
===ARTICLE_START===
{article}
===ARTICLE_END==="""

        self.progress("Having Claude fix identified issues (delta mode)...")
        response = self.call_llm(AEO_PASS_SYSTEM, user_prompt)

        # Parse and apply find/replace pairs (using shared parser from BaseAgent)
        changes = self.parse_delta_response(response)
        state.aeo_pass_article = self.apply_delta_changes(input_article, changes)
        state.aeo_changes = [{"change": c["find"][:50], "reason": c["replace"][:50]} for c in changes]

        if issues and not changes:
            self.log(f"[yellow]Warning: {len(issues)} issues identified but no changes parsed from response.[/yellow]")

        self.log(f"Audit found {len(issues)} issues. Applied {len(changes)} changes via delta mode.")
        return state

    # ── Programmatic AEO audit ──

    def _audit(self, article: str, state: PipelineState) -> dict:
        """Run concrete AEO checks in Python."""
        issues = []
        report_lines = []
        lines = article.split("\n")

        # ── 1. Question-style H2s need direct answer blocks ──
        question_h2s = []
        h2s_missing_answers = []
        for i, line in enumerate(lines):
            if line.strip().startswith("## "):
                h2_text = line.strip()[3:].strip()
                is_question = h2_text.rstrip().endswith("?") or any(
                    h2_text.lower().startswith(w) for w in ["what ", "how ", "why ", "when ", "where ", "who ", "can ", "do ", "does ", "is ", "are ", "should "]
                )
                if is_question:
                    question_h2s.append(h2_text)
                    following_words = []
                    for j in range(i + 1, min(i + 10, len(lines))):
                        stripped = lines[j].strip()
                        if stripped.startswith("#"):
                            break
                        if stripped:
                            following_words.extend(stripped.split())
                    first_50 = " ".join(following_words[:50])
                    if len(following_words) < 10 or first_50.count("?") > 0:
                        h2s_missing_answers.append(h2_text)

        if h2s_missing_answers:
            issues.append(
                f"QUESTION H2s WITHOUT DIRECT ANSWER BLOCKS: {h2s_missing_answers}. "
                f"Each question-style H2 must be immediately followed by a concise 1-3 sentence direct answer "
                f"within the first 50 words. AI engines extract these as featured answers."
            )
        report_lines.append(f"Question-style H2s: {len(question_h2s)} found, {len(h2s_missing_answers)} missing direct answers")

        # ── 2. Key terms defined on first use ──
        kw_parts = state.target_keyword.lower().split()
        key_terms = set()
        for part in kw_parts:
            if len(part) > 4 and part not in ("about", "their", "these", "those", "which", "could", "would", "should"):
                key_terms.add(part)

        text_lower = article.lower()
        undefined_terms = []
        for term in key_terms:
            first_pos = text_lower.find(term)
            if first_pos >= 0:
                surrounding = text_lower[max(0, first_pos - 20):first_pos + 200]
                has_definition = any(phrase in surrounding for phrase in [
                    f"{term} is ", f"{term} are ", f"{term} refers to",
                    f"{term} means", f"{term}, which", f"{term}, a ",
                    f"{term}, an ",
                ])
                if not has_definition:
                    undefined_terms.append(term)

        if undefined_terms:
            issues.append(
                f"KEY TERMS NOT CLEARLY DEFINED ON FIRST USE: {undefined_terms}. "
                f"On first mention, add a brief definition clause (e.g., 'An addendum, a supplemental document that adds new terms, ...'). "
                f"AI engines rely on clear definitions to extract accurate answers."
            )
        report_lines.append(f"Key term definitions: {len(key_terms) - len(undefined_terms)}/{len(key_terms)} defined on first use")

        # ── 3. Statistics without named sources in text ──
        stat_lines_without_sources = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            has_stat = bool(re.search(r'\d+%|\$[\d,]+|\d+\s*(billion|million|percent)', stripped, re.IGNORECASE))
            if has_stat:
                has_source = bool(re.search(
                    r'(according to|per |found that|reported|study|survey|research|report by|data from|'
                    r'Gartner|Forrester|McKinsey|World Commerce|Deloitte|PwC|American Bar)',
                    stripped, re.IGNORECASE
                ))
                has_link = "[" in stripped and "](" in stripped
                if not has_source and not has_link:
                    stat_lines_without_sources.append(stripped[:80])

        if stat_lines_without_sources:
            issues.append(
                f"STATISTICS WITHOUT NAMED SOURCES IN TEXT: {len(stat_lines_without_sources)} found. "
                f"Examples: {stat_lines_without_sources[:2]}. "
                f"AI engines need the source name in the text (not just a link) to cite accurately. "
                f"Add 'according to [Source Name]' or 'a [Source Name] study found' to each."
            )
        report_lines.append(f"Statistics with named sources: {len(stat_lines_without_sources)} missing attribution")

        # ── 4. People Also Ask coverage ──
        paa = state.keyword_data.get("questions_people_ask", []) if state.keyword_data else []
        addressed_paa = []
        unaddressed_paa = []
        for q in paa[:8]:
            q_words = set(w.lower() for w in q.split() if len(w) > 3)
            body_text = text_lower
            coverage = sum(1 for w in q_words if w in body_text) / max(len(q_words), 1)
            if coverage > 0.5:
                addressed_paa.append(q)
            else:
                unaddressed_paa.append(q)

        if unaddressed_paa:
            issues.append(
                f"PAA QUESTIONS NOT ADDRESSED: {unaddressed_paa[:4]}. "
                f"Add content that answers these questions. They can be woven into existing sections "
                f"or added as new subsections. AI engines surface articles that answer related questions."
            )
        report_lines.append(f"PAA coverage: {len(addressed_paa)}/{len(paa[:8])} questions addressed")

        # ── 5. Numbered steps for process content ──
        process_h2s = [h for h in re.findall(r"^## (.+)$", article, re.MULTILINE)
                       if any(w in h.lower() for w in ["how to", "steps", "process", "guide", "write"])]
        for h2 in process_h2s:
            h2_pos = article.find(f"## {h2}")
            next_h2 = article.find("\n## ", h2_pos + 1)
            section = article[h2_pos:next_h2] if next_h2 > 0 else article[h2_pos:]
            has_numbered_list = bool(re.search(r"^\d+\.", section, re.MULTILINE))
            if not has_numbered_list:
                issues.append(
                    f"PROCESS SECTION WITHOUT NUMBERED STEPS: '{h2}'. "
                    f"How-to sections should use numbered steps for AI extractability."
                )
        report_lines.append(f"Process sections with numbered steps: checked {len(process_h2s)} sections")

        # ── 6. ContractSafe entity clarity ──
        if "contractsafe" in text_lower:
            first_mention = text_lower.find("contractsafe")
            surrounding = article[max(0, first_mention - 10):first_mention + 200]
            if not any(phrase in surrounding.lower() for phrase in [
                "contract management", "contract lifecycle", "clm", "software"
            ]):
                issues.append(
                    "CONTRACTSAFE NOT CLEARLY IDENTIFIED on first mention. "
                    "Add a brief identifier like 'ContractSafe, a contract management platform,' "
                    "so AI engines understand what the entity is."
                )

        report = "\n".join(report_lines)
        return {"issues": issues, "report": report}
