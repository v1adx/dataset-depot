import pandas as pd

from depot_gui.components import aggrid_table, perspective_table, pivot_table
from depot_gui.components.frames import records, split_datetime_columns


# --- AgGrid ---

def test_aggrid_has_no_columns_for_a_dataset_with_no_rows():
    assert aggrid_table.column_defs(pd.DataFrame()) == []


def test_aggrid_picks_a_filter_per_dtype():
    defs = aggrid_table.column_defs(pd.DataFrame({
        "n": [1], "f": [1.5], "s": ["a"], "d": pd.to_datetime(["2026-01-01"]),
    }))
    assert {c["field"]: c["filter"] for c in defs} == {
        "n": "agNumberColumnFilter",
        "f": "agNumberColumnFilter",
        "s": "agTextColumnFilter",
        "d": "agDateColumnFilter",
    }


def test_aggrid_serialises_nan_as_none():
    assert records(pd.DataFrame({"n": [1.0, None]}))[1]["n"] is None


def test_aggrid_serialises_a_datetime_as_iso():
    rows = records(pd.DataFrame({"d": pd.to_datetime(["2026-01-02 03:04:05"])}))
    assert rows[0]["d"] == "2026-01-02T03:04:05"


# --- Datetime parts, shared by every pivot ---

def test_a_datetime_is_split_into_parts():
    df = pd.DataFrame({"d": pd.to_datetime(["2026-01-15 10:00", "2026-07-20 14:00"])})
    result = split_datetime_columns(df)
    assert "d.Year" not in result.columns          # one year throughout: a useless column
    assert "d.Month" in result.columns
    assert "d.Quarter" in result.columns


def test_splitting_keeps_the_original_column():
    df = pd.DataFrame({"d": pd.to_datetime(["2026-01-15", "2026-07-20"])})
    assert "d" in split_datetime_columns(df).columns


def test_a_frame_without_datetimes_is_left_alone():
    df = pd.DataFrame({"a": [1, 2]})
    assert list(split_datetime_columns(df).columns) == ["a"]


# --- Perspective ---

def test_perspective_maps_a_dtype_to_a_column_type():
    schema = perspective_table.schema_of(pd.DataFrame({
        "n": [1], "f": [1.5], "b": [True], "s": ["a"],
        "d": pd.to_datetime(["2026-01-01"]),
    }))
    assert schema == {
        "n": "integer", "f": "float", "b": "boolean",
        "s": "string", "d": "datetime",
    }


def test_perspective_types_a_datetime_part_as_a_string():
    """"01" must stay a label. Typed as a number it would sort as one, and the
    viewer would offer to sum the months."""
    df = split_datetime_columns(pd.DataFrame({
        "d": pd.to_datetime(["2026-01-15", "2026-07-20"]),
    }))
    assert perspective_table.schema_of(df)["d.Month"] == "string"


def test_perspective_serialises_rows_for_its_schema():
    rows = records(pd.DataFrame({
        "n": [1.0, None], "d": pd.to_datetime(["2026-01-02 03:04:05", None]),
    }))
    assert rows[0]["d"] == "2026-01-02T03:04:05"
    assert rows[1]["n"] is None
    assert rows[1]["d"] is None


def test_perspective_opens_a_new_view_with_its_sidebar_out():
    """A bare grid gives no hint that the columns are draggable."""
    assert perspective_table.config_or_default({})["settings"] is True


def test_perspective_opens_a_new_view_with_no_columns_chosen():
    """One empty slot, not an empty list: restore() reads [] as "no opinion"
    and the plugin answers it with every column in the schema."""
    assert perspective_table.config_or_default({})["columns"] == [None]


def test_a_view_that_has_been_laid_out_keeps_its_own_token():
    assert perspective_table.config_or_default({"group_by": ["a"]}) == {"group_by": ["a"]}


def test_the_defaults_handed_out_are_a_copy():
    """Perspective's token is handed to json.dumps and never mutated here, but
    a shared dict between two new views is one edit away from a bug."""
    first = perspective_table.config_or_default({})
    first["settings"] = False
    assert perspective_table.config_or_default({})["settings"] is True


def test_a_table_name_survives_a_dataset_key():
    """restore() looks a Table up by name, so the name has to be the same one
    the token was saved against a restart earlier."""
    assert perspective_table.table_name("staging:daily_sales") == "staging_daily_sales"


def test_perspective_shims_the_clipboard_before_the_bundle_reads_it():
    """The viewer is served over plain http on a LAN address, where
    navigator.clipboard does not exist and Copy silently does nothing. The
    bundle reads window.ClipboardItem once as it evaluates, and a module script
    is deferred — so the classic shim script has to come first in the head."""
    head = perspective_table.HEAD
    assert head.index("window.ClipboardItem =") < head.index('<script type="module">')


# --- Pivot (pivottable.js) ---

def test_pivot_stringifies_every_value():
    """pivottable.js groups by discrete values; a float 1.0 and an int 1 must
    not become two different groups."""
    result = pivot_table.rows(pd.DataFrame({"n": [1], "s": ["a"]}))
    assert result == [{"n": "1", "s": "a"}]


def test_pivot_gets_the_datetime_parts_to_group_by():
    result = pivot_table.rows(pd.DataFrame({
        "d": pd.to_datetime(["2026-01-15", "2026-07-20"]),
    }))
    assert "d.Month" in result[0]


