"""The box's own state must survive a redeploy, and stay out of the repo.

Three lists have to agree: the rsync excludes in `push.sh` (laptop -> box),
the ones in `bootstrap.sh` (clone -> /opt/pcs), and `.gitignore`. Both rsyncs
run with `--delete`, so a file missing from either exclude list is deleted or
overwritten on every deploy. A file missing from `.gitignore` can be committed
from the laptop, and the next deploy ships that stale copy over the live one.

They were kept in step by hand. `health.json` made it into both excludes and
not into `.gitignore`; the ledger lock, added with `ledger.locked`, is a fourth
file that has to be in all three.
"""
import fnmatch
import re

import pytest

from pcs import health, learning, marketdata, watchlist
from pcs.config import DATA_DIR, LEDGER_JSON, PROPOSALS_JSON, ROOT

EXCLUDE = re.compile(r"--exclude '([^']+)'")


def excludes(script: str) -> set[str]:
    return set(EXCLUDE.findall((ROOT / "deploy" / script).read_text()))


def ignored() -> set[str]:
    lines = (ROOT / ".gitignore").read_text().splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith(("#", "!"))}


def covered(rel: str, patterns: set[str]) -> bool:
    return any(fnmatch.fnmatch(rel, p) for p in patterns)


# Everything the timers write under data/ on the box.
STATE = [LEDGER_JSON, LEDGER_JSON.with_name(LEDGER_JSON.name + ".lock"), PROPOSALS_JSON,
         DATA_DIR / "settings.json", learning.JOURNAL_JSON, health.HEALTH_JSON,
         marketdata.SNAPSHOT_CACHE, watchlist.WATCHLIST_JSON]


def test_both_deploy_paths_spare_the_same_files():
    assert excludes("push.sh") == excludes("bootstrap.sh")


@pytest.mark.parametrize("path", STATE, ids=lambda p: p.name)
def test_box_state_is_spared_by_both_rsyncs(path):
    rel = path.relative_to(ROOT).as_posix()
    assert covered(rel, excludes("push.sh")), f"push.sh would overwrite {rel}"
    assert covered(rel, excludes("bootstrap.sh")), f"bootstrap.sh would overwrite {rel}"


def test_every_spared_data_file_is_also_ignored():
    data = {p for p in excludes("push.sh") | excludes("bootstrap.sh")
            if p.startswith("data/")}
    missing = sorted(data - ignored())
    assert not missing, f"excluded from deploys but committable: {missing}"
