# ContractSafe Content Agent System

A 13-agent content production pipeline for ContractSafe. Takes a topic and content type as input, runs through research, writing, editing, SEO/AEO optimization, fact-checking, and validation to produce a publish-ready article with social copy.

## Prerequisites

- **Python 3.9+**
- **Claude CLI** (`claude`) installed and authenticated with a Claude Max plan
- **Tavily API key** (free tier: 1000 searches/month) — [sign up here](https://tavily.com)

## Setup

```bash
git clone <repo-url>
cd contractsafe-agent

# Install dependencies
pip install -r requirements.txt

# Configure API keys
cp .env.example .env
# Edit .env and add your Tavily API key (required)
# Optionally add KeywordsPeopleUse and SEMrush keys
```

## Usage

```bash
python main.py
```

The CLI will prompt for:
1. **Content type** — blog post, email, or webpage copy
2. **Topic** — what the article is about

Everything else is derived automatically. The pipeline runs all 13 agents sequentially with three review gates where you can provide feedback:
- **Brief review** (after research, before writing)
- **Draft review** (after first draft)
- **Final review** (after all optimization passes)

Press Enter at any gate to approve and continue, or type feedback to request changes.

## Pipeline

| # | Agent | What it does |
|---|-------|-------------|
| 1 | Product Knowledge | Identifies relevant ContractSafe features for the topic |
| 2 | Subject Researcher | Web research, statistics, narrative material |
| 3 | Competitor/KW Research | Competitor analysis, keyword expansion |
| 4 | SEO Researcher | SERP analysis, H2 structure, featured snippet opportunities |
| 5 | Link Researcher | Builds verified citation map (5+ internal, 3+ external links) |
| 6 | Brief Consolidator | Combines all research into a writer's brief |
| 7 | Content Writer | Writes the article with brand voice and extended metaphor |
| 8 | Brand Voice Pass | Catches and fixes voice violations |
| 9 | Fact Checker | Verifies every claim and statistic |
| 10 | SEO Pass | Keyword placement, heading structure, link distribution |
| 11 | AEO Pass | Answer engine optimization for AI search |
| 12 | Social Copy | Meta description, LinkedIn post, X/Twitter post |
| 13 | Final Validator | Comprehensive pass/fail checklist |

## Optional API Keys

Add these to your `.env` for enhanced capabilities:

- **`KEYWORDS_PEOPLE_USE_API_KEY`** — Adds People Also Ask and semantic keyword data
- **`SEMRUSH_API_KEY`** — Adds search volume, keyword difficulty, competitor keyword gap analysis

The system works without these (uses Google Autocomplete as a free fallback for keyword research).

## Project Structure

```
contractsafe-agent/
├── main.py                  # CLI orchestrator
├── config.py                # Configuration and API keys
├── state.py                 # PipelineState dataclass
├── link_policy.py           # Link rules, competitor blocklist, source tiers
├── requirements.txt
├── agents/
│   ├── base.py              # BaseAgent (calls claude CLI)
│   ├── knowledge_loader.py  # Force-loads North Star articles + brand files
│   ├── product_knowledge.py # Agent 1
│   ├── subject_researcher.py# Agent 2
│   ├── competitor_kw.py     # Agent 3
│   ├── seo_researcher.py    # Agent 4
│   ├── link_researcher.py   # Agent 5
│   ├── brief_consolidator.py# Agent 6
│   ├── content_writer.py    # Agent 7
│   ├── brand_voice_pass.py  # Agent 8
│   ├── fact_checker.py      # Agent 9
│   ├── seo_pass.py          # Agent 10
│   ├── aeo_pass.py          # Agent 11
│   ├── social_copy.py       # Agent 12
│   └── final_validator.py   # Agent 13
├── prompts/
│   └── templates.py         # All agent system prompts
├── tools/
│   ├── web_search.py        # Tavily API wrapper
│   ├── web_fetch.py         # HTTP fetch + HTML extraction
│   ├── keyword_research.py  # Google Autocomplete + KeywordsPeopleUse
│   └── semrush.py           # SEMrush API wrapper (optional)
├── knowledge/
│   ├── brand_voice.md       # Brand voice guidelines
│   ├── product_info.md      # Product knowledge base
│   ├── style_rules.md       # Mechanical style rules
│   └── north_star_articles/ # Reference articles for voice matching
└── output/                  # Generated articles saved here
```
