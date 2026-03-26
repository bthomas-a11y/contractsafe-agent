"""Link policy configuration and enforcement for ContractSafe content.

All link rules are enforced here in Python, not just in LLM prompts.
"""

from __future__ import annotations

# ── Minimums ──
MIN_INTERNAL_LINKS = 5
MIN_EXTERNAL_LINKS = 3

# ── Placement ──
# Target: most links in the first third of the article
FRONT_LOAD_TARGET = 0.6  # 60% of links should be in the first 1/3 of word count

# ── Blocked Competitor Domains ──
# These domains will NEVER appear in external links.
BLOCKED_DOMAINS = [
    # Direct CLM competitors
    "agiloft.com",
    "contractbook.com",
    "contractworks.com",
    "cobblestone.com",
    "cobblestonesoftware.com",
    "concord.com",
    "concordnow.com",
    "contractzen.com",
    "docusign.com",
    "gatekeeper.com",
    "gatekeeperhq.com",
    "ironclad.com",
    "ironcladapp.com",
    "juro.com",
    "linksquares.com",
    "nomio.com",
    "outlaw.com",
    "filevine.com",
    "spotdraft.com",
    "evisort.com",
    "icertis.com",
    "conga.com",
    "agiloft.com",
    "pandadoc.com",
    "proposify.com",
    "precisely.com",
    "concord.app",
    "contractlogix.com",
    "docjuris.com",
    "lexion.ai",
    "malbek.io",
    "contractpodai.com",
    "contracts365.com",
    "hyperstart.com",
    "mydock365.com",
    "debtbook.com",
    "contracthound.com",
    "volody.com",
    "sirion.ai",
    "sirionlabs.com",
    "zefort.com",
    "streamline.ai",
    "dealsign.ai",
    "salesforce.com",
    "agiloft.com",
    "softwaresuggest.com",
    "sourceforge.net",
    # CRM/donor platforms (not CLM — irrelevant as external link targets)
    "goodera.com",
    "kindsight.io",
    "bloomerang.com",
    "doublethedonation.com",
    "wildapricot.com",
    # Generic competitors
    "g2.com",
    "capterra.com",
    "softwareadvice.com",
    "getapp.com",
    "trustradius.com",
]

# ── Approved External Source Tiers ──
# Tier 1: Always preferred. Industry-standard authoritative sources.
TIER_1_DOMAINS = [
    # Legal industry
    "americanbar.org",
    "law.com",
    "reuters.com",
    "bloomberglaw.com",
    "artificiallawyer.com",
    "legaltechlever.com",
    "acc.com",  # Association of Corporate Counsel
    # Research / consulting
    "gartner.com",
    "forrester.com",
    "mckinsey.com",
    "deloitte.com",
    "pwc.com",
    "ey.com",
    "kpmg.com",
    "bain.com",
    "bcg.com",
    "hbr.org",
    "iaccm.com",
    "worldcc.com",  # World Commerce & Contracting (formerly IACCM)
    # Government / regulatory
    "gov",  # matches any .gov domain
    "sec.gov",
    "ftc.gov",
    "congress.gov",
    "europa.eu",
    # Academic / research
    "edu",  # matches any .edu domain
    "arxiv.org",
    "ssrn.com",
    "nist.gov",
    # Major business press
    "forbes.com",
    "wsj.com",
    "nytimes.com",
    "ft.com",
    "economist.com",
    "businessinsider.com",
    "techcrunch.com",
    "wired.com",
    "zdnet.com",
]

# Tier 2: Acceptable. Reputable publications and industry orgs.
TIER_2_DOMAINS = [
    "medium.com",
    "linkedin.com",
    "statista.com",
    "ibm.com",
    "microsoft.com",
    "salesforce.com",
    "hubspot.com",
    "investopedia.com",
    "wikipedia.org",
    "shrm.org",
    "abajournal.com",
    "natlawreview.com",
    "law360.com",
    "lexisnexis.com",
    "thomsonreuters.com",
    "wolterskluwer.com",
    "contractnerds.com",
]


# ── Evergreen External Links ──
# Pre-verified, stable URLs from authoritative sources. Used as a fallback when
# web search returns fewer than MIN_EXTERNAL_LINKS usable results. These must be
# pages that (a) will stay live for years, (b) are broadly relevant to contract
# management / legal ops, and (c) are from .gov, .edu, or major industry orgs.
EVERGREEN_EXTERNAL_LINKS = [
    {
        "url": "https://www.nist.gov/system/files/documents/2017/06/19/ContractManagementBody.pdf",
        "title": "NIST Contract Management Body of Knowledge",
        "anchor": "federal contract management standards",
        "tier": 1,
    },
    {
        "url": "https://www.gao.gov/contracting",
        "title": "GAO Contracting and National Security Acquisitions",
        "anchor": "government contracting oversight",
        "tier": 1,
    },
    {
        "url": "https://www.law.cornell.edu/ucc/article2",
        "title": "Cornell Law — UCC Article 2: Sales",
        "anchor": "Uniform Commercial Code contract law",
        "tier": 1,
    },
    {
        "url": "https://www.worldcc.com/Resources",
        "title": "World Commerce & Contracting Resources",
        "anchor": "contract management best practices",
        "tier": 1,
    },
    {
        "url": "https://hbr.org/2019/01/getting-the-most-out-of-your-contract-negotiations",
        "title": "HBR — Getting the Most Out of Contract Negotiations",
        "anchor": "contract negotiation strategies",
        "tier": 1,
    },
    {
        "url": "https://www.americanbar.org/groups/business_law/resources/business-law-today/",
        "title": "ABA Business Law Today",
        "anchor": "legal industry business law resources",
        "tier": 1,
    },
]

# Evergreen internal links — used when search fails to find ContractSafe pages
EVERGREEN_INTERNAL_LINKS = [
    {
        "url": "https://www.contractsafe.com/contract-management",
        "title": "Contract Management Software",
        "anchor": "contract management",
        "keywords": ["contract", "management", "software", "platform"],
    },
    {
        "url": "https://www.contractsafe.com/blog/contract-lifecycle-management-challenges",
        "title": "Contract Lifecycle Management Challenges",
        "anchor": "contract lifecycle management",
        "keywords": ["lifecycle", "clm", "challenges", "process"],
    },
    {
        "url": "https://www.contractsafe.com/blog/best-contract-lifecycle-management-software-features-2026",
        "title": "Best CLM Software Features",
        "anchor": "contract lifecycle management software",
        "keywords": ["features", "software", "clm", "lifecycle"],
    },
    {
        "url": "https://www.contractsafe.com/blog/clm-vs-contract-management-solutions",
        "title": "CLM vs Contract Management Solutions",
        "anchor": "CLM vs contract management solutions",
        "keywords": ["clm", "solutions", "comparison", "difference"],
    },
    {
        "url": "https://www.contractsafe.com/blog/contract-renewal-strategy",
        "title": "Contract Renewal Strategy",
        "anchor": "contract renewal",
        "keywords": ["renewal", "strategy", "expiration", "deadline"],
    },
    {
        "url": "https://www.contractsafe.com/blog/best-practices",
        "title": "Contract Management Best Practices",
        "anchor": "contract management best practices",
        "keywords": ["best", "practices", "compliance", "organize"],
    },
    {
        "url": "https://www.contractsafe.com/blog/tag/security-and-compliance",
        "title": "Security and Compliance",
        "anchor": "contract security and compliance",
        "keywords": ["security", "compliance", "audit", "risk"],
    },
    {
        "url": "https://www.contractsafe.com/blog/finance-contract-management",
        "title": "Finance Contract Management",
        "anchor": "finance contract management",
        "keywords": ["finance", "procurement", "cost", "savings", "spend"],
    },
]


def is_blocked(url: str) -> bool:
    """Check if a URL belongs to a blocked competitor domain."""
    url_lower = url.lower()
    for domain in BLOCKED_DOMAINS:
        if domain in url_lower:
            return True
    return False


def get_source_tier(url: str) -> int:
    """
    Get the source tier for a URL.

    Returns 1 (best), 2 (acceptable), or 3 (unvetted).
    """
    url_lower = url.lower()
    for domain in TIER_1_DOMAINS:
        if domain in url_lower:
            return 1
    for domain in TIER_2_DOMAINS:
        if domain in url_lower:
            return 2
    return 3


def is_internal(url: str) -> bool:
    """Check if a URL is a ContractSafe internal link."""
    return "contractsafe.com" in url.lower()


def format_link_policy_for_prompt() -> str:
    """Format the link policy as text to inject into LLM prompts."""
    blocked = ", ".join(BLOCKED_DOMAINS[:15]) + "..."
    tier1 = ", ".join(TIER_1_DOMAINS[:10]) + "..."
    return f"""## LINK POLICY (MANDATORY)

### Minimums
- **{MIN_INTERNAL_LINKS} internal links** (contractsafe.com) minimum per article
- **{MIN_EXTERNAL_LINKS} external links** minimum per article
- All links MUST be verified as live (HTTP 200). No 404s.

### Placement
- **{int(FRONT_LOAD_TARGET * 100)}% of all links should appear in the first third** of the article
- Links must be spread across sections, not bunched in one paragraph
- Every link must use natural, organic anchor text from relevant keywords
- Never use "click here," "learn more," or "read more" as anchor text

### External Link Rules
- **BLOCKED DOMAINS (competitors, never link to these):** {blocked}
- **PREFERRED SOURCES (Tier 1):** {tier1}
- External sources must be neutral, authoritative, and non-competitive
- Every external link must be relevant to the specific claim or section it appears in
- The agent must READ the linked page to verify relevance, not just check metadata

### Internal Link Rules
- Internal links must point to real ContractSafe pages (verified live)
- Anchor text should use natural keywords relevant to the target page
- Spread across the article with emphasis on early placement"""
