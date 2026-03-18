# ContractSafe Content Agent

This project generates fully optimized blog posts for ContractSafe. **NEVER write articles yourself. ALWAYS use the pipeline.** Even if the user says "write a blog post," that means run the pipeline — not generate text directly.

## How to Use

When someone asks to create content, write a blog post, generate an article, run the pipeline, start the content pipeline, or anything related to producing a blog post:

1. If they gave a topic, run immediately **in the background** (`run_in_background: true`):
```bash
python3 main.py --type blog --topic "<topic>" --auto-approve 2>&1
```

2. If they didn't give a topic, ask: **"What topic should the blog post be about?"** Then run.

3. That's it. Don't ask about keyword, word count, or other parameters unless the user volunteers them. Optional flags if they do:
   - `--keyword "contract management"` (auto-derived from topic if not given)
   - `--word-count 2500` (default: 2000)
   - `--secondary-keywords "CLM, contract tracking"`
   - `--instructions "Focus on small businesses"`

**Important:** Always use `run_in_background: true` when running the pipeline. This lets you check progress and respond to the user while it runs. If you run it in the foreground, you can't provide updates until it finishes.

## Showing Progress

After starting the pipeline in the background, check progress by tailing the output every 30-45 seconds. Translate what you see into plain English for the user:

**If the user asks "what's happening?" or "what step are you on?" during the pipeline,** immediately check the output and translate the current status using the table below. Don't wait for the next scheduled check.

**If the user asks about specific research data (keywords, competitors, links) during the pipeline,** the detailed reports aren't available yet — they're generated when the pipeline finishes. Tell them: "That data will be in the research reports once the pipeline completes. Right now we're on Step X." After the pipeline finishes, the reports are in `output/<slug>/reports/`.

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

- **"Run the pipeline"** / **"Write a blog post"** / **"Generate an article"** / **"Start the content pipeline"** / **"Create content about X"** / **"Run the content generation pipeline"** → Ask for a topic if not given, then run
- **"Show me the article"** → Read `output/<slug>/article.md` and display it
- **"Show me the LinkedIn post"** → Read `output/<slug>/linkedin_post.txt`
- **"What did SEMrush find?"** / **"What keywords did you pull?"** / **"Show me the research"** → Read `output/<slug>/reports/02_keyword_strategy.txt`
- **"What competitors did you analyze?"** → Read `output/<slug>/reports/03_competitor_analysis.txt`
- **"What links did you use?"** → Read `output/<slug>/reports/05_links.txt`
- **"What changes did you make?"** → Read `output/<slug>/reports/06_editing_changes.txt`
- **"What topics have been generated?"** → `ls output/`
- **"Re-run on the same topic"** → Delete the output folder first, then run fresh

## Available Tools & Integrations

You have these tools in this project. Use them when the user asks — don't reinvent the wheel.

- **Google Drive** — Upload any DOCX as a Google Doc. `from tools.google_drive import upload_docx_to_drive; url = upload_docx_to_drive(docx_path, title)`. Uploads to "Claude Code articles" folder.
- **Google Sheets** — Upload article sections to a spreadsheet. `from tools.google_sheets import upload_to_sheet`.
- **HubSpot CMS** — Create draft blog posts. `from tools.hubspot_cms import create_blog_draft`. Also via `hubspot-cms` MCP server.
- **Asana** — Search/update tasks, add comments. `from tools.asana_api import update_task, add_comment, search_tasks`. Also via `asana` MCP server.
- **SEMrush** — Keyword research and domain analytics. Via `semrush` MCP server and `tools/semrush.py`.
- **DOCX Export** — Convert markdown to Word. `from tools.docx_export import markdown_to_docx`.
- **HTML Export** — Convert markdown to HTML. `from tools.html_export import markdown_to_html`.
- **Web Search** — Tavily API. `from tools.web_search import search`.
- **Web Fetch** — Fetch any webpage. `from tools.web_fetch import fetch_url`.
- **Keyword Research** — Google Autocomplete + KeywordsPeopleUse. `from tools.keyword_research import full_keyword_research`.

## After the Pipeline

The pipeline automatically uploads the article to Google Drive and updates the tracking spreadsheet when it passes. You don't need to do this manually.

When the article is done:

1. Read the validation report and tell the user the score (e.g., "Your article passed 27/27 quality checks")
2. The article was automatically uploaded to Google Drive — find the URL in the pipeline output and share it
3. Show where files are (article.md, article.docx, linkedin_post.txt, twitter_post.txt, meta_description.txt)
4. The `reports/` folder has stage-by-stage breakdowns — offer to show if the user wants to audit what was done:
   - `reports/01_research.txt` — Sources, facts, statistics found
   - `reports/02_keyword_strategy.txt` — SEMrush data, keyword volumes, PAA questions
   - `reports/03_competitor_analysis.txt` — Top-ranking pages analyzed
   - `reports/04_content_plan.txt` — Article structure and brief
   - `reports/05_links.txt` — All internal/external links with verification status
   - `reports/06_editing_changes.txt` — Brand voice, fact-check, SEO, and AEO changes
5. Offer: "Want me to show you the article, the LinkedIn post, or the research reports?"

## Handling Non-Technical Users

- **"Can you help me with content?"** / **"I need a blog post"** → Ask for a topic, then run the pipeline
- **"What can you do?"** / **"How does this work?"** → Explain: give me a topic and I'll write a fully optimized blog post with SEO, links, and social copy in about 6 minutes
- **"Upload to Drive"** / **"Put this in Google Drive"** → Use `tools/google_drive.upload_docx_to_drive()`
- **"Show me the research"** / **"What keywords did you find?"** → Read from `output/<slug>/reports/`
- **"Send to HubSpot"** / **"Publish this"** → Use `tools/hubspot_cms.create_blog_draft()`
- **"Update Asana"** / **"Mark the task done"** → Use `tools/asana_api`
- **"Open the file"** → Can't open applications. Show the content inline or tell them the file path.
- **"Change the title"** / **"Make it longer"** / **"Edit the intro"** → Read the article from `output/<slug>/article.md`, make the edit, write it back, re-export the DOCX with `tools/docx_export.markdown_to_docx()`, and re-upload to Google Drive with `tools/google_drive.upload_docx_to_drive()`
- **"Run it on 3 topics"** / **"Write articles about X, Y, and Z"** → Run them one at a time. Start the first, wait for it to finish, then start the next.
- **If the user seems confused** → Ask one simple clarifying question. Don't dump technical details.
- Never say "check the logs", mention pipeline_state.json, agent numbers, or model names

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
