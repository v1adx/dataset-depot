import pandas as pd
import pytest
from depot import Dataset

from depot_gui.components.tabulator_table import (
    _build_column_defs,
    _coerce_equality_value,
    _parse_entry,
    _serialize_df,
    _sync_columns,
    _unique_values,
)


def test_serialisation_turns_nan_into_none():
    rows = _serialize_df(pd.DataFrame({"n": [1.0, None]}))
    assert rows[1]["n"] is None


def test_serialisation_keeps_numbers_as_numbers():
    rows = _serialize_df(pd.DataFrame({"n": [1, 2]}))
    assert rows[0]["n"] == 1 and isinstance(rows[0]["n"], int)


def test_column_defs_cover_every_column():
    cols = _build_column_defs(pd.DataFrame({"a": [1], "b": ["x"]}))
    assert {c["field"] for c in cols} == {"a", "b"}


def test_sync_adds_a_new_column_and_reports_the_change():
    config, changed = _sync_columns(pd.DataFrame({"a": [1], "b": [2]}), {"a": {"visible": True}})
    assert changed is True
    assert "b" in config


def test_sync_leaves_an_unchanged_config_alone():
    config, changed = _sync_columns(pd.DataFrame({"a": [1]}), {"a": {"visible": True}})
    assert changed is False


def test_unique_values_stop_at_the_limit():
    data = [{"f": str(i)} for i in range(50)]
    assert _unique_values(data, "f", limit=10) is None


def test_unique_values_are_returned_when_few():
    data = [{"f": "a"}, {"f": "b"}, {"f": "a"}]
    assert sorted(_unique_values(data, "f", limit=10)) == ["a", "b"]


def test_equality_value_is_coerced_to_the_column_type():
    data = [{"f": True}, {"f": False}]
    assert _coerce_equality_value(data, "f", "true") is True


def test_parse_entry_returns_four_parts():
    parsed = _parse_entry({"groupBy": ["a"], "columns": {"a": {"visible": True}}})
    assert len(parsed) == 4


def test_the_state_file_is_keyed_by_the_dataset_key(settings):
    from depot_gui.components.tabulator_table import _state

    _state().set("store/helper:categories", {"groupby": ["a"]})
    assert _state().get("store/helper:categories") == {"groupby": ["a"]}
