"""Everything persisted beside a dataset: its dataframe and its metadata.

Two files, both derived from the dataset's identity:

    <cache_dir>/<type>/<name>.parquet   the dataframe
    <cache_dir>/<type>/<name>.meta      a small JSON sidecar

The metafile carries the whole design. Every scheduling decision is made by
reading it, so an entire graph can be walked without opening a single parquet.
It also records the schema and shape, so a dataset can be described without
being loaded, and the list of columns that had to travel as JSON, because
parquet cannot hold lists or dicts natively.

Two fingerprints live there as well, and both exist to answer "did anything
actually change". One is of the stored content, so work that produces what was
already there does not count as a new version. The other is of the module that
declared the dataset, because the code is an input like any other — editing it
and leaving the old parquet in place is how a dataset comes to serve something
its own module can no longer produce.

Data and metadata are written by one call, so the two cannot disagree about
which columns were encoded.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import orjson
import pandas as pd

from . import config
from .dataset import Dataset


@dataclass
class Meta:
    """What is known about a stored dataset without opening its data."""

    timestamp: float = 0.0
    changed: float = 0.0
    nested: list[str] = field(default_factory=list)
    schema: dict[str, str] = field(default_factory=dict)
    shape: tuple[int, int] | None = None
    digest: str = ""  # fingerprint of the stored content, see save()
    source: str = ""  # fingerprint of the module that declared the dataset


def _stem(dts: Dataset) -> Path:
    """The cache mirrors the source tree: ``<type>/<name>``, folders and all.

    Nothing is folded on the way in, so the mapping runs both ways and two
    datasets cannot land on one path — which is the whole reason the type
    keeps its separator rather than being squeezed into a single word.
    """
    return config.cache_dir().joinpath(*dts.type.split("/"), dts.name)


def data_path(dts: Dataset) -> Path:
    return _stem(dts).with_suffix(".parquet")


def meta_path(dts: Dataset) -> Path:
    return _stem(dts).with_suffix(".meta")


def exists(dts: Dataset) -> bool:
    return data_path(dts).is_file()


def read_meta(dts: Dataset) -> Meta:
    """Read the sidecar. A missing or unreadable file means "never run"."""
    path = meta_path(dts)
    if not path.is_file():
        return Meta()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        shape = payload.get("shape")
        return Meta(
            timestamp=float(payload["timestamp"]),
            changed=float(payload["changed"]),
            nested=list(payload.get("nested", [])),
            schema=dict(payload.get("schema", {})),
            shape=tuple(shape) if shape else None,
            digest=str(payload.get("digest", "")),
            source=str(payload.get("source", "")),
        )
    except (ValueError, KeyError, TypeError):
        return Meta()


def _write_meta(dts: Dataset, meta: Meta) -> None:
    path = meta_path(dts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "timestamp": meta.timestamp,
                "changed": meta.changed,
                "nested": meta.nested,
                "schema": meta.schema,
                "shape": list(meta.shape) if meta.shape else None,
                "digest": meta.digest,
                "source": meta.source,
            }
        ),
        encoding="utf-8",
    )


def _nested_columns(df: pd.DataFrame) -> list[str]:
    found = []
    for col in df.columns:
        if df[col].dtype != object:
            continue
        sample = df[col].dropna()
        if len(sample) > 0 and isinstance(sample.iloc[0], (list, dict)):
            found.append(str(col))
    return found


def _json_default(value):
    """numpy scalars unwrap to Python; anything else is an honest error."""
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def _decode(value):
    """JSON string to object. NaN to None. Anything else untouched."""
    if isinstance(value, str):
        return orjson.loads(value)
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN is the only thing
        return None                                  # pandas puts here for None
    return value


def load(dts: Dataset) -> pd.DataFrame:
    """The stored dataframe, or an empty one when nothing is stored."""
    path = data_path(dts)
    if not path.is_file():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    for col in read_meta(dts).nested:
        if col in df.columns:
            df[col] = df[col].apply(_decode)
    return df


def source_digest(dts: Dataset) -> str:
    """A fingerprint of the module that declared the dataset.

    The code is an input like any other. Without this, editing a transform
    leaves the old parquet in place and the runner sees nothing to do — the
    dataset quietly serves data its own module can no longer produce.
    """
    if dts.source is None:
        return ""
    try:
        return hashlib.sha1(Path(dts.source).read_bytes()).hexdigest()
    except OSError:
        return ""


def _digest(df: pd.DataFrame) -> str:
    """A fingerprint of the frame as it will be stored.

    Hashed after encoding, so the nested columns are JSON strings by now and
    hash like any other value. The column names and dtypes go in as well: the
    same numbers under a different name are different data.
    """
    values = pd.util.hash_pandas_object(df).values.tobytes()
    schema = f"{list(df.columns)}|{[str(t) for t in df.dtypes]}".encode()
    return hashlib.sha1(values + schema).hexdigest()


def save(dts: Dataset) -> bool:
    """Persist the dataset. Returns whether the stored content actually moved.

    Metadata is written even for a dataset that stores no dataframe. Its
    product went elsewhere — into a database, into a spreadsheet — but its timer
    still has to survive a restart, and only the metafile carries that.

    A cached dataset that produces exactly what is already stored has not
    changed, however much work went into finding that out. Saying so is the
    other half of the empty-delta rule: a loader says "nothing arrived" with an
    empty frame, and a product says it by matching its own fingerprint. The
    caller uses the answer to leave the version where it was.
    """
    meta = Meta(timestamp=dts.timestamp, changed=dts.changed, source=source_digest(dts))

    if not dts.cache:
        _write_meta(dts, meta)
        return True

    df = dts.dataframe
    meta.nested = _nested_columns(df)
    meta.schema = {str(col): str(dtype) for col, dtype in df.dtypes.items()}
    meta.shape = (int(df.shape[0]), int(df.shape[1]))

    encoded = _encode(df, meta.nested)
    meta.digest = _digest(encoded)

    stored = read_meta(dts)
    if meta.digest == stored.digest and data_path(dts).is_file():
        return False

    _write_parquet(dts, encoded)
    _write_meta(dts, meta)
    return True


def touch(dts: Dataset) -> None:
    """Record the visit to the source without rewriting the data.

    The dataframe on disk is still correct — the source had nothing new — so
    only the state moves. Keeping the stored schema and shape matters: they
    describe the parquet, which was not touched.
    """
    meta = read_meta(dts)
    meta.timestamp, meta.changed = dts.timestamp, dts.changed
    # The module fingerprint moves with the visit. Leaving it behind would
    # report the same edit as news on every run for ever after.
    meta.source = source_digest(dts)
    _write_meta(dts, meta)


def _encode(df: pd.DataFrame, nested: list[str]) -> pd.DataFrame:
    """The frame as parquet will hold it: nested columns become JSON strings."""
    if not nested:
        return df
    out = df.copy()
    for col in nested:
        out[col] = out[col].apply(
            lambda v: orjson.dumps(v, default=_json_default).decode()
            if v is not None else None
        )
    return out


def _write_parquet(dts: Dataset, encoded: pd.DataFrame) -> None:
    path = data_path(dts)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The index goes with it. pandas stores a plain RangeIndex as metadata and
    # anything else as a column, so this costs nothing for the usual case and
    # keeps a meaningful index — a remote table keyed by id, say — intact. A
    # frame whose index is looked up by .map() is a different frame without it.
    encoded.to_parquet(path)


def drop(dts: Dataset) -> None:
    """Remove everything stored for this dataset."""
    data_path(dts).unlink(missing_ok=True)
    meta_path(dts).unlink(missing_ok=True)
