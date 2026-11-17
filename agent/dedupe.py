"""Finds posts that cover the same topic (duplicates) and picks the one to keep."""
from __future__ import annotations

import re

from .text import html_to_text, jaccard, shingles, tokens, words


PART = re.compile(r"\b(continued|part\s*(\d+|i+|two|three))\b", re.I)


def similarity(a: dict, b: dict) -> float:
    """Duplicate = same topic. Same title (or slug with a -2 suffix) is enough; a similar title needs mostly the same
    text too; a different title counts only when the text is nearly identical. Part 2 / "continued" posts are kept apart."""
    sa, sb = a.get("slug", ""), b.get("slug", "")
    if sa and sb and sa.rstrip("-0123456789") == sb.rstrip("-0123456789"):
        return 1.0
    ra, rb = a["title"]["raw"], b["title"]["raw"]
    title = jaccard(tokens(ra), tokens(rb))
    body = jaccard(a["_sh"], b["_sh"])
    if bool(PART.search(ra)) != bool(PART.search(rb)):
        return body if body >= 0.9 else 0.0
    if title >= 0.95:
        return 1.0
    return body if body >= 0.85 else 0.0  # a different or partly similar title: only near-identical text


def groups(posts: list[dict], threshold: float) -> list[list[dict]]:
    """Union-find clusters of posts whose similarity >= threshold."""
    for p in posts:
        if "_text" not in p:
            p["_text"] = html_to_text(p["content"]["raw"])
            p["_sh"] = shingles(p["_text"])
            p["_words"] = words(p["_text"])
    parent = list(range(len(posts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(posts)):
        for j in range(i + 1, len(posts)):
            if similarity(posts[i], posts[j]) >= threshold:
                parent[find(i)] = find(j)
    clusters: dict[int, list[dict]] = {}
    for i, p in enumerate(posts):
        clusters.setdefault(find(i), []).append(p)
    return [c for c in clusters.values() if len(c) > 1]


def pick_keeper(group: list[dict], priority: list[str]) -> dict:
    """Keep the one Google already knows (priority list / published), else the longest."""
    def score(p):
        in_prio = p["slug"] in priority
        return (p["status"] == "publish", in_prio, -priority.index(p["slug"]) if in_prio else 0, p["_words"])
    return max(group, key=score)
