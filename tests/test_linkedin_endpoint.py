"""The /api/linkedin/push edge: token auth, origin exemption, idempotent counts."""

from __future__ import annotations

import contextlib

import pytest
from fastapi.testclient import TestClient

from jobd.domain.keys import storage_key
from jobd.domain.raw import RawMessage
from jobd.web import api as api_module
from jobd.web.app import app

TOKEN = "test-token-123"


def _payload(**overrides):
    base = {
        "account": "doe-alex",
        "messages": [
            {
                "external_id": "urn:li:msg_message:2-MTc1",
                "conversation_id": "urn:li:msg_conversation:2-YZ",
                "direction": "inbound",
                "partner_id": "jane-recruiter",
                "partner_name": "Jane Recruiter",
                "text": "Are you open to a Staff role at Acme?",
                "sent_at": "2026-08-15T14:30:12+00:00",
            }
        ],
    }
    base.update(overrides)
    return base


class _MemoryStorage:
    def __init__(self):
        self.objects: dict[str, RawMessage] = {}

    def key_for(self, message):
        return storage_key(message)

    def exists(self, key):
        return key in self.objects

    def put(self, message):
        key = storage_key(message)
        self.objects.setdefault(key, message)
        return key


class _MemoryMessages:
    def __init__(self):
        self.rows = {}

    def by_storage_key(self, key):
        return self.rows.get(key)

    def add(self, message):
        self.rows[message.storage_key] = message
        return message

    def known_external_ids(self, channel, account, external_ids):
        return set()


class _Repos:
    def __init__(self, messages):
        self.messages = messages


class _Conn:
    def commit(self):
        pass


@pytest.fixture
def client(monkeypatch):
    storage, messages = _MemoryStorage(), _MemoryMessages()
    monkeypatch.setenv("JOBD_LINKEDIN_TOKEN", TOKEN)
    monkeypatch.setattr(api_module, "_storage", lambda: storage)
    monkeypatch.setattr(api_module, "_repos", lambda conn: _Repos(messages))

    @contextlib.contextmanager
    def fake_connect():
        yield _Conn()

    monkeypatch.setattr(api_module, "_connect", fake_connect)
    test_client = TestClient(app)
    test_client.storage = storage
    test_client.messages = messages
    return test_client


def _post(client, json, token=TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/linkedin/push", json=json, headers=headers)


def test_unconfigured_token_is_503(client, monkeypatch):
    monkeypatch.delenv("JOBD_LINKEDIN_TOKEN")
    assert _post(client, _payload()).status_code == 503


def test_wrong_token_is_401(client):
    assert _post(client, _payload(), token="nope").status_code == 401


def test_missing_token_is_401(client):
    assert _post(client, _payload(), token=None).status_code == 401


def test_push_ingests_and_reports_counts(client):
    response = _post(client, _payload())
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 1
    assert body["stored"] == 1
    assert body["rows_inserted"] == 1
    assert body["errors"] == []
    (row,) = client.messages.rows.values()
    assert row.channel == "linkedin"
    assert row.account == "doe-alex@linkedin.invalid"


def test_second_push_is_noop(client):
    _post(client, _payload())
    body = _post(client, _payload()).json()
    assert body["stored"] == 0
    assert body["already_stored"] == 1
    assert body["rows_existing"] == 1


def test_bad_direction_reported_not_fatal(client):
    payload = _payload()
    payload["messages"][0]["direction"] = "sideways"
    body = _post(client, payload).json()
    assert body["rows_inserted"] == 0
    assert len(body["errors"]) == 1


def test_extension_origin_passes_middleware(client):
    # chrome-extension:// origin can never contain the Host header value; the
    # push path must be exempt from the same-origin write check because the
    # bearer token is its auth. Other write paths stay covered.
    response = client.post(
        "/api/linkedin/push",
        json=_payload(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Origin": "chrome-extension://abcdefg",
        },
    )
    assert response.status_code == 200


def test_other_writes_still_origin_checked(client):
    response = client.post(
        "/api/message/00000000-0000-0000-0000-000000000000/reply/suggest",
        json={},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
