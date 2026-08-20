"""Everything project-specific the interface needs enters here and nowhere else.

The package knows depot and nicegui. About a particular project it knows a
colour dictionary, three paths and a window title, and nothing beyond that.
Before adding a field here, ask: is this the project's vocabulary, or is it
layout? Layout lives in theme.py and is not a setting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# The one other place besides catalog.py that reaches into depot directly —
# see configure() below and catalog.py's docstring. catalog.py cannot import
# Settings *and* be imported back from settings.py, so the alternative would
# have been no bootstrapping of depot.config at all.
from depot import config as depot_config


@dataclass(frozen=True)
class Settings:
    datasets: Path                              # the datasets root, i.e. DEPOT_SOURCE
    state: Path                                 # directory holding the interface's json state
    artifacts: Path                             # served as /html-artifacts
    colors: dict[str, str] = field(default_factory=dict)
    title: str = "Datasets"
    port: int = 9000

    UNKNOWN_COLOR = "#A5A5A5"

    def color(self, dataset_type: str) -> str:
        """A node's colour, by type.

        A nested type takes the colour of its root: store/helper holds helpers
        of store, and giving them a colour of their own would assert they are a
        different family. The node's shape already tells them apart.
        """
        if not dataset_type:
            return self.UNKNOWN_COLOR
        root = dataset_type.split("/")[0]
        return self.colors.get(root, self.UNKNOWN_COLOR)


_active: Settings | None = None


def configure(settings: Settings) -> None:
    """Remember the settings for this process, point the depot at their datasets
    root, and create the directories we are going to write into.

    Settings.datasets on its own points the framework nowhere: depot derives a
    dataset's type and its cache path from depot.config.source() and
    cache_dir(), and those read DEPOT_SOURCE / DEPOT_CACHE out of the
    environment, not out of Settings. An application that puts the same value
    in both makes them agree by coincidence. Without the explicit set_source
    below, Settings.datasets would mean one thing while the framework quietly
    derived types from Path("datasets") — another. That line is the only place
    where Settings actually becomes what it calls itself.

    Settings.artifacts is deliberately not passed on the same way: where to
    write an artifact is the dataset's own business, and whether that directory
    is the one we serve statically is the project's responsibility, not the
    framework's.
    """
    global _active
    depot_config.set_source(settings.datasets)
    settings.state.mkdir(parents=True, exist_ok=True)
    settings.artifacts.mkdir(parents=True, exist_ok=True)
    _active = settings


def active() -> Settings:
    if _active is None:
        raise RuntimeError(
            "depot_gui is not configured — call depot_gui.start(settings) "
            "or settings.configure(settings) first"
        )
    return _active
