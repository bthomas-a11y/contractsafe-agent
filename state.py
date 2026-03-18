"""Pipeline state management for the ContractSafe Content Agent System."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import json
from datetime import datetime
from pathlib import Path


@dataclass
class PipelineState:
    # --- User Inputs ---
    topic: str = ""
    content_type: str = ""  # "blog_post" | "email" | "webpage_copy"
    target_word_count: int = 0
    target_keyword: str = ""
    secondary_keywords: list[str] = field(default_factory=list)
    uploaded_article_path: Optional[str] = None
    uploaded_article_text: Optional[str] = None
    additional_instructions: str = ""

    # --- Agent 1: Product Knowledge ---
    product_knowledge: str = ""

    # --- Agent 2: Subject Research ---
    subject_research: str = ""
    key_facts: list[dict] = field(default_factory=list)
    statistics: list[dict] = field(default_factory=list)

    # --- Agent 3: Competitor/KW Research ---
    competitor_pages: list[dict] = field(default_factory=list)
    keyword_data: dict = field(default_factory=dict)

    # --- Agent 4: SEO Research ---
    seo_brief: str = ""
    serp_features: list[str] = field(default_factory=list)
    recommended_h2s: list[str] = field(default_factory=list)
    keyword_clusters: list[dict] = field(default_factory=list)  # SEMrush: groups of related keywords by intent
    keyword_gaps: list[dict] = field(default_factory=list)  # SEMrush: keywords competitors rank for that we don't

    # --- Agent 5: Link Research ---
    internal_links: list[dict] = field(default_factory=list)
    external_links: list[dict] = field(default_factory=list)
    citation_map: dict = field(default_factory=dict)

    # --- Agent 6: Brief ---
    consolidated_brief: str = ""

    # --- Agent 7: Content ---
    draft_article: str = ""
    extended_metaphor: str = ""
    metaphor_mapping: dict = field(default_factory=dict)

    # --- Agent 8: Brand Voice ---
    voice_pass_article: str = ""
    voice_issues_found: list[dict] = field(default_factory=list)

    # --- Agent 9: Fact Check ---
    fact_check_article: str = ""
    fact_check_results: list[dict] = field(default_factory=list)

    # --- Agent 10: SEO ---
    seo_pass_article: str = ""
    seo_changes: list[dict] = field(default_factory=list)

    # --- Agent 11: AEO ---
    aeo_pass_article: str = ""
    aeo_changes: list[dict] = field(default_factory=list)

    # --- Agent 12: Social Copy ---
    meta_description: str = ""
    linkedin_post: str = ""
    twitter_post: str = ""

    # --- Agent 13: Final Validation ---
    final_article: str = ""
    validation_report: str = ""
    pass_fail: bool = False

    # --- Metadata ---
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    current_agent: int = 0
    completed_agents: list[int] = field(default_factory=list)

    def save(self, path: str):
        """Save pipeline state to JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(self.__dict__, f, indent=2, default=str)

    @classmethod
    def load(cls, path: str) -> "PipelineState":
        """Load pipeline state from JSON file."""
        with open(path) as f:
            data = json.load(f)
        # Filter to only known fields
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    def get_topic_slug(self) -> str:
        """Generate a URL-friendly slug from the topic."""
        slug = self.topic.lower()
        slug = slug.replace(":", "").replace("?", "").replace("!", "")
        slug = "-".join(slug.split())
        return slug[:80]
