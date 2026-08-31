"""Where OAuth tokens live (PRD §7, M4 gate 4).

Two requirements, in priority order:

1. **Never in the repo and never in Postgres.** Both are absolute. The repo gets
   pushed; Postgres gets dumped, backed up, and rebuilt.
2. **OS secret storage when the OS has one.** macOS Keychain, GNOME Keyring,
   Windows Credential Locker — reached through `keyring`.

Requirement 2 cannot always be met. A headless Linux container has no Secret
Service, and `keyring` there resolves to a backend that raises on every call.
Pretending otherwise would mean the wizard fails on the machine this project is
actually developed in.

So there is a fallback: a 0600 file under `~/.jobd/secrets/`, which is the
mounted volume — outside the workspace, outside git, outside the database.
That is weaker than a keychain and it is labelled as such: :attr:`TokenStore
.backend` reports which one is in use, `jobd config show` prints it, and the
wizard says so out loud before it writes anything. An accepted risk that
announces itself is a different thing from a silent downgrade. See
SECURITY.md §3.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

import keyring
from keyring.errors import KeyringError

SERVICE = "jobd-ai"

#: Fallback location. `~/.jobd` is the persistent volume in the devcontainer, so
#: this survives a rebuild while staying out of the workspace.
FALLBACK_DIR = Path(os.environ.get("JOBD_HOME", "~/.jobd")).expanduser() / "secrets"


def _keyring_works() -> bool:
    """True when this machine has a usable secret store.

    Probed by writing and deleting, not by inspecting the backend class: the
    class can look fine and still fail at the D-Bus call.
    """
    try:
        keyring.set_password(SERVICE, "__probe__", "1")
        keyring.delete_password(SERVICE, "__probe__")
    except Exception:
        # Deliberately bare. Backends raise KeyringError, D-Bus errors, and
        # OSError depending on the platform, and any of them means the same
        # thing here: there is no usable keyring.
        return False
    return True


class TokenStore:
    """Stores one JSON credential per account.

    Args:
        prefer_keyring: Set False to force the file backend. Tests use it; so
            can a user who does not want jobd touching their keychain.
    """

    def __init__(
        self, *, prefer_keyring: bool = True, directory: Path | None = None
    ) -> None:
        self._dir = directory or FALLBACK_DIR
        self._use_keyring = prefer_keyring and _keyring_works()

    @property
    def backend(self) -> str:
        """Human-readable name of the backend actually in use."""
        if self._use_keyring:
            return f"keyring ({type(keyring.get_keyring()).__name__})"
        return f"file {self._dir} (0600, no OS keyring on this machine)"

    @property
    def is_os_keyring(self) -> bool:
        """False when running on the weaker file fallback."""
        return self._use_keyring

    def _path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_.@" else "_" for c in key)
        return self._dir / f"{safe}.json"

    def save(self, key: str, payload: dict[str, Any]) -> None:
        """Store a credential. Overwrites any previous one for this key."""
        blob = json.dumps(payload, sort_keys=True)
        if self._use_keyring:
            keyring.set_password(SERVICE, key, blob)
            return

        self._dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)
        path = self._path(key)
        # Create with 0600 from the start. Writing then chmod-ing leaves a
        # window where the token is world-readable, which is the whole point of
        # the file being 0600 in the first place.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(blob)

    def load(self, key: str) -> dict[str, Any] | None:
        """Return a stored credential, or None."""
        if self._use_keyring:
            blob = keyring.get_password(SERVICE, key)
        else:
            path = self._path(key)
            blob = path.read_text() if path.is_file() else None
        if blob is None:
            return None
        loaded: dict[str, Any] = json.loads(blob)
        return loaded

    def delete(self, key: str) -> None:
        """Remove a credential. Silent if it was not there."""
        if self._use_keyring:
            with contextlib.suppress(KeyringError):
                keyring.delete_password(SERVICE, key)
            return
        self._path(key).unlink(missing_ok=True)

    def accounts(self) -> list[str]:
        """Known credential keys.

        Only the file backend can enumerate: `keyring` has no portable listing
        API. On a keychain, the account list comes from config instead — which
        is why `jobd ingest gmail` takes explicit `--account` values rather than
        discovering them here.
        """
        if self._use_keyring or not self._dir.is_dir():
            return []
        # Reverse the _path transformation: the first underscore after an alphabetic
        # prefix (like "gmail") should be restored to a colon
        keys = []
        for p in self._dir.glob("*.json"):
            stem = p.stem
            # Restore first underscore to colon (e.g., "gmail_user@x.com" → "gmail:user@x.com")
            if "_" in stem:
                prefix, rest = stem.split("_", 1)
                if prefix.isalpha():  # Only restore if prefix is alphabetic (like "gmail")
                    keys.append(f"{prefix}:{rest}")
                else:
                    keys.append(stem)
            else:
                keys.append(stem)
        return sorted(keys)
