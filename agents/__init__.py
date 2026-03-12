from agents.product_knowledge import ProductKnowledgeAgent
from agents.subject_researcher import SubjectResearcherAgent
from agents.competitor_kw import CompetitorKWAgent
from agents.seo_researcher import SEOResearcherAgent
from agents.link_researcher import LinkResearcherAgent
from agents.brief_consolidator import BriefConsolidatorAgent
from agents.content_writer import ContentWriterAgent
from agents.brand_voice_pass import BrandVoicePassAgent
from agents.fact_checker import FactCheckerAgent
from agents.seo_pass import SEOPassAgent
from agents.aeo_pass import AEOPassAgent
from agents.social_copy import SocialCopyAgent
from agents.final_validator import FinalValidatorAgent

AGENT_PIPELINE = [
    ProductKnowledgeAgent,
    SubjectResearcherAgent,
    CompetitorKWAgent,
    SEOResearcherAgent,
    LinkResearcherAgent,
    BriefConsolidatorAgent,
    ContentWriterAgent,
    BrandVoicePassAgent,
    FactCheckerAgent,
    SEOPassAgent,
    AEOPassAgent,
    SocialCopyAgent,
    FinalValidatorAgent,
]

__all__ = ["AGENT_PIPELINE"]
