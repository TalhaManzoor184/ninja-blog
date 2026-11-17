"""Turns an old draft into a complete, current, step-by-step article with Gemini."""
from __future__ import annotations

import json
import logging

from . import gemini
from .config import cfg
from .schemas import Article, Research

log = logging.getLogger(__name__)

SYSTEM = """You are a senior Microsoft Power Platform and Dynamics 365 consultant who writes the blog of {site} for {audience}.
You rewrite old, thin or messy blog drafts into genuinely useful tutorials that a developer would bookmark.

Rules:
- Keep the original post's topic and intent. Keep every correct technical detail, code sample and example from it; fix what is outdated.
- Write from hands-on experience: concrete menu paths, exact property and function names, real error messages, gotchas and limits.
- Never invent product features, menu items, settings, numbers, prices or limits. If you are not sure something exists, leave it out.
- Use current Microsoft names (Microsoft Entra ID, Dataverse, Power Platform admin center, Copilot Studio, etc.).
- Every "how to" part must be a `steps` block: numbered steps, each one action, with code where it helps.
- Use plain, clear English. Short sentences. No filler, no hype, no "In today's fast-paced world", no "Let's dive in", no "As an AI".
- Do not mention that the post was rewritten or generated.
- Code must be correct and complete enough to copy. Use the right language tag.
- Images: never ask for screenshots or anything with readable text, logos or brand marks. Conceptual illustrations only.
- Link to other pages of this site only from the list you are given, and only where it truly helps the reader. Use markdown links [label](url).
- Output must match the JSON schema exactly."""


def _system() -> str:
    s = cfg()["site"]
    return SYSTEM.format(site=s["name"], audience=s["audience"])


def research(title: str, original: str) -> Research | None:
    """Check the topic against current Microsoft docs (Google Search grounding). Optional."""
    if not cfg()["run"].get("use_search") or not gemini.search_available():
        return None
    prompt = (
        f"Blog post title: {title}\n\nOriginal post (may be outdated):\n{original[:12000]}\n\n"
        "Using current official Microsoft documentation, list the current facts, product names and menu paths a "
        "tutorial on this topic needs, what in the original is outdated, and the questions people search for. "
        "Only include facts you could verify."
    )
    try:
        it = gemini._call(model=cfg()["models"]["text"], input=prompt + "\n\nReturn ONLY a JSON object matching this JSON Schema:\n"
                          + json.dumps(Research.model_json_schema()), tools=[{"type": "google_search"}],
                          system_instruction=_system(), **gemini._thinking("low"))
        return Research.model_validate_json(gemini._extract_json(gemini._output_text(it)))
    except Exception as e:  # research is a bonus; writing continues without it
        log.warning("Research step skipped: %s", e)
        return None


def write(title: str, original: str, *, categories: list[str], links: list[dict], related_text: str = "",
          research_notes: Research | None = None, feedback: str = "") -> Article:
    a = cfg()["article"]
    parts = [
        f"ORIGINAL TITLE: {title}",
        f"ORIGINAL POST:\n{original[:20000]}",
    ]
    if related_text:
        parts.append(f"OTHER DRAFTS ON THE SAME TOPIC (duplicates; merge anything useful from them):\n{related_text[:12000]}")
    if research_notes:
        parts.append("CURRENT FACTS FROM MICROSOFT DOCS:\n" + json.dumps(research_notes.model_dump(), indent=1)[:8000])
    parts.append("CATEGORIES (pick exactly one name):\n" + "\n".join(f"- {c}" for c in categories))
    parts.append("PAGES ON THIS SITE YOU MAY LINK TO:\n" + "\n".join(f"- {l['title']}: {l['url']}" for l in links[:80]))
    parts.append(
        f"Write the full article: {a['min_words']}-{a['max_words']} words in total, at least {a['min_sections']} sections. "
        "Include at least one `steps` block. "
        f"Include {a.get('diagrams', 0)} `diagram` block(s) where a flow or architecture helps (Mermaid flowchart, max 8 short nodes) "
        f"and {a.get('inline_ai_images', 0)} `image` block(s) with a conceptual illustration prompt. "
        "Add a `table` block if a comparison helps. End with FAQs that match real searches."
    )
    if feedback:
        parts.append(f"YOUR PREVIOUS ATTEMPT WAS REJECTED. Fix these problems:\n{feedback}")
    thinking = cfg()["models"].get("thinking_level")
    return gemini.structured("\n\n".join(parts), Article, system=_system(), thinking=thinking)
