from __future__ import annotations

import asyncio
import contextlib
import io
import queue as _queue
from html import escape
from pathlib import Path
from typing import Callable

from nicegui import app as _nicegui_app
from nicegui import ui

from depot import Dataset

from ..settings import active

_STATIC_ROUTE = "/artifacts"
_mounted = False

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

# The artifact's styling belongs to the viewer: depot writes a fragment and
# knows nothing about how it will look. Hence `body` among the selectors — the
# fragment travels inside a document of its own (see artifact_frame).
_ARTIFACT_STYLE = """
  body {
    margin: 0;
    padding: 24px;
    background: #fafafa;
    color: #333;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 14px;
    line-height: 1.5;
  }
  h1 { font-size: 20px; font-weight: 600; margin: 0 0 20px; }
  h2 {
    font-size: 15px;
    font-weight: 600;
    margin: 28px 0 8px;
    padding-bottom: 6px;
    border-bottom: 1px solid #ddd;
  }
  table { width: 100%; border-collapse: collapse; }
  th, td {
    border: 1px solid #e0e0e0;
    padding: 6px 10px;
    text-align: left;
    white-space: pre-line;
  }
  th { background: #f0f0f0; font-weight: 600; }
"""


def mount_artifacts() -> None:
    """Serve the artifacts directory as static files. Called from start(), not
    at import time: the path is only known after configure(), and nicegui will
    not accept the same route twice."""
    global _mounted
    if not _mounted:
        _nicegui_app.add_static_files(_STATIC_ROUTE, str(active().artifacts))
        _mounted = True


def artifact_file(result: object) -> Path | None:
    """The file an artifact returned, or None if it returned nothing.

    A path with no file behind it is None as well: an artifact that handed back
    something it never wrote did not succeed, and its log will say more about
    that than an empty frame would. The check lives here rather than in the
    framework: by depot's contract an artifact is just a file, and what to do
    with a malformed answer is decided by whoever set out to display it.
    """
    if not isinstance(result, (str, Path)) or not result:
        return None
    file = Path(result)
    return file if file.is_file() else None


def artifact_kind(path: Path) -> str:
    """How to show the file: as markup, as an image, or as a download link."""
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        return "markup"
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    return "file"


def artifact_frame(markup: str) -> str:
    """Build a document around the fragment and put it inside an iframe.

    Two requirements meet here and nowhere else. The styling is ours rather
    than the dataset's, which means we are the ones who build the document. And
    the artifact's scripts have to run, while markup assigned through innerHTML
    does not execute its own `<script>` — which means the document travels in
    `srcdoc`, where it does. Isolation comes back as a bonus: the artifact's
    layout cannot reach the page around it.
    """
    document = f"<style>{_ARTIFACT_STYLE}</style>\n{markup}"
    return (
        f'<iframe srcdoc="{escape(document, quote=True)}" '
        f'style="width:100%;height:500px;border:none;"></iframe>'
    )


class _Capture(io.TextIOBase):
    def __init__(self, q: _queue.Queue):
        self._q = q

    def write(self, text: str) -> int:
        if text:
            self._q.put(text)
        return len(text)

    def flush(self) -> None:
        pass


class FunctionRunner:
    def __init__(self):
        self._dialog = ui.dialog().props("persistent full-width")
        with self._dialog, ui.card().classes("w-full").style("max-height:90vh;overflow-y:auto;max-width:none;"):
            with ui.row().classes("items-center justify-between w-full"):
                self._title = ui.label("").classes("font-bold text-sm")
                with ui.row().classes("items-center gap-2"):
                    self._status = ui.spinner(size="sm")
                    ui.button(icon="close", on_click=self._dialog.close).props("flat round size=sm")
            # Result slots pre-created here (above the log) so property updates work
            # reliably from async context — no dynamic child creation after await.
            self._result_img = ui.image("").classes("w-full rounded")
            self._result_html = ui.html("", sanitize=False).classes("w-full")
            self._log = ui.log(max_lines=500).classes("w-full font-mono text-xs").style("height:200px;")
        self._reset_result()

    def _reset_result(self) -> None:
        self._result_img.set_visibility(False)
        self._result_html.set_visibility(False)

    def _render_result(self, artifact: Path | None) -> None:
        """Show the artifact's file. A function with nothing to show has no
        business here: its output is already in the log."""
        self._reset_result()
        if artifact is None:
            return

        kind = artifact_kind(artifact)
        if kind == "image":
            self._result_img.source = f"{_STATIC_ROUTE}/{artifact.name}"
            self._result_img.set_visibility(True)
            return

        if kind == "markup":
            self._result_html.content = artifact_frame(artifact.read_text(encoding="utf-8"))
        else:
            self._result_html.content = (
                f'<a href="{_STATIC_ROUTE}/{artifact.name}" download '
                f'class="text-primary underline">{artifact.name}</a>'
            )
        self._result_html.set_visibility(True)

    async def run(self, func: Callable, dts: Dataset) -> None:
        q: _queue.Queue = _queue.Queue()
        capture = _Capture(q)
        result_container: list[Path | None] = [None]

        self._title.set_text(func.__name__)
        self._log.clear()
        self._reset_result()
        self._status.set_visibility(True)
        self._dialog.open()

        loop = asyncio.get_running_loop()

        def _run():
            with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
                result_container[0] = artifact_file(func(dts))

        fut = loop.run_in_executor(None, _run)

        def _drain():
            while not q.empty():
                text = q.get_nowait()
                for line in text.split("\n"):
                    self._log.push(line)

        try:
            while not fut.done():
                await asyncio.sleep(0.1)
                _drain()
            await fut
            _drain()
            self._render_result(result_container[0])
            self._status.set_visibility(False)
            ui.notify(f"{func.__name__} completed", type="positive")
        except Exception as exc:
            _drain()
            self._log.push(f"\n[ERROR] {exc}")
            self._status.set_visibility(False)
            ui.notify(f"{func.__name__} failed: {exc}", type="negative")
