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

from rich.console import Console, Group
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
from config import OUTPUT_DIR, SEMRUSH_API_KEY, TAVILY_API_KEY, KEYWORDS_PEOPLE_USE_API_KEY, CLAUDE_CLI
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
    """Persistent pipeline progress display."""

    def __init__(self):
        self.agent_status = {}  # agent_num -> "pending" | "running" | "done" | "skipped"
        self.agent_times = {}   # agent_num -> elapsed seconds
        self.agent_detail = {}  # agent_num -> latest progress message
        self.total_start = None
        for num, _, _, _ in AGENT_INFO:
            self.agent_status[num] = "pending"

    def build_display(self) -> Table:
        """Build the full pipeline status display."""
        done_count = sum(1 for s in self.agent_status.values() if s in ("done", "skipped"))
        running = [n for n, s in self.agent_status.items() if s == "running"]
        elapsed = time.time() - self.total_start if self.total_start else 0

        outer = Table.grid(padding=(0, 0))

        agent_table = Table(
            box=box.SIMPLE,
            show_header=True,
            header_style="bold",
            padding=(0, 1),
            expand=True,
        )
        agent_table.add_column("#", width=4, justify="right")
        agent_table.add_column("", width=3)  # emoji
        agent_table.add_column("Agent", min_width=26)
        agent_table.add_column("Phase", width=10)
        agent_table.add_column("Status", width=12)
        agent_table.add_column("Time", width=8, justify="right")
        agent_table.add_column("Detail", ratio=1)

        for num, emoji, label, phase in AGENT_INFO:
            status = self.agent_status[num]
            elapsed_str = ""
            detail = self.agent_detail.get(num, "")

            if status == "done":
                status_text = Text("\u2713 Done", style="green")
                elapsed_str = f"{self.agent_times.get(num, 0):.0f}s"
                detail = ""
            elif status == "running":
                status_text = Text("\u25b6 Running", style="bold yellow")
                elapsed_str = f"{time.time() - self.agent_times.get(num, time.time()):.0f}s"
            elif status == "skipped":
                status_text = Text("- Skipped", style="dim")
            else:
                status_text = Text("\u2500 Pending", style="dim")

            if len(detail) > 50:
                detail = detail[:47] + "..."

            agent_table.add_row(
                str(num),
                emoji,
                label,
                Text(phase, style="dim"),
                status_text,
                elapsed_str,
                Text(detail, style="dim italic"),
            )

        outer.add_row(agent_table)

        bar_width = 30
        filled = int(bar_width * done_count / 13)
        bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
        pct = int(100 * done_count / 13)

        progress_line = Text()
        progress_line.append(f"\n  Progress: ", style="bold")
        progress_line.append(bar, style="green" if pct == 100 else "yellow")
        progress_line.append(f"  {done_count}/13 agents  ({pct}%)", style="bold")
        progress_line.append(f"  |  Total: {elapsed:.0f}s", style="dim")

        if running:
            running_names = ", ".join(AGENT_INFO[r-1][2] for r in running)
            progress_line.append(f"  |  Now: {running_names}", style="yellow")

        outer.add_row(progress_line)
        return outer

    def start(self):
        self.total_start = time.time()

    def mark_running(self, agent_num: int):
        self.agent_status[agent_num] = "running"
        self.agent_times[agent_num] = time.time()
        self.agent_detail[agent_num] = ""

    def mark_done(self, agent_num: int):
        self.agent_status[agent_num] = "done"
        start = self.agent_times.get(agent_num, time.time())
        self.agent_times[agent_num] = time.time() - start

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
        default_keyword = state.topic.lower().strip().rstrip(".:!?")
        # Strip common title prefixes
        for prefix in ["a complete guide to ", "the ultimate guide to ", "guide to ", "how to ", "what is ", "why "]:
            if default_keyword.startswith(prefix):
                default_keyword = default_keyword[len(prefix):]
                break
        # Strip common title suffixes (the title is not the keyword)
        import re as _re
        default_keyword = _re.split(
            r'\s*[:|\-,]\s*(?:key differences|differences|a (?:complete |comprehensive )?guide'
            r'|explained|what you need to know|everything you need to know'
            r'|best practices|tips and (?:tricks|strategies)|pros (?:and|&) cons)\b',
            default_keyword, maxsplit=1, flags=_re.IGNORECASE
        )[0].strip()
        # Cap at 5 words — anything longer is a title, not a keyword
        kw_words = default_keyword.split()
        if len(kw_words) > 5:
            default_keyword = " ".join(kw_words[:5])
        state.target_keyword = default_keyword

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
    """Run a single agent with progress tracking."""
    global _tracker, _live

    agent = agent_cls()

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
    console.print(f"\n{'='*60}")
    console.print(f"  STARTING Agent {agent_num}/13: {agent_label} (model: {agent.model}, timeout: {agent.timeout}s)")
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

    console.print(f"  COMPLETED Agent {agent_num}/13: {agent_label} in {elapsed:.0f}s")
    sys.stdout.flush()

    if _tracker:
        _tracker.mark_done(agent_num)
        if _live:
            try:
                _live.update(_tracker.build_display())
            except Exception:
                pass

    assert_post_conditions(state, agent_num)
    return state, elapsed


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

    def _maybe_live():
        """Context manager: Rich Live if TTY, otherwise a no-op."""
        if use_live:
            return Live(_tracker.build_display(), console=console, refresh_per_second=2, transient=False)
        from contextlib import nullcontext
        return nullcontext()

    # ═══════════════════════════════════════════════════════════
    # RESEARCH PHASE: Agents 1-3 in PARALLEL, then Agent 4
    # (Agent 4 depends on Agent 3's competitor_pages for H2s),
    # then Agent 5 (depends on Agents 3+4)
    # ═══════════════════════════════════════════════════════════
    if start_from <= 5:
        with _maybe_live() as live:
            _live = live if use_live else None

            # Run agents 1-3 in parallel (no dependencies on each other)
            # Use completed_agents check (not start_from) to avoid re-running on resume
            parallel_agents = [(i, AGENT_PIPELINE[i-1]) for i in range(1, 4) if i not in state.completed_agents]
            if parallel_agents:
                console.print(f"\n{'='*60}")
                console.print(f"  PARALLEL RESEARCH: Running agents {[a[0] for a in parallel_agents]} concurrently")
                console.print(f"{'='*60}")
                sys.stdout.flush()

                import copy as _copy

                def _run_parallel_agent(agent_num, agent_cls, agent_state):
                    """Run one agent on its own copy of state (called from thread pool)."""
                    agent = agent_cls()
                    agent_label = AGENT_INFO[agent_num - 1][2]

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

                    console.print(f"\n  STARTING Agent {agent_num}/13: {agent_label} (model: {agent.model}, timeout: {agent.timeout}s)")
                    sys.stdout.flush()

                    if _tracker:
                        _tracker.mark_running(agent_num)

                    start_time = time.time()
                    result_state = agent.run(agent_state)
                    elapsed = time.time() - start_time

                    console.print(f"  COMPLETED Agent {agent_num}/13: {agent_label} in {elapsed:.0f}s")
                    sys.stdout.flush()

                    if _tracker:
                        _tracker.mark_done(agent_num)

                    return agent_num, result_state, elapsed

                with ThreadPoolExecutor(max_workers=3) as executor:
                    # Each agent gets its own copy of state to avoid mutation conflicts
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

                            # Merge results: each agent writes to different fields
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

            # Agent 4 depends on Agent 3's competitor_pages — run AFTER agents 1-3
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

        _live = None

    # ═══════════════════════════════════════════════════════════
    # AGENT 6: Brief Consolidation + user gate
    # ═══════════════════════════════════════════════════════════
    if 6 not in state.completed_agents:
        with _maybe_live() as live:
            _live = live if use_live else None
            state.current_agent = 6
            _, elapsed = run_agent(AGENT_PIPELINE[5], state, 6)
            agent_timings[6] = elapsed
            state.completed_agents.append(6)
            save_state(state)
        _live = None
        state = brief_gate(state)

    # ═══════════════════════════════════════════════════════════
    # AGENT 7: Content Writer + user gate
    # ═══════════════════════════════════════════════════════════
    if 7 not in state.completed_agents:
        with _maybe_live() as live:
            _live = live if use_live else None
            state.current_agent = 7
            _, elapsed = run_agent(AGENT_PIPELINE[6], state, 7)
            agent_timings[7] = elapsed
            state.completed_agents.append(7)
            save_state(state)
        _live = None
        state = draft_gate(state)

    # ═══════════════════════════════════════════════════════════
    # AGENTS 8-9: Brand Voice + Fact Check (sequential)
    # ═══════════════════════════════════════════════════════════
    with _maybe_live() as live:
        _live = live if use_live else None
        for i in [8, 9]:
            if i not in state.completed_agents:
                state.current_agent = i
                _, elapsed = run_agent(AGENT_PIPELINE[i-1], state, i)
                agent_timings[i] = elapsed
                state.completed_agents.append(i)
                save_state(state)
    _live = None

    # ═══════════════════════════════════════════════════════════
    # AGENTS 10+11: SEO Pass then AEO Pass (sequential)
    # Running sequentially because AEO must operate on the
    # SEO-passed article to avoid losing SEO changes.
    # ═══════════════════════════════════════════════════════════
    with _maybe_live() as live:
        _live = live if use_live else None
        for i in [10, 11]:
            if i not in state.completed_agents:
                state.current_agent = i
                _, elapsed = run_agent(AGENT_PIPELINE[i-1], state, i)
                agent_timings[i] = elapsed
                state.completed_agents.append(i)
                save_state(state)
    _live = None

    # ═══════════════════════════════════════════════════════════
    # AGENT 12: Social Copy + AGENT 13: Final Validator
    # ═══════════════════════════════════════════════════════════
    with _maybe_live() as live:
        _live = live if use_live else None
        for i in [12, 13]:
            if i not in state.completed_agents:
                state.current_agent = i
                _, elapsed = run_agent(AGENT_PIPELINE[i-1], state, i)
                agent_timings[i] = elapsed
                state.completed_agents.append(i)
                save_state(state)
    _live = None

    # ═══════════════════════════════════════════════════════════
    # DONE — Timing summary + output
    # ═══════════════════════════════════════════════════════════
    total_elapsed = time.time() - _tracker.total_start
    console.print()
    if use_live:
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
