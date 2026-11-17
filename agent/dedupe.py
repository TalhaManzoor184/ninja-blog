"""Finds posts that cover the same topic (duplicates) and picks the one to keep."""
from __future__ import annotations

from .text import html_to_text, jaccard, shingles, tokens, words


def similarity(a: dict, b: dict) -> float:
    ta, tb = tokens(a["title"]["raw"]), tokens(b["title"]["raw"])
    title = jaccard(ta, tb)
    body = jaccard(a["_sh"], b["_sh"])
    # Same slug apart from a -2/-3 suffix is a duplicate for sure.
    sa, sb = a.get("slug", ""), b.get("slug", "")
    if sa and sb and (sa.rstrip("-0123456789") == sb.rstrip("-0123456789")):
        return 1.0
    return max(body, title if body > 0.2 or title >= 0.8 else title * 0.8)


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
