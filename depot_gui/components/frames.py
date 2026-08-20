"""Turning a dataset's frame into something a browser table can read.

Three components hand the same frame to three different JavaScript tables, and
each of them needs the same two things done to it first: datetime columns split
into parts a pivot can group by, and every value put into a shape json can
carry. Both used to live twice — once in aggrid_table.py, once in
pivot_table.py — with a third copy on the way in perspective_table.py, so they
live here instead.

Nothing here knows which table it is feeding. The one place where that leaks is
the ISO 8601 datetime, which happens to be what AgGrid's date filter reads and
what Perspective coerces a datetime column from — a coincidence, but a stable
one, and a table that wanted another format would be free to convert.
"""
from __future__ import annotations

import pandas as pd


def split_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Insert Year/Quarter/Month/Week/Day/Hour beside every datetime column.

    A pivot groups by discrete values, and a timestamp is not one — grouping by
    it yields a row per row. The parts are strings on purpose: "01" keeps its
    order and stays a label, rather than becoming a number the table would
    offer to sum. A part that holds one value throughout says nothing about the
    data and is left out.
    """
    df = df.copy()
    datetime_cols = [
        c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])
    ]
    for col in datetime_cols:
        quarter = df[col].dt.quarter.map(
            lambda q: f"Q{int(q)}" if pd.notna(q) else float("nan")
        )
        week = df[col].dt.isocalendar().week.map(
            lambda w: f"{int(w):02d}" if pd.notna(w) else float("nan")
        )
        parts = [
            (f"{col}.Year", df[col].dt.strftime("%Y")),
            (f"{col}.Quarter", quarter),
            (f"{col}.Month", df[col].dt.strftime("%m")),
            (f"{col}.Week", week),
            (f"{col}.Day", df[col].dt.strftime("%d")),
            (f"{col}.Hour", df[col].dt.strftime("%H")),
        ]
        insert_at = df.columns.get_loc(col) + 1
        for name, series in parts:
            if series.nunique(dropna=True) <= 1:
                continue
            df.insert(insert_at, name, series)
            insert_at += 1
    return df


def serialize_value(val, kind: str):
    """One cell, in a form json can carry.

    The column's dtype kind decides, not the value: iterrows() hands back a
    Series per row, and a row of mixed dtypes upcasts an integer column to
    float on the way. Casting by the column keeps the integer an integer.
    """
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if kind in ("i", "u"):
        return int(val)
    if kind == "f":
        return float(val)
    if kind == "b":
        return bool(val)
    if kind == "M":
        return val.isoformat()
    return str(val)


def records(df: pd.DataFrame) -> list[dict]:
    """The frame as a list of json-ready row dicts."""
    kinds = {c: df[c].dtype.kind for c in df.columns}
    return [
        {c: serialize_value(row[c], kinds[c]) for c in df.columns}
        for _, row in df.iterrows()
    ]
