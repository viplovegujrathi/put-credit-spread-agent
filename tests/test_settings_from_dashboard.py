"""Changing a setting from the dashboard, and the agent picking it up.

This is the first write path from a browser into anything the agent reads, so
the tests are mostly about what it REFUSES. The allowlist is the security
boundary: every login sees the same page and there is no admin tier, so a key
reachable here is a key any viewer can change.
"""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from pcs import authd, config, viewers
from pcs.config import Settings

SECRET = b"k" * 48


@pytest.fixture
def overrides(tmp_path, monkeypatch):
    path = tmp_path / "overrides.json"
    monkeypatch.setattr(config, "OVERRIDES_JSON", path)
    return path


@pytest.fixture
def base(tmp_path, overrides):
    store = tmp_path / "viewers"
    viewers.add(store, "tester", "correct horse battery")
    authd.Handler.secret = SECRET
    authd.Handler.viewers_file = store
    srv = ThreadingHTTPServer(("127.0.0.1", 0), authd.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def post(base, path, form, cookie=None, origin=None, referer=None):
    req = urllib.request.Request(base + path,
                                 data=urllib.parse.urlencode(form).encode(),
                                 method="POST")
    if cookie:
        req.add_header("Cookie", cookie)
    if origin:
        req.add_header("Origin", origin)
    if referer:
        req.add_header("Referer", referer)
    try:
        return urllib.request.build_opener(_NoRedirect).open(req, timeout=5)
    except urllib.error.HTTPError as e:
        return e


@pytest.fixture
def signed_in():
    return f"{authd.COOKIE}={authd.mint('tester', SECRET)}"


# --- the override file ------------------------------------------------------
def test_a_dashboard_value_is_applied_over_settings_json(tmp_path, overrides):
    """The whole point: no restart, no redeploy. The next run reads it."""
    sfile = tmp_path / "settings.json"
    Settings(max_open_positions=4).save(sfile)
    config.set_override("max_open_positions", 9, overrides)
    assert Settings.load(sfile, overrides).max_open_positions == 9


def test_without_an_override_settings_json_still_wins(tmp_path, overrides):
    sfile = tmp_path / "settings.json"
    Settings(max_open_positions=4).save(sfile)
    assert Settings.load(sfile, overrides).max_open_positions == 4


def test_the_default_max_positions_is_ten():
    assert Settings().max_open_positions == 10


def test_a_key_that_is_not_settable_is_refused(overrides):
    """paper_trading arms the whole agent. It must not be reachable from a
    browser at any bound, by anyone who can log in."""
    with pytest.raises(ValueError):
        config.set_override("paper_trading", 1, overrides)
    with pytest.raises(ValueError):
        config.set_override("auto_approve", 1, overrides)


@pytest.mark.parametrize("bad", [0, -1, 51, 10_000])
def test_an_out_of_bounds_value_is_refused(overrides, bad):
    with pytest.raises(ValueError):
        config.set_override("max_open_positions", bad, overrides)


@pytest.mark.parametrize("bad", ["", "abc", "3.5", None, "1e9"])
def test_a_non_integer_is_refused(overrides, bad):
    with pytest.raises(ValueError):
        config.set_override("max_open_positions", bad, overrides)


def test_a_corrupt_override_file_falls_back_rather_than_crashing(tmp_path, overrides):
    """This file is written by a web request. A bad one must not take the
    agent down on its next run."""
    overrides.write_text("{ not json")
    sfile = tmp_path / "settings.json"
    Settings(max_open_positions=4).save(sfile)
    assert Settings.load(sfile, overrides).max_open_positions == 4


def test_an_unknown_key_in_the_file_is_ignored(tmp_path, overrides):
    """Defence in depth: the allowlist is enforced on read as well as write,
    so hand-editing the file cannot widen it."""
    overrides.write_text(json.dumps({"paper_trading": False, "max_open_positions": 7}))
    sfile = tmp_path / "settings.json"
    s = Settings.load(sfile, overrides)
    assert s.max_open_positions == 7
    assert s.paper_trading is True


def test_clearing_an_override_hands_control_back(tmp_path, overrides):
    config.set_override("max_open_positions", 9, overrides)
    assert config.clear_override("max_open_positions", overrides) is True
    sfile = tmp_path / "settings.json"
    Settings(max_open_positions=4).save(sfile)
    assert Settings.load(sfile, overrides).max_open_positions == 4


def test_clearing_a_key_that_was_never_set_is_not_an_error(overrides):
    assert config.clear_override("max_open_positions", overrides) is False


def test_the_write_is_atomic(overrides):
    config.set_override("max_open_positions", 6, overrides)
    assert not list(overrides.parent.glob("*.tmp"))


# --- the endpoint -----------------------------------------------------------
def test_a_signed_in_viewer_can_change_it(base, overrides, signed_in):
    r = post(base, "/settings",
             {"key": "max_open_positions", "value": "12", "next": "/"},
             cookie=signed_in)
    assert r.status == 303
    assert config.load_overrides(overrides)["max_open_positions"] == 12


def test_a_signed_out_request_changes_nothing(base, overrides):
    r = post(base, "/settings", {"key": "max_open_positions", "value": "12"})
    assert r.status == 401
    assert config.load_overrides(overrides) == {}


def test_a_forged_cookie_changes_nothing(base, overrides):
    bad = f"{authd.COOKIE}={authd.mint('tester', b'x' * 48)}"
    assert post(base, "/settings", {"key": "max_open_positions", "value": "12"},
                cookie=bad).status == 401
    assert config.load_overrides(overrides) == {}


def test_a_cross_origin_post_is_refused(base, overrides, signed_in):
    """SameSite=Lax already withholds the cookie here. This is the second lock
    on the one endpoint that changes a risk limit."""
    r = post(base, "/settings", {"key": "max_open_positions", "value": "12"},
             cookie=signed_in, origin="https://evil.example")
    assert r.status == 403
    assert config.load_overrides(overrides) == {}


def test_a_cross_origin_referer_is_refused(base, overrides, signed_in):
    r = post(base, "/settings", {"key": "max_open_positions", "value": "12"},
             cookie=signed_in, referer="https://evil.example/x")
    assert r.status == 403
    assert config.load_overrides(overrides) == {}


def test_the_endpoint_refuses_a_key_outside_the_allowlist(base, overrides, signed_in):
    r = post(base, "/settings", {"key": "paper_trading", "value": "1"},
             cookie=signed_in)
    assert r.status == 400
    assert config.load_overrides(overrides) == {}


def test_the_endpoint_refuses_an_out_of_bounds_value(base, overrides, signed_in):
    r = post(base, "/settings", {"key": "max_open_positions", "value": "999"},
             cookie=signed_in)
    assert r.status == 400
    assert config.load_overrides(overrides) == {}


def test_the_redirect_target_cannot_leave_the_site(base, overrides, signed_in):
    r = post(base, "/settings",
             {"key": "max_open_positions", "value": "5", "next": "//evil.example"},
             cookie=signed_in)
    assert r.status == 303
    assert r.headers["Location"] == "/"


def test_an_oversized_body_is_refused_before_parsing(base, overrides, signed_in):
    r = post(base, "/settings",
             {"key": "max_open_positions", "value": "5", "pad": "x" * 8000},
             cookie=signed_in)
    assert r.status == 413
    assert config.load_overrides(overrides) == {}


# --- logout -----------------------------------------------------------------
def test_logout_clears_the_cookie(base, signed_in):
    r = post(base, "/logout", {}, cookie=signed_in)
    assert r.status == 303
    assert "Max-Age=0" in r.headers["Set-Cookie"]
    assert r.headers["Location"] == "/login"


def test_a_logged_out_cookie_no_longer_authorises(base):
    """The browser is told to drop it; what matters is that an empty cookie
    is not somehow a valid session."""
    req = urllib.request.Request(base + "/_auth")
    req.add_header("Cookie", f"{authd.COOKIE}=")
    try:
        r = urllib.request.build_opener(_NoRedirect).open(req, timeout=5)
    except urllib.error.HTTPError as e:
        r = e
    assert r.status == 401


# --- the CLI and the dashboard must not disagree silently -------------------
def test_config_set_clears_a_dashboard_override(tmp_path, overrides, monkeypatch):
    """Otherwise `./run.py config --set max_open_positions=6` writes
    settings.json, the override keeps winning, and the command looks like it
    was ignored."""
    import argparse
    from dataclasses import asdict

    import run as cli

    sfile = tmp_path / "settings.json"
    monkeypatch.setattr(cli.Settings, "save",
                        lambda self, path=None: (
                            sfile.write_text(json.dumps(asdict(self), indent=2)), sfile)[1])

    config.set_override("max_open_positions", 9, overrides)
    s = Settings.load(sfile, overrides)
    assert s.max_open_positions == 9

    assert cli.cmd_config(argparse.Namespace(set=["max_open_positions=6"]), s) == 0
    assert config.load_overrides(overrides) == {}
    assert Settings.load(sfile, overrides).max_open_positions == 6
