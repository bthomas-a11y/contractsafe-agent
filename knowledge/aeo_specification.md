# AEO Pass Agent: Research-Backed Specification for 2026

## Part 1: What the Research Actually Says Matters

Here's what I found across academic research (the Princeton GEO paper), practitioner experiments (HubSpot's 642% citation increase), and the 2026 AEO guides from Conductor, Evergreen Media, Search Engine Land, CXL, LLMrefs, AirOps, and others. I've organized these into tiers based on how much evidence supports each one.

---

### Tier 1: Proven by Research (GEO Paper + Practitioner Data)

**1. Statistics & Quantifiable Claims**
The Princeton GEO paper tested nine optimization methods. Statistics Addition produced a ~40% visibility boost and was the single highest-performing tactic alongside Quotation Addition. "Pricing varies" is invisible to AI; "$15-60/month" gets cited. Vague claims get skipped; specific numbers get extracted.

**2. Source Citation in Text (Not Just Links)**
Citing sources by name inline ("according to Gartner" not just a hyperlink) boosted visibility ~30% when combined with other methods. AI engines treat named source attribution as a credibility signal. Perplexity in particular weights this heavily because its entire UX is built around showing citations.

**3. Fluency + Readability**
Fluency Optimization produced a 15-30% visibility boost. The best-performing *combination* of any two tactics was Fluency Optimization + Statistics Addition. This means clean, readable prose with specific data beats everything else. Critically, keyword stuffing showed *no improvement or negative impact*.

**4. Passage-Level Extractability (Self-Contained Paragraphs)**
This is the mechanical backbone of AEO. AI engines don't read whole pages; they retrieve specific passages via RAG (Retrieval Augmented Generation). Google's patent for "Selecting Answer Spans" describes scoring specific text chunks within documents. Anthropic's Citations API maps citations to exact sentences. The practical implication: paragraphs that retain full meaning when extracted in isolation get cited. Paragraphs that rely on "this," "as mentioned above," or "they" to make sense get skipped.

**5. Answer Blocks (Front-Loaded Direct Answers)**
Multiple sources converge on this: place a concise, direct answer (40-60 words) within the first 2-5 sentences after each heading. Research from Veza Digital found 44% of AI citations come from the first 30% of a page's content. AirOps' analysis found that sections where the opening doesn't directly respond to the heading see a measurable drop in extraction confidence.

---

### Tier 2: Strong Practitioner Consensus (Multiple Expert Sources Agree)

**6. Semantic Triples (Subject-Predicate-Object)**
HubSpot's head of blog strategy reported that semantic triples were part of a strategy producing a 642% increase in AI citations. The framework: write at least one sentence per core concept in "Subject does/is Object" form. Example: "ContractSafe extracts key contract terms automatically." This helps AI engines build entity-to-concept associations in their knowledge graphs. The rule of thumb: one triple per core concept, not plastered everywhere (that reads like early SEO garbage).

**7. Entity Clarity & Consistent Terminology**
AI engines use entity recognition to understand what your content is *about*. Pick one term per concept and stick to it (don't rotate between "CLM," "contract management software," and "contract lifecycle platform" across the same article). Define key terms explicitly on first use. Spell out acronyms. Make the entity relationships unambiguous: "ContractSafe is a contract management software company" not just "ContractSafe" with context clues.

**8. Self-Describing Headings (Question-Based or Definitional H2s)**
AI engines may extract headings independently as labels for content chunks. Headings need to communicate complete context without surrounding text. "What Is Contract Lifecycle Management?" works. "The Big Picture" doesn't. Each H2 should function as a standalone label that tells the AI what answer lives beneath it.

**9. Structured Formats for Comparison/Process Content**
Tables, numbered steps, and bulleted lists create natural "passage boundaries" that citation systems can target without clipping mid-thought. The GEO paper's Quotation Addition method (which often introduced structured examples) was the highest single-method performer at +41%. For comparison content, tables outperform prose because they present unambiguous attribute-to-value relationships.

**10. Follow-Up Query Coverage ("Next Click" / Query Fan-Out)**
When a user asks ChatGPT a question, the system generates dozens of synthetic sub-queries behind the scenes (this is called "query fan-out"). Your content doesn't just need to answer the original question; it needs to rank for those hidden sub-queries too. Covering likely follow-up questions ("What does X cost?" → "How long does X take to implement?" → "What are alternatives to X?") increases the number of sub-queries your content matches.

---

### Tier 3: Emerging Best Practice (Expert Recommendation, Less Hard Data)

**11. Depth Architecture (Front-Loading Critical Content)**
Since 44% of citations come from the top 30% of page content, the most important information, definitions, key stats, and core answers should appear early. This doesn't mean burying depth; it means leading with substance rather than lengthy introductions.

**12. Unique/Proprietary Data**
AI engines cite content that provides "information gain" over competing sources. Original statistics, proprietary frameworks, unique benchmarks, or first-party research give AI a reason to cite you over the twelve other articles saying the same thing. If your article has nothing competitors don't also have, citation probability drops.

**13. Content Freshness Signals**
Perplexity reportedly weights freshness at ~40% of ranking influence. Current dates, recent data, and "updated for [year]" signals matter. Stale content (3+ years without updates) gets deprioritized.

**14. Cross-Platform Entity Consistency**
AI engines check multiple sources before confidently citing a brand. If your About page says one thing, your product page says another, and your LinkedIn says a third thing, the AI loses confidence. Consistent entity facts across your web presence strengthen citation probability.

**15. No Authoritative/Persuasive Tone Optimization**
This is a NEGATIVE finding from the GEO paper: making content more "persuasive and authoritative" in tone showed NO significant improvement. AI engines are already somewhat robust to tonal manipulation. This is actually good news for ContractSafe's conversational voice, which prioritizes authenticity over persuasion.

---

### What Does NOT Work

- **Keyword stuffing:** Negative or zero impact in the GEO paper
- **Authoritative/persuasive tone:** No significant improvement
- **Vague marketing language:** "Streamline your workflow" is invisible to AI
- **Context-dependent references:** "This," "as mentioned above," "they" in key passages
- **Long introductions before answers:** AI skips ahead to find the answer or cites a competitor
- **Burying answers in deep paragraphs:** If the answer isn't near the heading, extraction confidence drops

---

## Part 2: The Tension with ContractSafe's Voice

Here's the hard part. Several AEO best practices appear to conflict with ContractSafe's meandering, conversational voice:

| AEO Says | ContractSafe Voice Says |
|---|---|
| Front-load answers immediately after headings | Meander, take the scenic route, don't rush |
| Write atomic 1-3 sentence paragraphs | Paragraphs under 42 words (similar, but voice sometimes needs room) |
| Use direct, declarative sentences | Use digressions, tangents, philosophical connections |
| Pick one term and never vary it | Natural conversation uses variety |

**The resolution:** These don't have to conflict if you think of them as operating at different scales.

- **At the section level:** Each H2 section can START with a 1-2 sentence direct answer (the "answer block") and then immediately meander into the extended, conversational explanation. The AI gets its extractable passage from sentence 1-2. The human reader gets the full ContractSafe experience from sentence 3 onward.
- **At the paragraph level:** KEY paragraphs (definitions, stats, product claims) should be self-contained and extractable. NARRATIVE paragraphs (stories, digressions, metaphors) don't need to be extractable because they serve the human reader, not the AI.
- **At the entity level:** Use consistent core terminology ("contract management software," "ContractSafe") while still varying the surrounding language naturally.

The mantra: **AEO is a layer, not a replacement.** You're adding extractable elements INTO the conversational voice, not replacing the voice with extractable content.

---

## Part 3: The 11 AEO Checks

### CHECK 1: Answer Blocks (Critical)
**Pass:** Every H2 section's first 1-2 sentences contain a clear, direct, factual answer (20-50 words).
**Fail action:** Add 1-2 sentence direct answer BEFORE existing conversational opening. Must read as natural flow, not bolted-on summary. No "Quick Answer:" labels.

### CHECK 2: Semantic Triples (High)
**Pass:** At least one subject-predicate-object statement per core concept. Brand entity has 2-3 triples (what it IS, DOES, FOR).
**Fail action:** Weave triples into existing sentences. One per concept, not plastered everywhere.

### CHECK 3: Passage Extractability (Critical)
**Pass:** Key paragraphs (definitions, stats, claims) make sense when extracted in isolation. No "this approach," "as mentioned above," "they" in critical passages.
**Fail action:** Rewrite context-dependent key passages to be self-contained. Narrative paragraphs don't need fixing.

### CHECK 4: Quantifiable Claims (Critical)
**Pass:** Every major claim backed by specific number. At least 3 data points per 1,000 words. Each stat names its source. No unsupported superlatives.
**Fail action:** Replace vague claims with specific ones. Find numbers or qualify honestly.

### CHECK 5: Source Attribution Inline (Critical)
**Pass:** Every statistic names its source in text ("according to Gartner"). Sources are credible and near the claim.
**Fail action:** Add source attribution inline next to claims.

### CHECK 6: Entity Consistency (Medium)
**Pass:** One primary term per concept throughout. ContractSafe identified in first 200 words. Acronyms spelled out.
**Fail action:** Standardize terminology in key passages.

### CHECK 7: Self-Describing Headings (Medium)
**Pass:** Every H2 communicates complete context when read alone. No "The Big Picture" or "Why It Matters" without specifics.
**Fail action:** Rewrite vague headings to be specific while keeping conversational voice.

### CHECK 8: Follow-Up Query Coverage (Medium)
**Pass:** Article addresses 3-5 PAA questions from keyword research. Integrated naturally, not bolted-on FAQ.
**Fail action:** Weave answers to PAA gaps into existing sections.

### CHECK 9: Structured Formats (Medium)
**Pass:** Comparisons use tables. Processes use numbered steps. Tables/lists include enough context for extraction.
**Fail action:** Convert comparison prose to tables, process prose to numbered steps.

### CHECK 10: Unique Value (Strategic)
**Pass:** At least one unique element (proprietary stat, original framework, first-party case study). Clearly attributed and in extractable passage.
**Fail action:** FLAG for user as strategic gap. Suggest ContractSafe-specific data.

### CHECK 11: Freshness (Lower)
**Pass:** Current/recent year reference. Statistics use most recent data. No vague "recently" without dates.
**Fail action:** Update stale references. Add specific recent data point.

---

## Part 4: Golden Rules

1. **VOICE ALWAYS WINS.** If AEO makes a sentence sound corporate/robotic, rewrite until both AEO-optimized AND conversational.
2. **AEO IS A LAYER, NOT A REPLACEMENT.** Adding extractable elements into conversational content.
3. **NOT EVERY PARAGRAPH NEEDS TO BE EXTRACTABLE.** Focus on key paragraphs (definitions, stats, claims).
4. **NO REDUNDANCY.** Don't add duplicate answer blocks restating what's already there.
5. **KEYWORD STUFFING DOESN'T WORK.** GEO research proved it. Natural language wins.
6. **THE BEST AEO CONTENT IS ALSO THE BEST HUMAN CONTENT.** If AEO makes the article worse for humans, it's wrong.

---

## Part 5: Priority Ranking

| Priority | Check | Impact | Evidence |
|----------|-------|--------|----------|
| 1 | Answer Blocks | Critical | 44% of citations from top 30%; multiple sources |
| 2 | Quantifiable Claims | Critical | GEO: ~40% visibility boost |
| 3 | Source Attribution | Critical | GEO: ~30% boost; Perplexity weighting |
| 4 | Passage Extractability | Critical | RAG architecture; Google/Anthropic patents |
| 5 | Semantic Triples | High | HubSpot 642% increase |
| 6 | Fluency + Readability | High | GEO: 15-30% boost |
| 7 | Self-Describing Headings | High | AirOps, LLMrefs |
| 8 | Entity Consistency | Medium | HubSpot; entity recognition |
| 9 | Follow-Up Query Coverage | Medium | Query fan-out patents |
| 10 | Structured Formats | Medium | GEO: Quotation Addition +41% |
| 11 | Unique Value | Strategic | Citation economy |
| 12 | Freshness | Lower | Perplexity ~40% freshness |
