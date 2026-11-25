#!/usr/bin/env python3
"""E2E smoke против живого Stash API: python scripts/smoke.py [base_url]

Создаёт временного пользователя, прогоняет весь контракт RestApi и удаляет аккаунт.
"""
import base64
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
TOKEN = None
FAILED = []


def req(method, path, body=None, token=None, headers=None, raw=False):
    h = dict(headers or {})
    # Cloudflare Browser Integrity Check blocks the default python-urllib UA.
    h.setdefault("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) StashSmoke/1.0")
    if body is not None:
        h["Content-Type"] = "application/json"
    t = token if token is not None else TOKEN
    if t:
        h["Authorization"] = f"Bearer {t}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            content = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            if raw:
                return resp.status, content, headers
            parsed = json.loads(content) if content and resp.status != 204 else None
            return resp.status, parsed, headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300], {}


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if not cond else ""))
    if not cond:
        FAILED.append(name)


def main():
    global TOKEN
    email = f"smoke-{int(time.time())}@test.local"

    s, b, _ = req("GET", "/health")
    check("health", s == 200 and b.get("ok") is True, str(b))

    s, b, _ = req("POST", "/auth/signup", {"name": "Smoke", "email": email, "password": "smoke-pass-123"})
    check("signup", s == 200 and "token" in b and b["user"]["email"] == email, str(b))
    TOKEN = b["token"]

    s, b, _ = req("POST", "/auth/signin", {"email": email.upper(), "password": "smoke-pass-123"})
    check("signin (citext)", s == 200 and "token" in b, str(b))
    s, b, _ = req("POST", "/auth/signin", {"email": email, "password": "wrong"})
    check("signin wrong password -> 401", s == 401)
    s, _, _ = req("GET", "/items", token="bad-token")
    check("bad token -> 401", s == 401)

    s, b, _ = req("GET", "/settings/ai")
    check("ai defaults", s == 200 and b["model"] == "deepseek/deepseek-v4.1-flash"
          and b["baseUrl"] == "https://ru.airouter.host/v1" and b["apiKeySet"] is False, str(b))

    s, b, _ = req("GET", "/items")
    check("empty feed", s == 200 and b == {"items": [], "hasMore": False, "total": 0}, str(b))

    s, note, _ = req("POST", "/items/text", {"text": "Первая заметка про поиск и архив", "comment": "коммент"})
    check("save text", s == 200 and note["type"] == "text" and note["lang"] == "ru"
          and note["original"]["text"].startswith("Первая"), str(note))
    s, link, _ = req("POST", "/items/text", {"text": "https://example.com посмотри"})
    check("single URL becomes link", s == 200 and link["type"] == "link"
          and link["source"]["url"] == "https://example.com", str(link))

    s, b, _ = req("GET", "/items?limit=10")
    check("feed total=2 asc", s == 200 and b["total"] == 2
          and b["items"][0]["capturedAt"] <= b["items"][1]["capturedAt"], str(b))
    s, b, _ = req("GET", f"/items?cursor={b['items'][0]['capturedAt']}&limit=10")
    check("cursor page empty", s == 200 and b["items"] == [] and b["hasMore"] is False, str(b))

    s, b, _ = req("GET", f"/items/{note['id']}")
    check("get item", s == 200 and b["id"] == note["id"])
    s, b, _ = req("PATCH", f"/items/{note['id']}", {"title": "Новый заголовок", "userTags": ["важное"], "userComment": "обновлено"})
    check("patch item", s == 200 and b["title"] == "Новый заголовок"
          and b["userTags"] == ["важное"] and b["userComment"] == "обновлено", str(b))

    s, col, _ = req("POST", "/collections", {"name": "Тест", "description": "описание"})
    check("create collection", s == 200 and col["name"] == "Тест", str(col))
    s, _, _ = req("POST", f"/collections/{col['id']}/items", {"itemIds": [note["id"]]})
    check("add to collection", s == 204)
    s, b, _ = req("GET", f"/items/{note['id']}")
    check("collectionIds on item", s == 200 and b["collectionIds"] == [col["id"]])
    s, b, _ = req("POST", "/collections", {"name": "Тест"})
    check("duplicate collection -> 409", s == 409)

    s, tags, _ = req("GET", "/tags")
    check("user tag listed", s == 200 and any(t["name"] == "важное" and t["origin"] == "user" for t in tags), str(tags))
    tag_id = next(t["id"] for t in tags if t["name"] == "важное")
    s, b, _ = req("PATCH", f"/tags/{tag_id}", {"name": "срочное"})
    check("rename tag", s == 200 and b["name"] == "срочное")
    s, b, _ = req("GET", f"/items/{note['id']}")
    check("rename propagated", s == 200 and b["userTags"] == ["срочное"])

    s, b, _ = req("POST", "/search?q=" + urllib.parse.quote("заметка") + "&mode=keywords", {"mode": "keywords"})
    check("keyword search", s == 200 and isinstance(b, list) and any(r["item"]["id"] == note["id"] for r in b), str(b)[:200])
    s, b, _ = req("GET", "/search/recent")
    check("recent queries", s == 200 and "заметка" in b, str(b))

    s, b, _ = req("GET", "/items/attention-count")
    check("attention count", s == 200 and isinstance(b["count"], int))
    s, b, _ = req("GET", f"/items/{note['id']}/related")
    check("related is list", s == 200 and isinstance(b, list))

    s, b, _ = req("POST", "/export", {"scope": "all", "format": "json", "includeOriginals": True,
                                      "includeExtracted": True, "includeAi": True, "includeComments": True})
    check("export json", s == 200 and "items" in b and len(b["items"]) == 2, str(b)[:200])
    s, b, h = req("POST", "/export", {"scope": "collection", "collectionId": col["id"], "format": "markdown",
                                      "includeOriginals": True, "includeExtracted": True, "includeAi": True, "includeComments": True}, raw=True)
    check("export markdown", s == 200 and b.startswith(b"# ") and "text/markdown" in h.get("content-type", ""), str(b)[:100])
    s, b, _ = req("GET", "/usage")
    check("usage", s == 200 and b["bytesUsed"] == 0, str(b))

    s, b, _ = req("POST", "/api-tokens", {"name": "extension", "expiresInDays": 30})
    check("create api token", s == 200 and b["secret"].startswith("stash_") and b["token"]["name"] == "extension", str(b))
    secret = b["secret"]
    s, b, _ = req("GET", "/collections", token=secret)
    check("api token auth", s == 200 and len(b) == 1)
    s, b, _ = req("POST", "/items/link", {"url": "https://example.org/page"}, token=secret)
    check("extension save link", s == 200 and b["type"] == "link")
    s, b, _ = req("GET", "/api-tokens")
    tok_id = b[0]["id"]
    s, _, _ = req("DELETE", f"/api-tokens/{tok_id}")
    check("revoke token", s == 204)
    s, _, _ = req("GET", "/collections", token=secret)
    check("revoked token -> 401", s == 401)

    pdf = b"%PDF-1.4 smoke test file content for range requests 0123456789"
    data_url = "data:application/pdf;base64," + base64.b64encode(pdf).decode()
    s, fitem, _ = req("POST", "/items/file", {"file": {"uri": data_url, "name": "smoke.pdf", "mimeType": "application/pdf", "size": len(pdf)}})
    check("save file", s == 200 and fitem["type"] == "pdf" and fitem["original"]["file"]["size"] == len(pdf), str(fitem)[:200])
    file_url = fitem["original"]["file"]["url"]
    parts = urllib.parse.urlsplit(file_url)
    path = parts.path + ("?" + parts.query if parts.query else "")
    s, b, h = req("GET", path, raw=True)
    check("signed file download", s == 200 and b == pdf, f"{s} {b[:40]!r}")
    s, b, h = req("GET", path, headers={"Range": "bytes=0-3"}, raw=True)
    check("range request", s == 206 and b == pdf[:4] and h.get("content-range", "").startswith("bytes 0-3/"), f"{s} {b!r}")
    s, _, _ = req("GET", path.replace("sig=", "sig=0"), raw=True)
    check("tampered signature -> 403", s == 403)
    s, b, _ = req("GET", "/usage")
    check("usage counts file", s == 200 and b["bytesUsed"] == len(pdf), str(b))

    s, b, _ = req("PATCH", f"/items/{fitem['id']}", {"collectionIds": [col["id"]]})
    check("patch collections", s == 200 and b["collectionIds"] == [col["id"]])
    s, _, _ = req("DELETE", "/collections/" + col["id"] + "/items", {"itemIds": [fitem["id"]]})
    check("remove from collection", s == 204)
    s, _, _ = req("DELETE", "/items", {"ids": [fitem["id"], link["id"]]})
    check("bulk delete", s == 204)
    s, b, _ = req("GET", "/items")
    check("feed after delete", s == 200 and b["total"] == 2, str(b)[:100])

    s, _, _ = req("PATCH", "/account", {"name": "Smoke2"})
    check("update account", s == 204)
    s, _, _ = req("DELETE", "/account")
    check("delete account", s == 204)
    s, _, _ = req("GET", "/items")
    check("session dead after delete", s == 401)

    print(f"\n{len(FAILED)} failed" if FAILED else "\nALL PASS")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
