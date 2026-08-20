"""Fixtures shared across the package's tests.

The framework is not mocked here, and must not be. Its only dependency is
pandas, so there is nothing heavy to stand in for, and a mocked Dataset that
hands back one and the same object turns passing tests into meaningless ones.
We work with the real thing.

nicegui is mocked, and only the parts that reach into a page's context when a
component is imported (add_head_html, ui.on, run_javascript). That is
substituting the toolkit, not the code under test.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from depot_gui.settings import Settings, configure


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        datasets=tmp_path / "datasets",
        state=tmp_path / "state",
        artifacts=tmp_path / "artifacts",
        colors={"source": "#F8BE00", "staging": "#519DCF", "reports": "#F04561"},
    )
    s.datasets.mkdir(parents=True, exist_ok=True)
    configure(s)
    return s


@pytest.fixture()
def quiet_nicegui():
    with patch("nicegui.ui.add_head_html"), \
         patch("nicegui.ui.on"), \
         patch("nicegui.ui.run_javascript"), \
         patch("nicegui.ui.html", return_value=MagicMock()), \
         patch("nicegui.ui.label", return_value=MagicMock()):
        yield
