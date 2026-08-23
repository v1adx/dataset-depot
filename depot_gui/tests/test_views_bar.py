"""The two pieces of the views row that work without a page: the URL a view
button points at, and the handler that writes a view's settings.

Neither needs widgets, so neither is tested through them. `save_config` is a
plain function precisely because the page that receives the event and the row
that lists the views are no longer the same client — see the module docstring
of views_bar.
"""
from types import SimpleNamespace

from depot_gui.components.views_bar import KINDS, save_config, view_url
from depot_gui.views import ViewStore


def event(payload):
    """What ui.on hands a handler when the browser emits."""
    return SimpleNamespace(args=payload)


def store(tmp_path) -> ViewStore:
    return ViewStore(tmp_path / "views")


def test_every_offered_kind_is_a_component():
    for module in KINDS.values():
        assert isinstance(module.LABEL, str) and module.LABEL
        assert isinstance(module.ICON, str) and module.ICON
        assert callable(module.render)


def test_the_column_picker_is_not_on_offer():
    """It draws inline under the meta card, so [+] must not offer it."""
    assert "columns" not in KINDS


# --- The link a view opens in ---

def test_a_view_opens_on_the_page_of_its_own_dataset():
    assert view_url("staging:sales", "ab12cd") == "/dts/staging:sales?view=ab12cd"


def test_a_nested_key_keeps_its_slash():
    """The route takes the key as `{key:path}`, so a slash is part of it and
    must not be folded away: store/helper:a and store:helper_a are two
    datasets."""
    assert view_url("store/helper:a", "ab12cd") == "/dts/store/helper:a?view=ab12cd"


# --- Saving a layout ---

def test_a_config_is_saved_against_the_view_the_browser_named(tmp_path):
    s = store(tmp_path)
    view = s.add("staging:sales", "pivot", "Pivot")
    save_config(s, event({"key": "staging:sales", "view": view.id,
                          "config": {"rows": ["a"]}}))
    assert s.config("staging:sales", view.id) == {"rows": ["a"]}


def test_a_config_wrapped_in_a_list_is_saved_too(tmp_path):
    """Some nicegui versions hand the handler [payload] rather than payload."""
    s = store(tmp_path)
    view = s.add("staging:sales", "pivot", "Pivot")
    save_config(s, event([{"key": "staging:sales", "view": view.id,
                           "config": {"rows": ["a"]}}]))
    assert s.config("staging:sales", view.id) == {"rows": ["a"]}


def test_a_view_tab_writes_to_its_own_dataset_not_whichever_is_on_the_graph(tmp_path):
    """A view is its own page now, so the tab that emits and the graph that
    lists the views are different clients. The payload names its own
    destination, which is what keeps them from drifting apart."""
    s = store(tmp_path)
    old = s.add("staging:old", "pivot", "Pivot")
    current = s.add("staging:sales", "pivot", "Pivot")
    save_config(s, event({"key": "staging:old", "view": old.id,
                          "config": {"rows": ["b"]}}))
    assert s.config("staging:old", old.id) == {"rows": ["b"]}
    assert s.config("staging:sales", current.id) == {}


def test_an_event_without_a_key_is_ignored(tmp_path):
    save_config(store(tmp_path), event({"view": "abc", "config": {"rows": ["a"]}}))
    assert not (tmp_path / "views").exists()


def test_an_event_without_a_view_is_ignored(tmp_path):
    save_config(store(tmp_path), event({"key": "staging:sales",
                                        "config": {"rows": ["a"]}}))
    assert not (tmp_path / "views").exists()
