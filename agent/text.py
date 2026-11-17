"""HTML <-> text helpers and similarity scores (no external deps)."""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "table", "ul", "ol", "figure"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.pre = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n\n## " if tag == "h2" else "\n\n### ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "pre":
            self.pre += 1
            self.out.append("\n```\n")
        elif tag == "img":
            alt = dict(attrs).get("alt") or ""
            self.out.append(f"\n[image: {alt}]\n" if alt else "\n[image]\n")
        elif tag in _BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self.out.append("\n```\n")
        elif tag in _BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data if self.pre else re.sub(r"\s+", " ", data))


def html_to_text(raw: str) -> str:
    raw = re.sub(r"<!--.*?-->", "", raw or "", flags=re.S)  # Gutenberg block comments
    p = _Text()
    p.feed(raw)
    t = "".join(p.out)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][\w'’.-]*", text))


_STOP = set("a an the and or of to in on for with by from is are how what why when your you using use do does can i my it this that into vs".split())


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", html.unescape(text).lower()) if w not in _STOP and len(w) > 1}


def shingles(text: str, n: int = 5) -> set[str]:
    w = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def inline(text: str) -> str:
    """Plain text with `code`, **bold** and [label](url) -> safe inline HTML."""
    t = html.escape(text or "", quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)

    def link(m):
        url = html.unescape(m.group(2))
        if not re.match(r"^(https?://|/)", url):
            return m.group(1)
        return f'<a href="{html.escape(url)}">{m.group(1)}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, t)
