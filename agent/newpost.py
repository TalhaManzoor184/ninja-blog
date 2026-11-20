"""New posts on current topics: Microsoft's own blogs (RSS) -> pick a fresh topic -> original step-by-step tutorial.

The announcement is only source material: the writer turns it into a practical how-to, never a copy.
Used announcements are remembered in state/new_posts.json so a topic is never written twice.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import json
import logging
import re
import xml.etree.ElementTree as ET

import requests
from pydantic import BaseModel, Field

from . import gemini
from .config import STATE_DIR, cfg
from .text import html_to_text, jaccard, tokens

log = logging.getLogger(__name__)
USED_FILE = STATE_DIR / "new_posts.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; PowerAppsNinja-BlogAgent/1.0)"}


class Pick(BaseModel):
    index: int = Field(description="index of the chosen announcement in the list, or -1 if none is a good tutorial topic")
    working_title: str = Field(description="How-to title for the tutorial, e.g. 'How to Use Server Logic in Power Pages (Step by Step)'")
    focus_keyword: str
    angle: str = Field(description="What the tutorial teaches and the example scenario it walks through")


PICK = """You plan new posts for the blog of {site} ({audience}).
From the Microsoft announcements below, choose ONE topic that makes the most useful, searchable how-to tutorial for
{topics}. Prefer features that are generally available or widely used, that people will search for, and that the
blog has not covered yet (existing titles are listed). Skip events, marketing, customer stories, pricing news and
anything a maker cannot try. Return index -1 if nothing fits."""


def _used() -> dict:
    if USED_FILE.exists():
        return json.loads(USED_FILE.read_text(encoding="utf-8"))
    return {"used": {}}


def _save_used(d: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    USED_FILE.write_text(json.dumps(d, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def feed_items(days: int) -> list[dict]:
    c = cfg()["new_posts"]
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    kw = re.compile("|".join(c["keywords"]), re.I)
    items, seen = [], set()
    for url in c["feeds"]:
        try:
            r = requests.get(url, headers=UA, timeout=40)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as e:
            log.warning("Feed skipped %s: %s", url, e)
            continue
        for it in root.iter("item"):
            link = (it.findtext("link") or "").strip()
            title = (it.findtext("title") or "").strip()
            if not link or link in seen:
                continue
            try:
                when = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "")
            except Exception:
                when = None
            if when and when < since:
                continue
            cats = " ".join((c_.text or "") for c_ in it.findall("category"))
            desc = html_to_text(it.findtext("description") or "")[:500]
            if not kw.search(f"{title} {cats} {desc}"):
                continue
            seen.add(link)
            items.append({"title": title, "link": link, "date": when.date().isoformat() if when else "",
                          "categories": cats, "summary": desc})
    return items


def article_text(url: str) -> str:
    try:
        r = requests.get(url, headers=UA, timeout=40)
        r.raise_for_status()
    except Exception as e:
        log.warning("Could not fetch %s: %s", url, e)
        return ""
    html = r.text
    m = re.search(r"<article\b.*?</article>", html, re.S | re.I) or re.search(r"<main\b.*?</main>", html, re.S | re.I)
    return html_to_text(m.group(0) if m else html)[:15000]


def choose(items: list[dict], existing_titles: list[str]) -> tuple[dict, Pick] | None:
    s, c = cfg()["site"], cfg()["new_posts"]
    listing = "\n\n".join(f"[{i}] {it['date']} | {it['title']}\n{it['categories']}\n{it['summary'][:300]}" for i, it in enumerate(items))
    prompt = (f"ANNOUNCEMENTS:\n\n{listing}\n\nEXISTING POST TITLES ON THE BLOG:\n" + "\n".join(f"- {t}" for t in existing_titles[:300]))
    pick = gemini.structured(prompt, Pick, system=PICK.format(site=s["name"], audience=s["audience"], topics=", ".join(c["topics"])),
                             thinking="low")
    if not 0 <= pick.index < len(items):
        return None
    # never a second post on a topic the blog already has
    t = tokens(pick.working_title)
    for old in existing_titles:
        if jaccard(t, tokens(old)) >= 0.7:
            log.info("Skipping %r: too close to existing post %r", pick.working_title, old)
            return None
    return items[pick.index], pick


def run(wp, process, state: dict, *, count: int, dry_run: bool, categories: list[dict], links_for) -> int:
    used = _used()
    items = [it for it in feed_items(cfg()["new_posts"]["days"]) if it["link"] not in used["used"]]
    log.info("%d fresh announcements to choose from", len(items))
    made = 0
    for _ in range(count * 2):
        if made >= count or not items:
            break
        posts = wp.posts()
        got = choose(items, [p["title"]["raw"] for p in posts])
        if not got:
            break
        item, pick = got
        items.remove(item)
        body = article_text(item["link"]) or item["summary"]
        source = (f"SOURCE: Microsoft announcement \"{item['title']}\" ({item['date']}), {item['link']}\n"
                  f"TUTORIAL PLAN: {pick.angle}\nFOCUS KEYWORD: {pick.focus_keyword}\n\n{body}")
        slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", pick.working_title.lower())).strip("-")[:80]
        if dry_run:
            post = {"id": 0, "slug": slug, "status": "draft", "date": None, "link": "", "title": {"raw": pick.working_title},
                    "content": {"raw": source}}
        else:
            post = wp.create_post({"title": pick.working_title, "slug": slug, "status": "draft", "content": ""})
            post["content"] = {"raw": source}
            post["title"] = {"raw": pick.working_title}
        result = process(wp, post, related=[], categories=categories, links=links_for(posts, post["id"]),
                         dry_run=dry_run, state=state, mode="new", source_url=item["link"])
        if not dry_run:
            used["used"][item["link"]] = {"post_id": post["id"], "title": pick.working_title, "result": result,
                                          "at": dt.date.today().isoformat()}
            _save_used(used)
        if result in ("published", "dry_run"):
            made += 1
    return made
