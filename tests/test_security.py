import logging

import pytest

from app.main import _FileSignatureFilter
from app.pipeline.extract import check_public_url
from app.ratelimit import RateLimiter


def test_access_log_hides_file_signatures():
    f = _FileSignatureFilter()
    rec = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "GET", "/files/abc?u=x&exp=1&sig=deadbeef", "1.1", 200),
        None,
    )
    assert f.filter(rec)
    assert rec.args[2] == "/files/abc?<signed>"
    other = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "GET", "/items?limit=20", "1.1", 200),
        None,
    )
    f.filter(other)
    assert other.args[2] == "/items?limit=20"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/health",
        "http://192.168.1.1/",
        "http://10.0.0.5/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "ftp://example.com/",
        "http://",
        "file:///etc/passwd",
    ],
)
def test_ssrf_guard_rejects(url):
    with pytest.raises(ValueError):
        check_public_url(url)


def test_ssrf_guard_accepts_public_ip_literal():
    check_public_url("https://93.184.216.34/")


def test_rate_limiter_window(monkeypatch):
    lim = RateLimiter(max_attempts=3, window_seconds=60)
    now = [1000.0]
    monkeypatch.setattr("app.ratelimit.time.monotonic", lambda: now[0])
    assert lim.allow("k")
    assert lim.allow("k")
    assert lim.allow("k")
    assert not lim.allow("k")
    assert lim.allow("other")
    now[0] += 61
    assert lim.allow("k")
