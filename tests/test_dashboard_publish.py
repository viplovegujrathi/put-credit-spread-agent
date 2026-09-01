"""The rendered page and the page that actually gets served.

`render()` writes dashboard.html next to the code, but nginx serves
/var/www/pcs/index.html. For a while the only thing copying one to the other
was a step in bootstrap.sh, so the served page froze at install time: propose
opened four positions, rewrote dashboard.html, and the dashboard kept showing
an empty book. A stale page that looks current is the worst failure this
project has, because every other safeguard reports through it.
"""
import datetime as dt

import pytest

from pcs import brand, dashboard
from pcs.ledger import Ledger
from pcs.session import SessionState


@pytest.fixture
def sess():
    return SessionState(dt.datetime(2026, 9, 1, 11, 0), True, "open", "live", "open")


@pytest.fixture
def led(settings, tmp_path):
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def test_the_served_copy_is_written_on_every_render(monkeypatch, tmp_path, led,
                                                    settings, sess):
    web = tmp_path / "www" / "index.html"
    web.parent.mkdir()
    monkeypatch.setattr(dashboard, "WEB_INDEX", web)
    out = dashboard.render(led, [], settings, sess, path=tmp_path / "dashboard.html")
    assert web.read_text() == out.read_text()


def test_no_web_root_is_not_an_error(monkeypatch, tmp_path, led, settings, sess):
    """A laptop has no /var/www/pcs. Rendering must not care."""
    monkeypatch.setattr(dashboard, "WEB_INDEX", None)
    assert dashboard.render(led, [], settings, sess,
                            path=tmp_path / "dashboard.html").exists()


def test_an_unwritable_web_root_warns_loudly_instead_of_failing_silently(
        monkeypatch, tmp_path, led, settings, sess, capsys):
    """Silence here would recreate the original bug in a new costume."""
    monkeypatch.setattr(dashboard, "WEB_INDEX", tmp_path / "nope" / "index.html")
    dashboard.render(led, [], settings, sess, path=tmp_path / "dashboard.html")
    assert "STALE" in capsys.readouterr().err


def test_the_publish_is_atomic(monkeypatch, tmp_path, led, settings, sess):
    """A half-written page must never be served, so the copy lands via replace()
    and no .tmp file survives the call."""
    web = tmp_path / "www" / "index.html"
    web.parent.mkdir()
    monkeypatch.setattr(dashboard, "WEB_INDEX", web)
    dashboard.render(led, [], settings, sess, path=tmp_path / "dashboard.html")
    assert list(web.parent.iterdir()) == [web]


# --- the mark --------------------------------------------------------------
def _y(point: str) -> float:
    return float(point.split()[-1])


def test_the_icon_points_up():
    """It used to end lower than it started -- a declining chart on an agent
    that sells puts into a bounce. Last point must sit above the first (SVG y
    grows downward, so 'above' is a smaller number)."""
    pts = brand.MARK_LINE.replace("M", "").split("L")
    assert _y(pts[-1]) < _y(pts[0])


def test_the_logo_and_the_favicon_cannot_drift():
    """They were two separate string literals drawing the same path."""
    assert brand.MARK_LINE in brand.LOGO_SVG
    import urllib.parse
    assert brand.MARK_LINE in urllib.parse.unquote(brand.favicon())
