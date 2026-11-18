"""Audit every post: duplicates and low-value content, with a delete / keep verdict.

Writes state/audit.md (readable) and state/audit.csv (sortable). Changes nothing in WordPress.
"""
from __future__ import annotations

import csv
import json
import logging
import re

from pydantic import BaseModel, Field

from . import gemini
from .config import PRIORITY_FILE, STATE_DIR, cfg
from .dedupe import groups, pick_keeper
from .text import html_to_text, words

log = logging.getLogger(__name__)

PROTECT_IMPRESSIONS = 20  # posts Google already shows this often are never suggested for deletion


class Verdict(BaseModel):
    id: int
    verdict: str = Field(description="'delete' or 'keep'")
    reason: str = Field(description="One short sentence")


class Verdicts(BaseModel):
    items: list[Verdict]


JUDGE = """You audit the blog of {site}, written for {audience}.
For each post decide 'delete' or 'keep'.

delete only if at least one is true:
- off-topic: not about Power Platform, Dynamics 365, Dataverse, Microsoft 365 development or closely related developer work
- a test, placeholder or nearly empty post with no clear topic
- a dated news item or announcement with no lasting how-to value (e.g. "X is now available", event recap)
- about a retired product or feature with no current use (and not useful as a migration guide)

keep if the topic is useful for the audience, even if the post is short or badly written: a rewrite agent will expand it.
When unsure, keep. Answer for every id."""


def impressions() -> dict[str, int]:
    out = {}
    if PRIORITY_FILE.exists():
        for line in PRIORITY_FILE.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([^#\s]+)\s*#\s*(\d+) impressions", line)
            if m:
                out[m.group(1).rstrip("/").split("/")[-1]] = int(m.group(2))
    return out


def judge(posts: list[dict]) -> dict[int, Verdict]:
    s = cfg()["site"]
    system = JUDGE.format(site=s["name"], audience=s["audience"])
    out: dict[int, Verdict] = {}
    for i in range(0, len(posts), 40):
        batch = posts[i:i + 40]
        listing = "\n\n".join(
            f"id={p['id']} | {p['_words']} words | title: {p['title']['raw']}\nstart: {' '.join(p['_text'].split())[:350]}"
            for p in batch)
        try:
            res = gemini.structured(f"POSTS:\n\n{listing}", Verdicts, system=system, thinking="low")
            for v in res.items:
                out[v.id] = v
        except Exception as e:
            log.warning("AI review of batch %d failed (%s); those posts are judged by rules only", i // 40 + 1, e)
    return out


def run(wp) -> str:
    base = cfg()["site"]["url"]
    posts = wp.posts()
    for p in posts:
        p["_text"] = html_to_text(p["content"]["raw"])
        p["_words"] = words(p["_text"])
    imp = impressions()
    prio = list(imp)

    dup_of: dict[int, dict] = {}
    for g in groups(posts, cfg()["duplicates"]["similarity"]):
        keeper = pick_keeper(g, prio)
        for p in g:
            if p is not keeper:
                dup_of[p["id"]] = keeper

    verdicts = judge([p for p in posts if p["id"] not in dup_of and p["status"] != "trash"])

    rows = []
    for p in posts:
        hits = imp.get(p.get("slug", ""), 0)
        v = verdicts.get(p["id"])
        if p["id"] in dup_of:
            k = dup_of[p["id"]]
            verdict, reason = "delete", f"Duplicate of #{k['id']} \"{k['title']['raw']}\" (keep that one)"
        elif p["_words"] < 40 and not hits:
            verdict, reason = "delete", f"Almost empty ({p['_words']} words)"
        elif v and v.verdict.lower().startswith("del"):
            verdict, reason = "delete", v.reason
        else:
            verdict, reason = "keep", (v.reason if v else "Not reviewed by AI; kept")
        if verdict == "delete" and hits >= PROTECT_IMPRESSIONS and p["id"] not in dup_of:
            verdict, reason = "keep", f"Google shows it ({hits} impressions in 3 months); rewrite instead. AI said: {reason}"
        rows.append({
            "id": p["id"], "verdict": verdict, "status": p["status"], "title": p["title"]["raw"], "words": p["_words"],
            "impressions": hits, "reason": reason, "edit_url": f"{base}/wp-admin/post.php?post={p['id']}&action=edit",
            "url": p.get("link", ""),
        })

    rows.sort(key=lambda r: (r["verdict"] != "delete", "Duplicate" not in r["reason"], -r["impressions"], r["words"]))
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "audit.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    dels = [r for r in rows if r["verdict"] == "delete"]
    md = [f"# Blog audit — {len(posts)} posts", "",
          f"**Suggested for deletion: {len(dels)}** (duplicates: {sum('Duplicate' in r['reason'] for r in dels)}). "
          "Nothing was changed. Posts Google already shows are never on this list.", "",
          "| # | Status | Title | Words | Why | Edit |", "|---|---|---|---|---|---|"]
    for r in dels:
        t = r["title"].replace("|", "/")
        md.append(f"| {r['id']} | {r['status']} | [{t}]({r['url']}) | {r['words']} | {r['reason'].replace('|', '/')} | [edit]({r['edit_url']}) |")
    md += ["", f"Full list with every post's verdict: `state/audit.csv`."]
    text = "\n".join(md) + "\n"
    (STATE_DIR / "audit.md").write_text(text, encoding="utf-8")
    return text
