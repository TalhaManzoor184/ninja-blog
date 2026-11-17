"""Small WordPress REST API client (Application Password auth)."""
from __future__ import annotations

import logging
import mimetypes
import time
from typing import Any

import requests

from .config import cfg, env

log = logging.getLogger(__name__)
UA = "PowerAppsNinja-BlogAgent/1.0 (+https://github.com/TalhaManzoor184/ninja-blog)"


class WPError(RuntimeError):
    pass


class WP:
    def __init__(self, base: str | None = None, user: str | None = None, password: str | None = None):
        self.base = (base or cfg()["site"]["url"]).rstrip("/")
        self.api = self.base + "/wp-json/wp/v2"
        self.s = requests.Session()
        self.s.auth = (user or env("WP_USER"), (password or env("WP_APP_PASSWORD")).replace(" ", ""))
        self.s.headers["User-Agent"] = UA

    # ---- low level -------------------------------------------------------
    def _req(self, method: str, path: str, **kw) -> requests.Response:
        url = path if path.startswith("http") else self.api + path
        for attempt in range(4):
            try:
                r = self.s.request(method, url, timeout=90, **kw)
            except requests.RequestException as e:
                if attempt == 3:
                    raise WPError(f"{method} {url} failed: {e}") from e
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code >= 400:
                hint = ""
                if r.status_code == 401:
                    hint = (" — check WP_USER / WP_APP_PASSWORD. If they are right, the host may strip the Authorization "
                            "header: see README > Troubleshooting.")
                elif r.status_code == 403:
                    hint = " — a security plugin or firewall may be blocking the REST API for GitHub's servers."
                raise WPError(f"{method} {url} -> {r.status_code}: {r.text[:400]}{hint}")
            return r
        raise WPError("unreachable")

    def get(self, path: str, **params) -> Any:
        return self._req("GET", path, params=params).json()

    def get_all(self, path: str, **params) -> list[dict]:
        out, page = [], 1
        while True:
            r = self._req("GET", path, params={**params, "per_page": 100, "page": page})
            out.extend(r.json())
            if page >= int(r.headers.get("X-WP-TotalPages", "1") or 1):
                return out
            page += 1

    # ---- checks ----------------------------------------------------------
    def whoami(self) -> dict:
        return self.get("/users/me", context="edit")

    # ---- posts -----------------------------------------------------------
    def posts(self, status: str = "draft,publish,pending,future,private") -> list[dict]:
        return self.get_all("/posts", status=status, context="edit", orderby="date", order="desc")

    def post(self, post_id: int) -> dict:
        return self.get(f"/posts/{post_id}", context="edit")

    def update_post(self, post_id: int, data: dict) -> dict:
        return self._req("POST", f"/posts/{post_id}", json=data).json()

    def trash_post(self, post_id: int) -> dict:
        return self._req("DELETE", f"/posts/{post_id}").json()

    # ---- taxonomy --------------------------------------------------------
    def categories(self) -> list[dict]:
        return self.get_all("/categories", hide_empty="false")

    def tag_ids(self, names: list[str]) -> list[int]:
        ids = []
        for name in names:
            name = name.strip()
            if not name:
                continue
            found = [t for t in self.get("/tags", search=name, per_page=20) if t["name"].lower() == name.lower()]
            if found:
                ids.append(found[0]["id"])
            else:
                try:
                    ids.append(self._req("POST", "/tags", json={"name": name}).json()["id"])
                except WPError as e:  # term_exists race: reuse the existing id
                    log.warning("Tag %r: %s", name, e)
        return ids

    # ---- media -----------------------------------------------------------
    def upload_media(self, data: bytes, filename: str, alt: str, caption: str = "", post_id: int | None = None) -> dict:
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"', "Content-Type": ctype}
        media = self._req("POST", "/media", data=data, headers=headers).json()
        meta = {"alt_text": alt, "title": alt[:90]}
        if caption:
            meta["caption"] = caption
        if post_id:
            meta["post"] = post_id
        return self._req("POST", f"/media/{media['id']}", json=meta).json()
