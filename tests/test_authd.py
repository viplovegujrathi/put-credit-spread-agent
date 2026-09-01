"""The login page and the session it issues.

This replaced HTTP Basic auth, so these tests carry the whole weight of "who
can read the account". They drive a real socket rather than calling handler
methods directly -- the parts most likely to be wrong (cookie attributes, the
redirect, the status code nginx keys off) only exist on the wire.
"""
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from pcs import authd, viewers

SECRET = b"k" * 48


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "viewers"
    viewers.add(path, "tester", "correct horse battery")
    return path


@pytest.fixture
def base(store):
    """A live server on an ephemeral port, torn down with the test."""
    authd.Handler.secret = SECRET
    authd.Handler.viewers_file = store
    srv = ThreadingHTTPServer(("127.0.0.1", 0), authd.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(base, path, cookie=None, redirect=True):
    req = urllib.request.Request(base + path)
    if cookie:
        req.add_header("Cookie", cookie)
    opener = (urllib.request.build_opener() if redirect
              else urllib.request.build_opener(_NoRedirect))
    try:
        return opener.open(req, timeout=5)
    except urllib.error.HTTPError as e:
        return e


def post(base, path, form, cookie=None):
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(base + path, data=data, method="POST")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        return urllib.request.build_opener(_NoRedirect).open(req, timeout=5)
    except urllib.error.HTTPError as e:
        return e


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


# --- the token -------------------------------------------------------------
def test_a_minted_token_reads_back():
    assert authd.read_session(authd.mint("tester", SECRET), SECRET) == "tester"


def test_a_tampered_token_is_rejected():
    tok = authd.mint("tester", SECRET)
    assert authd.read_session(tok[:-2] + "AA", SECRET) is None


def test_a_token_signed_with_another_key_is_rejected():
    assert authd.read_session(authd.mint("tester", b"other" * 10), SECRET) is None


def test_an_expired_token_is_rejected():
    assert authd.read_session(authd.mint("tester", SECRET, ttl=-1), SECRET) is None


def test_the_username_cannot_be_swapped_without_the_key():
    """The name is signed, not merely carried."""
    tok = authd.mint("tester", SECRET)
    _, name, exp, sig = tok.split(".")
    forged = f"v1.{authd._b64u(b'admin')}.{exp}.{sig}"
    assert authd.read_session(forged, SECRET) is None


# --- open redirect ---------------------------------------------------------
@pytest.mark.parametrize("bad", ["//evil.com", "https://evil.com", "/a\\b",
                                 "/x\nLocation: y", "", "javascript:alert(1)"])
def test_login_never_redirects_off_site(bad):
    """A login page that forwards anywhere is a phishing primitive: the victim
    really did authenticate here, then landed wherever the link said."""
    assert authd.safe_next(bad) == "/"


def test_a_local_path_is_kept():
    assert authd.safe_next("/#watchlist") == "/#watchlist"


# --- the wire --------------------------------------------------------------
def test_the_login_page_renders_a_form(base):
    body = get(base, "/login").read().decode()
    assert 'name="username"' in body and 'name="password"' in body


def test_no_www_authenticate_header_anywhere(base):
    """The entire point. That header is what summons the browser's own
    credential dialog, which cannot be styled."""
    for r in (get(base, "/login"), get(base, "/_auth"),
              post(base, "/login", {"username": "x", "password": "y"})):
        assert r.headers.get("WWW-Authenticate") is None


def test_good_credentials_set_a_session_and_redirect(base):
    r = post(base, "/login", {"username": "tester",
                              "password": "correct horse battery", "next": "/"})
    assert r.status == 303 and r.headers["Location"] == "/"
    assert authd.read_session(
        r.headers["Set-Cookie"].split(";")[0].split("=", 1)[1], SECRET) == "tester"


def test_the_cookie_is_httponly_secure_and_samesite(base):
    r = post(base, "/login", {"username": "tester", "password": "correct horse battery"})
    c = r.headers["Set-Cookie"]
    assert "HttpOnly" in c and "Secure" in c and "SameSite=Lax" in c and "Path=/" in c


def test_bad_credentials_get_401_and_no_cookie(base):
    r = post(base, "/login", {"username": "tester", "password": "wrong"})
    assert r.status == 401
    assert r.headers.get("Set-Cookie") is None


def test_the_failure_message_does_not_say_which_half_was_wrong(base):
    """Distinguishing them tells an attacker when they have found a real name."""
    unknown = post(base, "/login", {"username": "ghost", "password": "x"}).read()
    known = post(base, "/login", {"username": "tester", "password": "x"}).read()
    assert b"Wrong username or password." in unknown
    assert unknown.replace(b"ghost", b"tester") == known


def test_auth_refuses_without_a_cookie(base):
    assert get(base, "/_auth", redirect=False).status == 401


def test_auth_accepts_a_valid_cookie_and_names_the_user(base):
    r = get(base, "/_auth", cookie=f"{authd.COOKIE}={authd.mint('tester', SECRET)}")
    assert r.status == 200 and r.headers["X-Pcs-User"] == "tester"


def test_auth_refuses_a_forged_cookie(base):
    bad = authd.mint("tester", b"z" * 48)
    assert get(base, "/_auth", cookie=f"{authd.COOKIE}={bad}", redirect=False).status == 401


def test_logout_clears_the_cookie(base):
    r = post(base, "/logout", {}, cookie=f"{authd.COOKIE}={authd.mint('tester', SECRET)}")
    assert r.status == 303 and "Max-Age=0" in r.headers["Set-Cookie"]


def test_an_oversized_body_is_refused_before_it_is_parsed(base):
    r = post(base, "/login", {"username": "x" * 9000, "password": "y"})
    assert r.status == 413


def test_the_error_message_is_escaped(base):
    body = post(base, "/login", {"username": "<script>x</script>",
                                 "password": "n"}).read().decode()
    assert "<script>" not in body


# --- the signing key -------------------------------------------------------
def test_the_key_is_created_unreadable_to_others(tmp_path):
    key = tmp_path / "session.key"
    authd.load_secret(key)
    assert oct(key.stat().st_mode & 0o777) == "0o600"


def test_the_key_is_stable_across_calls(tmp_path):
    """Regenerating it on every start would log everyone out on every deploy."""
    key = tmp_path / "session.key"
    assert authd.load_secret(key) == authd.load_secret(key)
