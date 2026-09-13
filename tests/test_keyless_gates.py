"""The two gates a keyless deploy (the Codespaces demo) must close cleanly.

Live-caught pair: with no OPENROUTER_API_KEY, the compose default for
JOBD_CHAT_MODEL made chat and summaries *look* configured and both surfaced
a raw 401 AuthenticationError; clicking Sync on the demo record recorded a
failed run over mail that never existed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from jobd.config.profile import Profile, load_settings
from jobd.services.demo import DEMO_ACCOUNT
from jobd.web.app import app


def test_openrouter_chat_model_without_key_is_unconfigured():
    settings = load_settings(
        {"JOBD_CHAT_MODEL": "openrouter/google/gemini-3.7-flash"}
    )
    assert settings.chat_model is None


def test_blank_key_counts_as_absent():
    settings = load_settings(
        {
            "JOBD_CHAT_MODEL": "openrouter/google/gemini-3.7-flash",
            "OPENROUTER_API_KEY": "   ",
        }
    )
    assert settings.chat_model is None


def test_openrouter_chat_model_with_key_stays_configured():
    settings = load_settings(
        {
            "JOBD_CHAT_MODEL": "openrouter/google/gemini-3.7-flash",
            "OPENROUTER_API_KEY": "sk-or-v1-test",
        }
    )
    assert settings.chat_model == "openrouter/google/gemini-3.7-flash"


def test_non_openrouter_chat_model_needs_no_key():
    settings = load_settings({"JOBD_CHAT_MODEL": "ollama/qwen3:8b"})
    assert settings.chat_model == "ollama/qwen3:8b"
    assert settings.profile is Profile.LOCAL


def test_scrape_start_refuses_demo_only_accounts():
    client = TestClient(app)
    resp = client.post(
        "/api/scrape/start",
        json={"accounts": [DEMO_ACCOUNT]},
        headers={"Origin": "http://testserver", "Host": "testserver"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "synthetic demo corpus" in body["message"]
