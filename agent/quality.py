"""Checks an article before it is published. Returns a list of problems (empty = OK)."""
from __future__ import annotations

import re

from .config import cfg
from .schemas import Article
from .text import words

BANNED = [
    "as an ai", "language model", "in today's fast-paced", "let's dive in", "delve", "in conclusion,",
    "lorem ipsum", "[insert", "todo:", "tbd", "your-domain", "example.com/your",
]


def article_text(a: Article) -> str:
    out = [a.h1, *a.intro, *a.key_takeaways]
    for s in a.sections:
        out.append(s.heading)
        for b in s.blocks:
            out += [b.text or "", *(b.items or []), b.code or "", b.caption or ""]
            for st in b.steps or []:
                out += [st.title, st.detail, st.code or ""]
            for r in b.table_rows or []:
                out += r
    for f in a.faqs:
        out += [f.q, f.a]
    return "\n".join(out)


def _trim(text: str, limit: int) -> str:
    """Shorten at a sentence end if possible, else at a word boundary."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    dot = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if dot >= limit * 0.6:
        return cut[:dot + 1]
    return cut[:cut.rfind(" ")].rstrip(",;:-") + "."


def repair(a: Article, categories: list[str]) -> None:
    """Fix small, mechanical problems instead of rejecting a good article."""
    if len(a.meta_description) > 158:
        a.meta_description = _trim(a.meta_description, 155)
    if len(a.seo_title) > 65:
        a.seo_title = _trim(a.seo_title, 60).rstrip(".")
    if categories and a.category not in categories:
        low = {c.lower(): c for c in categories}
        a.category = low.get(a.category.lower(), a.category)


def check(a: Article, categories: list[str], site_url: str) -> list[str]:
    c = cfg()["article"]
    problems: list[str] = []
    if not a.keep:
        return []  # handled by the caller
    repair(a, categories)
    n = words(article_text(a))
    if n < c["min_words"]:
        problems.append(f"Too short: {n} words, need at least {c['min_words']}.")
    if n > c["max_words"] * 1.25:
        problems.append(f"Too long: {n} words, keep it under {c['max_words']}.")
    if len(a.sections) < c["min_sections"]:
        problems.append(f"Only {len(a.sections)} sections, need at least {c['min_sections']}.")
    if not any(b.type == "steps" and b.steps for s in a.sections for b in s.blocks):
        problems.append("No `steps` block: the how-to part must be numbered steps.")
    if not 30 <= len(a.seo_title) <= 65:
        problems.append(f"seo_title is {len(a.seo_title)} characters; make it 50-60.")
    if not 110 <= len(a.meta_description) <= 160:
        problems.append(f"meta_description is {len(a.meta_description)} characters; make it 130-155.")
    if len(a.faqs) < 3:
        problems.append("Add at least 3 FAQs.")
    if categories and a.category not in categories:
        problems.append(f"category '{a.category}' is not in the list; pick one exactly as written.")
    low = article_text(a).lower()
    for b in BANNED:
        if b in low:
            problems.append(f"Remove the phrase '{b}'.")
    host = re.sub(r"^https?://", "", site_url).split("/")[0]
    for url in re.findall(r"\]\((https?://[^)\s]+)\)", article_text(a)):
        if host in url and url not in a.internal_links_used:
            a.internal_links_used.append(url)
    return problems
