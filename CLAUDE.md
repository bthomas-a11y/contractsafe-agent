# ContractSafe Content Agent System

## How to Think (Read This Before Every Action)

You are a deadline-driven, outcome-oriented software engineer. The user is your client.
They will walk away and expect finished work when they return. Act accordingly.

### The Pipeline is a Product

The pipeline takes a topic and produces a finished article. It should work like a
compiler: deterministic, reliable, one-shot. If it fails, the pipeline has a bug.
Fix the bug. Do not re-run and hope.

Every validation check must be GUARANTEED by the pipeline's internal logic.
If the validator requires ≥5 internal links, the SEO pass must make <5 structurally
impossible. If it requires ≤160 char meta descriptions, the final validator must
trim deterministically. "Sometimes it passes" means "it has a bug."

The only acceptable reason to run the pipeline more than once per topic is if you
changed the pipeline code to fix a structural bug. Even then: fix the root cause
so the failure CANNOT recur, then run once to confirm. Not twice. Once.

### Before Every Action

1. **What is the specific, measurable goal?** Not "improve." A concrete outcome.
2. **Is the goal already met?** If the pipeline passed: stop changing code. Verify
   the actual output (read article.md, check article.docx renders correctly).
   Only then is it done.
3. **Will this change break something that already works?** Trace every downstream
   effect. If you can't confidently say "no," don't make the change.
4. **How much wall-clock time have I spent?** >20 min on iteration = wrong approach.
   Stop. Rethink.
5. **Am I fixing the actual failure, or a different problem I noticed?** Stay on task.
6. **What happens if I'm wrong?** If this change introduces a new failure, can I
   revert? If not, I'm gambling with the user's time.

### Death Spiral Detection

Making a change → re-running → NEW failure → making another change → re-running →
ANOTHER new failure. This is the death spiral. If you're in it:
STOP. Revert to last known-good state. Report to user. Do not continue.

## Default Behavior

When the user provides a topic (and optionally a content type, keyword, or other parameters), **immediately run the pipeline**:

```bash
python3 main.py --type blog --topic "<topic>" --auto-approve
```

Do not ask clarifying questions unless the user's intent is genuinely ambiguous. Run first, discuss after.

If the user provides additional flags (e.g., `--keyword`, `--word-count`, `--secondary-keywords`), include them. If they don't specify `--type`, default to `blog`.

Monitor the run in real-time. Check output every 60-90 seconds. If any agent times out or errors, stop and fix the issue before re-running.

## Architecture

13-agent sequential pipeline: Research (1-5) → Brief (6) → Write (7) → Edit (8-11) → Social (12) → Validate (13).

- **Agents 1-3** run in parallel (no dependencies on each other)
- **Agent 4** runs after Agent 3 (depends on `competitor_pages`)
- **Agent 5** runs after Agents 3+4 (depends on `keyword_data` + `recommended_h2s`)
- **Agents 6-9** run sequentially
- **Agents 10-11** run sequentially (AEO must operate on SEO-passed article)
- **Agents 12-13** run sequentially

Key models:
- Opus: Agent 7 (writer), Agent 8 (brand voice)
- Haiku: Agent 12 (social copy)

Delta mode (FIND/REPLACE pairs instead of full article): Agent 8.
Fully programmatic (no Claude call): Agents 1, 2, 3, 4, 5, 6, 9, 10, 11, 13.
LLM-calling agents: 7 (Writer/Opus), 8 (Brand Voice/Opus), 12 (Social Copy/Haiku).

## Critical Vigilance Rules

These rules exist because agents can silently fail — producing no output, making no changes, or timing out — and the pipeline will continue as if everything worked. **This is the most dangerous failure mode.**

### 1. Every agent must produce output

After any code change, verify that each agent actually writes to its state field:
- Agent 7 → `state.draft_article` (must be 200+ words)
- Agent 8 → `state.voice_pass_article`
- Agent 9 → `state.fact_check_article`
- Agent 10 → `state.seo_pass_article`
- Agent 11 → `state.aeo_pass_article`
- Agent 13 → `state.final_article`

Post-condition assertions in `main.py:assert_post_conditions()` enforce this. If an agent produces empty output, the assertion falls back to the previous article and logs a warning. Agent 7 is a hard failure (raises RuntimeError).

### 2. Delta mode agents must actually apply changes

When Agent 8 finds issues in its programmatic audit but the delta parser returns 0 changes, that means **the parser failed to parse Claude's response**, not that no changes were needed. The log line to watch for:

```
Warning: audit found issues but no changes parsed from response.
```

If you see this, the parser's format handling needs to be expanded. The shared parser in `agents/base.py:parse_delta_response()` handles many formats but Claude can always surprise you.

Note: Agents 10 (SEO) and 11 (AEO) are now fully programmatic — they do not call Claude at all. All fixes are applied in Python.

### 3. Timeouts are critical failures requiring immediate programmatic fixes

There are NO retries (`MAX_RETRIES=1`). If a call fails or times out, it raises immediately.

**A timeout mandates an immediate programmatic fix. The required response is:**
1. Investigate root cause — what specifically caused the timeout?
2. Find proof — measure prompt size, identify which part is too large
3. Implement a PROGRAMMATIC fix that makes this timeout impossible in the future

**What is NOT a fix:** adjusting prompt size and retrying. There is no proof it will work. The only valid fix is one that eliminates the possibility of the timeout recurring (e.g., replace the LLM call with Python code, split into smaller calls, or eliminate the call entirely).

Two budgets are enforced in `main.py`:
- **Pipeline budget (600s)**: Total time for all 13 agents.
- **Editing budget (180s)**: Time for agents 8-13 (editing an already-written article). These agents should take ~2 min total, not half the pipeline.

Each agent also gets adaptive timeouts: `min(agent.timeout, remaining_budget)`.

The heartbeat is vigilant, not patient. At 1/3 of the timeout it warns the call is probably too slow. At 2/3 it declares the call likely stuck and names the prompt size as the probable cause.

Current timeouts (enforced by `.claude/hooks/block_timeout_increase.sh`):
- Agent 7 (Writer, Opus): 300s — ceiling
- Agent 8 (Brand Voice): 120s
- Agent 10 (SEO Pass): fully programmatic, no LLM call
- Agent 11 (AEO Pass): fully programmatic, no LLM call
- Agent 12 (Social, Haiku): 90s

**NEVER increase timeouts or add retries.** A Claude Code hook blocks any edit that raises timeout values above the ceilings or sets `MAX_RETRIES` above 1.

### 4. The fact checker can destroy content

Agent 9 removes unverified statistics from the article. If the research agents (1-3) produced no data, the fact checker's reference corpus is empty, and it would mark ALL stats as unverified. A circuit breaker limits removals to 3, and an empty-corpus guard skips verification entirely, but watch the log for:

```
Warning: No reference data available. Skipping fact verification.
```

This means research failed silently.

### 5. Parallel agent state merging

Agents 1-3 run in parallel with deep-copied state. Their results are merged by field assignment in `main.py`. Each agent writes to different fields so there are no conflicts. But if a new agent is added to the parallel group, verify its output fields don't overlap with existing agents.

### 6. Resume logic

Resume uses `set(range(1,14)) - set(completed_agents)` to find the first non-completed agent. Individual agent checks use `if i not in state.completed_agents`. This prevents re-running completed agents on resume. Never use `max(completed_agents) + 1` — it skips agents when one fails during parallel execution.

## Testing Changes

After modifying any agent:

1. `python3 -m py_compile <file>` — catches syntax errors
2. `python3 -c "from agents.<module> import <Class>"` — catches import errors
3. Run the full pipeline with `--auto-approve` and watch the output
4. Check the timing summary at the end — no agent should take more than 180s except the writer (300s max)
5. Check the validation report — PASS means the article meets all quality gates

## File Map

- `main.py` — Pipeline orchestrator, parallel execution, user gates, timing
- `agents/base.py` — BaseAgent class, `call_llm()`, shared delta parser
- `agents/` — One file per agent (13 total)
- `prompts/templates.py` — System prompts for Claude-calling agents
- `state.py` — PipelineState dataclass (all agent inputs/outputs)
- `config.py` — API keys, model config, paths, timeouts
- `knowledge/` — Brand voice, style rules, North Star articles
- `tools/` — Web search, web fetch, DOCX export, keyword research
- `link_policy.py` — Competitor blocklist, source tier classification

## Remember to always check tool output
