"""An interface onto a depot of datasets: the graph, the panel, the tables.

The package knows nothing about any particular project — everything
project-specific enters through Settings. start() brings the whole application
up; the individual components are not part of the public contract.
"""
from .settings import Settings, active, configure

__all__ = ["Settings", "active", "configure", "start"]


def start(settings: Settings) -> None:
    """Bring the interface up. The import is inside: pages pulls in nicegui and
    Settings does not, and a test of the settings has no business depending on
    the toolkit being installed."""
    from .pages import start as _start

    _start(settings)
