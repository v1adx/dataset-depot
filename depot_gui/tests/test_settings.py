from pathlib import Path

import pytest

from depot_gui.settings import Settings, active, configure


def make(tmp_path: Path, **kw) -> Settings:
    defaults = dict(
        datasets=tmp_path / "datasets",
        state=tmp_path / "state",
        artifacts=tmp_path / "artifacts",
        colors={"source": "#F8BE00"},
    )
    return Settings(**{**defaults, **kw})


def test_known_type_gets_its_colour(tmp_path):
    assert make(tmp_path).color("source") == "#F8BE00"


def test_unknown_type_falls_back_to_grey(tmp_path):
    assert make(tmp_path).color("exotic") == Settings.UNKNOWN_COLOR


def test_nested_type_uses_its_first_segment(tmp_path):
    # store/helper is a helper of store and belongs to the same colour family
    assert make(tmp_path, colors={"store": "#58A015"}).color("store/helper") == "#58A015"


def test_empty_type_is_unknown(tmp_path):
    assert make(tmp_path).color("") == Settings.UNKNOWN_COLOR


def test_active_before_configure_is_an_error(tmp_path):
    import depot_gui.settings as module
    module._active = None
    with pytest.raises(RuntimeError, match="not configured"):
        active()


def test_configure_then_active_returns_it(tmp_path):
    settings = make(tmp_path)
    configure(settings)
    assert active() is settings


def test_state_directory_is_created_by_configure(tmp_path):
    settings = make(tmp_path)
    configure(settings)
    assert settings.state.is_dir()
    assert settings.artifacts.is_dir()
