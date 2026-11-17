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


def ai_image(prompt: str, aspect: str = "16:9") -> bytes | None:
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
