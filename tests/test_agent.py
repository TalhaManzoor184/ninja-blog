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
        if path.endswith("/posts"):
            data = json.loads(body)
            p = {"id": 900 + len(self.posts), "slug": data["slug"], "status": data["status"], "date": "2026-11-21T10:00:00",
                 "title": {"raw": data["title"]}, "content": {"raw": data.get("content", "")}, "categories": [], "tags": []}
            p["link"] = f"http://x/2026/11/21/{p['slug']}/"
            self.posts.append(p)
            self.updates.append(("create", data))
            return self._send(201, p)
        m = re.search(r"/posts/(\d+)$", path)
        if m:
            data = json.loads(body)
            p = next(p for p in self.posts if p["id"] == int(m.group(1)))
            self.updates.append((p["id"], data))
            for k in ("status", "date", "slug"):
                if k in data and data[k]:
                    p[k] = data[k]
            p["link"] = f"http://x/{p['date'][:10].replace('-', '/')}/{p['slug']}/"
            return self._send(200, p)
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
    monkeypatch.setattr(writer, "research", lambda title, original: fake_structured("", Research))
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
    assert len(FakeWP.media) == 4  # featured + 1 inline illustration + 2 step cards (no mermaid in test env)
    assert "<h6 class=\"wp-block-heading\">Step 1: Open the maker portal</h6>" in c and '{"level": 6}' in c
    assert c.index("Step 1: Open") < c.index("wp-image-") < c.index("Go to make.powerapps.com")
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


def test_featured_falls_back_to_title_card_without_image_quota(env, monkeypatch):
    def no_quota(prompt, aspect_ratio="16:9"):
        raise gemini.QuotaError("limit: 0 requests per day on Free Tier")
    monkeypatch.setattr(gemini, "image", no_quota)
    assert main.main(["run"]) == 0
    _, data = FakeWP.updates[-1]
    assert data["featured_media"] and len(FakeWP.media) == 3  # 2 step cards + title card, no inline AI image
    assert all(m["bytes"] > 10000 for m in FakeWP.media)


def test_duplicate_rules():
    from agent.dedupe import similarity
    from agent.text import shingles

    def P(t, body, slug=""):
        return {"title": {"raw": t}, "_sh": shingles(body), "slug": slug}
    a = " ".join(f"word{i}" for i in range(200))
    b = " ".join(f"other{i}" for i in range(200))
    assert similarity(P("How to use Autonumber field in Model Driven Apps", a), P("How to use Autonumber field in Model Driven Apps", b)) == 1.0
    assert similarity(P("Status reason", a, "status-reason"), P("Other", b, "status-reason-2")) == 1.0
    assert similarity(P("Open as Dialog using NavigateTo", a), P("Add on Form as control", b)) == 0.0
    assert similarity(P("Next stage in BPF", a), P("Next stage in BPF (continued)", b)) == 0.0
    assert similarity(P("Totally different", a), P("Another title", a)) == 1.0  # same text, new title


def test_cloudflare_image_used_first(monkeypatch):
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (1024, 1024), "purple").save(buf, "JPEG")
    sent = {}

    class R:
        status_code = 200
        def json(self):
            return {"success": True, "result": {"image": base64.b64encode(buf.getvalue()).decode()}}

    def post(url, headers, json, timeout):
        sent.update(url=url, auth=headers["Authorization"], prompt=json["prompt"])
        return R()
    import requests
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setenv("CF_ACCOUNT_ID", "acc123")
    monkeypatch.setenv("CF_API_TOKEN", "tok")
    monkeypatch.setattr(gemini, "image", lambda *a, **k: (_ for _ in ()).throw(AssertionError("gemini should not be called")))
    data = images.ai_image("data bridge")
    im = Image.open(io.BytesIO(data))
    assert im.size == (1024, 576)  # cropped to 16:9
    assert "accounts/acc123/ai/run/@cf/black-forest-labs/flux-1-schnell" in sent["url"] and sent["auth"] == "Bearer tok"
    assert "no text" in sent["prompt"].lower() or "no text" in sent["prompt"]


def test_repair_trims_long_meta_instead_of_failing():
    from agent import quality
    a = fake_article()
    a.meta_description = ("Show live OData data in Power Apps without copying it into Dataverse. Set up a virtual table "
                          "step by step with the OData v4 provider, plus the limits you should know.")
    assert len(a.meta_description) > 158
    a.category = "power apps"
    assert quality.check(a, ["Power Apps"], "http://x") == []
    assert len(a.meta_description) <= 155 and a.meta_description.endswith(".") and a.category == "Power Apps"


def test_audit_lists_duplicates_and_low_value_but_protects_google_posts(env, tmp_path, monkeypatch):
    from agent import audit
    from agent.audit import Verdict, Verdicts
    FakeWP.posts.append({**make_posts()[2], "id": 5, "slug": "event-recap", "title": {"raw": "We attended a conference"},
                         "content": {"raw": "<p>" + "Nice event with friends and food. " * 20 + "</p>"}})
    FakeWP.posts.append({**make_posts()[2], "id": 6, "slug": "status-reason", "title": {"raw": "Old off-topic but famous"},
                         "content": {"raw": "<p>" + "Off topic text here. " * 30 + "</p>"}})
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path / "state")
    prio = tmp_path / "p.txt"
    prio.write_text("status-reason  # 313 impressions, 8 clicks\n")
    monkeypatch.setattr(audit, "PRIORITY_FILE", prio)
    monkeypatch.setattr(gemini, "structured", lambda prompt, schema, **k: Verdicts(items=[
        Verdict(id=5, verdict="delete", reason="Event recap, no lasting value"),
        Verdict(id=6, verdict="delete", reason="Off-topic")]))
    text = main.main(["audit"]) or (tmp_path / "state/audit.md").read_text()
    rows = {int(r["id"]): r for r in __import__("csv").DictReader(open(tmp_path / "state/audit.csv"))}
    assert rows[2]["verdict"] == "delete" and "Duplicate of #1" in rows[2]["reason"]
    assert rows[5]["verdict"] == "delete"
    assert rows[6]["verdict"] == "keep" and "313 impressions" in rows[6]["reason"]
    assert rows[1]["verdict"] == "keep" and rows[4]["verdict"] == "delete" and "Almost empty" in rows[4]["reason"]
    assert "post.php?post=2&action=edit" in (tmp_path / "state/audit.md").read_text()


def test_audit_draft_moves_flagged_and_agent_skips_them(env, tmp_path, monkeypatch):
    from agent import audit
    from agent.audit import Verdicts
    for p in FakeWP.posts:
        p["status"] = "publish"
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(gemini, "structured", lambda prompt, schema, **k: Verdicts(items=[]) if schema is Verdicts else fake_article())
    main.main(["audit", "--draft"])
    drafted = {pid for pid, d in FakeWP.updates if d == {"status": "draft"}}
    assert 2 in drafted and 4 in drafted  # duplicate + nearly empty post
    st = json.loads((tmp_path / "state/progress.json").read_text())
    assert st["posts"]["2"]["status"] == "flagged"
    # published posts are now in the agent's queue, flagged ones are not
    main.main(["run", "--count", "3"])
    rewritten = [pid for pid, d in FakeWP.updates if "content" in d]
    assert set(rewritten) == {1} and FakeWP.posts[1]["status"] == "draft"  # 2 duplicate and 3 near-empty stay flagged


def test_restore_dates_puts_posts_back_on_google_urls(env, tmp_path, monkeypatch):
    from agent import restore
    for p in FakeWP.posts:
        p["status"], p["date"] = "publish", "2026-11-18T09:30:00"
    FakeWP.posts[0]["slug"] = "virtual-tables-guide-3"  # WordPress added a suffix; the real -guide-2 post still exists
    FakeWP.posts[1]["slug"] = "virtual-tables-guide-2"
    f = tmp_path / "orig.txt"
    f.write_text("# header\n"
                 "status-reason | 2024-06-09 | http://x/2024/06/09/status-reason/ | 313\n"
                 "published-post | 2026-11-18 | http://x/2026/11/18/published-post/ | 5\n"
                 "gone-post | 2023-01-01 | http://x/2023/01/01/gone-post/ | 9\n")
    monkeypatch.setattr(restore, "FILE", f)
    monkeypatch.setattr(restore, "STATE_DIR", tmp_path / "state")
    assert main.main(["restore-dates", "--dry-run"]) == 0 and not FakeWP.updates
    assert main.main(["restore-dates"]) == 0
    assert FakeWP.updates == [(3, {"date": "2024-06-09T09:30:00"})]
    rep = (tmp_path / "state/restore-urls.md").read_text()
    assert "Moved back to the Google URL: **1**" in rep and "Already on the right URL: **1**" in rep and "gone-post" in rep


def test_draft_thin_moves_short_unrewritten_posts_and_agent_republishes_them(env, tmp_path, monkeypatch):
    from agent import restore, thin
    for p in FakeWP.posts:
        p["status"] = "publish"
    FakeWP.posts[2]["content"]["raw"] = "<p>" + "short words here " * 30 + "</p>"  # post 3: 90 words
    st = tmp_path / "state/progress.json"
    st.parent.mkdir(parents=True)
    st.write_text(json.dumps({"posts": {"4": {"status": "published"}}}))  # already rewritten: never touched
    f = tmp_path / "o.txt"
    f.write_text("status-reason | 2024-06-08 | http://x/2024/06/08/status-reason/ | 313\n")
    monkeypatch.setattr(restore, "FILE", f)
    monkeypatch.setattr(thin, "STATE_DIR", tmp_path / "state")
    main.main(["draft-thin", "--max-words", "100"])
    drafted = [pid for pid, d in FakeWP.updates if d == {"status": "draft"}]
    assert drafted == [3]  # 1 and 2 are long, 4 was rewritten
    assert "| 3 | 90 | 313 |" in (tmp_path / "state/thin-drafted.md").read_text()
    main.main(["run"])
    pid, data = FakeWP.updates[-1]
    assert pid == 3 and data["status"] == "publish" and data["date"] == "2024-06-08T10:00:00"


def test_new_post_from_microsoft_feed(env, tmp_path, monkeypatch):
    from agent import newpost
    from agent.newpost import Pick
    monkeypatch.setattr(newpost, "USED_FILE", tmp_path / "state/new_posts.json")
    monkeypatch.setattr(newpost, "STATE_DIR", tmp_path / "state")
    items = [{"title": "Announcing GA of Server Logic in Power Pages", "link": "https://www.microsoft.com/en-us/power-platform/blog/server-logic/",
              "date": "2026-11-10", "categories": "Power Pages", "summary": "Server logic is now GA."},
             {"title": "Customer story: Contoso", "link": "https://www.microsoft.com/x/contoso/", "date": "2026-11-09",
              "categories": "Power Apps", "summary": "story"}]
    monkeypatch.setattr(newpost, "feed_items", lambda days: list(items))
    monkeypatch.setattr(newpost, "article_text", lambda url: "Server logic lets you run JavaScript on the server in Power Pages. " * 20)
    seen = {}
    real = gemini.structured

    def fake(prompt, schema, **k):
        if schema is Pick:
            seen["pick_prompt"] = prompt
            return Pick(index=0, working_title="How to Use Server Logic in Power Pages (Step by Step)",
                        focus_keyword="power pages server logic", angle="Validate a form on the server")
        seen["write_prompt"] = prompt
        return real(prompt, schema, **k)
    monkeypatch.setattr(gemini, "structured", fake)
    assert main.main(["new", "--count", "1"]) == 0
    assert "Dirty fields in JavaScript" in seen["pick_prompt"]  # existing titles are given to avoid repeats
    assert "This is a NEW post" in seen["write_prompt"] and "do not copy" in seen["write_prompt"].lower()
    created = [d for k, d in FakeWP.updates if k == "create"][0]
    assert created["status"] == "draft"
    pid, data = FakeWP.updates[-1]
    assert data["status"] == "publish" and "date" not in data
    assert "https://www.microsoft.com/en-us/power-platform/blog/server-logic/" in data["content"]  # source in References
    used = json.loads((tmp_path / "state/new_posts.json").read_text())
    assert items[0]["link"] in used["used"]
    # the same announcement is never used twice
    main.main(["new", "--count", "1"])
    assert len([1 for k, _ in FakeWP.updates if k == "create"]) == 2 or "Contoso" in seen["pick_prompt"]
    assert "Server Logic in Power Pages" not in seen["pick_prompt"].split("EXISTING POST TITLES")[0]
