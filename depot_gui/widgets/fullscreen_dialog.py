from __future__ import annotations

from typing import Callable
from nicegui import ui


class FullscreenDialog:
    def __init__(self):
        self._on_delete: Callable[[], None] | None = None

        with ui.dialog().props("maximized") as self._dialog:
            with ui.card().classes("w-full h-full no-shadow"):
                with ui.row().classes("items-center justify-between w-full q-pa-sm"):
                    self._title = ui.label("").classes("text-base font-bold")
                    with ui.row().classes("items-center gap-1"):
                        self._delete_button = ui.button(
                            icon="delete", on_click=self._delete
                        ).props("flat round")
                        ui.button(icon="close", on_click=self.close).props("flat round")
                self._content = ui.column().classes("w-full flex-1")

        self._delete_button.set_visibility(False)

    def open(
        self,
        title: str,
        build_content: Callable[[], None],
        on_delete: Callable[[], None] | None = None,
    ) -> None:
        """The bin appears only for content that can be thrown away."""
        self._title.set_text(title)
        self._on_delete = on_delete
        self._delete_button.set_visibility(on_delete is not None)
        self._content.clear()
        with self._content:
            build_content()
        self._dialog.open()

    def close(self) -> None:
        self._dialog.close()

    def _delete(self) -> None:
        if self._on_delete:
            self._on_delete()
