"""Local inference against Ollama, over plain HTTP.

Deliberately dependency-free: `urllib` from the standard library, no SDK. P2
calls a local model a first-class configuration, and a first-class path should
not be the one that needs an extra install — the cloud path is the one that
does (`pip install 'jobd-ai[llm]'`).

`is_local` is True, and the only host this module will talk to is the one in
`base_url`, which defaults to loopback. M5 gate 5 asserts no outbound call
happens on this path; that assertion is checking a property of this file.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
DEFAULT_MODEL = "llama3.1"
DEFAULT_EMBED_MODEL = "nomic-embed-text"

#: nomic-embed-text is 768-wide; the `message.embedding` column is 1536. See
#: docs/schema.md — changing embedder width is a migration, and this constant is
#: where that bites.
EMBED_DIMENSIONS = 768


class OllamaError(RuntimeError):
    """Raised when the local model is unreachable or returns nonsense."""


class OllamaProvider:
    """One local model, reached over HTTP."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        base_url: str = DEFAULT_BASE_URL,
        embed_model: str = DEFAULT_EMBED_MODEL,
        timeout: float = 120.0,
    ) -> None:
        self._model = model
        self._base = base_url.rstrip("/")
        self._embed_model = embed_model
        self._timeout = timeout

    @property
    def name(self) -> str:
        return f"ollama/{self._model}"

    @property
    def is_local(self) -> bool:
        """True — and `base_url` is the only host this provider contacts."""
        return True

    def extract(
        self, prompt: str, schema: dict[str, Any], *, text: str
    ) -> dict[str, Any]:
        """Structured extraction, with the schema enforced by Ollama itself.

        Ollama's `format` accepts a JSON Schema and constrains decoding to it,
        so a malformed object is not something to defend against downstream —
        it cannot be generated. `temperature: 0` because extraction is not a
        creative task and a re-derive should reproduce the same record (I3).
        """
        payload = {
            "model": self._model,
            "prompt": f"{prompt}\n\n---\n{text}\n---\n",
            "format": schema,
            "stream": False,
            "options": {"temperature": 0},
        }
        response = self._post("/api/generate", payload)
        try:
            parsed: dict[str, Any] = json.loads(response["response"])
        except (KeyError, json.JSONDecodeError) as exc:
            raise OllamaError(
                f"{self.name} returned unparseable output: {exc}"
            ) from exc
        return parsed

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch. One request per text — Ollama's batch API is newer
        than the version many people are running, and a 404 here is a worse
        failure than a slower loop."""
        vectors: list[list[float]] = []
        for text in texts:
            response = self._post(
                "/api/embeddings", {"model": self._embed_model, "prompt": text}
            )
            vectors.append([float(v) for v in response["embedding"]])
        return vectors

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._base}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as handle:
                body: dict[str, Any] = json.loads(handle.read())
                return body
        except urllib.error.URLError as exc:
            raise OllamaError(
                f"Cannot reach Ollama at {self._base} ({exc.reason}). Start it "
                f"with `ollama serve`, then `ollama pull {self._model}`."
            ) from exc
