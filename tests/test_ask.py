from app.pipeline.ask import build_context, cited_sources


def _row(i, chunk=0, content="тело", title="Материал"):
    return dict(item_id=f"id{i}", chunk_no=chunk, content=content, dist=0.3,
                title=title, type="text", source={"url": f"https://x/{i}"})


def test_context_numbers_and_cap():
    ctx, src = build_context([_row(1), _row(2)], max_chars=10_000)
    assert ctx.startswith("[1] «Материал»") and "[2]" in ctx
    assert [s["n"] for s in src] == [1, 2]
    assert src[0]["url"] == "https://x/1"


def test_context_stops_at_budget():
    rows = [_row(i, content="я" * 800) for i in range(20)]
    ctx, src = build_context(rows, max_chars=2000, per_chunk=800)
    assert len(src) <= 3
    assert sum(len(s["title"]) for s in src) > 0


def test_source_json_string_tolerated():
    r = _row(1); r["source"] = '{"url": "https://y/1"}'
    _, src = build_context([r])
    assert src[0]["url"] == "https://y/1"


def test_citations_filter_and_fallback():
    src = [{"n": 1}, {"n": 2}, {"n": 3}]
    assert cited_sources("ответ [2]", src) == [{"n": 2}]
    assert len(cited_sources("ответ без ссылок", src)) == 3


def test_resolve_key_and_availability(monkeypatch):
    from app.config import cfg
    from app.pipeline.ai import ai_available, resolve_key

    monkeypatch.setattr(cfg, "default_ai_key", "sk-owner", raising=False)
    own = {"provider": "openai", "api_key_enc": None}
    assert resolve_key(own) == "sk-owner"          # owner fallback
    assert ai_available(own) is True
    assert ai_available({"provider": "ollama", "api_key_enc": None}) is True
    assert ai_available(None) is False
    monkeypatch.setattr(cfg, "default_ai_key", "", raising=False)
    assert resolve_key(own) is None
    assert ai_available(own) is False


def test_sources_deduped_per_item():
    from app.pipeline.ask import dedupe_sources
    picked = [
        {"n": 1, "itemId": "a", "chunkNo": 0, "title": "A"},
        {"n": 2, "itemId": "a", "chunkNo": 3, "title": "A"},
        {"n": 3, "itemId": "b", "chunkNo": 1, "title": "B"},
    ]
    out = dedupe_sources(picked)
    assert [s["itemId"] for s in out] == ["a", "b"]
    assert out[0]["chunks"] == [0, 3]


def test_narrow_cuts_noise_after_strong_hit():
    from app.pipeline.ask import narrow
    rows = [{"dist": d} for d in (0.251, 0.503, 0.514, 0.523)]
    assert [r["dist"] for r in narrow(rows)] == [0.251]


def test_narrow_keeps_whole_relevant_cluster():
    from app.pipeline.ask import narrow
    rows = [{"dist": d} for d in (0.425, 0.432, 0.469, 0.475)]
    assert len(narrow(rows)) == 4
