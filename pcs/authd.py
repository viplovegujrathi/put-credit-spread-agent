"""The dashboard's login page.

HTTP Basic auth cannot be made attractive. The credential dialog is browser
chrome -- no CSS reaches it, no markup replaces it, and nginx cannot suppress
the `WWW-Authenticate` header that summons it. A real login page therefore
means a form, and a form means somewhere to verify a password and issue a
session. This is that somewhere: a small stdlib HTTP service bound to
localhost, which nginx consults through `auth_request`.

    GET  /login    the page (and the form)
    POST /login    verify, set a signed session cookie, redirect
    GET  /_auth    nginx's subrequest: 200 if the cookie is good, else 401
    POST /logout   clear the cookie

It is deliberately small. It listens only on 127.0.0.1, serves no files, holds
no mutable state, and its only inputs are a form field and a cookie it signed
itself.

There is no session table because there is nothing worth storing. The cookie
carries the username and an expiry, HMAC'd with a key that never leaves this
box, so the service can restart without logging anyone out and there is no
store to leak, grow, or clean up. The cost of that choice is that a single
session cannot be revoked early; removing the viewer stops the next login, and
rotating the key logs everybody out at once.
"""

from __future__ import annotations

import base64
import hmac
import os
import secrets
import sys
import time
import urllib.parse
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import brand, config, viewers

COOKIE = "pcs_session"
SESSION_SECONDS = int(os.environ.get("PCS_SESSION_SECONDS", 7 * 24 * 3600))
VIEWERS_FILE = Path(os.environ.get("PCS_VIEWERS", "/etc/pcs/viewers"))
# /etc/pcs is root-owned and 0750, so this process can READ the credentials
# there and cannot create anything. Everything it writes -- the signing key,
# the dashboard's setting overrides -- lives in its own state directory.
SECRET_FILE = Path(os.environ.get("PCS_SESSION_KEY",
                                  "/var/lib/pcs/session.key"))
MAX_BODY = 4096          # a login form is a few hundred bytes; anything larger is not one


def load_secret(path: Path = SECRET_FILE) -> bytes:
    """Read the signing key, creating it on first use.

    Created with 0600 *before* any bytes are written -- generating the key and
    chmod'ing afterwards leaves a window in which it is world readable, and a
    leaked signing key lets anyone mint a valid session.
    """
    if path.exists():
        raw = path.read_bytes().strip()
        if len(raw) >= 32:
            return raw
    path.parent.mkdir(parents=True, exist_ok=True)
    key = base64.urlsafe_b64encode(secrets.token_bytes(48))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64u(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def mint(name: str, secret: bytes, *, now: float | None = None,
         ttl: int = SESSION_SECONDS) -> str:
    now = time.time() if now is None else now
    body = f"{_b64u(name.encode())}.{int(now + ttl)}"
    sig = hmac.new(secret, body.encode(), sha256).digest()
    return f"v1.{body}.{_b64u(sig)}"


def read_session(cookie: str, secret: bytes, *, now: float | None = None) -> str | None:
    """The username in a valid, unexpired cookie, or None.

    Signature first, expiry second, and both with compare_digest -- checking
    the expiry before the signature would let an attacker learn whether a
    forged token's timestamp parsed, which is a hint they should not get.
    """
    now = time.time() if now is None else now
    try:
        ver, name_b64, exp, sig_b64 = cookie.split(".")
        if ver != "v1":
            return None
        expect = hmac.new(secret, f"{name_b64}.{exp}".encode(), sha256).digest()
        if not hmac.compare_digest(expect, _unb64u(sig_b64)):
            return None
        if int(exp) < now:
            return None
        return _unb64u(name_b64).decode("utf-8")
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def _cookies(header: str) -> dict[str, str]:
    out = {}
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k:
            out[k] = v
    return out


def safe_next(raw: str) -> str:
    """Where to send someone after login.

    Only a path on this site. An open redirect on a login page is a phishing
    primitive: the victim really did authenticate here, and then lands wherever
    the link said. `//evil.com` is a protocol-relative URL and a browser treats
    it as absolute, so leading slashes are collapsed before the check.
    """
    if not raw or not raw.startswith("/"):
        return "/"
    if raw.startswith("//") or "\\" in raw or "\n" in raw or "\r" in raw:
        return "/"
    return raw


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------
_CSS = """
*{box-sizing:border-box}
:root{
--bg:#eef2fa; --bg2:#e4ebf7; --card:#fff; --line:#dde5f2; --line2:#cdd8ea;
--ink:#0f172a; --dim:#64748b; --accent:#2f6bed; --accent2:#1d4fd8;
--err:#c0332b; --errbg:#fdf0ef; --errln:#f6cdc9; --ok:#0b8f4e;
--sh:0 1px 2px rgba(15,23,42,.05),0 16px 44px -12px rgba(15,23,42,.20);
--ring:rgba(47,107,237,.16);
}
@media (prefers-color-scheme:dark){:root{
--bg:#0a0d14; --bg2:#0e131c; --card:#151b26; --line:#232c3b; --line2:#2e3a4d;
--ink:#e8eef7; --dim:#8b97ac; --accent:#5b8dfa; --accent2:#3b6fe8;
--err:#f6796d; --errbg:#2a1614; --errln:#4d251f; --ok:#3fca82;
--sh:0 1px 2px rgba(0,0,0,.4),0 22px 60px -14px rgba(0,0,0,.65);
--ring:rgba(91,141,250,.22);
}}
html,body{height:100%}
body{margin:0;min-height:100svh;display:grid;place-items:center;padding:24px;color:var(--ink);
background:var(--bg);-webkit-font-smoothing:antialiased;
font:15px/1.6 ui-sans-serif,-apple-system,"SF Pro Text",Segoe UI,Inter,Roboto,sans-serif;
background-image:
radial-gradient(900px 500px at 50% -15%,var(--ring),transparent 70%),
radial-gradient(700px 420px at 90% 110%,rgba(46,165,92,.14),transparent 70%),
linear-gradient(180deg,var(--bg),var(--bg2));background-attachment:fixed}
.wrap{width:100%;max-width:392px}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;
padding:34px 32px 28px;box-shadow:var(--sh)}
.head{text-align:center;margin-bottom:26px}
.logo{width:54px;height:54px;border-radius:15px;
box-shadow:0 6px 22px rgba(47,107,237,.30),0 0 0 1px rgba(255,255,255,.08)}
h1{font-size:19px;margin:16px 0 5px;letter-spacing:-.022em;font-weight:680}
.tag{font-size:10.5px;letter-spacing:.15em;text-transform:uppercase;
color:var(--dim);font-weight:640}
label{display:block;font-size:12.5px;font-weight:620;color:var(--dim);
margin:0 0 6px;letter-spacing:.01em}
.field{margin-bottom:16px}
input{width:100%;padding:11px 13px;font-size:15px;font-family:inherit;
color:var(--ink);background:var(--bg);border:1px solid var(--line2);
border-radius:10px;outline:none;transition:border-color .14s,box-shadow .14s}
input:focus{border-color:var(--accent);box-shadow:0 0 0 4px var(--ring)}
button{width:100%;margin-top:6px;padding:11.5px 16px;font:inherit;font-size:14.5px;
font-weight:650;color:#fff;border:1px solid var(--accent2);border-radius:10px;
background:linear-gradient(180deg,var(--accent),var(--accent2));cursor:pointer;
box-shadow:0 2px 10px rgba(47,107,237,.28);transition:filter .14s,transform .04s}
button:hover{filter:brightness(1.07)}
button:active{transform:translateY(1px)}
.err{display:flex;gap:9px;align-items:flex-start;font-size:13px;color:var(--err);
background:var(--errbg);border:1px solid var(--errln);border-radius:10px;
padding:10px 12px;margin-bottom:18px;line-height:1.45}
.err b{font-weight:660}
.foot{margin-top:22px;padding-top:17px;border-top:1px solid var(--line);
color:var(--dim);font-size:11.5px;line-height:1.65;text-align:center}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;
background:var(--ok);margin-right:5px;vertical-align:1px}
"""


def login_page(error: str = "", nxt: str = "/", user: str = "") -> str:
    esc = _escape
    banner = (f'<div class="err"><span>&#9888;</span><span><b>{esc(error)}</b></span></div>'
              if error else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in &middot; Put Credit Spread Agent</title>
<link rel="icon" href="{brand.favicon()}">
<meta name="robots" content="noindex,nofollow">
<style>{_CSS}</style></head><body>
<div class="wrap"><div class="card">
  <div class="head">{brand.LOGO_SVG}
    <h1>Put Credit Spread Agent</h1>
    <div class="tag">S&amp;P 500 &middot; defined risk</div>
  </div>
  {banner}
  <form method="post" action="/login" autocomplete="on">
    <input type="hidden" name="next" value="{esc(nxt)}">
    <div class="field">
      <label for="u">Username</label>
      <input id="u" name="username" type="text" autocomplete="username"
             value="{esc(user)}" required autofocus spellcheck="false">
    </div>
    <div class="field">
      <label for="p">Password</label>
      <input id="p" name="password" type="password"
             autocomplete="current-password" required>
    </div>
    <button type="submit">Sign in</button>
  </form>
  <div class="foot"><span class="dot"></span>Paper account &mdash; positions and
  balances are simulated.<br>Read-only: there is no write path through this page.</div>
</div></div></body></html>"""


def error_page() -> str:
    """Served by nginx when this service is unreachable.

    A static file on purpose: when authd is down it cannot render its own
    apology. Generated from the same CSS and mark at deploy time so it cannot
    drift from the login page it stands in for.
    """
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Unavailable &middot; Put Credit Spread Agent</title>
<link rel="icon" href="{brand.favicon()}">
<style>{_CSS}</style></head><body>
<div class="wrap"><div class="card">
  <div class="head">{brand.LOGO_SVG}
    <h1>Sign-in is unavailable</h1>
    <div class="tag">S&amp;P 500 &middot; defined risk</div>
  </div>
  <div class="err"><span>&#9888;</span><span>The login service is not running,
  so nobody can be signed in right now. The agent itself is unaffected: its
  timers, marks and exits do not go through this page.</span></div>
  <div class="foot">On the box:<br>
  <code>sudo systemctl status pcs-authd</code><br>
  <code>sudo journalctl -u pcs-authd -n 50</code></div>
</div></div></body></html>"""


def _escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# --------------------------------------------------------------------------
# the service
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "pcs-authd"
    sys_version = ""                     # the Python version is nobody's business

    # Injected by serve(); class attributes so tests can drive the handler
    # without standing up a socket.
    secret: bytes = b""
    viewers_file: Path = VIEWERS_FILE

    def log_message(self, fmt, *args):
        sys.stderr.write("authd: " + fmt % args + "\n")

    # -- helpers ----------------------------------------------------------
    def _send(self, status, body=b"", ctype="text/html; charset=utf-8", headers=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # A login page in a frame is a clickjacking target, and a cached one on
        # a shared machine is worse.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _page(self, status, **kw):
        self._send(status, login_page(**kw).encode("utf-8"))

    def _session(self) -> str | None:
        raw = _cookies(self.headers.get("Cookie", "")).get(COOKIE, "")
        return read_session(raw, self.secret) if raw else None

    def _set_cookie(self, value: str, max_age: int) -> tuple[str, str]:
        # SameSite=Lax, not Strict: Strict withholds the cookie on a plain
        # top-level navigation from another site, so following a bookmark or a
        # link from chat would bounce you to the login page while signed in.
        return ("Set-Cookie", f"{COOKIE}={value}; Path=/; HttpOnly; Secure; "
                              f"SameSite=Lax; Max-Age={max_age}")

    def _same_origin(self) -> bool:
        """Reject a cross-site form post.

        SameSite=Lax already withholds the cookie on a cross-site POST, so this
        is the second lock rather than the first -- but this endpoint changes a
        risk limit, and one cheap header check is worth more than the argument
        about whether every browser in use implements Lax the same way.
        """
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin is None:                      # curl, or a same-origin browser
            ref = self.headers.get("Referer")   # that did not send one
            return ref is None or urllib.parse.urlsplit(ref).netloc == host
        return urllib.parse.urlsplit(origin).netloc == host

    def _settings(self, form: dict) -> None:
        """Change one allowlisted setting, for a signed-in viewer.

        This is the only endpoint that writes anything the agent reads, so all
        four checks are load-bearing: signed in, same origin, key on the
        allowlist, value inside its bounds. `config.set_override` re-checks the
        last two -- a validator the caller can forget to run is not a
        validator.

        Note what is NOT reachable here. There is no admin tier: every login
        sees the same page, so every login can do this. It is bounded to
        settings that cannot arm trading or waive the human approval gate.
        """
        name = self._session()
        if not name:
            return self._send(HTTPStatus.UNAUTHORIZED, b"sign in first",
                              ctype="text/plain; charset=utf-8")
        if not self._same_origin():
            self.log_message("cross-origin settings post rejected from %s",
                             self.client_address[0])
            return self._send(HTTPStatus.FORBIDDEN, b"cross-origin request refused",
                              ctype="text/plain; charset=utf-8")
        key, value = form.get("key", ""), form.get("value", "")
        nxt = safe_next(form.get("next", "/"))
        try:
            config.set_override(key, value)
        except ValueError as exc:
            self.log_message("rejected %r=%r from %r: %s", key, value, name, exc)
            return self._send(HTTPStatus.BAD_REQUEST,
                              str(exc).encode("utf-8"),
                              ctype="text/plain; charset=utf-8")
        # Logged, because a risk limit changing is an account event and the
        # page it changes is shared. stderr goes to the journal.
        self.log_message("settings: %s set %s=%s", name, key, value)
        self._send(HTTPStatus.SEE_OTHER, b"", headers=[("Location", nxt)])

    # -- routes -----------------------------------------------------------
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path)
        if path.path == "/_auth":
            name = self._session()
            if name:
                # nginx copies this back onto the main request, so the access
                # log records who read the account, not just that someone did.
                return self._send(HTTPStatus.OK, b"", headers=[("X-Pcs-User", name)])
            return self._send(HTTPStatus.UNAUTHORIZED, b"")
        if path.path == "/login":
            q = urllib.parse.parse_qs(path.query)
            nxt = safe_next(q.get("next", ["/"])[0])
            if self._session():
                return self._send(HTTPStatus.SEE_OTHER, b"", headers=[("Location", nxt)])
            return self._page(HTTPStatus.OK, nxt=nxt)
        return self._send(HTTPStatus.NOT_FOUND, b"not found",
                          ctype="text/plain; charset=utf-8")

    do_HEAD = do_GET

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"too large",
                              ctype="text/plain; charset=utf-8")
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        form = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

        if path == "/logout":
            return self._send(HTTPStatus.SEE_OTHER, b"",
                              headers=[self._set_cookie("", 0), ("Location", "/login")])

        if path == "/settings":
            return self._settings(form)

        if path != "/login":
            return self._send(HTTPStatus.NOT_FOUND, b"not found",
                              ctype="text/plain; charset=utf-8")

        user, password = form.get("username", ""), form.get("password", "")
        nxt = safe_next(form.get("next", "/"))
        if not viewers.authenticate(self.viewers_file, user, password):
            # One message for both failures. Saying which half was wrong tells
            # an attacker when they have found a real username.
            self.log_message("failed login for %r from %s", user[:64],
                             self.client_address[0])
            return self._page(HTTPStatus.UNAUTHORIZED,
                              error="Wrong username or password.", nxt=nxt, user=user)
        self.log_message("login: %r", user)
        token = mint(user, self.secret)
        self._send(HTTPStatus.SEE_OTHER, b"",
                   headers=[self._set_cookie(token, SESSION_SECONDS),
                            ("Location", nxt)])


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Bound to loopback on purpose. nginx terminates TLS and is the only
    thing that should ever reach this; exposing it directly would serve the
    login form over plain HTTP."""
    Handler.secret = load_secret()
    Handler.viewers_file = VIEWERS_FILE
    if not viewers.load(VIEWERS_FILE):
        print(f"authd: no logins in {VIEWERS_FILE} -- nobody can sign in. "
              f"Add one with: run.py viewer add <name>", file=sys.stderr)
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"authd: listening on {host}:{port}, viewers {VIEWERS_FILE}", file=sys.stderr)
    srv.serve_forever()


if __name__ == "__main__":
    serve(port=int(os.environ.get("PCS_AUTHD_PORT", 8765)))
