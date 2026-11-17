"""End-to-end test against a fake WordPress server and a fake Gemini. Run: python -m pytest -q"""
from __future__ import annotations

import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent import config, gemini, images, main, writer
from agent.schemas import FAQ, Article, Block, Research, Section, Step

AUTH = "Basic " + base64.b64encode(b"bot:abcd efgh".replace(b" ", b"")).decode()
LONG = " ".join(["Dataverse virtual tables show external data live without copying it."] * 20)


def make_posts():
    def post(i, slug, title, body, status="draft", date="2024-06-08T10:00:00"):
        return {"id": i, "slug": slug, "status": status, "date": date, "link": f"http://x/2024/06/08/{slug}/",
                "title": {"raw": title}, "content": {"raw": body}, "excerpt": {"raw": ""}, "categories": [1], "tags": [],
                "featured_media": 0}
    return [
        post(1, "virtual-tables-guide", "Create a virtual table in Power Apps", f"<!-- wp:paragraph --><p>{LONG}</p><!-- /wp:paragraph -->"),
        post(2, "virtual-tables-guide-2", "Create a virtual table in Power Apps", f"<p>{LONG}</p>"),  # duplicate
        post(3, "status-reason", "Understanding Status Reason", "<p>Status and status reason short draft.</p>"),
        post(4, "published-post", "Dirty fields in JavaScript", "<p>live</p>", status="publish"),
    ]


class FakeWP(BaseHTTPRequestHandler):
    posts: list = []
    media: list = []
    tags: list = []
    updates: list = []
    trashed: list = []

    def log_message(self, *a):
        pass

    def _send(self, code, obj, headers=None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _auth(self):
        if self.headers.get("Authorization") != AUTH:
            self._send(401, {"code": "rest_not_logged_in"})
            return False
        return True

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n)

    def do_GET(self):
        if not self._auth():
            return
        path, _, qs = self.path.partition("?")
        q = dict(x.split("=", 1) for x in qs.split("&") if "=" in x)
        if path.endswith("/users/me"):
            return self._send(200, {"name": "Bot", "roles": ["administrator"]})
        if path.endswith("/posts"):
            sts = q.get("status", "publish").replace("%2C", ",").split(",")
            items = [p for p in self.posts if p["status"] in sts]
            page, per = int(q.get("page", 1)), int(q.get("per_page", 10))
            per = 2  # force pagination in the test
            chunk = items[(page - 1) * per: page * per]
            return self._send(200, chunk, {"X-WP-TotalPages": str(max(1, -(-len(items) // per)))})
        m = re.search(r"/posts/(\d+)$", path)
        if m:
            return self._send(200, next(p for p in self.posts if p["id"] == int(m.group(1))))
        if path.endswith("/categories"):
            return self._send(200, [{"id": 1, "name": "Uncategorized"}, {"id": 7, "name": "Power Apps"}], {"X-WP-TotalPages": "1"})
        if path.endswith("/tags"):
            name = q.get("search", "").replace("+", " ").replace("%20", " ")
            return self._send(200, [t for t in self.tags if t["name"].lower() == name.lower()])
        self._send(404, {})

    def do_POST(self):
        if not self._auth():
            return
        path = self.path.split("?")[0]
        body = self._body()
        if path.endswith("/media"):
            assert self.headers["Content-Disposition"].startswith("attachment; filename=")
            mid = 100 + len(self.media)
            self.media.append({"id": mid, "bytes": len(body)})
            return self._send(201, {"id": mid, "source_url": f"http://x/uploads/{mid}.jpg"})
        m = re.search(r"/media/(\d+)$", path)
        if m:
            return self._send(200, {"id": int(m.group(1)), "source_url": f"http://x/uploads/{m.group(1)}.jpg", **json.loads(body)})
        if path.endswith("/tags"):
            t = {"id": 200 + len(self.tags), "name": json.loads(body)["name"]}
            self.tags.append(t)
            return self._send(201, t)
        m = re.search(r"/posts/(\d+)$", path)
        if m:
            data = json.loads(body)
            p = next(p for p in self.posts if p["id"] == int(m.group(1)))
            self.updates.append((p["id"], data))
            if "status" in data:
                p["status"] = data["status"]
            return self._send(200, {**p, "link": f"http://x/2024/06/08/{data.get('slug', p['slug'])}/"})
        self._send(404, {})

    def do_DELETE(self):
        if not self._auth():
            return
        self.trashed.append(self.path)
        self._send(200, {})


def fake_article(feedback=""):
    steps = Block(type="steps", steps=[Step(title="Open the maker portal", detail="Go to make.powerapps.com."),
                                       Step(title="Add the data source", detail="Pick OData v4.", code="GET /api/data", code_language="http")])
    body = " ".join(["This paragraph explains a concrete part of virtual tables with useful detail for makers."] * 22)
    secs = [Section(heading=f"Section {i}", blocks=[Block(type="paragraph", text=body + " [JSON Formatter](http://x/json-formatter/)")]) for i in range(3)]
    secs[0].blocks.append(steps)
    secs[1].blocks.append(Block(type="image", image_prompt="data flowing between two systems", caption="Data stays in the source"))
    secs[2].blocks.append(Block(type="table", table_header=["Option", "Live?"], table_rows=[["Virtual table", "Yes"], ["Import", "No", "extra"]]))
    return Article(keep=True, seo_title="Power Apps Virtual Table with OData v4: Step-by-Step Guide",
                   h1="How to Create a Virtual Table in Power Apps (OData v4)",
                   meta_description="Show live OData data in Power Apps without copying it into Dataverse. Set up a virtual table step by step, plus the limits to know.",
                   focus_keyword="virtual table power apps", intro=["Virtual tables show external data live.", "Here is how."],
                   sections=secs, key_takeaways=["Data is live", "Read-only by default"],
                   faqs=[FAQ(q=f"Question {i}?", a="Answer.") for i in range(3)], category="Power Apps",
                   tags=["Dataverse", "Virtual tables"], featured_image_prompt="abstract data bridge", featured_image_alt="Data bridge",
                   quality_notes="ok")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    FakeWP.posts, FakeWP.media, FakeWP.tags, FakeWP.updates, FakeWP.trashed = make_posts(), [], [], [], []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeWP)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("WP_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("WP_USER", "bot")
    monkeypatch.setenv("WP_APP_PASSWORD", "abcd efgh")
    for name, val in {"STATE_FILE": tmp_path / "state/progress.json", "BACKUP_DIR": tmp_path / "state/backups",
                      "REPORT_FILE": tmp_path / "state/report.md", "OUTPUT_DIR": tmp_path / "output"}.items():
        monkeypatch.setattr(main, name, val)
    prio = tmp_path / "priority.txt"
    prio.write_text("status-reason  # most impressions\nvirtual-tables-guide\n")
    monkeypatch.setattr(main, "PRIORITY_FILE", prio)
    config.cfg.cache_clear()
    calls = {"write": 0}

    def fake_structured(prompt, schema, **kw):
        if schema is Research:
            return Research(current_facts=["Azure AD is Microsoft Entra ID"], outdated_in_original=[], search_questions=[],
                            reference_urls=["https://learn.microsoft.com/power-apps/maker/data-platform/create-virtual-tables", "https://evil.example/x"])
        calls["write"] += 1
        return fake_article()

    monkeypatch.setattr(gemini, "structured", fake_structured)
    monkeypatch.setattr(gemini, "image", lambda prompt, aspect_ratio="16:9": b"\xff\xd8fakejpeg")
    yield calls
    srv.shutdown()
    config.cfg.cache_clear()


def test_run_publishes_priority_post_and_sets_aside_duplicate(env, tmp_path):
    assert main.main(["run"]) == 0
    # status-reason is first in priority.txt
    pid, data = FakeWP.updates[-1]
    assert pid == 3
    assert data["status"] == "publish" and data["slug"] == "status-reason" and data["date"] == "2024-06-08T10:00:00"
    assert data["categories"] == [7] and data["featured_media"] and len(data["tags"]) == 2
    assert data["meta"]["_yoast_wpseo_metadesc"] == data["excerpt"]
    c = data["content"]
    assert c.count("<!-- wp:") == c.count("<!-- /wp:"), "unbalanced Gutenberg blocks"
    assert "Step 2: Add the data source" in c and "<code>GET /api/data</code>" in c
    assert '"@type": "FAQPage"' in c
    assert "learn.microsoft.com" in c and "evil.example" not in c
    assert len(FakeWP.media) == 2  # featured + 1 inline illustration (no mermaid in test env)
    state = json.loads((tmp_path / "state/progress.json").read_text())
    assert state["posts"]["3"]["status"] == "published"
    assert state["posts"]["2"]["status"] == "duplicate" and state["posts"]["2"]["kept"] == 1
    assert (tmp_path / "state/backups/3.json").exists()
    assert "Duplicates" in (tmp_path / "state/report.md").read_text()
    # next run picks the next post and never repeats one
    assert main.main(["run"]) == 0
    assert FakeWP.updates[-1][0] == 1
    assert not FakeWP.trashed


def test_dry_run_changes_nothing(env, tmp_path):
    assert main.main(["run", "--dry-run"]) == 0
    assert not FakeWP.updates and not FakeWP.media
    assert (tmp_path / "output/status-reason/preview.html").exists()
    assert not (tmp_path / "state/progress.json").exists()


def test_quality_failure_retries_then_needs_review(env, tmp_path, monkeypatch):
    short = fake_article()
    short.sections = short.sections[:1]
    monkeypatch.setattr(writer, "write", lambda *a, **k: short)
    main.main(["run", "--count", "1"])
    state = json.loads((tmp_path / "state/progress.json").read_text())
    assert all(v["status"] in ("needs_review", "duplicate") for v in state["posts"].values())
    assert not [u for u in FakeWP.updates if u[1].get("status") == "publish"]


def test_bad_password_gives_clear_error(env, monkeypatch):
    monkeypatch.setenv("WP_APP_PASSWORD", "wrong")
    with pytest.raises(Exception, match="401.*WP_APP_PASSWORD"):
        main.main(["check"])
