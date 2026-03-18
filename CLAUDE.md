# ContractSafe Content Agent

This is a blog post generator for ContractSafe. Give it a topic and it produces a fully optimized article with social copy.

## How to Use

When someone asks to create content, write a blog post, or run the pipeline:

1. If they gave a topic, run immediately:
```bash
python3 main.py --type blog --topic "<topic>" --auto-approve 2>&1
```

2. If they didn't give a topic, ask: **"What topic should the blog post be about?"** Then run.

3. Optional parameters they might provide:
   - **Keyword**: `--keyword "contract management"` (auto-derived from topic if not given)
   - **Word count**: `--word-count 2500` (default: 2000)
   - **Secondary keywords**: `--secondary-keywords "CLM, contract tracking"`
   - **Extra instructions**: `--instructions "Focus on small businesses"`

## Showing Progress

Run the pipeline in the background (`run_in_background: true`) and check progress by tailing the output file every 30-45 seconds. Translate what you see into plain English for the user:

| What you see in the output | What to tell the user |
|---|---|
| `Searching:`, `Extracting statistics`, `Product Knowledge` | **"Step 1/6: Researching your topic..."** Pulling keyword data from SEMrush, analyzing the search landscape, gathering industry statistics, and reviewing what competitors have published on this topic. |
| `STARTING Agent 4` or `SEO Research` | **"Step 2/6: Building your content strategy..."** Analyzing the SERP (search engine results page), choosing the best keywords, identifying People Also Ask questions, and planning the article's section structure. |
| `STARTING Agent 5` or `Link Research` | **"Step 2/6 (continued): Finding sources and links..."** Searching for authoritative external sources (.gov, research papers, industry reports) and relevant ContractSafe pages to link to. Verifying each link is live and relevant. |
| `STARTING Agent 6` or `Brief` | **"Step 2/6 (continued): Finalizing the content brief..."** Combining all research into a structured brief with section outlines, keyword targets, and citation assignments. |
| `STARTING Agent 7` or `Content Writer` | **"Step 3/6: Writing your article..."** This is the main writing step — generating the full article section by section with ContractSafe's conversational voice. Takes 1-2 minutes. |
| `STARTING Agent 8` or `Brand Voice` | **"Step 4/6: Editing your article..."** Applying brand voice polish, fixing formatting (dashes, quotes, paragraph length), and cleaning up any rough edges from the writing step. |
| `STARTING Agent 9` or `Fact Check` | **"Step 4/6 (continued): Fact-checking..."** Cross-referencing every statistic and claim against the research data gathered in Step 1. Removing anything that can't be verified. |
| `STARTING Agent 10` or `SEO Pass` | **"Step 4/6 (continued): SEO optimization..."** Inserting internal links to ContractSafe pages, external links to authoritative sources, placing keywords naturally, and ensuring the meta description fits. |
| `STARTING Agent 11` or `AEO Pass` | **"Step 4/6 (continued): AI search optimization..."** Making sure the article is structured so AI assistants (like ChatGPT, Perplexity, Google AI) can extract and cite key passages. Adding answer blocks, data points, and source attributions. |
| `STARTING Agent 12` or `Social` | **"Step 5/6: Writing social media posts..."** Generating a LinkedIn post and a Twitter/X post promoting the article. |
| `STARTING Agent 13` or `Final Validator` | **"Step 6/6: Running quality checks..."** Scoring the article against 27 quality criteria — word count, link counts, keyword placement, formatting, SEO compliance, and AI-readiness. |
| `FINAL RESULT: PASS` | Pipeline succeeded — show the score and results |
| `FINAL RESULT: FAIL` | Pipeline finished but some checks didn't pass — show score and what failed |
| `PIPELINE HALTED` | Something broke — read the error and explain it in plain terms |

**Do not show raw agent numbers, model names, timeouts, or budget numbers to the user.** They don't need to know about Opus, Sonnet, or agent internals. Translate everything into the step numbers and descriptions above.

## Showing Results

When the pipeline finishes:

1. Read the validation report: `output/<topic-slug>/validation_report.txt`
2. Tell the user the score (e.g., "Your article passed 27/27 quality checks")
3. Show them where their files are:
   - **Article**: `output/<topic-slug>/article.md` and `article.docx`
   - **LinkedIn post**: `output/<topic-slug>/linkedin_post.txt`
   - **Twitter post**: `output/<topic-slug>/twitter_post.txt`
   - **Meta description**: `output/<topic-slug>/meta_description.txt`
4. Offer to show any of these — e.g., "Want me to show you the article, the LinkedIn post, or both?"

## Common Requests

- **"Run the pipeline"** / **"Write a blog post"** → Ask for a topic, then run
- **"Show me the article"** → Read `output/<slug>/article.md` and display it
- **"Show me the LinkedIn post"** → Read `output/<slug>/linkedin_post.txt`
- **"What topics have been generated?"** → `ls output/`
- **"Re-run on the same topic"** → Delete the output folder first, then run fresh

## If Something Goes Wrong

- If the pipeline errors, read the last 20 lines of output to diagnose
- The most common issue is a search API returning empty results — the pipeline handles this with fallbacks
- If an agent times out, the prompt is too large — this is a code issue, not a user issue. Don't ask the user to fix it.
- Never tell the user to "check the logs" or "look at pipeline_state.json" — diagnose it yourself

---

## Developer Reference

Everything below is for developers modifying the pipeline code, not for end users.

### Architecture

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

### Critical Vigilance Rules

These rules exist because agents can silently fail — producing no output, making no changes, or timing out — and the pipeline will continue as if everything worked.

#### 1. Every agent must produce output

After any code change, verify that each agent actually writes to its state field:
- Agent 7 → `state.draft_article` (must be 200+ words)
- Agent 8 → `state.voice_pass_article`
- Agent 9 → `state.fact_check_article`
- Agent 10 → `state.seo_pass_article`
- Agent 11 → `state.aeo_pass_article`
- Agent 13 → `state.final_article`

#### 2. Delta mode agents must actually apply changes

When Agents 8, 10, or 11 find issues in their programmatic audit but the delta parser returns 0 changes, that means the parser failed to parse Claude's response. Watch for:
```
Warning: audit found issues but no changes parsed from response.
```

#### 3. Timeouts are real failures

Current timeouts: Opus agents (7, 8): 300s writer, 180s voice pass. Sonnet agents (10, 11): 120s each. Haiku agent (12): 60s. Fix by reducing prompt size, not by increasing timeout.

#### 4. The fact checker can destroy content

Agent 9 removes unverified statistics. If research agents produced no data, the reference corpus is empty. A circuit breaker limits removals to 3, and an empty-corpus guard skips verification entirely.

#### 5. Parallel agent state merging

Agents 1-3 run in parallel with deep-copied state. Results merged by field assignment. Each agent writes to different fields — no conflicts.

#### 6. Resume logic

Resume uses `set(range(1,14)) - set(completed_agents)` to find the first non-completed agent. Never use `max(completed_agents) + 1`.

### Testing Changes

1. `python3 -m py_compile <file>` — catches syntax errors
2. `python3 -c "from agents.<module> import <Class>"` — catches import errors
3. Run the full pipeline with `--auto-approve` and watch the output
4. Check the validation report — PASS means the article meets all quality gates

### File Map

- `main.py` — Pipeline orchestrator, parallel execution, user gates, timing
- `agents/base.py` — BaseAgent class, `call_llm()`, shared delta parser
- `agents/` — One file per agent (13 total)
- `prompts/templates.py` — System prompts for Claude-calling agents
- `state.py` — PipelineState dataclass (all agent inputs/outputs)
- `config.py` — API keys, model config, paths, timeouts
- `knowledge/` — Brand voice, style rules, North Star articles
- `tools/` — Web search, web fetch, DOCX export, keyword research
- `link_policy.py` — Competitor blocklist, source tier classification
