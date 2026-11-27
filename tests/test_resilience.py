import uuid

import httpx

from app.pipeline.ai import _is_transient
from app.pipeline import queue


def _resp(code):
    return httpx.Response(code, request=httpx.Request("POST", "https://x"))


def test_transient_vs_permanent():
    assert _is_transient(httpx.ReadTimeout("t"))
    assert _is_transient(httpx.ConnectError("c"))
    assert _is_transient(httpx.HTTPStatusError("r", request=_resp(429).request, response=_resp(429)))
    assert not _is_transient(httpx.HTTPStatusError("r", request=_resp(401).request, response=_resp(401)))
    assert not _is_transient(ValueError("bad json"))


def test_duplicate_enqueue_collapsed():
    iid, uid = uuid.uuid4(), uuid.uuid4()
    before = queue._queues[queue.LIGHT].qsize()
    queue.enqueue(iid, uid, ("extract",), queue.LIGHT)
    queue.enqueue(iid, uid, ("extract",), queue.LIGHT)
    queue.enqueue(iid, uid, ("extract",), queue.HEAVY)
    assert queue._queues[queue.LIGHT].qsize() == before + 1
    queue._pending[queue.LIGHT].discard(str(iid))
