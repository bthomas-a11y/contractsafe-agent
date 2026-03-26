"""Agent 0: Keyword Cluster Builder — programmatic data gathering + Sonnet synthesis.

Runs before the main pipeline. Produces a strategic keyword cluster that
informs all downstream agents: target keyword, supporting keywords,
content gaps, H2 recommendations, and differentiation strategy.

Data gathering is fully programmatic (DataForSEO, SEMrush, Google Autocomplete, web fetch).
Synthesis uses a single focused Sonnet call for strategic judgment.
"""

import re
import time
import json

from agents.base import BaseAgent
from state import PipelineState
from config import SEMRUSH_API_KEY, DATAFORSEO_LOGIN, RESEARCH_MODEL


# Domains to skip when selecting competitors from SERP
SKIP_DOMAINS = {
    "youtube.com", "wikipedia.org", "reddit.com", "quora.com",
    "linkedin.com", "g2.com", "capterra.com", "getapp.com",
    "softwareadvice.com", "trustradius.com", "facebook.com",
}

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "for", "of", "to", "in",
    "on", "at", "by", "is", "are", "was", "were", "be", "been",
    "your", "our", "their", "this", "that", "with", "how", "what",
    "why", "when", "best", "top", "guide", "tips", "does", "can",
    "do", "should", "would", "will", "which", "where", "who",
}


class KeywordClusterBuilder(BaseAgent):
    name = "Keyword Cluster Builder"
    description = "Build strategic keyword cluster from SERP data, competitor analysis, and keyword expansion"
    agent_number = 0
    emoji = "\U0001f52c"
    model = RESEARCH_MODEL
    timeout = 180  # Synthesis call needs 60-120s; data gathering is ~30-60s

    def run(self, state: PipelineState) -> PipelineState:
        self.progress("Building keyword cluster...")

        # ── Step 1: Generate seed keywords ──
        seeds = self._generate_seeds(state.topic, state.target_keyword)
        self.progress(f"Seeds: {seeds}")

        # ── Step 2-3: SERP analysis (DataForSEO, with expansion) ──
        serp_data, all_paa, all_related, cs_positions, competitor_urls = (
            self._gather_serp_data(seeds)
        )

        # ── Step 4: SEMrush volume data ──
        volume_data = self._gather_volume_data(seeds, all_related, state.target_keyword)

        # ── Step 5: Google Autocomplete expansion ──
        autocomplete_kws = self._gather_autocomplete(state.target_keyword)

        # ── Step 6: Fetch and analyze competitor pages ──
        competitor_analysis = self._analyze_competitors(competitor_urls)

        # ── Step 6b: Supplement PAA with autocomplete questions if sparse ──
        if len(all_paa) < 3:
            self.progress(f"Only {len(all_paa)} PAA questions, supplementing with autocomplete...")
            extra_q = self._gather_question_autocomplete(state.target_keyword)
            for q in extra_q:
                if q not in all_paa:
                    all_paa.append(q)
            self.progress(f"PAA + autocomplete questions: {len(all_paa)}")

        # ── Step 7: Programmatic gap detection ──
        gaps = self._detect_gaps(competitor_analysis, all_paa, all_related)
        self.progress(f"Detected {len(gaps)} content gaps")

        # ── Step 8: Sonnet synthesis ──
        self.progress("Sonnet synthesis: strategic keyword cluster...")
        cluster = self._synthesize(
            state, serp_data, cs_positions, all_paa, all_related,
            volume_data, autocomplete_kws, competitor_analysis, gaps,
        )

        # ── Step 9: Store in state ──
        raw_data = {
            "serp_queries": list(serp_data.keys()),
            "cs_positions": cs_positions,
            "paa_questions": all_paa,
            "related_searches": all_related,
            "volume_data": volume_data,
            "autocomplete": autocomplete_kws,
            "competitor_analysis": [
                {k: v for k, v in c.items() if k != "content_preview"}
                for c in competitor_analysis
            ],
            "programmatic_gaps": gaps,
        }

        if cluster and not cluster.get("raw_response"):
            cluster["_raw"] = raw_data
            state.keyword_cluster = cluster

            # Override target keyword if Sonnet recommends different
            new_target = cluster.get("target_keyword", "")
            if new_target and new_target.lower() != state.target_keyword.lower():
                self.log(
                    f"Target keyword: '{state.target_keyword}' -> '{new_target}'"
                )
                state.target_keyword = new_target.lower()

            # Populate secondary keywords from cluster
            supporting = cluster.get("supporting_keywords", [])
            if supporting:
                state.secondary_keywords = [
                    kw["keyword"]
                    for kw in supporting[:8]
                    if kw.get("keyword", "").lower() != state.target_keyword.lower()
                ]

            self.log(
                f"Cluster built: target='{cluster.get('target_keyword')}', "
                f"{len(supporting)} supporting, "
                f"{len(cluster.get('content_gaps', []))} gaps, "
                f"{len(cluster.get('recommended_h2s', []))} H2s"
            )
        else:
            self.log("[yellow]Sonnet synthesis failed. Storing raw data only.[/yellow]")
            state.keyword_cluster = {"synthesis_failed": True, "_raw": raw_data}

        return state

    # ══════════════════════════════════════════════════════════════
    # DATA GATHERING — fully programmatic
    # ══════════════════════════════════════════════════════════════

    def _generate_seeds(self, topic: str, target_keyword: str) -> list[str]:
        """Generate 4-8 seed keywords from topic and derived keyword."""
        seeds = []
        seen = set()

        def add(kw):
            kw = kw.lower().strip()
            if kw and kw not in seen and 1 < len(kw.split()) <= 6:
                seen.add(kw)
                seeds.append(kw)

        add(target_keyword)

        # Clean topic into keyword-like phrase
        topic_clean = re.sub(r'[:\?\!"\']', '', topic.lower()).strip()
        topic_clean = re.sub(r'\s+', ' ', topic_clean)
        add(topic_clean)

        # Detect comparison topics (vs/versus) — don't add "software" or "best"
        is_comparison = bool(re.search(r'\bvs\.?\b|\bversus\b', target_keyword, re.IGNORECASE))

        if is_comparison:
            # For "X vs Y", also try "Y vs X" and individual terms
            parts = re.split(r'\s+vs\.?\s+|\s+versus\s+', target_keyword, flags=re.IGNORECASE)
            if len(parts) == 2:
                add(f"{parts[1].strip()} vs {parts[0].strip()}")
                add(f"what is a {parts[0].strip()}")
                add(f"what is a {parts[1].strip()}")
                add(f"{parts[0].strip()} {parts[1].strip()} difference")
        else:
            add(f"{target_keyword} software")
            add(f"best {target_keyword}")

            # Extract "for X" modifier if present
            for_match = re.search(r'\bfor\s+([\w\-]+(?:\s+[\w\-]+)?)', topic, re.IGNORECASE)
            if for_match:
                modifier = for_match.group(1).lower()
                base = re.sub(r'\s*for\s+.*', '', target_keyword, flags=re.IGNORECASE).strip()
                if base and base != target_keyword:
                    add(base)
                    add(f"{modifier} {base}")
                    add(f"{modifier} {base} software")

        # Swap word order for all topics
        kw_words = target_keyword.split()
        if len(kw_words) >= 2:
            add(" ".join(reversed(kw_words)))

        return seeds[:8]

    def _gather_serp_data(self, seeds: list[str]) -> tuple:
        """Run DataForSEO SERP on seeds + expansion queries."""
        serp_data = {}
        all_paa = []
        all_related = []
        cs_positions = {}
        competitor_urls = []

        if not DATAFORSEO_LOGIN:
            self.progress("DataForSEO not configured, skipping SERP analysis")
            return serp_data, all_paa, all_related, cs_positions, competitor_urls

        from tools.dataforseo import serp_organic

        # SERP on top 3 seeds
        for seed in seeds[:3]:
            self.progress(f"SERP: \"{seed}\"")
            result = serp_organic(seed, load_ai_overview=True)
            if not result.get("organic"):
                continue

            serp_data[seed] = result

            for paa in result.get("people_also_ask", []):
                q = paa.get("question", "")
                if q and q not in all_paa:
                    all_paa.append(q)

            for rs in result.get("related_searches", []):
                if rs and rs not in all_related:
                    all_related.append(rs)

            for r in result["organic"]:
                if "contractsafe" in r.get("domain", "").lower():
                    cs_positions[seed] = r["position"]
                    break

            for r in result["organic"][:10]:
                domain = r.get("domain", "").replace("www.", "")
                if (
                    "contractsafe" not in domain
                    and domain not in SKIP_DOMAINS
                    and r["url"] not in [c["url"] for c in competitor_urls]
                ):
                    competitor_urls.append({
                        "url": r["url"],
                        "domain": domain,
                        "title": r["title"],
                        "position": r["position"],
                        "query": seed,
                    })
            time.sleep(0.3)

        # Expand with top 2 related searches not already in seeds
        seed_set = {s.lower() for s in seeds}
        expansions = [rs for rs in all_related if rs.lower() not in seed_set][:2]
        for query in expansions:
            self.progress(f"SERP expansion: \"{query}\"")
            result = serp_organic(query, load_ai_overview=True)
            if not result.get("organic"):
                continue
            serp_data[query] = result
            for paa in result.get("people_also_ask", []):
                q = paa.get("question", "")
                if q and q not in all_paa:
                    all_paa.append(q)
            for rs in result.get("related_searches", []):
                if rs and rs not in all_related:
                    all_related.append(rs)
            for r in result["organic"]:
                if "contractsafe" in r.get("domain", "").lower() and query not in cs_positions:
                    cs_positions[query] = r["position"]
                    break
            time.sleep(0.3)

        self.progress(
            f"SERP: {len(serp_data)} queries, {len(all_paa)} PAA, "
            f"{len(all_related)} related, CS positions: {cs_positions}"
        )
        return serp_data, all_paa, all_related, cs_positions, competitor_urls

    def _gather_volume_data(self, seeds, related, target_keyword) -> dict:
        """Batch SEMrush volume/CPC/difficulty for all discovered keywords."""
        if not SEMRUSH_API_KEY:
            self.progress("SEMrush not configured, skipping volume data")
            return {}

        from tools.semrush import batch_keyword_overview

        all_kws = list(set(seeds + related + [target_keyword]))
        self.progress(f"SEMrush batch: {len(all_kws)} keywords")
        results = batch_keyword_overview(all_kws[:100])

        volume_data = {}
        for r in results:
            kw = r.get("Keyword", r.get("Ph", "")).lower()
            vol = int(r.get("Search Volume", r.get("Nq", "0")) or 0)
            cpc = float(r.get("CPC", r.get("Cp", "0")) or 0)
            volume_data[kw] = {"volume": vol, "cpc": cpc}

        self.progress(f"SEMrush: {len(volume_data)} keywords with data")
        return volume_data

    def _gather_autocomplete(self, target_keyword: str) -> list[str]:
        """Google Autocomplete expansion on the target keyword."""
        from tools.keyword_research import google_autocomplete

        kws = []
        self.progress(f"Autocomplete: \"{target_keyword}\"")

        # Base
        kws.extend(google_autocomplete(target_keyword))
        time.sleep(0.3)

        # Question prefixes
        for prefix in ["how to", "what is", "best", "why", "free"]:
            kws.extend(google_autocomplete(f"{prefix} {target_keyword}"))
            time.sleep(0.3)

        # Buyer intent
        for modifier in ["software", "tools", "template", "for small"]:
            kws.extend(google_autocomplete(f"{target_keyword} {modifier}"))
            time.sleep(0.3)

        kws = list(dict.fromkeys(kws))  # deduplicate preserving order
        self.progress(f"Autocomplete: {len(kws)} suggestions")
        return kws

    def _gather_question_autocomplete(self, target_keyword: str) -> list[str]:
        """Supplementary question expansion when PAA data is sparse."""
        from tools.keyword_research import google_autocomplete

        questions = []
        for prefix in [
            f"what is {target_keyword}",
            f"how does {target_keyword}",
            f"why {target_keyword}",
            f"is {target_keyword}",
            f"do I need {target_keyword}",
            f"{target_keyword} vs",
        ]:
            suggestions = google_autocomplete(prefix)
            questions.extend(suggestions)
            time.sleep(0.3)

        return list(dict.fromkeys(questions))

    def _analyze_competitors(self, competitor_urls: list[dict]) -> list[dict]:
        """Fetch top competitor pages and extract structure."""
        from tools.web_fetch import web_fetch

        # Deduplicate by domain, take top 5
        seen = set()
        targets = []
        for c in competitor_urls:
            if c["domain"] not in seen:
                seen.add(c["domain"])
                targets.append(c)
            if len(targets) >= 5:
                break

        results = []
        for t in targets:
            self.progress(f"Fetching: {t['domain']} (#{t['position']})")
            data = web_fetch(t["url"])
            if not data.get("content") or data.get("error"):
                continue

            content = data["content"]
            h2s = re.findall(r"^##\s+(.+)$", content, re.MULTILINE)
            if not h2s:
                h2s = re.findall(r"<h2[^>]*>([^<]+)</h2>", content, re.IGNORECASE)

            results.append({
                "domain": t["domain"],
                "url": t["url"],
                "title": t["title"],
                "rank": t["position"],
                "query": t["query"],
                "h2s": h2s[:15],
                "word_count": len(content.split()),
                "content_preview": content[:1200],
            })

        self.progress(f"Analyzed {len(results)} competitor pages")
        return results

    def _detect_gaps(
        self, competitors: list, paa: list, related: list
    ) -> list[dict]:
        """Topics in PAA/related that competitors don't cover in H2s."""
        covered_words = set()
        for comp in competitors:
            for h2 in comp.get("h2s", []):
                words = set(re.findall(r'[a-z]+', h2.lower())) - STOPWORDS
                covered_words.update(words)

        gaps = []
        seen = set()
        for source, items in [("paa", paa), ("related_search", related)]:
            for item in items:
                item_words = set(re.findall(r'[a-z]+', item.lower())) - STOPWORDS
                if not item_words or len(item_words) < 2:
                    continue
                overlap = len(item_words & covered_words) / len(item_words)
                if overlap < 0.4:
                    key = " ".join(sorted(list(item_words)[:3]))
                    if key not in seen:
                        seen.add(key)
                        gaps.append({
                            "topic": item,
                            "source": source,
                            "coverage_score": round(overlap, 2),
                        })

        return sorted(gaps, key=lambda g: g["coverage_score"])[:15]

    # ══════════════════════════════════════════════════════════════
    # SYNTHESIS — single Sonnet call
    # ══════════════════════════════════════════════════════════════

    def _synthesize(self, state, serp_data, cs_positions, all_paa,
                    all_related, volume_data, autocomplete_kws,
                    competitor_analysis, gaps) -> dict:
        """Feed all raw data to Sonnet for strategic analysis."""

        # ── Format SERP data ──
        serp_text = ""
        for query, data in serp_data.items():
            cs_pos = cs_positions.get(query, "not ranking")
            has_aio = bool(data.get("ai_overview"))
            serp_text += f'\nQuery: "{query}"\n'
            serp_text += (
                f"  Results: {data.get('total_results', 'N/A'):,} | "
                f"AI Overview: {'Yes' if has_aio else 'No'} | "
                f"CS position: #{cs_pos}\n"
            )
            for r in data.get("organic", [])[:8]:
                serp_text += f"  #{r['position']}: {r['domain']} - {r['title']}\n"
            aio = data.get("ai_overview")
            if aio and aio.get("has_ai_overview"):
                refs = aio.get("references", [])
                if refs:
                    cited = ", ".join(r.get("domain", "") for r in refs[:5])
                    serp_text += f"  AI Overview cites: {cited}\n"

        # ── Format volume data ──
        vol_text = ""
        if volume_data:
            sorted_v = sorted(
                volume_data.items(),
                key=lambda x: x[1].get("volume", 0),
                reverse=True,
            )
            for kw, d in sorted_v[:25]:
                if d.get("volume", 0) > 0 or d.get("cpc", 0) > 0:
                    vol_text += f"  {kw}: vol={d['volume']}/mo, CPC=${d['cpc']:.2f}\n"
        if not vol_text:
            vol_text = "  No SEMrush data available.\n"

        # ── Format competitor analysis ──
        comp_text = ""
        for c in competitor_analysis[:5]:
            comp_text += f"\n{c['domain']} (#{c['rank']} for \"{c['query']}\")\n"
            comp_text += f"  Title: {c['title']}\n"
            h2_str = ", ".join(c["h2s"][:8]) if c["h2s"] else "None"
            comp_text += f"  H2s: {h2_str}\n"
            comp_text += f"  Words: ~{c['word_count']}\n"
            preview = c.get("content_preview", "")[:800]
            if preview:
                comp_text += f"  Content:\n    {preview}\n"

        # ── Format gaps ──
        gap_text = ""
        for g in gaps[:10]:
            gap_text += (
                f"  - \"{g['topic']}\" ({g['source']}, "
                f"{g['coverage_score']:.0%} covered by competitors)\n"
            )

        system_prompt = (
            "You are an expert SEO strategist for ContractSafe, a contract "
            "management software company targeting in-house legal teams, "
            "contract managers, and operations professionals at small-to-mid-size "
            "organizations.\n\n"
            "Your job: analyze keyword research data and produce a strategic "
            "keyword cluster for a blog post. Make genuine strategic judgments.\n\n"
            "Key principles:\n"
            "- Blog posts target INFORMATIONAL intent. If ContractSafe already "
            "ranks top-3 with a product page, the blog should target a DIFFERENT "
            "keyword variant to avoid cannibalization.\n"
            "- Content gaps (topics competitors miss) are more valuable than "
            "high-volume keywords with saturated SERPs.\n"
            "- The target keyword should reflect how real people search, not "
            "industry jargon (if data shows searchers don't use that jargon).\n"
            "- H2s should cover gaps that differentiate the article.\n\n"
            "Respond with ONLY valid JSON. No markdown fencing."
        )

        user_prompt = (
            f'Topic: "{state.topic}"\n'
            f'Current keyword: "{state.target_keyword}"\n\n'
            f"## SERP Rankings (Real Google Data)\n{serp_text or 'None available.'}\n\n"
            f"## ContractSafe Positions\n{json.dumps(cs_positions) if cs_positions else 'None found.'}\n\n"
            f"## People Also Ask\n"
            + "\n".join(f"  - {q}" for q in all_paa[:12])
            + "\n\n"
            f"## Related Searches\n"
            + "\n".join(f"  - {rs}" for rs in all_related[:12])
            + "\n\n"
            f"## Keyword Volumes (SEMrush)\n{vol_text}\n"
            f"## Autocomplete\n"
            + "\n".join(f"  - {kw}" for kw in autocomplete_kws[:15])
            + "\n\n"
            f"## Competitor Pages\n{comp_text or 'None analyzed.'}\n\n"
            f"## Content Gaps (programmatic detection)\n{gap_text or 'None detected.'}\n\n"
            "## Output JSON\n"
            "{\n"
            '  "target_keyword": "2-4 word primary keyword",\n'
            '  "target_keyword_rationale": "why this keyword",\n'
            '  "vocabulary_note": "market terminology insight or null",\n'
            '  "supporting_keywords": [\n'
            '    {"keyword": "...", "intent": "informational|transactional|comparison|navigational", "role": "..."}\n'
            "  ],\n"
            '  "content_gaps": [\n'
            '    {"topic": "...", "explanation": "why this matters"}\n'
            "  ],\n"
            '  "recommended_h2s": ["H2 1", "H2 2", ...],\n'
            '  "h2_rationale": "why this structure",\n'
            '  "article_angle": "differentiation strategy",\n'
            '  "cannibalization_notes": "risks or null",\n'
            '  "ai_overview_strategy": "citability approach or null",\n'
            '  "strategic_notes": ["insight 1", "insight 2"]\n'
            "}\n\n"
            "Rules: 8-12 supporting keywords, 3-8 content gaps, "
            "6-8 H2s, specific and actionable."
        )

        return self.call_llm_json(system_prompt, user_prompt)

    # ══════════════════════════════════════════════════════════════
    # REPORT — human-readable output
    # ══════════════════════════════════════════════════════════════

    def build_report(self, state: PipelineState) -> str:
        """Generate human-readable keyword cluster report."""
        c = state.keyword_cluster
        if not c or c.get("synthesis_failed"):
            return "Keyword cluster synthesis failed. Raw data stored."

        lines = [
            "KEYWORD CLUSTER REPORT",
            "=" * 60,
            "",
            f"Topic: {state.topic}",
            f"Target Keyword: {c.get('target_keyword', 'N/A')}",
            f"Rationale: {c.get('target_keyword_rationale', 'N/A')}",
            "",
        ]

        if c.get("vocabulary_note"):
            lines.append(f"Vocabulary Note: {c['vocabulary_note']}")
            lines.append("")

        if c.get("supporting_keywords"):
            lines.append("SUPPORTING KEYWORDS")
            lines.append("-" * 40)
            for kw in c["supporting_keywords"]:
                lines.append(
                    f"  {kw.get('keyword', '')}: "
                    f"[{kw.get('intent', '')}] {kw.get('role', '')}"
                )
            lines.append("")

        if c.get("content_gaps"):
            lines.append("CONTENT GAPS")
            lines.append("-" * 40)
            for gap in c["content_gaps"]:
                lines.append(f"  {gap.get('topic', '')}")
                lines.append(f"    {gap.get('explanation', '')}")
            lines.append("")

        if c.get("recommended_h2s"):
            lines.append("RECOMMENDED H2 STRUCTURE")
            lines.append("-" * 40)
            for i, h2 in enumerate(c["recommended_h2s"], 1):
                lines.append(f"  {i}. {h2}")
            lines.append(f"\n  Rationale: {c.get('h2_rationale', 'N/A')}")
            lines.append("")

        if c.get("article_angle"):
            lines.append(f"ARTICLE ANGLE: {c['article_angle']}")
            lines.append("")

        if c.get("cannibalization_notes"):
            lines.append(f"CANNIBALIZATION: {c['cannibalization_notes']}")
            lines.append("")

        if c.get("ai_overview_strategy"):
            lines.append(f"AI OVERVIEW STRATEGY: {c['ai_overview_strategy']}")
            lines.append("")

        if c.get("strategic_notes"):
            lines.append("STRATEGIC NOTES")
            lines.append("-" * 40)
            for note in c["strategic_notes"]:
                lines.append(f"  - {note}")
            lines.append("")

        raw = c.get("_raw", {})
        lines.append("DATA SOURCES")
        lines.append("-" * 40)
        lines.append(f"  SERP queries: {len(raw.get('serp_queries', []))}")
        lines.append(f"  PAA questions: {len(raw.get('paa_questions', []))}")
        lines.append(f"  Related searches: {len(raw.get('related_searches', []))}")
        lines.append(f"  SEMrush keywords: {len(raw.get('volume_data', {}))}")
        lines.append(f"  Autocomplete: {len(raw.get('autocomplete', []))}")
        lines.append(f"  Competitors analyzed: {len(raw.get('competitor_analysis', []))}")
        lines.append(f"  CS positions: {raw.get('cs_positions', {})}")

        return "\n".join(lines)
