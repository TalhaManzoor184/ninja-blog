"""Put posts back on the URLs Google knows.

Permalinks contain the date (/2024/06/09/slug/). Publishing all drafts at once gave them today's date,
so their URLs changed. This sets each post's date (and, if WordPress added a -2 suffix, its slug) back
to the URL in content/original_urls.txt. Posts already on the right URL are left alone.
"""
from __future__ import annotations

import logging
import re

from .config import ROOT, STATE_DIR

log = logging.getLogger(__name__)
FILE = ROOT / "content" / "original_urls.txt"


def originals() -> dict[str, tuple[str, str, int]]:
    out = {}
    for line in FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "|" not in line:
            continue
        slug, date, url, imp = [x.strip() for x in line.split("|")]
        out[slug] = (date, url, int(imp or 0))
    return out


def run(wp, dry_run: bool = False) -> str:
    orig = originals()
    posts = wp.posts()
    by_slug = {p["slug"]: p for p in posts if p.get("slug")}
    done, ok, missing, blocked = [], [], [], []
    for slug, (date, url, imp) in orig.items():
        p = by_slug.get(slug)
        new_slug = None
        if not p:
            # WordPress gave the post a -2/-3 suffix because another post held the slug at the time
            cands = [q for q in posts if re.fullmatch(re.escape(slug) + r"-\d+", q.get("slug", ""))]
            if len(cands) == 1:
                p, new_slug = cands[0], slug
        if not p:
            missing.append((slug, url, imp))
            continue
        if p["status"] != "publish":
            blocked.append((p, url, f"is {p['status']}"))
            continue
        cur_date = (p.get("date") or "")[:10]
        if cur_date == date and not new_slug:
            ok.append(p)
            continue
        time = (p.get("date") or "T10:00:00")[10:] or "T10:00:00"
        data = {"date": date + time}
        if new_slug:
            data["slug"] = new_slug
        if not dry_run:
            saved = wp.update_post(p["id"], data)
            got = saved.get("link", "")
            if got.rstrip("/") != url.rstrip("/"):
                blocked.append((p, url, f"WordPress saved it as {got}"))
                continue
        done.append((p, cur_date, url))

    lines = [f"# Restore original URLs {'(dry run)' if dry_run else ''}", "",
             f"- Moved back to the Google URL: **{len(done)}**",
             f"- Already on the right URL: **{len(ok)}**",
             f"- Not restored (see below): **{len(blocked)}**",
             f"- Google URL with no matching post (deleted or renamed): **{len(missing)}**", ""]
    if done:
        lines += ["## Restored", ""] + [f"- #{p['id']} {cur} → {url}" for p, cur, url in done] + [""]
    if blocked:
        lines += ["## Not restored", ""] + [f"- #{p['id']} {p['title']['raw']} — {why} (Google URL: {url})" for p, url, why in blocked] + [""]
    if missing:
        lines += ["## No matching post", ""] + [f"- {url} ({imp} impressions)" for _, url, imp in missing] + [""]
    text = "\n".join(lines) + "\n"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "restore-urls.md").write_text(text, encoding="utf-8")
    return text
