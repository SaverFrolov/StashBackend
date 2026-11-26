"""Интеграционный тест /ask. Запускается только при явном TEST_DATABASE_DSN."""
import os
import time

import pytest

DSN = os.environ.get("TEST_DATABASE_DSN", "")
if not DSN:
    pytest.skip(
        "TEST_DATABASE_DSN не задан — интеграция /ask пропущена",
        allow_module_level=True,
    )
os.environ["DATABASE_DSN"] = DSN

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture
def auth():
    try:
        r = client.post(
            "/auth/signup",
            json={
                "name": "Ask",
                "email": f"ask{time.time_ns()}@t.local",
                "password": "ask-test-123",
            },
        )
    except Exception as e:  # БД недоступна — не валим suite
        pytest.skip(f"БД недоступна: {e}")
    if r.status_code != 200:
        pytest.skip(f"signup недоступен: {r.status_code}")
    return r.json()["token"]


def test_empty_q_rejected(auth):
    r = client.post("/ask", json={"q": "   "}, headers={"Authorization": f"Bearer {auth}"})
    assert r.status_code == 422


def test_empty_archive_answers_without_sources(auth):
    r = client.post(
        "/ask",
        json={"q": "как приготовить пирожки"},
        headers={"Authorization": f"Bearer {auth}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"answer", "sources"}
    assert isinstance(body["sources"], list)


def test_requires_auth():
    assert client.post("/ask", json={"q": "тест"}).status_code in (401, 403)
