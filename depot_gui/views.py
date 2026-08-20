"""Views: what a dataset is shown with, and what each of those remembers.

One file per dataset, holding every view it has and the settings of each. The
file is named after the dataset key, percent-encoded: a key carries a colon,
and a nested type carries a slash as well, and neither belongs in a filename.
Folding them into underscores would be shorter and wrong — `store/helper:a`
and `store:helper_a` would then land in the same file.

The first view is always the column picker of the inline table. It has no
button and no dialog of its own; it is a view because its settings belong with
the rest of the dataset's, and because the list reads as "everything this
dataset is shown with" only if it is in there.

Nothing here knows nicegui or any component. The label of a new view's
component is passed in rather than looked up, which is what keeps the import
of the component registry out of this file.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from .state import StateFile

COLUMNS_ID = "columns"


@dataclass
class View:
    id: str
    kind: str
    title: str
    config: dict = field(default_factory=dict)


def _free_title(label: str, views: list[View]) -> str:
    """`Pivot`, then `Pivot 2`, then `Pivot 3`."""
    taken = {v.title for v in views}
    if label not in taken:
        return label
    n = 2
    while f"{label} {n}" in taken:
        n += 1
    return f"{label} {n}"


class ViewStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def list(self, key: str) -> list[View]:
        """Every view of a dataset, the column picker first.

        Nothing is written here. Browsing datasets must not seed the state
        directory with a file per dataset that has never been configured.
        """
        raw = self._file(key).read().get("views", [])
        views = [
            View(
                id=str(entry.get("id", "")),
                kind=str(entry.get("kind", "")),
                title=str(entry.get("title", "")),
                config=entry.get("config") or {},
            )
            for entry in raw
            if isinstance(entry, dict) and entry.get("id")
        ]
        if not any(v.id == COLUMNS_ID for v in views):
            views.insert(0, View(id=COLUMNS_ID, kind="columns", title="Columns"))
        return views

    def config(self, key: str, view_id: str) -> dict:
        for view in self.list(key):
            if view.id == view_id:
                return view.config
        return {}

    def add(self, key: str, kind: str, label: str) -> View:
        views = self.list(key)
        view = View(id=uuid.uuid4().hex[:6], kind=kind, title=_free_title(label, views))
        views.append(view)
        self._write(key, views)
        return view

    def remove(self, key: str, view_id: str) -> None:
        if view_id == COLUMNS_ID:
            return
        self._write(key, [v for v in self.list(key) if v.id != view_id])

    def save_config(self, key: str, view_id: str, config: dict) -> None:
        """Silently does nothing for an id that is gone — that is a dialog left
        open on a view somebody has since removed."""
        views = self.list(key)
        for view in views:
            if view.id == view_id:
                view.config = config
                self._write(key, views)
                return

    def _file(self, key: str) -> StateFile:
        return StateFile(self.root / f"{quote(key, safe='')}.json")

    def _write(self, key: str, views: list[View]) -> None:
        self._file(key).write({"views": [asdict(v) for v in views]})
