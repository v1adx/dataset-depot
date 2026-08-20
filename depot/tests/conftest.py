import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from depot import config


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path):
    """Give every test its own cache directory so config state cannot leak."""
    config.set_cache_dir(tmp_path / "cache")
    yield
    config.reset()


@pytest.fixture(autouse=True)
def _isolated_sys_path(monkeypatch):
    """Let discover() insert into sys.path without the entry outliving the test.

    monkeypatch restores the original list object on teardown, so anything the
    test appended to the copy goes away with it. Without this every test that
    builds a depot under tmp_path would leave a dead path behind, hundreds of
    them over a session.
    """
    monkeypatch.setattr(sys, "path", list(sys.path))
