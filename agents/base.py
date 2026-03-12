"""Base agent class that all agents inherit from. Uses claude CLI for LLM calls."""

import subprocess
import time
import json
from rich.console import Console
from state import PipelineState
from config import CLAUDE_CLI, CLAUDE_TIMEOUT, MAX_RETRIES, RETRY_BASE_DELAY

console = Console()


class BaseAgent:
    """Base class for all pipeline agents."""

    name: str = "BaseAgent"
    description: str = ""
    agent_number: int = 0
    model: str = "sonnet"
    emoji: str = ""

    def run(self, state: PipelineState) -> PipelineState:
        """Override in each agent. Takes state, returns modified state."""
        raise NotImplementedError

    def call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """
        Call Claude via the claude CLI.

        Uses the --print flag for non-interactive output and --system-prompt
        for the system prompt.
        """
        for attempt in range(MAX_RETRIES):
            try:
                cmd = [CLAUDE_CLI, "-p", "--model", self.model]

                # Build the full prompt with system instructions embedded
                full_prompt = f"""<system-instructions>
{system_prompt}
</system-instructions>

{user_prompt}"""

                result = subprocess.run(
                    cmd,
                    input=full_prompt,
                    capture_output=True,
                    text=True,
                    timeout=CLAUDE_TIMEOUT,
                )

                if result.returncode != 0:
                    error_msg = result.stderr.strip() or "Unknown error"
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_BASE_DELAY * (2 ** attempt)
                        self.log(f"[yellow]CLI error (attempt {attempt + 1}): {error_msg}. Retrying in {delay}s...[/yellow]")
                        time.sleep(delay)
                        continue
                    raise RuntimeError(f"claude CLI failed after {MAX_RETRIES} attempts: {error_msg}")

                return result.stdout.strip()

            except subprocess.TimeoutExpired:
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    self.log(f"[yellow]Timeout (attempt {attempt + 1}). Retrying in {delay}s...[/yellow]")
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"claude CLI timed out after {CLAUDE_TIMEOUT}s on all {MAX_RETRIES} attempts")

            except FileNotFoundError:
                raise RuntimeError(
                    "claude CLI not found. Make sure 'claude' is installed and in your PATH. "
                    "Install it with: npm install -g @anthropic-ai/claude-code"
                )

        return ""

    def call_llm_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Call Claude and parse the response as JSON."""
        json_system = system_prompt + "\n\nIMPORTANT: Respond with valid JSON only. No markdown fencing, no commentary."
        response = self.call_llm(json_system, user_prompt)

        # Strip markdown code fences if present
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (fences)
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

    def log(self, message: str):
        """Rich-formatted logging."""
        console.print(f"  [bold blue][{self.name}][/bold blue] {message}")

    def progress(self, message: str):
        """Log a progress step within the agent."""
        console.print(f"    [dim]{message}[/dim]")
