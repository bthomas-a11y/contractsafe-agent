"""Base agent class that all agents inherit from. Uses claude CLI for LLM calls."""

import os
import re
import sys
import subprocess
import threading
import time
import json
from datetime import datetime
from rich.console import Console
from state import PipelineState
from config import CLAUDE_CLI, MAX_RETRIES, RETRY_BASE_DELAY

console = Console()

# Per-agent-type timeout (seconds). Override with agent.timeout attribute.
DEFAULT_TIMEOUT = 180  # 3 minutes — most agents should finish well within this
WRITER_TIMEOUT = 300   # 5 minutes — the content writer gets more time

# Heartbeat interval: check every 15s (shorter than before — vigilance, not patience)
HEARTBEAT_INTERVAL = 15


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


class BaseAgent:
    """Base class for all pipeline agents."""

    name: str = "BaseAgent"
    description: str = ""
    agent_number: int = 0
    model: str = "sonnet"
    emoji: str = ""
    timeout: int = DEFAULT_TIMEOUT  # override per agent if needed

    def run(self, state: PipelineState) -> PipelineState:
        """Override in each agent. Takes state, returns modified state."""
        raise NotImplementedError

    def call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """
        Call Claude via the claude CLI with heartbeat monitoring and budget enforcement.

        No retries. If the call fails or times out, it raises immediately.
        Fix the root cause (prompt size, approach) instead of retrying.
        """
        prompt_len = len(system_prompt) + len(user_prompt)

        # Adaptive timeout: use the smaller of agent timeout and remaining budget
        budget = getattr(self, '_remaining_budget', None)
        effective_timeout = self.timeout
        if budget is not None and budget < effective_timeout:
            effective_timeout = max(int(budget), 10)  # never less than 10s

        self.progress(
            f"Calling Claude ({self.model}, ~{prompt_len // 1000}k chars prompt, "
            f"timeout {effective_timeout}s{f' [budget-capped from {self.timeout}s]' if effective_timeout < self.timeout else ''})..."
        )

        try:
            cmd = [
                CLAUDE_CLI, "-p",
                "--model", self.model,
                "--system-prompt", system_prompt,
                "--setting-sources", "user",
            ]

            full_prompt = user_prompt

            # Strip CLAUDECODE env var to allow nested claude CLI calls
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

            start_time = time.time()

            # Use Popen so we can monitor with heartbeats
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )

            # Vigilant heartbeat: assumes the call is probably failing.
            # At 1/3 timeout: warning. At 2/3: likely stuck. Escalating skepticism.
            stop_heartbeat = threading.Event()
            one_third = effective_timeout / 3
            two_thirds = effective_timeout * 2 / 3

            def heartbeat():
                while not stop_heartbeat.is_set():
                    stop_heartbeat.wait(HEARTBEAT_INTERVAL)
                    if not stop_heartbeat.is_set():
                        elapsed = time.time() - start_time
                        remaining = effective_timeout - elapsed
                        pct = elapsed / effective_timeout * 100

                        if elapsed < one_third:
                            self.progress(f"...waiting ({elapsed:.0f}s/{effective_timeout}s, {pct:.0f}%)")
                        elif elapsed < two_thirds:
                            self.log(
                                f"[yellow]WARNING: {elapsed:.0f}s elapsed ({pct:.0f}% of timeout). "
                                f"Prompt is ~{prompt_len // 1000}k chars. "
                                f"This call is probably too slow.[/yellow]"
                            )
                        else:
                            self.log(
                                f"[red]LIKELY STUCK: {elapsed:.0f}s elapsed ({pct:.0f}% of timeout). "
                                f"Only {remaining:.0f}s left. Prompt is ~{prompt_len // 1000}k chars. "
                                f"This call will almost certainly time out. "
                                f"Investigate prompt size immediately after failure.[/red]"
                            )

            hb_thread = threading.Thread(target=heartbeat, daemon=True)
            hb_thread.start()

            try:
                stdout, stderr = proc.communicate(input=full_prompt, timeout=effective_timeout)
            finally:
                stop_heartbeat.set()
                hb_thread.join(timeout=2)

            elapsed = time.time() - start_time
            self.progress(f"Claude responded in {elapsed:.0f}s ({len(stdout)} chars)")

            # Update remaining budget
            if budget is not None:
                self._remaining_budget = budget - elapsed

            if proc.returncode != 0:
                error_msg = stderr.strip() or "Unknown error"
                raise RuntimeError(
                    f"[{self.name}] claude CLI failed: {error_msg}. "
                    f"Prompt was ~{prompt_len // 1000}k chars."
                )

            return stdout.strip()

        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            stop_heartbeat.set()
            elapsed = time.time() - start_time
            raise RuntimeError(
                f"[{self.name}] CRITICAL TIMEOUT: claude CLI timed out after {elapsed:.0f}s "
                f"(limit: {effective_timeout}s). Prompt was ~{prompt_len // 1000}k chars.\n"
                f"THIS IS A CRITICAL FAILURE REQUIRING AN IMMEDIATE PROGRAMMATIC FIX.\n"
                f"DO NOT: increase timeout, adjust prompt size and retry, or add retries.\n"
                f"REQUIRED RESPONSE:\n"
                f"  1. Investigate root cause — what specifically caused the timeout?\n"
                f"  2. Find proof — measure prompt size, identify which part is too large\n"
                f"  3. Implement a PROGRAMMATIC fix that makes this timeout impossible in the future\n"
                f"     (e.g., replace the LLM call with Python code, split into smaller calls,\n"
                f"      or eliminate the call entirely like SEO/AEO passes)\n"
                f"  Adjusting prompt size and hoping it works is NOT a fix — there is no proof it will work."
            )

        except FileNotFoundError:
            raise RuntimeError(
                "claude CLI not found. Make sure 'claude' is installed and in your PATH. "
                "Install it with: npm install -g @anthropic-ai/claude-code"
            )

    def call_llm_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Call Claude and parse the response as JSON."""
        json_system = system_prompt + "\n\nIMPORTANT: Respond with valid JSON only. No markdown fencing, no commentary."
        response = self.call_llm(json_system, user_prompt)

        # Strip markdown code fences if present
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON within the response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
            # Try array
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
            self.log(f"[yellow]Warning: Could not parse JSON response. Using raw text.[/yellow]")
            return {"raw_response": response}

    # ── Shared delta mode parser ──

    def parse_delta_response(self, response: str) -> list[dict]:
        """Parse FIND/REPLACE pairs from a delta-mode LLM response.

        Handles:
        - Case-insensitive FIND/REPLACE keywords
        - Bold (**FIND:**), bullet (- FIND:), numbered (1. FIND:) prefixes
        - Quoted, backtick-wrapped, and unquoted text
        - Multiline text with proper indentation stripping
        - Empty REPLACE (for deletions)
        - Detection of full-article returns (no FIND: found)
        """
        changes = []

        # Normalize: strip markdown bold from FIND/REPLACE keywords
        normalized = re.sub(r'\*\*(FIND|REPLACE)\*\*', r'\1', response, flags=re.IGNORECASE)
        lines = normalized.split("\n")

        # Check if response looks like a full article return (no FIND/REPLACE at all)
        has_find = any(re.search(r'(?:^|\s)FIND\s*:', line, re.IGNORECASE) for line in lines)
        if not has_find:
            if len(response) > 500 and re.search(r'^#{1,3}\s', response, re.MULTILINE):
                self.progress("[yellow]WARNING: Response looks like a full article, not delta changes. No changes parsed.[/yellow]")
            return changes

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Match FIND: with various prefixes (numbered, bulleted, bold)
            find_match = re.match(
                r'^(?:\d+\.?\s*)?(?:-\s*)?FIND\s*:\s*(.*)$',
                line, re.IGNORECASE
            )

            if find_match:
                raw_find = find_match.group(1).strip()
                find_text, find_end_line = self._extract_delimited_text(raw_find, lines, i)

                if find_text is not None:
                    # Look for REPLACE: on next non-empty line after FIND ends
                    j = find_end_line + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1

                    if j < len(lines):
                        replace_line = lines[j].strip()
                        replace_match = re.match(
                            r'^(?:\d+\.?\s*)?(?:-\s*)?REPLACE\s*:\s*(.*)$',
                            replace_line, re.IGNORECASE
                        )

                        if replace_match:
                            raw_replace = replace_match.group(1).strip()
                            replace_text, replace_end_line = self._extract_delimited_text(
                                raw_replace, lines, j, allow_empty=True
                            )

                            if replace_text is not None:
                                changes.append({"find": find_text, "replace": replace_text})
                                i = replace_end_line + 1
                                continue

            i += 1

        return changes

    def _extract_delimited_text(
        self, first_part: str, lines: list[str], start_line: int, allow_empty: bool = False
    ) -> tuple:
        """Extract text from a FIND: or REPLACE: value, handling various delimiters.

        Returns (extracted_text, end_line_index) or (None, start_line) on failure.
        """
        # Case 1: Empty value (for deletions)
        if not first_part or first_part in ('""', '""', '``', "''"):
            if allow_empty:
                return "", start_line
            return None, start_line

        # Determine delimiter type
        if first_part.startswith(('"', '\u201c')):
            open_delim = first_part[0]
            close_delim = '"' if open_delim == '"' else '\u201d'
            content_start = first_part[1:]
        elif first_part.startswith('`'):
            open_delim = '`'
            close_delim = '`'
            content_start = first_part[1:]
        else:
            # Unquoted — accumulate continuation lines until a boundary
            accumulated = first_part
            j = start_line
            while j + 1 < len(lines):
                j += 1
                next_line = lines[j].strip()
                # Stop at: empty line, new FIND/REPLACE pair
                if not next_line:
                    break
                if re.match(r'^(?:\d+\.?\s*)?(?:-\s*)?(?:FIND|REPLACE)\s*:', next_line, re.IGNORECASE):
                    j -= 1  # back up so the outer loop sees this line
                    break
                accumulated += "\n" + next_line
            return accumulated, j

        # Check if single-line (content ends with closing delimiter)
        if content_start.rstrip().endswith(close_delim):
            text = content_start.rstrip()[:-1]
            return text if text or allow_empty else None, start_line

        # Multiline: accumulate until we find closing delimiter on its own or at end of line
        accumulated = content_start
        j = start_line
        while j + 1 < len(lines):
            j += 1
            next_line = lines[j]

            # Strip indentation that Claude adds for readability (up to 8 spaces)
            stripped = re.sub(r'^ {1,8}', '', next_line)

            # Check if this line has the closing delimiter
            # We need to find the LAST closing delimiter that isn't inside the content
            if stripped.rstrip().endswith(close_delim):
                # Check it's not a FIND/REPLACE keyword for the next pair
                if re.match(r'^(?:\d+\.?\s*)?(?:-\s*)?(?:FIND|REPLACE)\s*:', stripped, re.IGNORECASE):
                    break
                accumulated += "\n" + stripped.rstrip()[:-1]
                return accumulated, j

            # Check if next line starts a new FIND/REPLACE pair
            if re.match(r'^(?:\d+\.?\s*)?(?:-\s*)?(?:FIND|REPLACE)\s*:', stripped, re.IGNORECASE):
                break

            accumulated += "\n" + stripped

        # If we exit the loop without finding a closing delimiter,
        # try stripping any trailing delimiter characters from what we have
        text = accumulated.rstrip()
        if text.endswith(close_delim):
            text = text[:-1]
        return text if text or allow_empty else None, j

    def apply_delta_changes(self, article: str, changes: list[dict]) -> str:
        """Apply FIND/REPLACE changes to an article.

        Returns the modified article. Changes are applied sequentially.
        """
        result = article
        applied = 0
        failed = 0

        for change in changes:
            find_text = change["find"]
            replace_text = change["replace"]

            if find_text in result:
                result = result.replace(find_text, replace_text, 1)
                applied += 1
            else:
                failed += 1
                self.progress(
                    f"  [yellow]Could not find text to replace "
                    f"({len(find_text)} chars): '{find_text[:60]}'[/yellow]"
                )

        self.progress(f"Applied {applied}/{len(changes)} changes ({failed} failed)")
        return result

    def log(self, message: str):
        """Timestamped logging. Works in both TTY and piped output."""
        ts = _timestamp()
        console.print(f"  [{ts}] [bold blue][{self.name}][/bold blue] {message}")
        # Also flush to ensure piped output is visible immediately
        sys.stdout.flush()

    def progress(self, message: str):
        """Timestamped progress logging."""
        ts = _timestamp()
        console.print(f"    [{ts}] [dim]{message}[/dim]")
        sys.stdout.flush()
