"""Branded featured image (title card) drawn locally with Pillow — used when Gemini images are unavailable."""
from __future__ import annotations

import io
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF"]


def _font(bold: bool, size: int):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in FONT_DIRS:
        p = Path(d, name)
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size=size)


def title_card(title: str, label: str, site: str = "PowerApps Ninja", w: int = 1200, h: int = 630) -> bytes:
    img = Image.new("RGB", (w, h), "#2a0f2a")
    d = ImageDraw.Draw(img)
    top, bottom = (0x74, 0x27, 0x74), (0x2a, 0x0f, 0x2a)
    for y in range(h):  # vertical gradient in the brand purple
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    # soft decorative circles
    d.ellipse([w - 360, -160, w + 160, 360], outline=(255, 255, 255, 40), width=3)
    d.ellipse([w - 260, h - 200, w + 60, h + 120], outline=(94, 234, 212), width=3)

    pad = 80
    lab_font = _font(True, 26)
    lab = label.upper()[:40]
    lw = d.textlength(lab, font=lab_font)
    d.rounded_rectangle([pad, pad, pad + lw + 36, pad + 50], radius=25, fill=(94, 234, 212))
    d.text((pad + 18, pad + 10), lab, font=lab_font, fill="#1f1f1f")

    # largest font size that fits the title in 4 lines
    for size in (72, 64, 56, 50, 44):
        f = _font(True, size)
        chars = max(10, int((w - 2 * pad - 120) / (size * 0.56)))
        lines = textwrap.wrap(title, chars)
        if len(lines) <= 4:
            break
    lines = lines[:4]
    y = pad + 90
    for line in lines:
        d.text((pad, y), line, font=f, fill="white")
        y += int(size * 1.2)

    d.text((pad, h - pad - 30), site, font=_font(True, 30), fill=(94, 234, 212))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _wrap(d, text: str, font, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        t = f"{cur} {word}".strip()
        if d.textlength(t, font=font) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit(d, text: str, font, width: int) -> str:
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + " …", font=font) > width:
        text = text[:-1]
    return text.rstrip() + " …"


def step_card(n: int, total: int, title: str, detail: str, code: str | None = None, w: int = 1200) -> bytes:
    """Light 'Step N of M' card shown under each step: number, title, what to do and the first code line if any."""
    import re as _re
    purple, teal, ink, muted = (0x74, 0x27, 0x74), (94, 234, 212), "#1f1f1f", "#5b5b66"
    tx, right = 250, w - 100
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for size in (46, 40, 34):
        tf = _font(True, size)
        tl = _wrap(probe, title, tf, right - tx)
        if len(tl) <= 2:
            break
    tl = tl[:2]
    plain = _re.sub(r"\*\*|`", "", _re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", detail))
    df = _font(False, 28)
    dl = _wrap(probe, plain, df, right - tx)[: 3 if code else 4]
    mono = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
    cf = ImageFont.truetype(str(mono), 24) if mono.exists() else _font(False, 24)
    first = next((l.strip() for l in (code or "").splitlines() if l.strip()), "")
    y_title = 135
    y_detail = y_title + len(tl) * int(size * 1.25) + 14
    y_code = y_detail + len(dl) * 40 + 16
    h = max(330, (y_code + 64 if first else y_detail + len(dl) * 40) + 70)

    img = Image.new("RGB", (w, h), "#faf6fa")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 14, h], fill=purple)
    d.rounded_rectangle([40, 30, w - 40, h - 30], radius=28, fill="white", outline=(0xe6, 0xd6, 0xe6), width=2)
    lab = f"Step {n} of {total}"
    lf = _font(False, 24)
    d.text((right - d.textlength(lab, font=lf), 66), lab, font=lf, fill=muted)
    for i in range(1, total + 1):
        cx = 90 + (i - 1) * 34
        if cx > right - 260:
            break
        d.ellipse([cx, 70, cx + 18, 88], fill=purple if i == n else (0xe6, 0xd6, 0xe6))
    d.ellipse([90, 130, 210, 250], fill=purple)
    nf = _font(True, 64 if n < 10 else 52)
    d.text((150, 190), str(n), font=nf, fill="white", anchor="mm")
    y = y_title
    for line in tl:
        d.text((tx, y), line, font=tf, fill=ink)
        y += int(size * 1.25)
    y = y_detail
    for line in dl:
        d.text((tx, y), line, font=df, fill=muted)
        y += 40
    if first:
        d.rounded_rectangle([tx, y_code, right, y_code + 60], radius=12, fill="#2a0f2a")
        d.text((tx + 22, y_code + 15), _fit(d, first, cf, right - tx - 44), font=cf, fill=teal)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()
