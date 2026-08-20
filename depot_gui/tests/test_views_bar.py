"""The one handler that writes a view's settings, without a page.

`ViewsBar.__init__` builds widgets and subscribes to an event, neither of which
the saving logic needs. Constructing through `object.__new__` and giving it the
two attributes `_save` actually touches exercises the real code with no
widgets involved — the same approach `test_panel.py` takes.
"""
from types import SimpleNamespace

from depot_gui.components.views_bar import KINDS, ViewsBar
from depot_gui.views import ViewStore


def event(payload):
    """What ui.on hands a handler when the browser emits."""
    return SimpleNamespace(args=payload)


def bar(tmp_path) -> ViewsBar:
    obj = object.__new__(ViewsBar)
    obj._store = ViewStore(tmp_path / "views")
    obj._dts = None
    return obj


def test_every_offered_kind_is_a_component():
    for module in KINDS.values():
        assert isinstance(module.LABEL, str) and module.LABEL
        assert isinstance(module.ICON, str) and module.ICON
        assert callable(module.render)


def test_the_column_picker_is_not_on_offer():
    """It has no dialog, so [+] must not offer it."""
    assert "columns" not in KINDS


def test_a_config_is_saved_against_the_view_the_browser_named(tmp_path):
    b = bar(tmp_path)
    view = b._store.add("staging:sales", "pivot", "Pivot")
    b._save(event({"key": "staging:sales", "view": view.id, "config": {"rows": ["a"]}}))
    assert b._store.config("staging:sales", view.id) == {"rows": ["a"]}


def test_a_config_wrapped_in_a_list_is_saved_too(tmp_path):
    """Some nicegui versions hand the handler [payload] rather than payload."""
    b = bar(tmp_path)
    view = b._store.add("staging:sales", "pivot", "Pivot")
    b._save(event([{"key": "staging:sales", "view": view.id, "config": {"rows": ["a"]}}]))
    assert b._store.config("staging:sales", view.id) == {"rows": ["a"]}


def test_a_stale_dialog_writes_to_its_own_dataset_not_the_current_one(tmp_path):
    """A dialog left open on one dataset while the panel moved to another. The
    payload names its own destination, so there is nothing to get wrong."""
    b = bar(tmp_path)
    old = b._store.add("staging:old", "pivot", "Pivot")
    current = b._store.add("staging:sales", "pivot", "Pivot")
    b._save(event({"key": "staging:old", "view": old.id, "config": {"rows": ["b"]}}))
    assert b._store.config("staging:old", old.id) == {"rows": ["b"]}
    assert b._store.config("staging:sales", current.id) == {}


def test_an_event_without_a_key_is_ignored(tmp_path):
    b = bar(tmp_path)
    b._save(event({"view": "abc", "config": {"rows": ["a"]}}))
    assert not (tmp_path / "views").exists()


def test_an_event_without_a_view_is_ignored(tmp_path):
    b = bar(tmp_path)
    b._save(event({"key": "staging:sales", "config": {"rows": ["a"]}}))
    assert not (tmp_path / "views").exists()
