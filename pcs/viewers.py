"""Who may read the dashboard.

A tiny credential store, stdlib only. It replaces `htpasswd`/`.htpasswd` for
two reasons: the auth service needs to verify a password from Python, and
`htpasswd -c` silently truncates the file it is pointed at, which is a
destructive footgun sitting one typo away from locking everyone out.

Format, one line per viewer:

    username:pbkdf2_sha256$<iterations>$<b64 salt>$<b64 hash>

PBKDF2-HMAC-SHA256 at OWASP's 2023 iteration count. The cost is the point: a
verify takes a noticeable fraction of a second, which is what makes online
guessing expensive. It is also why the file is never consulted on a request
that already carries a valid session cookie.

Nothing here is reversible. A lost password is rotated, not recovered.
"""

from __future__ import annotations

import base64
import hmac
import os
import re
from dataclasses import dataclass
from pathlib import Path

ITERATIONS = 600_000
ALGO = "pbkdf2_sha256"

# Colons separate the fields and whitespace would not survive a round trip.
_VALID_NAME = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")


# Verified against when the username is unknown, so a bad name costs exactly as
# much as a bad password. It must carry the real iteration count: a cheap dummy
# would make "no such user" measurably faster and hand out valid usernames.
# The password behind it was random and discarded.
_DUMMY_PHC = ("pbkdf2_sha256$600000$3YA13rZlzyHxZx9pyckVJw==$luwMePBFR5DjXYnhjzOllqJWB8qou9z04SiqyWaBPL4=")


class ViewerError(Exception):
    """A caller mistake worth showing to a person, not a stack trace."""


@dataclass(frozen=True)
class Viewer:
    name: str
    phc: str


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str, *, iterations: int = ITERATIONS,
                  salt: bytes | None = None) -> str:
    import hashlib
    if not password:
        raise ViewerError("a password is required")
    salt = os.urandom(16) if salt is None else salt
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGO}${iterations}${_b64(salt)}${_b64(dk)}"


def verify(password: str, phc: str) -> bool:
    """Constant-time check of `password` against a stored hash.

    Returns False on a malformed record rather than raising: a corrupt line in
    the file must fail the login, not take the whole service down.
    """
    import hashlib
    try:
        algo, iters, salt_b64, hash_b64 = phc.split("$")
        if algo != ALGO:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 base64.b64decode(salt_b64), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(_b64(dk), hash_b64)


def load(path: Path) -> list[Viewer]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, _, phc = line.partition(":")
        out.append(Viewer(name, phc))
    return out


def save(path: Path, viewers: list[Viewer]) -> None:
    """Written atomically and readable only by owner and group.

    The mode is set on the temp file before any content reaches it -- writing
    first and chmod'ing after leaves a window where the hashes are world
    readable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("# dashboard logins -- pcs viewers. One per line.\n")
        for v in viewers:
            fh.write(f"{v.name}:{v.phc}\n")
    tmp.replace(path)


def check_name(name: str) -> str:
    if not _VALID_NAME.match(name or ""):
        raise ViewerError("a username may use letters, digits, dot, dash, "
                          "underscore and @, up to 64 characters")
    return name


def add(path: Path, name: str, password: str) -> bool:
    """Add or rotate. Returns True when the viewer already existed."""
    check_name(name)
    viewers = load(path)
    phc = hash_password(password)
    existed = any(v.name == name for v in viewers)
    viewers = [v for v in viewers if v.name != name] + [Viewer(name, phc)]
    save(path, viewers)
    return existed


def remove(path: Path, name: str) -> None:
    viewers = load(path)
    if not any(v.name == name for v in viewers):
        raise ViewerError(f"no login named {name!r}")
    if len(viewers) == 1:
        raise ViewerError(f"{name!r} is the only login left. Add another first, "
                          f"or nobody will be able to reach the dashboard.")
    save(path, [v for v in viewers if v.name != name])


def authenticate(path: Path, name: str, password: str) -> bool:
    """Verify a login attempt.

    An unknown username is checked against a dummy hash anyway. Returning
    early would make "no such user" measurably faster than "wrong password",
    which hands an attacker a list of valid usernames for free.
    """
    match = next((v for v in load(path) if v.name == name), None)
    ok = verify(password, match.phc if match else _DUMMY_PHC)
    return bool(match) and ok


def generate_password() -> str:
    """Printed once, never stored. Ambiguous glyphs are dropped so it survives
    being read down a phone or retyped from a screenshot."""
    import secrets
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))
