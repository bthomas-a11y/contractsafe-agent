#!/usr/bin/env python3
"""ContractSafe Content Agent System - CLI entry point and pipeline orchestrator."""

from __future__ import annotations

import sys
import time
import json
from pathlib import Path
from typing import Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.text import Text
from rich import box

from state import PipelineState
from config import OUTPUT_DIR, SEMRUSH_API_KEY, TAVILY_API_KEY, KEYWORDS_PEOPLE_USE_API_KEY
from agents import AGENT_PIPELINE
from agents.brief_consolidator import BriefConsolidatorAgent
from agents.content_writer import ContentWriterAgent

console = Console()

AGENT_INFO = [
    (1, "\U0001f50d", "Product Knowledge Base"),
    (2, "\U0001f4da", "Subject Matter Research"),
    (3, "\U0001f3f7\ufe0f", "Competitor/KW Research"),
    (4, "\U0001f4ca", "SEO Research"),
    (5, "\U0001f517", "Link Research"),
    (6, "\U0001f4cb", "Brief Consolidation"),
    (7, "\u270d\ufe0f", "Content Writer"),
    (8, "\U0001f3a4", "Brand Voice Pass"),
    (9, "\u2705", "Fact Check Pass"),
    (10, "\U0001f50e", "SEO Pass"),
    (11, "\U0001f916", "AEO Pass"),
    (12, "\U0001f4f1", "Social Copywriter"),
    (13, "\U0001f3c1", "Final Validator"),
]

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

    # Show which tools are active
    tools_status = []
    tools_status.append(f"  Tavily: [green]active[/green]" if TAVILY_API_KEY else "  Tavily: [red]not set[/red]")
    tools_status.append(f"  SEMrush: [green]active[/green]" if SEMRUSH_API_KEY else "  SEMrush: [dim]not set[/dim]")
    tools_status.append(f"  KeywordsPeopleUse: [green]active[/green]" if KEYWORDS_PEOPLE_USE_API_KEY else "  KeywordsPeopleUse: [dim]not set[/dim]")
    tools_status.append("  Google Autocomplete: [green]active[/green] (always free)")
    console.print(Panel("\n".join(tools_status), title="Tool Status", border_style="dim"))
    console.print()


def get_user_inputs() -> PipelineState:
    """Collect minimal user inputs. Derive everything else automatically."""
    state = PipelineState()

    # Content type — single choice
    console.print("[bold]Content type?[/bold]  [1] Blog Post  [2] Email  [3] Webpage Copy")
    choice = Prompt.ask("", choices=["1", "2", "3"], default="1", show_choices=False)
    state.content_type = {"1": "blog_post", "2": "email", "3": "webpage_copy"}[choice]
    console.print()

    # Topic — the one required input
    state.topic = Prompt.ask("[bold]Topic[/bold]")
    console.print()

    # Derive keyword from topic (user can override if they want)
    default_keyword = state.topic.lower().strip().rstrip(".:!?")
    # Remove common filler prefixes
    for prefix in ["a complete guide to ", "the ultimate guide to ", "guide to ", "how to ", "what is ", "why "]:
        if default_keyword.startswith(prefix):
            default_keyword = default_keyword[len(prefix):]
            break
    state.target_keyword = default_keyword

    # Set defaults
    state.target_word_count = DEFAULT_WORD_COUNTS[state.content_type]

    # Show what was auto-configured, offer quick override
    console.print(f"  [dim]Keyword:[/dim] {state.target_keyword}")
    console.print(f"  [dim]Word count:[/dim] {state.target_word_count}")
    console.print()

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


def check_resume() -> Tuple[Optional[PipelineState], Optional[Path]]:
    """Check for existing pipeline states. Only prompt if there are resumable runs."""
    if not OUTPUT_DIR.exists():
        return None, None

    state_files = list(OUTPUT_DIR.glob("*/pipeline_state.json"))
    if not state_files:
        return None, None

    # Only show incomplete runs
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
            next_agent = max(state.completed_agents) + 1 if state.completed_agents else 1
            console.print(f"[green]Resuming from agent {next_agent}[/green]\n")
            return state, sf.parent
    except (ValueError, IndexError):
        pass

    return None, None


def run_agent(agent_cls, state: PipelineState, agent_num: int) -> tuple[PipelineState, float]:
    """Run a single agent with progress display."""
    emoji, label = AGENT_INFO[agent_num - 1][1], AGENT_INFO[agent_num - 1][2]

    agent = agent_cls()
    start_time = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn(f"[{agent_num}/13] {emoji} {label}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("", total=None)
        state = agent.run(state)

    elapsed = time.time() - start_time
    console.print(f"  [{agent_num}/13] {emoji} {label} {'.' * (40 - len(label))} [green]\u2713[/green] ({elapsed:.0f}s)")
    return state, elapsed


def save_state(state: PipelineState):
    """Save pipeline state to disk."""
    slug = state.get_topic_slug()
    state_dir = OUTPUT_DIR / slug
    state_dir.mkdir(parents=True, exist_ok=True)
    state.save(str(state_dir / "pipeline_state.json"))


def brief_gate(state: PipelineState) -> PipelineState:
    """User gate after brief consolidation. Default is approve (just press Enter)."""
    console.print()
    console.print(Panel("BRIEF REVIEW", box=box.HEAVY, border_style="yellow", padding=(0, 2)))
    console.print()
    console.print(Panel(
        Markdown(state.consolidated_brief),
        title="Content Brief",
        border_style="blue",
        padding=(1, 2),
    ))

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
    """User gate after content writing. Default is approve."""
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

    # Validation report
    console.print(Panel(
        state.validation_report,
        title=f"Validation Report",
        border_style=status_style,
        padding=(1, 2),
    ))

    # Meta + social
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


def run_pipeline():
    """Main pipeline orchestrator. Runs all 13 agents sequentially."""
    show_welcome()

    # Check for resume
    state, _ = check_resume()
    if state is None:
        state = get_user_inputs()

    start_from = max(state.completed_agents) + 1 if state.completed_agents else 1

    console.print(Panel(
        f"[bold]Topic:[/bold] {state.topic}\n"
        f"[bold]Type:[/bold] {state.content_type}\n"
        f"[bold]Keyword:[/bold] {state.target_keyword}\n"
        f"[bold]Word Count:[/bold] {state.target_word_count}",
        title="Pipeline Configuration",
        border_style="blue",
    ))
    console.print()

    total_start = time.time()

    # ── Research Phase: Agents 1-5 (fully automatic) ──
    for i, agent_cls in enumerate(AGENT_PIPELINE[:5], 1):
        if i < start_from:
            console.print(f"  [{i}/13] [dim]Skipped (already completed)[/dim]")
            continue
        state.current_agent = i
        state, _ = run_agent(agent_cls, state, i)
        state.completed_agents.append(i)
        save_state(state)

    # ── Agent 6: Brief Consolidation + user gate ──
    if 6 >= start_from:
        state.current_agent = 6
        state, _ = run_agent(AGENT_PIPELINE[5], state, 6)
        state.completed_agents.append(6)
        save_state(state)
        state = brief_gate(state)

    # ── Agent 7: Content Writer + user gate ──
    if 7 >= start_from:
        state.current_agent = 7
        state, _ = run_agent(AGENT_PIPELINE[6], state, 7)
        state.completed_agents.append(7)
        save_state(state)
        state = draft_gate(state)

    # ── Refinement Phase: Agents 8-11 (fully automatic) ──
    for i, agent_cls in enumerate(AGENT_PIPELINE[7:11], 8):
        if i < start_from:
            console.print(f"  [{i}/13] [dim]Skipped (already completed)[/dim]")
            continue
        state.current_agent = i
        state, _ = run_agent(agent_cls, state, i)
        state.completed_agents.append(i)
        save_state(state)

    # ── Agents 12-13: Social + Validation (fully automatic) ──
    for i, agent_cls in enumerate(AGENT_PIPELINE[11:13], 12):
        if i < start_from:
            console.print(f"  [{i}/13] [dim]Skipped (already completed)[/dim]")
            continue
        state.current_agent = i
        state, _ = run_agent(agent_cls, state, i)
        state.completed_agents.append(i)
        save_state(state)

    # ── Done ──
    total_elapsed = time.time() - total_start
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


def main():
    try:
        run_pipeline()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. State saved for resume.[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red bold]Error:[/red bold] {e}")
        console.print("[yellow]Run again to resume from last completed agent.[/yellow]")
        raise


if __name__ == "__main__":
    main()
