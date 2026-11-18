"""Featured image, inline illustrations (Gemini) and diagrams (Mermaid -> PNG)."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import gemini
from .config import cfg

log = logging.getLogger(__name__)

STYLE = ("Clean modern flat vector illustration for a technical blog about Microsoft Power Platform. "
         "Soft purple (#742774) and teal accents on a light background, simple geometric shapes, abstract app windows, "
         "data cards and connecting lines. Absolutely no text, letters, numbers, logos, brand marks or real UI screenshots.")

MERMAID_CONFIG = '{"theme":"base","themeVariables":{"primaryColor":"#f3eaf3","primaryBorderColor":"#742774","primaryTextColor":"#1f1f1f","lineColor":"#742774","fontFamily":"Arial"}}'


def _crop(data: bytes, aspect: str) -> bytes:
    """Centre-crop to the wanted aspect ratio (FLUX schnell returns squares) and save as JPEG."""
    import io
    from PIL import Image
    w_r, h_r = (int(x) for x in aspect.split(":"))
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    if w / h > w_r / h_r:
        nw = int(h * w_r / h_r)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w * h_r / w_r)
        img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    return out.getvalue()


def cloudflare_image(prompt: str, aspect: str = "16:9") -> bytes | None:
    """Free FLUX image from Cloudflare Workers AI (10,000 free Neurons a day ~ 170 images)."""
    import base64
    import os
    import requests
    acc, tok = os.getenv("CF_ACCOUNT_ID", "").strip(), os.getenv("CF_API_TOKEN", "").strip()
    if not (acc and tok):
        return None
    model = os.getenv("CF_IMAGE_MODEL", "").strip() or cfg()["models"].get("cloudflare_image", "@cf/black-forest-labs/flux-1-schnell")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{model}"
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {tok}"},
                          json={"prompt": f"{STYLE}\n\nSubject: {prompt}"[:2000], "steps": 6}, timeout=120)
        if r.status_code >= 400:
            log.warning("Cloudflare image failed (%s): %s", r.status_code, r.text[:300])
            return None
        img = r.json().get("result", {}).get("image")
        if img:
            log.info("Image made with Cloudflare %s", model)
        return _crop(base64.b64decode(img), aspect) if img else None
    except Exception as e:
        log.warning("Cloudflare image skipped: %s", e)
        return None


def ai_image(prompt: str, aspect: str = "16:9") -> bytes | None:
    """Cloudflare (free) first, then Gemini; None if neither can make one."""
    data = cloudflare_image(prompt, aspect)
    if data:
        return data
    try:
        return gemini.image(f"{STYLE}\n\nSubject: {prompt}", aspect_ratio=aspect)
    except Exception as e:  # quota or safety block: publish with fewer images
        log.warning("AI image skipped: %s", e)
        return None


def mermaid_cmd() -> list[str] | None:
    if shutil.which("mmdc"):
        return ["mmdc"]
    return None


def clean_mermaid(src: str) -> str:
    src = src.strip().strip("`")
    src = re.sub(r"^mermaid\s*", "", src)
    if not re.match(r"^(flowchart|graph|sequenceDiagram)", src):
        src = "flowchart LR\n" + src
    return src


def diagram_png(src: str) -> bytes | None:
    cmd = mermaid_cmd()
    if not cmd or not cfg()["article"].get("diagrams"):
        return None
    with tempfile.TemporaryDirectory() as d:
        inp, out, conf = Path(d, "d.mmd"), Path(d, "d.png"), Path(d, "c.json")
        inp.write_text(clean_mermaid(src), encoding="utf-8")
        conf.write_text(MERMAID_CONFIG, encoding="utf-8")
        pp = Path(d, "p.json")
        pp.write_text('{"args":["--no-sandbox"]}', encoding="utf-8")
        try:
            subprocess.run(cmd + ["-i", str(inp), "-o", str(out), "-c", str(conf), "-p", str(pp), "-b", "white", "-w", "1400", "-s", "2"],
                           check=True, capture_output=True, timeout=120)
            return out.read_bytes()
        except Exception as e:
            log.warning("Diagram skipped (%s)", getattr(e, "stderr", b"")[-300:] if hasattr(e, "stderr") else e)
            return None
