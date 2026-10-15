"""Rows of the research-only table in docs/PLATFORM-MATRIX.md, derived from the distribution registry (issues #13-#37)."""
from __future__ import annotations

RESEARCH_IDS = (
    "medium", "substack", "lobsters", "lemmy", "github_discussions", "huggingface", "quora", "stackexchange", "tumblr",
    "xing", "vk", "naver_blog", "naver_cafe", "kakao_openchat", "line_openchat", "beehiiv", "ghost", "vimeo", "twitch",
    "snapchat", "flipboard", "researchgate", "behance", "dribbble", "viber", "slack_communities")
HEADER = ("| Surface | Registry id | Category | ReachWeaver delivery | Human only | Native scheduling | Checked | Source "
          "| Not verified |")


def not_verified(platform: dict) -> str:
    scheduling = platform["scheduling"]
    if not scheduling.get("official_source") and not platform.get("research_source"):
        return "No official page could be read; no rule is claimed"
    if scheduling["mode"] == "unverified":
        return "Scheduling and automation limits not sourced"
    return "Per-account and plan limits not checked"


def row(platform: dict) -> str:
    source = platform.get("research_source") or platform["scheduling"].get("official_source") or ""
    link = f"[page]({source})" if source else "none"
    scheduling = platform["scheduling"]["mode"]
    return (f"| {platform['label']} | `{platform['id']}` | {platform['category']} | `{platform['reachweaver_delivery']}` "
            f"| {'yes' if platform['human_only'] else 'no'} | `{scheduling}` | {platform['checked_on']} | {link} "
            f"| {not_verified(platform)} |")
