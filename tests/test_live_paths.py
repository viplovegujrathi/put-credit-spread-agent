"""The suite never points at the live install.

A deploy runs these tests inside /opt/pcs, as the user the timers run as, so
any path a test can write there is a live file. 2026-10-17: two tests in
`test_config_overrides.py` ran `run._auto_open`, which saves proposals to the
default path, and the box's ten tickets from 10-16 were replaced by one test
ticket. Every deploy since 09-01 had done the same.

`conftest.py` points the runtime folder, the web root and the login service's
files at a temporary folder before anything from `pcs` is imported. This pins
that every path the package holds -- at module level or as a parameter default
-- is under it.
"""
import importlib
import importlib.util
import inspect
import pkgutil
from pathlib import Path

import pcs
from pcs import config

# Read-only inputs that ship with the code. Every deploy rewrites them anyway.
INPUTS = {config.ROOT, config.SP500_CSV}


def _paths():
    mods = [importlib.import_module(f"pcs.{m.name}") for m in pkgutil.iter_modules(pcs.__path__)]
    spec = importlib.util.spec_from_file_location("run_for_paths", config.ROOT / "run.py")
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    for mod in [*mods, run]:
        for name, val in vars(mod).items():
            if isinstance(val, Path):
                yield f"{mod.__name__}.{name}", val
            funcs = [val] if inspect.isfunction(val) and val.__module__ == mod.__name__ else []
            if inspect.isclass(val) and val.__module__ == mod.__name__:
                funcs += [getattr(m, "__func__", m) for m in vars(val).values()]
            for f in funcs:
                if not inspect.isfunction(f):
                    continue
                for param, p in inspect.signature(f).parameters.items():
                    if isinstance(p.default, Path):
                        yield f"{mod.__name__}.{f.__qualname__}({param})", p.default


def test_the_suite_runs_against_a_temporary_runtime():
    assert config.RUNTIME != config.ROOT
    assert not config.RUNTIME.is_relative_to(config.ROOT)


def test_no_path_in_the_package_points_at_the_live_install():
    seen = list(_paths())
    assert any(name == "pcs.proposer.save(path)" for name, _ in seen)  # the 10-17 one
    live = [f"{name} -> {p}" for name, p in seen
            if p not in INPUTS and not p.is_relative_to(config.RUNTIME)]
    assert not live, "a test could write the live install:\n" + "\n".join(live)


def test_nothing_publishes_to_a_web_root():
    assert config.WEB_INDEX is None
