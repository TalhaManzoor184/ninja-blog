"""PowerApps Ninja blog agent.

  python -m agent.main check                 # test WordPress + Gemini access, change nothing
  python -m agent.main scan                  # list drafts, duplicates and the queue -> state/report.md
  python -m agent.main run                   # rewrite + publish today's post(s)
  python -m agent.main run --post-id 123     # one specific post
  python -m agent.main run --dry-run         # write previews to output/, change nothing in WordPress
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import sys
from pathlib import Path

from . import cards, images, quality, writer
from .config import BACKUP_DIR, OUTPUT_DIR, PRIORITY_FILE, REPORT_FILE, STATE_FILE, cfg
from .dedupe import groups, pick_keeper
from .render import Media, render
from .schemas import Block
from .text import html_to_text, words
from .wp import WP

log = logging.getLogger("agent")

DONE, DUP, NOT_KEPT, REVIEW, ERROR = "published", "duplicate", "not_kept", "needs_review", "error"
FLAGGED = "flagged"  # moved to draft by the audit (duplicate / low value): never rewritten or published again
FINAL = {DONE, DUP, NOT_KEPT, FLAGGED}
TODO = ("draft", "publish")  # posts the agent rewrites: drafts and published posts not rewritten yet


# ---------------------------------------------------------------- state ----
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"posts": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def mark(state: dict, post: dict, status: str, **extra) -> None:
    rec = state["posts"].setdefault(str(post["id"]), {})
    rec.update({"status": status, "slug": post.get("slug"), "title": post["title"]["raw"], "old_title": post["title"]["raw"],
                "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), **extra})


def priority_slugs() -> list[str]:
    if not PRIORITY_FILE.exists():
        return []
    out = []
    for line in PRIORITY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.append(line.rstrip("/").split("/")[-1])
    return out


# ---------------------------------------------------------------- plan -----
def plan(posts: list[dict], state: dict) -> tuple[list[dict], dict[int, list[dict]], list[tuple[dict, dict]]]:
    """Returns (queue of drafts to process, related drafts per keeper id, [(duplicate, keeper)])."""
    prio = priority_slugs()
    related: dict[int, list[dict]] = {}
    dups: list[tuple[dict, dict]] = []
    for g in groups(posts, cfg()["duplicates"]["similarity"]):
        keeper = pick_keeper(g, prio)
        for p in g:
            if p is keeper or p["status"] not in TODO:
                continue
            dups.append((p, keeper))
            related.setdefault(keeper["id"], []).append(p)
    dup_ids = {d["id"] for d, _ in dups}
    drafts = [p for p in posts if p["status"] in TODO and p["id"] not in dup_ids
              and state["posts"].get(str(p["id"]), {}).get("status") not in FINAL | {REVIEW}]

    def order(p):
        slug = p.get("slug") or ""
        return (prio.index(slug) if slug in prio else len(prio), -int(re.sub(r"\D", "", p.get("date", "0"))[:14] or 0))

    return sorted(drafts, key=order), related, dups


# ---------------------------------------------------------------- one post -
def process(wp: WP, post: dict, *, related: list[dict], categories: list[dict], links: list[dict],
            dry_run: bool, state: dict, mode: str = "rewrite", source_url: str | list[str] | None = None) -> str:
    title = post["title"]["raw"] or post.get("slug", "")
    original = html_to_text(post["content"]["raw"])
    log.info("Post %s: %r (%d words in the old draft)", post["id"], title, words(original))

    if not dry_run and mode == "rewrite":
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        bk = BACKUP_DIR / f"{post['id']}.json"
        if not bk.exists():  # keep the very first original
            bk.write_text(json.dumps({k: post.get(k) for k in ("id", "slug", "date", "status", "title", "content", "excerpt",
                                                                "categories", "tags", "featured_media", "link")},
                                     indent=1, ensure_ascii=False), encoding="utf-8")

    related_text = "\n\n---\n\n".join(f"# {r['title']['raw']}\n{html_to_text(r['content']['raw'])}" for r in related)
    cat_names = [c["name"] for c in categories]
    research = writer.research(title, original) if mode == "rewrite" else None
    article, problems = None, []
    for attempt in range(2):
        article = writer.write(title, original, categories=cat_names, links=links, related_text=related_text,
                               research_notes=research, feedback="\n".join(f"- {x}" for x in problems), mode=mode)
        if not article.keep:
            mark(state, post, NOT_KEPT, notes=article.quality_notes)
            log.warning("Writer says this topic is not worth publishing: %s", article.quality_notes)
            return NOT_KEPT
        problems = quality.check(article, cat_names, cfg()["site"]["url"], min_words=cfg()["new_posts"].get("min_words") if mode == "new" else None)
        if not problems:
            break
        log.warning("Quality check failed (attempt %d): %s", attempt + 1, problems)
    if problems:
        mark(state, post, REVIEW, notes="; ".join(problems))
        return REVIEW

    slug = post.get("slug") or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    out_dir = OUTPUT_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    def upload(data: bytes, name: str, alt: str, caption: str = "") -> Media:
        (out_dir / name).write_bytes(data)
        if dry_run:
            return Media(id=0, url=name, alt=alt, caption=caption)
        m = wp.upload_media(data, name, alt, caption, post_id=post["id"])
        return Media(id=m["id"], url=m.get("source_url", ""), alt=alt, caption=caption)

    budget = {"image": cfg()["article"].get("inline_ai_images", 0), "diagram": cfg()["article"].get("diagrams", 0)}
    n_img = 0

    def resolve(b: Block, idx: int) -> Media | None:
        nonlocal n_img
        if budget.get(b.type, 0) <= 0:
            return None
        alt = (b.caption or article.h1)[:200]
        data, name = None, None
        if b.type == "diagram" and b.mermaid:
            data, name = images.diagram_png(b.mermaid), f"{slug}-diagram-{idx}.png"
        elif b.type == "image" and b.image_prompt:
            data, name = images.ai_image(b.image_prompt), f"{slug}-illustration-{idx}.jpg"
        if not data:
            return None
        budget[b.type] -= 1
        n_img += 1
        return upload(data, name, alt, b.caption or "")

    def step_img(step, n: int, total: int, idx: int) -> Media | None:
        if not cfg()["article"].get("step_images", True):
            return None
        nonlocal n_img
        data = cards.step_card(n, total, step.title, step.detail, step.code)
        n_img += 1
        return upload(data, f"{slug}-step-{idx}-{n}.jpg", f"Step {n}: {step.title}"[:200])

    extra = [source_url] if isinstance(source_url, str) else list(source_url or [])
    refs = (research.reference_urls if research else []) + extra
    content = render(article, resolve, refs, step_img)

    featured_id = None
    if cfg()["article"].get("featured_image"):
        data = images.ai_image(article.featured_image_prompt)
        if not data:  # no image quota (e.g. free tier): branded title card instead
            data = cards.title_card(article.h1, article.category, cfg()["site"]["name"])
        featured_id = upload(data, f"{slug}-featured.jpg", article.featured_image_alt).id
        n_img += 1

    cat = next((c["id"] for c in categories if c["name"] == article.category), None)
    publish = cfg()["run"]["publish"]
    data = {
        "title": article.h1,
        "content": content,
        "excerpt": article.meta_description,
        "slug": slug,  # keep the old URL
        "date": post.get("date") if mode == "rewrite" else None,  # rewrite: keep the date so Google's URLs still work
        "meta": {
            "_yoast_wpseo_title": article.seo_title, "_yoast_wpseo_metadesc": article.meta_description,
            "_yoast_wpseo_focuskw": article.focus_keyword,
            "rank_math_title": article.seo_title, "rank_math_description": article.meta_description,
            "rank_math_focus_keyword": article.focus_keyword,
        },
    }
    if not data["date"]:
        del data["date"]
    if cat:
        data["categories"] = [cat]
    if featured_id:
        data["featured_media"] = featured_id
    if publish:
        data["status"] = "publish"

    (out_dir / "article.json").write_text(article.model_dump_json(indent=1), encoding="utf-8")
    (out_dir / "content.html").write_text(content, encoding="utf-8")
    (out_dir / "preview.html").write_text(
        f"<!doctype html><meta charset=utf-8><title>{article.seo_title}</title><body style='max-width:760px;margin:auto;font-family:system-ui'>"
        f"<h1>{article.h1}</h1><p><i>{article.meta_description}</i></p>{content}</body>", encoding="utf-8")

    n_words = words(quality.article_text(article))
    if dry_run:
        log.info("Dry run: preview written to %s (%d words, %d images)", out_dir, n_words, n_img)
        return "dry_run"

    if not cat:
        log.warning("Category %r not found; keeping the post's current categories", article.category)
    data["tags"] = wp.tag_ids(article.tags)
    saved = wp.update_post(post["id"], data)
    mark(state, post, DONE if publish else REVIEW, title=article.h1, link=saved.get("link"), words=n_words, images=n_img,
         seo_title=article.seo_title, notes=article.quality_notes if publish else "Rewritten; left as draft (publish: false)")
    log.info("%s: %s", "Published" if publish else "Saved as draft", saved.get("link"))
    return DONE if publish else REVIEW


# ---------------------------------------------------------------- report ---
def write_report(state: dict, queue: list[dict], dups: list[tuple[dict, dict]], extra: str = "") -> str:
    recs = state["posts"]
    lines = [f"# Blog agent report — {dt.date.today().isoformat()}", ""]
    if extra:
        lines += [extra, ""]
    by = lambda s: [(k, v) for k, v in recs.items() if v.get("status") == s]
    lines += [f"- Published: **{len(by(DONE))}**", f"- Waiting in queue: **{len(queue)}**",
              f"- Duplicates set aside: **{len(dups)}**", f"- Need your review: **{len(by(REVIEW))}**",
              f"- Not worth publishing (left as draft): **{len(by(NOT_KEPT))}**", ""]
    if by(REVIEW):
        lines += ["## Need your review", ""] + [f"- #{k} {v.get('title', '')} — {v.get('notes', '')}" for k, v in by(REVIEW)] + [""]
    if dups:
        lines += ["## Duplicates (copy → kept post)", ""] + [f"- #{d['id']} {d['title']['raw']} → #{k['id']} {k['title']['raw']}" for d, k in dups] + [""]
    if by(NOT_KEPT):
        lines += ["## Not worth publishing", ""] + [f"- #{k} {v.get('title', '')} — {v.get('notes', '')}" for k, v in by(NOT_KEPT)] + [""]
    lines += ["## Next in queue", ""] + [f"{i}. #{p['id']} {p['title']['raw']} (`{p.get('slug')}`)" for i, p in enumerate(queue[:15], 1)] + [""]
    if by(DONE):
        lines += ["## Published", ""] + [f"- {v.get('at', '')[:10]} [{v.get('title', '')}]({v.get('link', '')}) — {v.get('words', '?')} words" for _, v in sorted(by(DONE), key=lambda x: x[1].get("at", ""), reverse=True)]
    text = "\n".join(lines) + "\n"
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(text, encoding="utf-8")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    return text


# ---------------------------------------------------------------- commands -
def site_links(posts: list[dict], exclude: int | None = None) -> list[dict]:
    s = cfg()["site"]
    links = [{"title": t["name"] + " (free tool)", "url": s["url"] + t["path"]} for t in s["tools"]]
    links += [{"title": p["title"]["raw"], "url": p["link"]} for p in posts if p["status"] == "publish" and p["id"] != exclude]
    return links


def cmd_check(wp: WP) -> int:
    me = wp.whoami()
    roles = me.get("roles", [])
    posts = wp.posts()
    drafts = [p for p in posts if p["status"] == "draft"]
    print(f"WordPress OK: logged in as {me.get('name')} ({', '.join(roles)}). {len(posts)} posts, {len(drafts)} drafts.")
    if not set(roles) & {"administrator", "editor"}:
        print("WARNING: use an Administrator or Editor account so the agent can publish and upload images.")
    from . import gemini
    print("Gemini OK:", gemini.text("Reply with the single word OK.", thinking="low")[:20].strip())
    return 0


def cmd_scan(wp: WP) -> int:
    posts = wp.posts()
    state = load_state()
    queue, _, dups = plan(posts, state)
    drafts = [p for p in posts if p["status"] == "draft"]
    thin = sorted(drafts, key=lambda p: p["_words"])[:10]
    extra = (f"{len(posts)} posts in WordPress, {len(drafts)} drafts.\n\n## Thinnest drafts\n\n"
             + "\n".join(f"- #{p['id']} {p['title']['raw']} — {p['_words']} words" for p in thin))
    print(write_report(state, queue, dups, extra))
    return 0


def cmd_run(wp: WP, post_id: int | None, dry_run: bool, count: int | None) -> int:
    state = load_state()
    posts = wp.posts()
    queue, related, dups = plan(posts, state)

    action = cfg()["duplicates"]["action"]
    for d, keeper in dups:
        rec = state["posts"].get(str(d["id"]), {})
        if rec.get("status") == DUP:
            continue
        if action == "trash" and not dry_run:
            wp.trash_post(d["id"])
        elif d["status"] == "publish" and not dry_run:  # never leave a duplicate live
            wp.update_post(d["id"], {"status": "draft"})
        if not dry_run:
            mark(state, d, DUP, kept=keeper["id"], notes=f"Same topic as #{keeper['id']} ({action})")

    if post_id:
        queue = [p for p in posts if p["id"] == post_id]
        if not queue:
            print(f"Post {post_id} not found", file=sys.stderr)
            return 1
    categories = [c for c in wp.categories()]
    want = count or cfg()["run"]["posts_per_day"]
    done = tries = 0
    failures = 0
    for post in queue:
        if done >= want or tries >= cfg()["run"]["max_attempts_per_run"] * want:
            break
        tries += 1
        try:
            result = process(wp, post, related=related.get(post["id"], []), categories=categories,
                             links=site_links(posts, post["id"]), dry_run=dry_run, state=state)
        except Exception as e:
            log.exception("Post %s failed", post["id"])
            if not dry_run:
                mark(state, post, ERROR, notes=str(e)[:300])
            failures += 1
            result = ERROR
        if result in (DONE, "dry_run") or (result == REVIEW and not cfg()["run"]["publish"]):
            done += 1
        if not dry_run:
            save_state(state)
    if not dry_run:
        save_state(state)
        queue, _, dups = plan(posts, state)
        write_report(state, [p for p in queue if state["posts"].get(str(p["id"]), {}).get("status") is None], dups)
    print(f"Finished: {done} post(s) {'previewed' if dry_run else 'done'}, {failures} error(s).")
    return 0 if done or not queue else 1


def cmd_new(wp: WP, count: int | None, dry_run: bool) -> int:
    from . import newpost
    state = load_state()
    want = count if count is not None else cfg()["new_posts"]["per_day"]
    if want <= 0:
        return 0

    def proc(wp_, post, **kw):
        result = process(wp_, post, **kw)
        if result not in (DONE, "dry_run") and not dry_run and post.get("id"):
            wp_.trash_post(post["id"])  # a new post that did not pass leaves no empty draft behind
        return result

    made = newpost.run(wp, proc, state, count=want, dry_run=dry_run, categories=wp.categories(), links_for=site_links)
    if not dry_run:
        save_state(state)
    print(f"New posts: {made} {'previewed' if dry_run else 'published'}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="PowerApps Ninja blog agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    nw = sub.add_parser("new")
    nw.add_argument("--count", type=int)
    nw.add_argument("--dry-run", action="store_true")
    sub.add_parser("scan")
    th = sub.add_parser("draft-thin")
    th.add_argument("--max-words", type=int, default=500)
    th.add_argument("--dry-run", action="store_true")
    rs = sub.add_parser("restore-dates")
    rs.add_argument("--dry-run", action="store_true")
    au = sub.add_parser("audit")
    au.add_argument("--draft", action="store_true", help="move the posts it flags to draft")
    r = sub.add_parser("run")
    r.add_argument("--post-id", type=int)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--count", type=int)
    a = ap.parse_args(argv)
    wp = WP()
    if a.cmd == "check":
        return cmd_check(wp)
    if a.cmd == "scan":
        return cmd_scan(wp)
    if a.cmd == "new":
        return cmd_new(wp, a.count, a.dry_run)
    if a.cmd == "draft-thin":
        from . import thin
        text = thin.run(wp, a.max_words, a.dry_run)
        print(text)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(text)
        return 0
    if a.cmd == "restore-dates":
        from . import restore
        text = restore.run(wp, dry_run=a.dry_run)
        print(text)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(text)
        return 0
    if a.cmd == "audit":
        from . import audit
        state = load_state()
        text = audit.run(wp, draft=a.draft, state=state)
        if a.draft:
            save_state(state)
        print(text)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(text)
        return 0
    return cmd_run(wp, a.post_id, a.dry_run, a.count)


if __name__ == "__main__":
    sys.exit(main())
