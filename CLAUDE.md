# ContractSafe Content Agent System

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
- Sonnet: Agents 1-6, 9-11
- Haiku: Agent 12 (social copy)

Delta mode (FIND/REPLACE pairs instead of full article): Agents 8, 10, 11.
Fully programmatic (no Claude call): Agents 4, 5, 6, 9, 13.

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

When Agents 8, 10, or 11 find issues in their programmatic audit but the delta parser returns 0 changes, that means **the parser failed to parse Claude's response**, not that no changes were needed. The log line to watch for:

```
Warning: audit found issues but no changes parsed from response.
```

If you see this, the parser's format handling needs to be expanded. The shared parser in `agents/base.py:parse_delta_response()` handles many formats but Claude can always surprise you.

### 3. Timeouts are real failures

An agent that times out and retries is burning minutes. The current timeouts are:
- Opus agents (7, 8): 300s writer, 180s voice pass
- Sonnet agents (10, 11): 120s each
- Haiku agent (12): 60s

If an Opus call times out, the prompt is likely too large. Fix by reducing prompt size, not by increasing timeout. The 10-minute budget doesn't have room for 300s retries.

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
