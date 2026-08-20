"""Column visibility, read and written as the dataset's zeroth view.

`DatasetTable.__init__` builds widgets, which the visibility logic does not
need. The two methods are exercised through `object.__new__` with a stub table
standing in for the quasar one — the same approach `test_panel.py` takes.
"""
from types import SimpleNamespace

from depot_gui.components.dataset_table import DatasetTable
from depot_gui.views import COLUMNS_ID, ViewStore


def table(tmp_path, columns: list[str]) -> DatasetTable:
    obj = object.__new__(DatasetTable)
    obj._store = ViewStore(tmp_path / "views")
    obj._dts_key = "staging:sales"
    obj._table = SimpleNamespace(
        columns=[{"name": c, "classes": "", "headerClasses": ""} for c in columns],
        update=lambda: None,
    )
    return obj


def test_hiding_a_column_records_the_ones_left_visible(tmp_path):
    t = table(tmp_path, ["a", "b", "c"])
    t._toggle_column(t._table.columns[1], False)
    assert t._store.config("staging:sales", COLUMNS_ID) == {"visible": ["a", "c"]}


def test_showing_a_column_back_records_it_again(tmp_path):
    t = table(tmp_path, ["a", "b"])
    t._toggle_column(t._table.columns[0], False)
    t._toggle_column(t._table.columns[0], True)
    assert t._store.config("staging:sales", COLUMNS_ID) == {"visible": ["a", "b"]}


def test_visibility_is_written_under_the_columns_view_not_the_dataset(tmp_path):
    """The file holds a list of views; the column picker is one of them."""
    t = table(tmp_path, ["a", "b"])
    t._toggle_column(t._table.columns[0], False)
    views = t._store.list("staging:sales")
    assert [v.id for v in views] == [COLUMNS_ID]
    assert views[0].config == {"visible": ["b"]}


def test_two_datasets_keep_their_own_visibility(tmp_path):
    t = table(tmp_path, ["a", "b"])
    t._toggle_column(t._table.columns[0], False)
    t._dts_key = "staging:other"
    assert t._store.config("staging:other", COLUMNS_ID) == {}
