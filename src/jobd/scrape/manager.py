"""Single-flight scrape runner for the dashboard.

One scrape at a time, process-wide — two concurrent runs would fight over the
same frontier of Gmail quota and DB writes for zero benefit. The dashboard
POSTs start, then follows the emitter over SSE; the run itself executes on a
worker thread so the event loop never blocks.

Module-level singleton on purpose: the web app is one process, and "is a
scrape running" is a fact about the process. The CLI path doesn't use this at
all — a cron `jobd scrape` owns its whole process and needs no arbitration.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobd.scrape.events import Emitter


@dataclass
class ScrapeManager:
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _thread: threading.Thread | None = None
    emitter: Emitter | None = None
    started_at: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        *,
        accounts: list[str],
        window_days: int | None,
        model: str,
        bucket: str | None = None,
        local_store: Path | None = None,
    ) -> bool:
        """Begin a run; ``False`` if one is already going."""
        from jobd.scrape.service import run_scrape

        with self._lock:
            if self.running:
                return False
            emitter = Emitter()
            self.emitter = emitter
            self.started_at = datetime.now(UTC).isoformat()
            self.params = {
                "accounts": accounts,
                "window_days": window_days,
                "model": model,
            }
            self.result = None
            self.error = None

            def work() -> None:
                try:
                    self.result = run_scrape(
                        accounts=accounts,
                        window_days=window_days,
                        model=model,
                        bucket=bucket,
                        local_store=local_store,
                        emitter=emitter,
                    )
                except Exception as exc:  # surfaced via status + stream, not lost
                    self.error = f"{type(exc).__name__}: {exc}"
                    emitter.emit("error", message=self.error)
                    emitter.emit("done", counters={}, facts=[])

            self._thread = threading.Thread(target=work, name="scrape", daemon=True)
            self._thread.start()
            return True

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "params": self.params,
            "error": self.error,
            "result": self.result,
            "last_seq": self.emitter.last_seq if self.emitter else 0,
        }


#: The process-wide instance the web routes share.
MANAGER = ScrapeManager()
