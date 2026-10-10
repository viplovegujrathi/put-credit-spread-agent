"""The deploy scripts run under `set -euo pipefail`, and `head` does not mix.

`head -N` exits after N lines. If the writer is still writing, it dies of
SIGPIPE (141), `pipefail` makes that the pipeline's status, and `set -e` ends
the script there. Whether it happens is a race. 2026-10-10: `bootstrap.sh`
stopped right after `systemctl list-timers ... | head -4` -- code and timers
in place, self-check, nginx and the login service skipped, and `push.sh`
never reached its own verification. The same line on the box: 1 failure in 30.

`sed -n 1,Np` prints the same lines and reads its input to the end.
"""
import re

import pytest

from pcs.config import ROOT

HEAD = re.compile(r"\|\s*head\b")


@pytest.mark.parametrize("script", ["push.sh", "bootstrap.sh"])
def test_no_pipeline_ends_in_head(script):
    lines = (ROOT / "deploy" / script).read_text().splitlines()
    hits = [f"{script}:{n}: {ln.strip()}" for n, ln in enumerate(lines, 1)
            if HEAD.search(ln) and not ln.lstrip().startswith("#")]
    assert not hits, "\n".join(hits)


@pytest.mark.parametrize("script", ["push.sh", "bootstrap.sh"])
def test_the_scripts_still_run_under_pipefail(script):
    """The reason the rule above exists. If this ever changes, so does that."""
    assert "set -euo pipefail" in (ROOT / "deploy" / script).read_text()
