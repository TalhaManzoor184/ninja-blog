"""Article -> WordPress block-editor (Gutenberg) HTML."""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Callable, Optional

from .schemas import Article, Block
from .text import inline


@dataclass
class Media:
    id: int
    url: str
    alt: str
    caption: str = ""


# (block, index) -> uploaded media, or None if the image could not be made
MediaResolver = Callable[[Block, int], Optional[Media]]
# (step, step number, steps in the block, block index) -> image for that step, or None
StepResolver = Callable[[object, int, int, int], Optional[Media]]
SUB = 6  # sub-heading level for steps and FAQ questions (H6 keeps them small in the theme)


def slugify(t: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", t.lower())).strip("-")[:60] or "section"


def p(text: str) -> str:
    return f"<!-- wp:paragraph -->\n<p>{inline(text)}</p>\n<!-- /wp:paragraph -->"


def h(text: str, level: int = 2, anchor: str | None = None) -> str:
    attrs = {}
    if level != 2:
        attrs["level"] = level
    if anchor:
        attrs["anchor"] = anchor
    a = (" " + json.dumps(attrs)) if attrs else ""
    idattr = f' id="{anchor}"' if anchor else ""
    return f"<!-- wp:heading{a} -->\n<h{level} class=\"wp-block-heading\"{idattr}>{inline(text)}</h{level}>\n<!-- /wp:heading -->"


def ul(items: list[str], ordered: bool = False) -> str:
    tag = "ol" if ordered else "ul"
    attr = ' {"ordered":true}' if ordered else ""
    lis = "".join(f"<!-- wp:list-item -->\n<li>{inline(i)}</li>\n<!-- /wp:list-item -->" for i in items)
    return f"<!-- wp:list{attr} -->\n<{tag} class=\"wp-block-list\">{lis}</{tag}>\n<!-- /wp:list -->"


def code(src: str) -> str:
    return f"<!-- wp:code -->\n<pre class=\"wp-block-code\"><code>{html.escape(src.rstrip())}</code></pre>\n<!-- /wp:code -->"


def table(header: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{inline(c)}</th>" for c in header)
    body = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
    return (f"<!-- wp:table -->\n<figure class=\"wp-block-table\"><table><thead><tr>{th}</tr></thead>"
            f"<tbody>{body}</tbody></table></figure>\n<!-- /wp:table -->")


def image(m: Media) -> str:
    cap = f"<figcaption class=\"wp-element-caption\">{inline(m.caption)}</figcaption>" if m.caption else ""
    attrs = json.dumps({"id": m.id, "sizeSlug": "large", "linkDestination": "none"})
    return (f"<!-- wp:image {attrs} -->\n<figure class=\"wp-block-image size-large\"><img src=\"{html.escape(m.url)}\" "
            f"alt=\"{html.escape(m.alt)}\" class=\"wp-image-{m.id}\"/>{cap}</figure>\n<!-- /wp:image -->")


def raw_html(s: str) -> str:
    return f"<!-- wp:html -->\n{s}\n<!-- /wp:html -->"


def render_block(b: Block, idx: int, resolve: MediaResolver, step_img: StepResolver | None = None) -> list[str]:
    out: list[str] = []
    if b.type == "paragraph" and b.text:
        out.append(p(b.text))
    elif b.type == "note" and b.text:
        out.append(p("**Tip:** " + b.text))
    elif b.type == "list" and b.items:
        out.append(ul(b.items))
    elif b.type == "code" and b.code:
        out.append(code(b.code))
    elif b.type == "table" and b.table_header and b.table_rows:
        n = len(b.table_header)
        out.append(table(b.table_header, [(r + [""] * n)[:n] for r in b.table_rows]))
    elif b.type == "steps" and b.steps:
        for n, s in enumerate(b.steps, 1):
            out.append(h(f"Step {n}: {s.title}", SUB))
            m = step_img(s, n, len(b.steps), idx) if step_img else None
            if m:
                out.append(image(m))
            out.append(p(s.detail))
            if s.code:
                out.append(code(s.code))
    elif b.type in ("diagram", "image"):
        m = resolve(b, idx)
        if m:
            out.append(image(m))
    return out


def faq_schema(article: Article) -> str:
    data = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": f.q, "acceptedAnswer": {"@type": "Answer", "text": f.a}} for f in article.faqs]}
    return '<script type="application/ld+json">' + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + "</script>"


def render(article: Article, resolve: MediaResolver, references: list[str] | None = None,
           step_img: StepResolver | None = None) -> str:
    parts: list[str] = [p(t) for t in article.intro]
    if article.key_takeaways:
        parts.append(h("Key takeaways", 2, "key-takeaways"))
        parts.append(ul(article.key_takeaways))
    idx = 0
    seen: set[str] = set()
    for sec in article.sections:
        anchor = slugify(sec.heading)
        while anchor in seen:
            anchor += "-2"
        seen.add(anchor)
        parts.append(h(sec.heading, 2, anchor))
        for b in sec.blocks:
            parts.extend(render_block(b, idx, resolve, step_img))
            idx += 1
    if article.faqs:
        parts.append(h("Frequently asked questions", 2, "faq"))
        for f in article.faqs:
            parts.append(h(f.q, SUB))
            parts.append(p(f.a))
        parts.append(raw_html(faq_schema(article)))
    refs = [u for u in (references or []) if re.match(r"^https://learn\.microsoft\.com/", u)]
    if refs:
        parts.append(h("References", 2, "references"))
        parts.append(ul([f"[{u.split('learn.microsoft.com/')[-1].strip('/').split('/')[-1].replace('-', ' ') or 'Microsoft Learn'}]({u})" for u in refs[:6]]))
    return "\n\n".join(parts)
