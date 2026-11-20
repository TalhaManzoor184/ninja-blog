"""Move thin posts the agent has not rewritten yet to draft. They stay in the agent's queue: it rewrites them
(posts Google knows first) and publishes them again on the same URL."""
from __future__ import annotations

from .config import STATE_DIR
from .main import DONE, FINAL, load_state
from .restore import originals
from .text import html_to_text, words


def run(wp, max_words: int = 500, dry_run: bool = False) -> str:
    state = load_state()["posts"]
    imp = {slug: i for slug, (_, _, i) in originals().items()}
    moved = []
    for p in wp.posts(status="publish"):
        if state.get(str(p["id"]), {}).get("status") in FINAL | {DONE}:
            continue  # already rewritten by the agent (or set aside)
        n = words(html_to_text(p["content"]["raw"]))
        if n >= max_words:
            continue
        if not dry_run:
            wp.update_post(p["id"], {"status": "draft"})
        moved.append((p, n, imp.get(p.get("slug", ""), 0)))
    moved.sort(key=lambda x: -x[2])
    lines = [f"# Thin posts moved to draft{' (dry run)' if dry_run else ''}", "",
             f"**{len(moved)}** published posts under {max_words} words. The agent rewrites them, posts with Google "
             "impressions first, and publishes each again on its old URL.", "",
             "| # | Words | Google impressions | Title |", "|---|---|---|---|"]
    lines += [f"| {p['id']} | {n} | {i} | {p['title']['raw'].replace('|', '/')} |" for p, n, i in moved]
    text = "\n".join(lines) + "\n"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "thin-drafted.md").write_text(text, encoding="utf-8")
    return text
