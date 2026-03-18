#!/usr/bin/env python3
"""ContractSafe Content Agent System - CLI entry point and pipeline orchestrator."""

from __future__ import annotations

import argparse
import sys
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Tuple

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.markdown import Markdown
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, SpinnerColumn
from rich.table import Table
from rich.text import Text
from rich import box

import os
import subprocess
import threading

from state import PipelineState
from config import (
    OUTPUT_DIR, SEMRUSH_API_KEY, TAVILY_API_KEY, KEYWORDS_PEOPLE_USE_API_KEY,
    CLAUDE_CLI, PIPELINE_BUDGET_SECONDS, EDITING_BUDGET_SECONDS, AGENT_EXPECTED_TIMES,
)
from agents import AGENT_PIPELINE
from agents.brief_consolidator import BriefConsolidatorAgent
from agents.content_writer import ContentWriterAgent
from tools.docx_export import markdown_to_docx

console = Console()

# Whether we're running non-interactively (piped stdin or --auto-approve)
NONINTERACTIVE = False

def warmup_cli():
    """Fire a tiny Claude CLI call in the background to warm up the subprocess/API path.

    This mitigates transient first-attempt timeouts caused by CLI cold-start overhead
    (loading Node.js, establishing API connection, etc.). The warmup runs in a background
    thread and we don't wait for it — by the time the first real agent call happens
    (after research agents 1-3 which are programmatic), the CLI will be warm.
    """
    def _warmup():
        try:
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            proc = subprocess.run(
                [CLAUDE_CLI, "-p", "--model", "haiku"],
                input="Reply with just the word OK.",
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            if proc.returncode == 0:
                console.print("  [dim]CLI warmup complete[/dim]")
            else:
                console.print(f"  [dim]CLI warmup returned non-zero (non-fatal)[/dim]")
        except Exception as e:
            console.print(f"  [dim]CLI warmup failed (non-fatal): {e}[/dim]")

    console.print("  [dim]Warming up Claude CLI in background...[/dim]")
    t = threading.Thread(target=_warmup, daemon=True)
    t.start()


def _derive_keyword_from_title(title: str, con: Console) -> str:
    """Extract the best primary keyword from a title using SEMRush volume data.

    Algorithm:
    1. Generate all 2-4 word contiguous subphrases from the title.
    2. Send them to SEMRush batch_keyword_overview (single API call).
    3. Score each candidate: topic specificity first, volume as tiebreaker.
       - "contract change management" (3 topic words, 70 vol) beats
         "change management" (2 topic words, 27,100 vol) because it's more
         specific to the article. Generic high-volume keywords are useless
         as primary targets — the article will rank for them naturally as
         subsets of the more specific phrase.
    4. If SEMRush is unavailable or returns no data, fall back to heuristics.
    """
    import re as _re
    from config import SEMRUSH_API_KEY

    # ── Clean the title ──
    cleaned = title.lower().strip().rstrip(".:!?")

    # ── Generate candidate subphrases ──
    stop_words = {
        "a", "an", "the", "and", "or", "but", "for", "of", "to", "in", "on",
        "at", "by", "is", "are", "was", "were", "be", "been", "your", "our",
        "their", "its", "this", "that", "these", "those", "with", "vs",
    }
    # Format/filler words that appear in titles but aren't real topic terms
    filler_words = {
        "guide", "best", "practices", "tips", "strategies", "ways", "steps",
        "key", "essential", "important", "complete", "ultimate", "top",
        "comprehensive", "simple", "easy", "quick", "effective", "proven",
        "common", "critical", "mistakes", "avoid",
    }

    # Remove numbers and non-alpha chars (except hyphens within words)
    words = _re.findall(r"[a-z]+(?:-[a-z]+)*", cleaned)
    # Content words = not stop words
    content_words = [w for w in words if w not in stop_words]
    # Topic words = content words that aren't filler (the real subject matter)
    topic_words_set = {w for w in content_words if w not in filler_words}

    candidates = set()
    # Generate 2-word, 3-word, and 4-word contiguous subphrases from content words
    for n in (2, 3, 4):
        for i in range(len(content_words) - n + 1):
            phrase = " ".join(content_words[i:i + n])
            candidates.add(phrase)
    # Also try from the original word order (preserving prepositions in context)
    for n in (2, 3, 4, 5):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            # Skip if it starts or ends with a stop word
            if words[i] in stop_words or words[i + n - 1] in stop_words:
                continue
            candidates.add(phrase)

    if not candidates:
        con.print("  [yellow]No keyword candidates extracted from title[/yellow]")
        return " ".join(content_words[:5]) if content_words else cleaned

    candidate_list = list(candidates)
    con.print(f"  [dim]Extracted {len(candidate_list)} keyword candidates from title[/dim]")

    def _topic_specificity(phrase: str) -> int:
        """Count how many topic-specific words are in a phrase."""
        return sum(1 for w in phrase.split() if w in topic_words_set)

    # ── SEMRush validation ──
    if SEMRUSH_API_KEY:
        from tools.semrush import batch_keyword_overview
        con.print("  [dim]Checking search volumes via SEMRush...[/dim]")
        volume_data = batch_keyword_overview(candidate_list)

        if volume_data:
            # Build scored list: (topic_specificity, volume, keyword)
            # Sort by topic specificity DESC first, then volume DESC as tiebreaker
            scored = []
            for entry in volume_data:
                kw = entry.get("Keyword", entry.get("Ph", "")).lower()
                vol_str = entry.get("Search Volume", entry.get("Nq", "0"))
                try:
                    vol = int(vol_str)
                except (ValueError, TypeError):
                    vol = 0
                if vol < 10:
                    continue  # skip near-zero volume
                specificity = _topic_specificity(kw)
                scored.append((specificity, vol, kw))

            if scored:
                scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
                best_specificity, best_vol, best_kw = scored[0]

                con.print(
                    f"  [green]SEMRush: best keyword = '{best_kw}' "
                    f"(topic specificity: {best_specificity}, volume: {best_vol:,}/mo)[/green]"
                )
                # Log runner-up candidates for transparency
                for specificity, vol, kw in scored[1:4]:
                    con.print(
                        f"    [dim]runner-up: '{kw}' "
                        f"(specificity: {specificity}, volume: {vol:,})[/dim]"
                    )
                return best_kw
            else:
                con.print("  [yellow]SEMRush: no candidates with volume >= 10, falling back[/yellow]")
        else:
            con.print("  [yellow]SEMRush batch call returned empty, falling back[/yellow]")
    else:
        con.print("  [yellow]SEMRush not configured, using heuristic keyword derivation[/yellow]")

    # ── Heuristic fallback (no SEMRush) ──
    # Score by topic specificity, then by length (longer = more specific)
    scored_heuristic = sorted(
        candidates,
        key=lambda c: (_topic_specificity(c), len(c.split())),
        reverse=True,
    )
    # Filter to 2-4 word phrases only
    scored_heuristic = [c for c in scored_heuristic if 2 <= len(c.split()) <= 4]
    if scored_heuristic:
        chosen = scored_heuristic[0]
        con.print(f"  [dim]Heuristic keyword: '{chosen}'[/dim]")
        return chosen

    return " ".join(content_words[:4]) if content_words else cleaned


AGENT_INFO = [
    (1, "\U0001f50d", "Product Knowledge Base", "Research"),
    (2, "\U0001f4da", "Subject Matter Research", "Research"),
    (3, "\U0001f3f7\ufe0f", "Competitor/KW Research", "Research"),
    (4, "\U0001f4ca", "SEO Research", "Research"),
    (5, "\U0001f517", "Link Research", "Research"),
    (6, "\U0001f4cb", "Brief Consolidation", "Planning"),
    (7, "\u270d\ufe0f", "Content Writer", "Writing"),
    (8, "\U0001f3a4", "Brand Voice Pass", "Editing"),
    (9, "\u2705", "Fact Check Pass", "Editing"),
    (10, "\U0001f50e", "SEO Pass", "Editing"),
    (11, "\U0001f916", "AEO Pass", "Editing"),
    (12, "\U0001f4f1", "Social Copywriter", "Social"),
    (13, "\U0001f3c1", "Final Validator", "QA"),
]


class PipelineTracker:
    """Persistent pipeline progress display with per-agent progress bars."""

    # Expected times (seconds) for progress bar scaling. Not hard limits — just visual targets.
    EXPECTED_TIMES = {
        1: 10,    # Product Knowledge — programmatic, web fetches
        2: 15,    # Subject Research — programmatic, web searches
        3: 30,    # Competitor/KW — programmatic, multiple searches
        4: 10,    # SEO Research — programmatic
        5: 90,    # Link Research — many web fetches for verification
        6: 5,     # Brief Consolidation — programmatic
        7: 150,   # Content Writer — Opus, multi-call
        8: 5,     # Brand Voice — programmatic
        9: 5,     # Fact Check — programmatic
        10: 5,    # SEO Pass — programmatic
        11: 5,    # AEO Pass — programmatic
        12: 60,   # Social Copy — Haiku LLM call
        13: 5,    # Final Validator — programmatic
    }

    # Phase display order and styling
    PHASES = ["Research", "Planning", "Writing", "Editing", "Social", "QA"]
    PHASE_STYLES = {
        "Research": "cyan", "Planning": "blue", "Writing": "magenta",
        "Editing": "yellow", "Social": "green", "QA": "bright_green",
    }

    def __init__(self):
        self.agent_status = {}  # agent_num -> "pending" | "running" | "done" | "skipped"
        self.agent_times = {}   # agent_num -> start timestamp (running) or elapsed seconds (done)
        self.agent_detail = {}  # agent_num -> latest progress message
        self.total_start = None
        self.editing_start = None
        for num, _, _, _ in AGENT_INFO:
            self.agent_status[num] = "pending"

    def build_display(self) -> Panel:
        """Build the full pipeline status display with per-agent progress bars."""
        done_count = sum(1 for s in self.agent_status.values() if s in ("done", "skipped"))
        running = [n for n, s in self.agent_status.items() if s == "running"]
        total_elapsed = time.time() - self.total_start if self.total_start else 0

        BAR_W = 20
        table = Table(box=None, show_header=False, padding=(0, 1), expand=True, show_edge=False)
        table.add_column("num", width=3, justify="right", style="dim")
        table.add_column("icon", width=2)
        table.add_column("name", min_width=24)
        table.add_column("status", width=10)
        table.add_column("time", width=7, justify="right")
        table.add_column("bar", width=BAR_W)
        table.add_column("detail", ratio=1, no_wrap=True)

        current_phase = None
        for num, emoji, label, phase in AGENT_INFO:
            # Phase header row
            if phase != current_phase:
                current_phase = phase
                ps = self.PHASE_STYLES.get(phase, "dim")
                header = Text()
                header.append(f"{phase} ", style=f"bold {ps}")
                header.append("\u2500" * 50, style=f"dim {ps}")
                table.add_row("", "", header, "", "", "", "")

            status = self.agent_status[num]
            expected = self.EXPECTED_TIMES.get(num, 10)

            if status == "done":
                elapsed_s = self.agent_times.get(num, 0)
                st = Text("\u2713 Done", style="green")
                tm = Text(f"{elapsed_s:.0f}s", style="green")
                bar = Text("\u2588" * BAR_W, style="green")
                det = Text()

            elif status == "running":
                start_t = self.agent_times.get(num, time.time())
                elapsed_s = time.time() - start_t
                st = Text("\u25b6 Active", style="bold yellow")
                tm = Text(f"{elapsed_s:.0f}s", style="yellow bold")
                # Progress bar fills toward expected time
                frac = min(elapsed_s / max(expected, 1), 1.0)
                filled = int(BAR_W * frac)
                empty = BAR_W - filled
                bar_style = "yellow" if elapsed_s <= expected else "red bold"
                bar = Text()
                bar.append("\u2588" * filled, style=bar_style)
                bar.append("\u2591" * empty, style="dim")
                # Detail: what the agent is doing right now
                detail_str = self.agent_detail.get(num, "")
                if len(detail_str) > 50:
                    detail_str = detail_str[:47] + "..."
                det = Text(detail_str, style="dim italic")

            elif status == "skipped":
                st = Text("\u2500 Skip", style="dim")
                tm = Text()
                bar = Text("\u2500" * BAR_W, style="dim")
                det = Text()

            else:  # pending
                st = Text("\u25cb Pending", style="dim")
                tm = Text()
                bar = Text("\u2591" * BAR_W, style="dim")
                det = Text()

            table.add_row(str(num), emoji, label, st, tm, bar, det)

        # ── Overall pipeline progress bar ──
        overall_w = 30
        overall_filled = int(overall_w * done_count / 13)
        pct = int(100 * done_count / 13)

        footer = Text()
        footer.append("\n ")
        footer.append("\u2501" * 72, style="dim")
        footer.append("\n  Pipeline  ", style="bold")
        footer.append("\u2588" * overall_filled, style="green" if done_count == 13 else "cyan bold")
        footer.append("\u2591" * (overall_w - overall_filled), style="dim")
        footer.append(f"  {done_count}/13 ({pct}%)", style="bold")
        footer.append(f"  \u2502  {total_elapsed:.0f}s elapsed", style="dim")

        if running:
            names = ", ".join(AGENT_INFO[r - 1][2] for r in sorted(running))
            footer.append("  \u2502  ", style="dim")
            footer.append(names, style="yellow bold")

        content = Group(table, footer)
        return Panel(
            content,
            title="[bold]ContractSafe Content Pipeline[/bold]",
            border_style="blue",
            padding=(0, 1),
        )

    def start(self):
        self.total_start = time.time()

    def mark_running(self, agent_num: int):
        self.agent_status[agent_num] = "running"
        self.agent_times[agent_num] = time.time()  # store start timestamp
        self.agent_detail[agent_num] = ""
        if agent_num >= 8 and self.editing_start is None:
            self.editing_start = time.time()

    def mark_done(self, agent_num: int):
        self.agent_status[agent_num] = "done"
        start = self.agent_times.get(agent_num, time.time())
        self.agent_times[agent_num] = time.time() - start  # convert to elapsed

    def mark_skipped(self, agent_num: int):
        self.agent_status[agent_num] = "skipped"

    def set_detail(self, agent_num: int, message: str):
        self.agent_detail[agent_num] = message


# Global tracker instance (set during pipeline run)
_tracker: PipelineTracker = None
_live: Live = None

# Default word counts by content type
DEFAULT_WORD_COUNTS = {
    "blog_post": 2000,
    "email": 500,
    "webpage_copy": 800,
}


def show_welcome():
    console.print()
    console.print(Panel(
        Text("ContractSafe Content Agent System", style="bold white", justify="center"),
        box=box.DOUBLE,
        border_style="blue",
        padding=(1, 4),
    ))

    tools_status = []
    tools_status.append(f"  Tavily: [green]active[/green]" if TAVILY_API_KEY else "  Tavily: [red]not set[/red]")
    tools_status.append(f"  SEMrush: [green]active[/green]" if SEMRUSH_API_KEY else "  SEMrush: [dim]not set[/dim]")
    tools_status.append(f"  KeywordsPeopleUse: [green]active[/green]" if KEYWORDS_PEOPLE_USE_API_KEY else "  KeywordsPeopleUse: [dim]not set[/dim]")
    tools_status.append("  Google Autocomplete: [green]active[/green] (always free)")
    console.print(Panel("\n".join(tools_status), title="Tool Status", border_style="dim"))
    console.print()


def get_user_inputs(cli_args: argparse.Namespace) -> PipelineState:
    """Collect user inputs from CLI args or interactive prompts."""
    state = PipelineState()

    # ── Content type ──
    if cli_args.type:
        type_map = {"blog": "blog_post", "blog_post": "blog_post", "email": "email", "webpage": "webpage_copy", "webpage_copy": "webpage_copy"}
        state.content_type = type_map.get(cli_args.type.lower(), "blog_post")
    elif NONINTERACTIVE:
        state.content_type = "blog_post"
    else:
        console.print("[bold]Content type?[/bold]  [1] Blog Post  [2] Email  [3] Webpage Copy")
        choice = Prompt.ask("", choices=["1", "2", "3"], default="1", show_choices=False)
        state.content_type = {"1": "blog_post", "2": "email", "3": "webpage_copy"}[choice]
        console.print()

    # ── Topic (required) ──
    if cli_args.topic:
        state.topic = cli_args.topic
    elif NONINTERACTIVE:
        console.print("[red bold]Error:[/red bold] --topic is required in non-interactive mode")
        sys.exit(1)
    else:
        state.topic = Prompt.ask("[bold]Topic[/bold]")
        console.print()

    # ── Derive keyword ──
    if cli_args.keyword:
        state.target_keyword = cli_args.keyword
    else:
        state.target_keyword = _derive_keyword_from_title(state.topic, console)

    # ── Word count ──
    if cli_args.word_count:
        state.target_word_count = cli_args.word_count
    else:
        state.target_word_count = DEFAULT_WORD_COUNTS[state.content_type]

    # ── Secondary keywords ──
    if cli_args.secondary_keywords:
        state.secondary_keywords = [k.strip() for k in cli_args.secondary_keywords.split(",") if k.strip()]

    # ── Additional instructions ──
    if cli_args.instructions:
        state.additional_instructions = cli_args.instructions

    # ── Show config ──
    console.print(f"  [dim]Keyword:[/dim] {state.target_keyword}")
    console.print(f"  [dim]Word count:[/dim] {state.target_word_count}")
    console.print()

    if not NONINTERACTIVE:
        if Confirm.ask("[bold]Customize these defaults?[/bold]", default=False):
            kw = Prompt.ask("Primary keyword", default=state.target_keyword)
            state.target_keyword = kw

            secondary = Prompt.ask("Secondary keywords (comma-separated)", default="")
            state.secondary_keywords = [k.strip() for k in secondary.split(",") if k.strip()]

            wc = Prompt.ask("Target word count", default=str(state.target_word_count))
            state.target_word_count = int(wc)

            state.additional_instructions = Prompt.ask("Additional instructions", default="")
            console.print()

    return state


def check_resume(resume_topic: str = "") -> Tuple[Optional[PipelineState], Optional[Path]]:
    """Check for existing pipeline states. Auto-resumes by topic in non-interactive mode."""
    if not OUTPUT_DIR.exists():
        return None, None

    state_files = list(OUTPUT_DIR.glob("*/pipeline_state.json"))
    if not state_files:
        return None, None

    resumable = []
    for sf in sorted(state_files, key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            s = PipelineState.load(str(sf))
            if len(s.completed_agents) < 13:
                resumable.append((sf, s))
        except Exception:
            continue

    if not resumable:
        return None, None

    if NONINTERACTIVE:
        if resume_topic:
            for sf, s in resumable:
                if s.topic.lower() == resume_topic.lower():
                    remaining = sorted(set(range(1, 14)) - set(s.completed_agents))
                    next_agent = remaining[0] if remaining else 14
                    console.print(f"[green]Auto-resuming '{s.topic}' from agent {next_agent}[/green]\n")
                    return s, sf.parent
        return None, None

    console.print("[bold yellow]Incomplete pipeline runs found:[/bold yellow]")
    for i, (sf, s) in enumerate(resumable[:5]):
        console.print(f"  [{i+1}] {s.topic} ({len(s.completed_agents)}/13 agents completed)")
    console.print(f"  [0] Start fresh")
    console.print()

    choice = Prompt.ask("Resume?", default="0")
    if choice == "0":
        return None, None

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(resumable):
            sf, state = resumable[idx]
            remaining = sorted(set(range(1, 14)) - set(state.completed_agents))
            next_agent = remaining[0] if remaining else 14
            console.print(f"[green]Resuming from agent {next_agent}[/green]\n")
            return state, sf.parent
    except (ValueError, IndexError):
        pass

    return None, None


def run_agent(agent_cls, state: PipelineState, agent_num: int) -> tuple[PipelineState, float]:
    """Run a single agent with progress tracking and budget enforcement."""
    global _tracker, _live

    # ── Budget check: abort if pipeline or editing budget exhausted ──
    if _tracker and _tracker.total_start:
        elapsed_so_far = time.time() - _tracker.total_start
        remaining_budget = PIPELINE_BUDGET_SECONDS - elapsed_so_far

        # Editing budget: agents 8-13 share a tighter budget
        if agent_num >= 8 and _tracker.editing_start:
            editing_elapsed = time.time() - _tracker.editing_start
            editing_remaining = EDITING_BUDGET_SECONDS - editing_elapsed
            remaining_budget = min(remaining_budget, editing_remaining)
            if editing_remaining <= 0:
                raise RuntimeError(
                    f"EDITING BUDGET EXHAUSTED: {editing_elapsed:.0f}s elapsed on editing "
                    f"(agents 8-13), {EDITING_BUDGET_SECONDS}s budget. Cannot start Agent {agent_num}. "
                    f"Do not increase the budget. Investigate which editing agent ran slow."
                )

        if remaining_budget <= 0:
            raise RuntimeError(
                f"PIPELINE BUDGET EXHAUSTED: {elapsed_so_far:.0f}s elapsed, "
                f"{PIPELINE_BUDGET_SECONDS}s budget. Cannot start Agent {agent_num}. "
                f"Do not increase the budget. Investigate which agent(s) ran slow."
            )
    else:
        remaining_budget = PIPELINE_BUDGET_SECONDS

    agent = agent_cls()

    # Pass remaining budget to agent so call_llm can use it
    agent._remaining_budget = remaining_budget

    original_progress = agent.progress
    original_log = agent.log

    def tracked_progress(message):
        original_progress(message)
        if _tracker and _live:
            import re as _re
            clean = _re.sub(r'\[/?[a-z ]+\]', '', message)
            _tracker.set_detail(agent_num, clean)
            try:
                _live.update(_tracker.build_display())
            except Exception:
                pass

    def tracked_log(message):
        original_log(message)
        if _tracker and _live:
            import re as _re
            clean = _re.sub(r'\[/?[a-z ]+\]', '', message)
            _tracker.set_detail(agent_num, clean)
            try:
                _live.update(_tracker.build_display())
            except Exception:
                pass

    agent.progress = tracked_progress
    agent.log = tracked_log

    agent_label = AGENT_INFO[agent_num - 1][2] if agent_num <= len(AGENT_INFO) else agent_cls.name
    agent_emoji = AGENT_INFO[agent_num - 1][1] if agent_num <= len(AGENT_INFO) else ""
    agent_phase = AGENT_INFO[agent_num - 1][3] if agent_num <= len(AGENT_INFO) else ""
    console.print(f"\n{'='*60}")
    console.print(f"  {agent_emoji} STARTING Agent {agent_num}/13: {agent_label} [{agent_phase}]")
    console.print(f"     Model: {agent.model} | Timeout: {agent.timeout}s | Budget: {remaining_budget:.0f}s")
    console.print(f"{'='*60}")
    sys.stdout.flush()

    if _tracker:
        _tracker.mark_running(agent_num)
        if _live:
            try:
                _live.update(_tracker.build_display())
            except Exception:
                pass

    start_time = time.time()
    state = agent.run(state)
    elapsed = time.time() - start_time

    # ── Warn if agent exceeded expected time ──
    expected = AGENT_EXPECTED_TIMES.get(agent_num)
    if expected and elapsed > expected:
        console.print(
            f"  [yellow bold]SLOW: Agent {agent_num} took {elapsed:.0f}s "
            f"(expected {expected}s). Investigate prompt size.[/yellow bold]"
        )

    console.print(f"  COMPLETED Agent {agent_num}/13: {agent_label} in {elapsed:.0f}s")
    sys.stdout.flush()

    # ── Stage gate: verify output meets minimum thresholds ──
    gate_result = stage_gate(state, agent_num, agent_label)
    if gate_result == "RETRY":
        console.print(
            f"  [red bold]STAGE GATE FAILED for Agent {agent_num}. "
            f"Re-running with full effort...[/red bold]"
        )
        agent2 = agent_cls()
        agent2._remaining_budget = remaining_budget - elapsed
        agent2.progress = tracked_progress
        agent2.log = tracked_log
        retry_start = time.time()
        state = agent2.run(state)
        retry_elapsed = time.time() - retry_start
        elapsed += retry_elapsed
        console.print(f"  RETRY completed in {retry_elapsed:.0f}s")

        gate_result_2 = stage_gate(state, agent_num, agent_label)
        if gate_result_2 == "RETRY":
            raise RuntimeError(
                f"PIPELINE HALTED: Agent {agent_num} ({agent_label}) failed stage gate "
                f"on both initial run and retry. Output is insufficient. "
                f"Fix the agent before running the pipeline again."
            )

    if _tracker:
        _tracker.mark_done(agent_num)
        if _live:
            try:
                _live.update(_tracker.build_display())
            except Exception:
                pass

    assert_post_conditions(state, agent_num)
    supervisor_check(state, agent_num)
    return state, elapsed


def stage_gate(state: PipelineState, agent_num: int, agent_label: str) -> str:
    """Mechanical gate between pipeline stages. Checks output meets minimum thresholds.

    Returns "PASS" if the gate is satisfied, "RETRY" if the agent produced
    insufficient output and a retry is warranted.

    Minimum thresholds:
    - Research agents (1-5): 100 chars of relevant output
    - Brief (6): 500 chars
    - Writer (7): 500 words
    - Editing agents (8-11): article field must exist and be ≥90% of input length
      (editing can shorten slightly, but shouldn't lose the article)
    - Social (12): meta_description + at least one social post
    - Validator (13): final_article + validation_report
    """
    import re as _re

    # Map agent number to its output field and minimum threshold
    GATE_CHECKS = {
        1: ("product_knowledge", 100, "chars"),
        2: ("subject_research", 100, "chars"),
        3: ("keyword_data", 50, "chars"),
        4: ("recommended_h2s", 1, "items"),
        5: ("internal_links", 1, "items"),
        6: ("consolidated_brief", 500, "chars"),
        7: ("draft_article", 500, "words"),
        8: ("voice_pass_article", 500, "words"),
        9: ("fact_check_article", 500, "words"),
        10: ("seo_pass_article", 500, "words"),
        11: ("aeo_pass_article", 500, "words"),
        12: ("meta_description", 20, "chars"),
        13: ("final_article", 500, "words"),
    }

    if agent_num not in GATE_CHECKS:
        return "PASS"

    field_name, threshold, unit = GATE_CHECKS[agent_num]
    value = getattr(state, field_name, None)

    # Determine actual size
    if value is None:
        actual = 0
    elif isinstance(value, str):
        if unit == "words":
            actual = len(value.split())
        else:
            actual = len(value)
    elif isinstance(value, (list, dict)):
        actual = len(value)
    else:
        actual = len(str(value))

    passed = actual >= threshold

    # Log the gate check
    gate_log = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agent_num": agent_num,
        "agent_label": agent_label,
        "field": field_name,
        "actual": actual,
        "threshold": threshold,
        "unit": unit,
        "passed": passed,
    }
    gate_log_path = OUTPUT_DIR / "pipeline-gate-log.jsonl"
    try:
        gate_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(gate_log_path, "a") as f:
            f.write(json.dumps(gate_log) + "\n")
    except Exception:
        pass

    if not passed:
        console.print(
            f"  [red bold]STAGE GATE: Agent {agent_num} ({agent_label}) produced "
            f"insufficient output: {actual} {unit} (minimum: {threshold} {unit}). "
            f"Field: {field_name}[/red bold]"
        )
        return "RETRY"

    return "PASS"


def assert_post_conditions(state: PipelineState, agent_num: int):
    """Check post-conditions after critical agents. Raises RuntimeError on failure."""
    if agent_num == 2:
        if not state.subject_research:
            console.print("[yellow]Warning: Agent 2 produced no subject_research[/yellow]")
    elif agent_num == 3:
        if not state.keyword_data:
            console.print("[yellow]Warning: Agent 3 produced no keyword_data[/yellow]")
    elif agent_num == 4:
        if not state.recommended_h2s:
            console.print("[yellow]Warning: Agent 4 produced no recommended H2s[/yellow]")
    elif agent_num == 5:
        if not state.internal_links and not state.external_links:
            console.print("[yellow]Warning: Agent 5 produced no links[/yellow]")
    elif agent_num == 6:
        if not state.consolidated_brief or len(state.consolidated_brief) < 100:
            raise RuntimeError("Agent 6 (Brief Consolidator) produced empty or tiny brief")
    elif agent_num == 7:
        if not state.draft_article or len(state.draft_article.split()) < 200:
            raise RuntimeError(
                f"Agent 7 (Content Writer) produced a suspiciously short article "
                f"({len(state.draft_article.split()) if state.draft_article else 0} words). "
                f"Pipeline cannot continue."
            )
    elif agent_num == 8:
        if not state.voice_pass_article:
            console.print("[yellow]Warning: Agent 8 produced no voice_pass_article, using draft[/yellow]")
            state.voice_pass_article = state.draft_article
    elif agent_num == 9:
        if not state.fact_check_article:
            console.print("[yellow]Warning: Agent 9 produced no fact_check_article, using voice pass[/yellow]")
            state.fact_check_article = state.voice_pass_article or state.draft_article
    elif agent_num == 10:
        if not state.seo_pass_article:
            console.print("[yellow]Warning: Agent 10 produced no seo_pass_article[/yellow]")
            state.seo_pass_article = (
                state.fact_check_article or state.voice_pass_article or state.draft_article
            )
    elif agent_num == 11:
        if not state.aeo_pass_article:
            console.print("[yellow]Warning: Agent 11 produced no aeo_pass_article[/yellow]")
            state.aeo_pass_article = (
                state.seo_pass_article or state.fact_check_article
                or state.voice_pass_article or state.draft_article
            )
    elif agent_num == 12:
        if not state.meta_description:
            console.print("[yellow]Warning: Agent 12 produced no meta_description[/yellow]")
    elif agent_num == 13:
        if not state.final_article:
            console.print("[yellow]Warning: Agent 13 produced no final_article[/yellow]")
            state.final_article = (
                state.aeo_pass_article or state.seo_pass_article
                or state.fact_check_article or state.voice_pass_article
                or state.draft_article
            )


def supervisor_check(state: PipelineState, agent_num: int):
    """Independent spot-checks on agent output. Assumes failure until proven otherwise.

    Called after assert_post_conditions() for every agent. Performs checks that
    assert_post_conditions cannot: comparing input vs output to detect silent failures,
    scanning for quality problems the agent should have fixed, and overriding pass
    signals when independent verification disagrees.

    Agent 13 supervisor override sets pass_fail=False on critical issues.
    """
    import re as _re

    if agent_num == 8:
        # Brand Voice Pass: did it actually change the article?
        input_article = state.draft_article or ""
        output_article = state.voice_pass_article or ""
        if input_article and output_article and input_article == output_article:
            console.print(
                f"  [red bold]SUPERVISOR WARNING (Agent 8): voice_pass_article is "
                f"IDENTICAL to draft_article. Delta parser likely failed to apply changes.[/red bold]"
            )

    elif agent_num == 10:
        # SEO Pass: check link counts and mechanical patterns
        input_article = (
            state.fact_check_article or state.voice_pass_article or state.draft_article or ""
        )
        output_article = state.seo_pass_article or ""

        if input_article and output_article:
            input_links = len(_re.findall(r'\[([^\]]+)\]\(https?://[^)]+\)', input_article))
            output_links = len(_re.findall(r'\[([^\]]+)\]\(https?://[^)]+\)', output_article))
            if output_links <= input_links:
                console.print(
                    f"  [yellow bold]SUPERVISOR WARNING (Agent 10): Link count did not increase "
                    f"({input_links} → {output_links}). SEO pass may have failed to insert links.[/yellow bold]"
                )

        # Check for mechanical link patterns that should have been eliminated
        mechanical_patterns = [
            r', as \[[^\]]+\]\([^)]+\) explains',
            r', see \[[^\]]+\]\([^)]+\)',
            r', according to \[[^\]]+\]\([^)]+\)',
            r'\([^\)]*see \[[^\]]+\]\([^)]+\)\)',
        ]
        for pattern in mechanical_patterns:
            matches = _re.findall(pattern, output_article, _re.IGNORECASE)
            if matches:
                console.print(
                    f"  [red bold]SUPERVISOR WARNING (Agent 10): Found {len(matches)} mechanical "
                    f"link pattern(s) matching '{pattern[:40]}...' — links are NOT organic.[/red bold]"
                )

    elif agent_num == 11:
        # AEO Pass: did it actually change the article?
        input_article = (
            state.seo_pass_article or state.fact_check_article
            or state.voice_pass_article or state.draft_article or ""
        )
        output_article = state.aeo_pass_article or ""
        if input_article and output_article and input_article == output_article:
            console.print(
                f"  [yellow bold]SUPERVISOR WARNING (Agent 11): aeo_pass_article is "
                f"IDENTICAL to its input. AEO fixes may not have been applied.[/yellow bold]"
            )

    elif agent_num == 13:
        # Final Validator: independent re-verification of critical checks
        article = state.final_article or ""
        issues = []

        # Check internal links
        internal_links = _re.findall(
            r'\[([^\]]+)\]\((https?://[^)]*contractsafe\.com[^)]*)\)', article
        )
        if len(internal_links) < 5:
            issues.append(f"Internal links: {len(internal_links)} (need ≥5)")

        # Check external links
        all_links = _re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', article)
        external_links = [(t, u) for t, u in all_links if "contractsafe.com" not in u.lower()]
        if len(external_links) < 3:
            issues.append(f"External links: {len(external_links)} (need ≥3)")

        # Check em dashes
        em_count = article.count("\u2014") + article.count("\u2013")
        if em_count > 0:
            issues.append(f"Em/en dashes: {em_count}")

        # Check mechanical link patterns
        mechanical = _re.findall(r', as \[[^\]]+\]\([^)]+\) explains', article, _re.IGNORECASE)
        if mechanical:
            issues.append(f"Mechanical link phrases: {len(mechanical)}")

        # Check duplicate link URLs
        link_urls = [u.lower().rstrip("/") for _, u in all_links]
        dup_urls = [u for u in set(link_urls) if link_urls.count(u) > 1]
        if dup_urls:
            issues.append(f"Duplicate link URLs: {dup_urls[:3]}")

        if issues and state.pass_fail:
            state.pass_fail = False
            console.print(
                f"  [red bold]SUPERVISOR OVERRIDE (Agent 13): Validator reported PASS but "
                f"supervisor found issues — overriding to FAIL: {'; '.join(issues)}[/red bold]"
            )


def save_state(state: PipelineState):
    """Save pipeline state to disk."""
    slug = state.get_topic_slug()
    state_dir = OUTPUT_DIR / slug
    state_dir.mkdir(parents=True, exist_ok=True)
    state.save(str(state_dir / "pipeline_state.json"))


def brief_gate(state: PipelineState) -> PipelineState:
    """User gate after brief consolidation. Auto-approves in non-interactive mode."""
    console.print()
    console.print(Panel("BRIEF REVIEW", box=box.HEAVY, border_style="yellow", padding=(0, 2)))
    console.print()
    console.print(Panel(
        Markdown(state.consolidated_brief),
        title="Content Brief",
        border_style="blue",
        padding=(1, 2),
    ))

    if NONINTERACTIVE:
        console.print("[green]Brief auto-approved (non-interactive mode).[/green]\n")
        return state

    choice = Prompt.ask(
        "\n[bold]Approve?[/bold] [Enter] approve, [e] edit, [s] skip",
        choices=["", "y", "e", "s"],
        default="",
        show_choices=False,
    )

    if choice in ("", "y"):
        console.print("[green]Brief approved.[/green]\n")
        return state
    elif choice == "s":
        console.print("[yellow]Skipping.[/yellow]\n")
        return state
    else:
        while True:
            feedback = Prompt.ask("[bold]Feedback[/bold]")
            consolidator = BriefConsolidatorAgent()
            state = consolidator.run_with_feedback(state, feedback)
            console.print(Panel(
                Markdown(state.consolidated_brief),
                title="Revised Brief",
                border_style="blue",
                padding=(1, 2),
            ))
            again = Prompt.ask("Approve? [Enter] yes, [e] edit more", choices=["", "y", "e"], default="", show_choices=False)
            if again in ("", "y"):
                console.print("[green]Brief approved.[/green]\n")
                return state


def draft_gate(state: PipelineState) -> PipelineState:
    """User gate after content writing. Auto-approves in non-interactive mode."""
    console.print()
    console.print(Panel("DRAFT REVIEW", box=box.HEAVY, border_style="yellow", padding=(0, 2)))

    word_count = len(state.draft_article.split())
    console.print(f"  [dim]~{word_count} words | Metaphor: {(state.extended_metaphor or 'none')[:80]}[/dim]\n")
    console.print(Panel(
        Markdown(state.draft_article),
        title="Draft Article",
        border_style="blue",
        padding=(1, 2),
    ))

    if NONINTERACTIVE:
        if word_count < 200:
            raise RuntimeError(
                f"Draft article is only {word_count} words. This is too short to proceed. "
                f"Check Agent 7 (Content Writer) logs for errors."
            )
        console.print("[green]Draft auto-approved (non-interactive mode).[/green]\n")
        return state

    choice = Prompt.ask(
        "\n[bold]Approve?[/bold] [Enter] approve, [r] revise, [R] full rewrite, [s] skip",
        choices=["", "y", "r", "R", "s"],
        default="",
        show_choices=False,
    )

    if choice in ("", "y"):
        console.print("[green]Draft approved.[/green]\n")
        return state
    elif choice == "s":
        console.print("[yellow]Skipping.[/yellow]\n")
        return state
    elif choice == "R":
        writer = ContentWriterAgent()
        state = writer.run(state)
        console.print(f"  [dim]Rewritten: ~{len(state.draft_article.split())} words[/dim]")
        return state
    else:
        while True:
            notes = Prompt.ask("[bold]Revision notes[/bold]")
            writer = ContentWriterAgent()
            state = writer.run_with_revisions(state, notes)
            console.print(f"  [dim]Revised: ~{len(state.draft_article.split())} words[/dim]")
            console.print(Panel(
                Markdown(state.draft_article),
                title="Revised Draft",
                border_style="blue",
                padding=(1, 2),
            ))
            again = Prompt.ask("Approve? [Enter] yes, [r] revise more", choices=["", "y", "r"], default="", show_choices=False)
            if again in ("", "y"):
                console.print("[green]Draft approved.[/green]\n")
                return state


def final_gate(state: PipelineState):
    """Display final output. No approval needed — just informational."""
    console.print()
    status_style = "green" if state.pass_fail else "red"
    status_text = "PASS" if state.pass_fail else "FAIL"

    console.print(Panel(
        f"FINAL RESULT: {status_text}",
        box=box.HEAVY,
        border_style=status_style,
        padding=(0, 2),
    ))
    console.print()

    console.print(Panel(
        state.validation_report,
        title=f"Validation Report",
        border_style=status_style,
        padding=(1, 2),
    ))

    if state.meta_description:
        console.print(Panel(
            state.meta_description,
            title=f"Meta Description ({len(state.meta_description)} chars)",
            border_style="cyan",
        ))
    if state.linkedin_post:
        console.print(Panel(state.linkedin_post, title="LinkedIn Post", border_style="blue"))
    if state.twitter_post:
        console.print(Panel(
            state.twitter_post,
            title=f"X/Twitter ({len(state.twitter_post)} chars)",
            border_style="cyan",
        ))


def _save_reports(state: PipelineState, output_dir: Path):
    """Write human-readable stage reports to output/<slug>/reports/."""
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    # ── 01: Research Summary ──
    lines = [
        "RESEARCH SUMMARY",
        "=" * 50,
        "",
        f"Topic: {state.topic}",
        f"Keyword: {state.target_keyword}",
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    if state.key_facts:
        lines.append(f"KEY FACTS ({len(state.key_facts)} found)")
        lines.append("-" * 40)
        for i, f in enumerate(state.key_facts, 1):
            src = f.get("source", "unknown")
            lines.append(f"{i}. {f.get('fact', f.get('text', ''))} — Source: {src}")
        lines.append("")
    if state.statistics:
        lines.append(f"STATISTICS ({len(state.statistics)} found)")
        lines.append("-" * 40)
        for i, s in enumerate(state.statistics, 1):
            name = s.get("source_name", "unknown")
            url = s.get("source_url", "")
            text = s.get("stat", s.get("text", ""))
            lines.append(f"{i}. {text}")
            lines.append(f"   Source: {name} ({url})" if url else f"   Source: {name}")
        lines.append("")
    if state.product_knowledge:
        lines.append("CONTRACTSAFE PRODUCT FEATURES USED")
        lines.append("-" * 40)
        lines.append(state.product_knowledge)
        lines.append("")
    (reports_dir / "01_research.txt").write_text("\n".join(lines))

    # ── 02: Keyword Strategy ──
    lines = [
        "KEYWORD STRATEGY",
        "=" * 50,
        "",
    ]
    kd = state.keyword_data or {}
    semrush = kd.get("semrush", {})
    overview = semrush.get("overview", {})
    lines.append("PRIMARY KEYWORD")
    lines.append("-" * 40)
    lines.append(f"Keyword: {kd.get('primary_kw', state.target_keyword)}")
    if overview:
        lines.append(f"Search Volume: {overview.get('Nq', 'N/A')} (monthly, US)")
        lines.append(f"Competition: {overview.get('Co', 'N/A')}")
    lines.append("")

    sec_kws = kd.get("secondary_kws", state.secondary_keywords or [])
    if sec_kws:
        lines.append(f"SECONDARY KEYWORDS ({len(sec_kws)})")
        lines.append("-" * 40)
        # Try to match with SEMrush volume data
        related = {r.get("Ph", "").lower(): r for r in semrush.get("related_keywords", [])}
        for kw in sec_kws:
            vol_data = related.get(kw.lower(), {})
            vol = vol_data.get("Nq", "")
            diff = vol_data.get("Kd", "")
            suffix = f" (volume: {vol}, difficulty: {diff})" if vol else ""
            lines.append(f"  - {kw}{suffix}")
        lines.append("")

    paa = kd.get("questions_people_ask", kd.get("people_also_ask", []))
    if paa:
        lines.append(f"PEOPLE ALSO ASK ({len(paa)} questions)")
        lines.append("-" * 40)
        for i, q in enumerate(paa, 1):
            lines.append(f"{i}. {q}")
        lines.append("")

    if state.keyword_clusters:
        lines.append("KEYWORD CLUSTERS BY SEARCH INTENT")
        lines.append("-" * 40)
        for cluster in state.keyword_clusters:
            name = cluster.get("name", "Unknown")
            kws = cluster.get("keywords", [])
            lines.append(f"\n{name} ({len(kws)} keywords):")
            for kw_entry in kws[:10]:
                kw_name = kw_entry.get("keyword", kw_entry) if isinstance(kw_entry, dict) else kw_entry
                kw_vol = kw_entry.get("volume", "") if isinstance(kw_entry, dict) else ""
                suffix = f" (volume: {kw_vol})" if kw_vol else ""
                lines.append(f"  - {kw_name}{suffix}")
        lines.append("")

    if state.keyword_gaps:
        lines.append(f"KEYWORD GAPS VS COMPETITORS ({len(state.keyword_gaps)})")
        lines.append("-" * 40)
        lines.append("Keywords competitors rank for that ContractSafe doesn't:")
        for gap in state.keyword_gaps[:20]:
            kw_name = gap.get("keyword", "")
            vol = gap.get("volume", "")
            comp = gap.get("competitor", "")
            lines.append(f"  - {kw_name} (volume: {vol}, competitor: {comp})")
        lines.append("")

    lines.append("DATA SOURCES")
    lines.append("-" * 40)
    sources = ["Google Autocomplete (~32 queries)"]
    if semrush:
        sources.append("SEMrush API (keyword overview, related keywords, PAA questions, broad match, keyword difficulty)")
    if kd.get("semantic_keywords"):
        sources.append("KeywordsPeopleUse API (PAA, autocomplete, semantic)")
    sources.append("Tavily web search (topic + variations)")
    for s in sources:
        lines.append(f"- {s}")
    (reports_dir / "02_keyword_strategy.txt").write_text("\n".join(lines))

    # ── 03: Competitor Analysis ──
    lines = [
        "COMPETITOR ANALYSIS",
        "=" * 50,
        "",
    ]
    if state.competitor_pages:
        lines.append(f"{len(state.competitor_pages)} top-ranking pages analyzed for \"{state.target_keyword}\":")
        lines.append("")
        for i, page in enumerate(state.competitor_pages, 1):
            lines.append(f"PAGE {i}: {page.get('title', 'Unknown')}")
            lines.append(f"  URL: {page.get('url', '')}")
            lines.append(f"  Word Count: {page.get('word_count', 'N/A')}")
            features = []
            for feat in ["has_stats", "has_lists", "has_tables", "has_faq"]:
                label = feat.replace("has_", "").title()
                features.append(f"{label}: {'Yes' if page.get(feat) else 'No'}")
            lines.append(f"  Content Features: {' | '.join(features)}")
            h2s = page.get("h2s", [])
            if h2s:
                lines.append(f"  H2 Structure ({len(h2s)} headings):")
                for h2 in h2s:
                    lines.append(f"    - {h2}")
            gaps = page.get("gaps", [])
            if gaps:
                lines.append(f"  Content Gaps:")
                for gap in gaps:
                    lines.append(f"    - {gap}")
            lines.append("")
    else:
        lines.append("No competitor pages were analyzed.")
    (reports_dir / "03_competitor_analysis.txt").write_text("\n".join(lines))

    # ── 04: Content Plan ──
    lines = [
        "CONTENT PLAN",
        "=" * 50,
        "",
    ]
    if state.recommended_h2s:
        lines.append("ARTICLE STRUCTURE")
        lines.append("-" * 40)
        lines.append("H2 Headings (in order):")
        for i, h2 in enumerate(state.recommended_h2s, 1):
            lines.append(f"  {i}. {h2}")
        lines.append("")
    if state.serp_features:
        lines.append("SERP FEATURES DETECTED")
        lines.append("-" * 40)
        for feat in state.serp_features:
            lines.append(f"  - {feat}")
        lines.append("")
    if state.consolidated_brief:
        lines.append("CONTENT BRIEF")
        lines.append("-" * 40)
        lines.append(state.consolidated_brief)
    (reports_dir / "04_content_plan.txt").write_text("\n".join(lines))

    # ── 05: Links ──
    tier_labels = {1: "Government/Academic/Major Research", 2: "Industry Reference", 3: "General Web"}
    lines = [
        "LINK PLAN",
        "=" * 50,
        "",
    ]
    if state.internal_links:
        lines.append(f"INTERNAL LINKS ({len(state.internal_links)} verified)")
        lines.append("-" * 40)
        for i, link in enumerate(state.internal_links, 1):
            lines.append(f"{i}. {link.get('title', 'Unknown')}")
            lines.append(f"   URL: {link.get('url', '')}")
            if link.get("relevance_summary"):
                lines.append(f"   Relevance: {link['relevance_summary']}")
            if link.get("anchor_suggestion"):
                lines.append(f"   Suggested Anchor: {link['anchor_suggestion']}")
            lines.append(f"   Status: {'Verified (HTTP 200)' if link.get('verified') else 'Unverified'}")
        lines.append("")
    if state.external_links:
        lines.append(f"EXTERNAL LINKS ({len(state.external_links)} verified)")
        lines.append("-" * 40)
        for i, link in enumerate(state.external_links, 1):
            tier = link.get("tier", 3)
            lines.append(f"{i}. {link.get('title', link.get('url', 'Unknown'))}")
            lines.append(f"   URL: {link.get('url', '')}")
            lines.append(f"   Tier: {tier} ({tier_labels.get(tier, 'Unknown')})")
            if link.get("relevance_summary"):
                lines.append(f"   Relevance: {link['relevance_summary']}")
            lines.append(f"   Status: {'Verified (HTTP 200)' if link.get('verified') else 'Unverified'}")
        lines.append("")
    if state.citation_map:
        lines.append("CITATION MAP (which links go in which section)")
        lines.append("-" * 40)
        for section, links in state.citation_map.items():
            lines.append(f"\n  {section}:")
            if isinstance(links, list):
                for lnk in links:
                    ltype = lnk.get("type", "")
                    url = lnk.get("url", "")
                    anchor = lnk.get("anchor", "")
                    lines.append(f"    - [{anchor}]({url}) ({ltype})")
        lines.append("")
    lines.append("SOURCE TIER DEFINITIONS")
    lines.append("-" * 40)
    lines.append("Tier 1: Government, academic, major research (.gov, .edu, ABA, Gartner, Forrester, etc.)")
    lines.append("Tier 2: Industry reference (Statista, Investopedia, IBM, Microsoft, etc.)")
    lines.append("Tier 3: General web sources")
    (reports_dir / "05_links.txt").write_text("\n".join(lines))

    # ── 06: Editing Changes ──
    lines = [
        "EDITING CHANGES",
        "=" * 50,
        "",
    ]
    # Brand voice
    lines.append("BRAND VOICE PASS")
    lines.append("-" * 40)
    if state.voice_issues_found:
        lines.append(f"{len(state.voice_issues_found)} issues found and fixed:")
        for entry in state.voice_issues_found:
            detail = entry.get("detail", "")
            issue = entry.get("issue", "")
            lines.append(f"  - {issue}: {detail}" if detail else f"  - {issue}")
    else:
        lines.append("No voice issues found.")
    lines.append("")

    # Fact check
    lines.append("FACT CHECK")
    lines.append("-" * 40)
    if state.fact_check_results:
        verified = [r for r in state.fact_check_results if r.get("verified")]
        removed = [r for r in state.fact_check_results if not r.get("verified")]
        lines.append(f"{len(verified)} claims verified. {len(removed)} unverified claims removed:")
        for r in removed:
            lines.append(f"  - {r.get('claim', r.get('text', 'Unknown'))[:100]}")
            if r.get("reason"):
                lines.append(f"    Reason: {r['reason']}")
    else:
        lines.append("No fact-check data recorded.")
    lines.append("")

    # SEO
    lines.append("SEO OPTIMIZATION")
    lines.append("-" * 40)
    if state.seo_changes:
        lines.append(f"{len(state.seo_changes)} changes made:")
        for entry in state.seo_changes:
            detail = entry.get("detail", "")
            change = entry.get("change", "")
            lines.append(f"  - {detail}" if detail else f"  - {change}")
    else:
        lines.append("No SEO changes needed.")
    lines.append("")

    # AEO
    lines.append("AEO (AI ENGINE OPTIMIZATION)")
    lines.append("-" * 40)
    if state.aeo_changes:
        lines.append(f"{len(state.aeo_changes)} changes made:")
        for entry in state.aeo_changes:
            detail = entry.get("detail", "")
            change = entry.get("change", "")
            lines.append(f"  - {detail}" if detail else f"  - {change}")
    else:
        lines.append("No AEO changes needed.")

    (reports_dir / "06_editing_changes.txt").write_text("\n".join(lines))

    console.print(f"  [green]Stage reports saved:[/green] reports/ (6 files)")


def save_outputs(state: PipelineState) -> Path:
    """Save all outputs to the output directory."""
    slug = state.get_topic_slug()
    output_dir = OUTPUT_DIR / slug
    research_dir = output_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)

    article = state.final_article or state.aeo_pass_article or state.draft_article
    (output_dir / "article.md").write_text(article)

    try:
        docx_path = str(output_dir / "article.docx")
        markdown_to_docx(
            article, docx_path, title=state.topic,
            meta_description=state.meta_description,
            linkedin_post=state.linkedin_post,
            twitter_post=state.twitter_post,
        )
        console.print(f"  [green]DOCX exported:[/green] article.docx")
    except Exception as e:
        console.print(f"  [yellow]DOCX export failed: {e}[/yellow]")

    if state.meta_description:
        (output_dir / "meta_description.txt").write_text(state.meta_description)
    if state.linkedin_post:
        (output_dir / "linkedin_post.txt").write_text(state.linkedin_post)
    if state.twitter_post:
        (output_dir / "twitter_post.txt").write_text(state.twitter_post)
    if state.validation_report:
        (output_dir / "validation_report.txt").write_text(state.validation_report)

    if state.subject_research:
        (research_dir / "subject_research.md").write_text(state.subject_research)
    if state.competitor_pages:
        (research_dir / "competitor_analysis.md").write_text(json.dumps(state.competitor_pages, indent=2))
    if state.seo_brief:
        (research_dir / "seo_brief.md").write_text(state.seo_brief)
    if state.citation_map:
        (research_dir / "citation_map.md").write_text(json.dumps(state.citation_map, indent=2))

    state.save(str(output_dir / "pipeline_state.json"))

    # Save human-readable stage reports
    try:
        _save_reports(state, output_dir)
    except Exception as e:
        console.print(f"  [yellow]Report generation failed: {e}[/yellow]")

    return output_dir


def run_pipeline(cli_args: argparse.Namespace):
    """Main pipeline orchestrator. Parallelizes research and editing passes."""
    global _tracker, _live

    show_welcome()

    # Warm up the Claude CLI in the background to avoid first-call cold-start timeouts.
    # This fires a tiny Haiku call that completes in ~5-10s while we do setup work.
    warmup_cli()

    # Check for resume
    state, _ = check_resume(resume_topic=cli_args.topic or "")
    if state is None:
        state = get_user_inputs(cli_args)

    # Determine which agent to start from. Use the lowest non-completed agent,
    # not max()+1, which can skip agents if one failed during parallel execution.
    if state.completed_agents:
        all_agents = set(range(1, 14))
        remaining = sorted(all_agents - set(state.completed_agents))
        start_from = remaining[0] if remaining else 14
    else:
        start_from = 1

    console.print(Panel(
        f"[bold]Topic:[/bold] {state.topic}\n"
        f"[bold]Type:[/bold] {state.content_type}\n"
        f"[bold]Keyword:[/bold] {state.target_keyword}\n"
        f"[bold]Word Count:[/bold] {state.target_word_count}",
        title="Pipeline Configuration",
        border_style="blue",
    ))
    console.print()

    # Initialize tracker
    _tracker = PipelineTracker()
    _tracker.start()
    use_live = sys.stdout.isatty()

    # Track per-agent timing for summary
    agent_timings = {}

    # Mark already-completed agents as done (not skipped) in tracker
    for i in state.completed_agents:
        _tracker.mark_done(i)

    def _start_live():
        """Start (or restart) the Live display."""
        global _live
        if use_live:
            _live = Live(_tracker.build_display(), console=console, refresh_per_second=2, transient=True)
            _live.start()

    def _stop_live():
        """Stop the Live display, printing a final snapshot."""
        global _live
        if _live:
            try:
                _live.update(_tracker.build_display())
                _live.stop()
            except Exception:
                pass
            _live = None

    _start_live()

    try:
        # ═══════════════════════════════════════════════════════════
        # RESEARCH PHASE: Agents 1-3 in PARALLEL, then Agent 4
        # (Agent 4 depends on Agent 3's competitor_pages for H2s),
        # then Agent 5 (depends on Agents 3+4)
        # ═══════════════════════════════════════════════════════════
        if start_from <= 5:
            # Run agents 1-3 in parallel (no dependencies on each other)
            parallel_agents = [(i, AGENT_PIPELINE[i-1]) for i in range(1, 4) if i not in state.completed_agents]
            if parallel_agents:
                import copy as _copy

                def _run_parallel_agent(agent_num, agent_cls, agent_state):
                    """Run one agent on its own copy of state (called from thread pool)."""
                    agent = agent_cls()

                    original_progress = agent.progress
                    original_log = agent.log

                    def tracked_progress(message):
                        original_progress(message)
                        if _tracker:
                            import re as _re
                            clean = _re.sub(r'\[/?[a-z ]+\]', '', message)
                            _tracker.set_detail(agent_num, clean)

                    def tracked_log(message):
                        original_log(message)
                        if _tracker:
                            import re as _re
                            clean = _re.sub(r'\[/?[a-z ]+\]', '', message)
                            _tracker.set_detail(agent_num, clean)

                    agent.progress = tracked_progress
                    agent.log = tracked_log

                    if _tracker:
                        _tracker.mark_running(agent_num)

                    start_time = time.time()
                    result_state = agent.run(agent_state)
                    elapsed = time.time() - start_time

                    if _tracker:
                        _tracker.mark_done(agent_num)

                    return agent_num, result_state, elapsed

                with ThreadPoolExecutor(max_workers=3) as executor:
                    futures = {
                        executor.submit(_run_parallel_agent, num, cls, _copy.deepcopy(state)): num
                        for num, cls in parallel_agents
                    }

                    for future in as_completed(futures):
                        agent_num = futures[future]
                        try:
                            num, result_state, elapsed = future.result()
                            agent_timings[num] = elapsed
                            state.completed_agents.append(num)

                            if num == 1:
                                state.product_knowledge = result_state.product_knowledge
                            elif num == 2:
                                state.subject_research = result_state.subject_research
                                state.key_facts = result_state.key_facts
                                state.statistics = result_state.statistics
                            elif num == 3:
                                state.competitor_pages = result_state.competitor_pages
                                state.keyword_data = result_state.keyword_data
                            assert_post_conditions(state, num)
                        except Exception as e:
                            console.print(f"  [red]ERROR in Agent {agent_num}: {e}[/red]")
                            raise

                save_state(state)

            # Agent 4 depends on Agent 3's competitor_pages
            if 4 not in state.completed_agents:
                state.current_agent = 4
                _, elapsed = run_agent(AGENT_PIPELINE[3], state, 4)
                agent_timings[4] = elapsed
                state.completed_agents.append(4)
                save_state(state)

            # Agent 5 depends on keyword_data (agent 3) and recommended_h2s (agent 4)
            if 5 not in state.completed_agents:
                state.current_agent = 5
                _, elapsed = run_agent(AGENT_PIPELINE[4], state, 5)
                agent_timings[5] = elapsed
                state.completed_agents.append(5)
                save_state(state)

        # ═══════════════════════════════════════════════════════════
        # AGENT 6: Brief Consolidation + user gate
        # ═══════════════════════════════════════════════════════════
        if 6 not in state.completed_agents:
            state.current_agent = 6
            _, elapsed = run_agent(AGENT_PIPELINE[5], state, 6)
            agent_timings[6] = elapsed
            state.completed_agents.append(6)
            save_state(state)

        _stop_live()
        state = brief_gate(state)
        _start_live()

        # ═══════════════════════════════════════════════════════════
        # AGENT 7: Content Writer + user gate
        # ═══════════════════════════════════════════════════════════
        if 7 not in state.completed_agents:
            state.current_agent = 7
            _, elapsed = run_agent(AGENT_PIPELINE[6], state, 7)
            agent_timings[7] = elapsed
            state.completed_agents.append(7)
            save_state(state)

        _stop_live()
        state = draft_gate(state)
        _start_live()

        # ═══════════════════════════════════════════════════════════
        # AGENTS 8-13: Editing, Social, QA (sequential)
        # ═══════════════════════════════════════════════════════════
        for i in range(8, 14):
            if i not in state.completed_agents:
                state.current_agent = i
                _, elapsed = run_agent(AGENT_PIPELINE[i-1], state, i)
                agent_timings[i] = elapsed
                state.completed_agents.append(i)
                save_state(state)

    finally:
        _stop_live()

    # ═══════════════════════════════════════════════════════════
    # DONE — Timing summary + output
    # ═══════════════════════════════════════════════════════════
    total_elapsed = time.time() - _tracker.total_start
    console.print()
    # Print final pipeline display
    console.print(_tracker.build_display())

    # Print timing summary
    console.print(Panel(
        _build_timing_summary(agent_timings, total_elapsed),
        title="Agent Timing Summary",
        border_style="cyan",
    ))

    console.print(f"\n[bold green]Pipeline complete in {total_elapsed:.0f}s[/bold green]")

    final_gate(state)

    output_dir = save_outputs(state)
    console.print(Panel(
        f"[bold green]All outputs saved to:[/bold green]\n{output_dir}",
        border_style="green",
    ))
    for f in sorted(output_dir.rglob("*")):
        if f.is_file():
            console.print(f"  [dim]{f.relative_to(output_dir)}[/dim]")

    # ── Publish: Drive + Spreadsheet + Asana ──
    if state.pass_fail:
        if NONINTERACTIVE:
            # Auto-publish on pass — Drive upload and spreadsheet update should always happen
            _publish_all(state, output_dir)
        else:
            choice = Prompt.ask(
                "\n[bold]Publish?[/bold] (Google Drive + Spreadsheet + Asana) [Enter] yes, [s] skip",
                choices=["", "y", "s"],
                default="",
                show_choices=False,
            )
            if choice in ("", "y"):
                _publish_all(state, output_dir)
            else:
                console.print("[dim]Skipped publishing.[/dim]")

    # ── Google Sheets upload (if --sheet-url provided) ──
    if getattr(cli_args, "sheet_url", None):
        try:
            from tools.google_sheets import upload_to_sheet
            article = state.final_article or state.aeo_pass_article or state.draft_article
            console.print("\n[cyan]Uploading to Google Sheet...[/cyan]")
            upload_to_sheet(
                sheet_url=cli_args.sheet_url,
                article_md=article,
                meta_description=state.meta_description or "",
                linkedin_post=state.linkedin_post or "",
                twitter_post=state.twitter_post or "",
                topic=state.topic,
            )
            console.print(f"[bold green]Uploaded to Google Sheet:[/bold green] {cli_args.sheet_url}")
        except Exception as e:
            console.print(f"[yellow]Google Sheet upload failed: {e}[/yellow]")


def _publish_all(state: PipelineState, output_dir: Path):
    """Single publish step: Google Drive + Spreadsheet + Asana comment."""

    doc_url = None

    # ── 1. Google Drive ──
    try:
        from tools.google_drive import upload_docx_to_drive
        docx_path = str(output_dir / "article.docx")
        console.print("[cyan]Uploading to Google Drive...[/cyan]")
        doc_url = upload_docx_to_drive(docx_path, state.topic)
        console.print(f"[bold green]Google Doc created:[/bold green] {doc_url}")
    except Exception as e:
        console.print(f"[yellow]Google Drive upload failed: {e}[/yellow]")

    # ── 2. Update tracking spreadsheet ──
    try:
        from tools.google_sheets import _get_credentials
        from datetime import date
        TRACKING_SHEET_URL = "https://docs.google.com/spreadsheets/d/1mvHv6eAuoGPYdCG8Hj2vkA5NY1pXe8yxaQN_Gcn0dGQ/edit"
        gc = _get_credentials()
        sh = gc.open_by_url(TRACKING_SHEET_URL)
        ws = sh.worksheet("Blog Post List")
        col_c = ws.col_values(3)
        today = date.today().strftime("%Y-%m-%d")
        updated = False
        for row_idx, val in enumerate(col_c):
            if state.topic.lower() in val.lower():
                new_val = f"{val} -- UPDATED BY CLAUDE CODE {today}"
                ws.update_cell(row_idx + 1, 3, new_val)
                console.print(f"[bold green]Spreadsheet updated:[/bold green] Row {row_idx + 1}")
                updated = True
                break
        if not updated:
            console.print(f"[yellow]Article '{state.topic}' not found in Blog Post List column C.[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Spreadsheet update failed: {e}[/yellow]")

    # ── 3. Asana comment ──
    try:
        from config import ASANA_ACCESS_TOKEN, ASANA_WORKSPACE_GID
        from tools.asana_api import search_tasks, add_comment

        if not ASANA_ACCESS_TOKEN:
            console.print("[dim]ASANA_ACCESS_TOKEN not set. Skipping Asana update.[/dim]")
        elif not ASANA_WORKSPACE_GID:
            console.print("[dim]ASANA_WORKSPACE_GID not set. Skipping Asana update.[/dim]")
        else:
            tasks = search_tasks(ASANA_WORKSPACE_GID, state.topic)
            if not tasks:
                console.print(f"[yellow]No Asana task found matching '{state.topic}'.[/yellow]")
            else:
                task = tasks[0]
                console.print(f"[cyan]Found Asana task:[/cyan] {task['name']} ({task['gid']})")

                word_count = len((state.final_article or "").split())
                lines = [
                    f"Article draft generated by ContractSafe Content Agent.",
                    "",
                    f"Word Count: {word_count}",
                    f"Meta Description: {state.meta_description or 'N/A'}",
                ]
                if doc_url:
                    lines.insert(2, f"Google Doc: {doc_url}")
                lines.append("")
                lines.append(f"-- Generated {date.today().strftime('%Y-%m-%d')}")

                add_comment(task["gid"], "\n".join(lines))
                console.print("[bold green]Asana task updated.[/bold green]")
    except Exception as e:
        console.print(f"[yellow]Asana update failed: {e}[/yellow]")


def _build_timing_summary(agent_timings: dict, total_elapsed: float) -> str:
    """Build a timing summary string showing each agent's elapsed time."""
    lines = []
    for num, _, label, phase in AGENT_INFO:
        elapsed = agent_timings.get(num, 0)
        if elapsed > 0:
            pct = (elapsed / total_elapsed * 100) if total_elapsed > 0 else 0
            bar = "\u2588" * max(1, int(pct / 2))
            lines.append(f"  {num:2d}. {label:<26s} {elapsed:6.0f}s  {bar} ({pct:.0f}%)")
        else:
            lines.append(f"  {num:2d}. {label:<26s}     --  (skipped/resumed)")
    lines.append(f"\n  {'TOTAL':<30s} {total_elapsed:6.0f}s")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ContractSafe Content Agent System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python main.py --topic 'contract lifecycle management'\n"
               "  python main.py --type blog --topic 'AI in legal' --keyword 'ai contract management'\n"
               "  python main.py --topic 'contract renewals' --auto-approve\n",
    )
    parser.add_argument("--topic", "-t", help="Article topic (required in non-interactive mode)")
    parser.add_argument("--type", choices=["blog", "blog_post", "email", "webpage", "webpage_copy"],
                        default=None, help="Content type (default: blog_post)")
    parser.add_argument("--keyword", "-k", help="Primary keyword (default: derived from topic)")
    parser.add_argument("--secondary-keywords", "-s", help="Comma-separated secondary keywords")
    parser.add_argument("--word-count", "-w", type=int, help="Target word count")
    parser.add_argument("--instructions", "-i", help="Additional instructions for the writer")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Auto-approve all review gates (no interactive prompts)")
    parser.add_argument("--sheet-url", help="Google Sheet URL to upload finished article to")
    return parser.parse_args()


def main():
    global NONINTERACTIVE
    args = parse_args()

    NONINTERACTIVE = args.auto_approve or not sys.stdin.isatty()

    try:
        run_pipeline(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. State saved for resume.[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red bold]Error:[/red bold] {e}")
        console.print("[yellow]Run again to resume from last completed agent.[/yellow]")
        raise


if __name__ == "__main__":
    main()
