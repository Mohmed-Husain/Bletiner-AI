"""Non-blocking worker helper for the panels (thread pool + Qt-queued result delivery).

Panels must never block the Qt event loop on Docker/HTTP calls and must stay constructible with
``controller=None` for offscreen tests. :func:`run_async` therefore:

* executes the blocking callable on a shared thread pool;
* delivers the result back in the GUI thread through a queued signal on a QObject that lives in the
  GUI thread (plain QThread objects that are destroyed while still running are a classic test/exit
  crash, so they are avoided here);
* silently drops the delivery when the owning widget was destroyed while the call was in flight.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bletiner-panel")


class _ResultInvoker(QObject):
    """GUI-thread object that receives worker-thread emissions as queued signal deliveries."""

    done = Signal(object, object)  # (result: object, error: Exception | None)

    def __init__(self, owner: QWidget, on_done: Callable[[Any, Exception | None], None]) -> None:
        super().__init__(owner)
        self._on_done = on_done
        self._owner = owner
        self.done.connect(self._deliver)

    def _deliver(self, result: object, error: object) -> None:
        try:
            self._on_done(result, error if isinstance(error, Exception) else None)
        finally:
            keep_alive = getattr(self._owner, "_async_invokers", None)
            if isinstance(keep_alive, list) and self in keep_alive:
                keep_alive.remove(self)
            self.deleteLater()


def run_async(
    owner: QWidget,
    work: Callable[[], _T],
    on_done: Callable[[_T | None, Exception | None], None],
) -> None:
    """Run *work* off the GUI thread; call *on_done(result, error)* later on the GUI thread."""
    invoker = _ResultInvoker(owner, on_done)
    keep_alive = getattr(owner, "_async_invokers", None)
    if not isinstance(keep_alive, list):
        keep_alive = []
        owner._async_invokers = keep_alive  # type: ignore[attr-defined]
    keep_alive.append(invoker)

    def _run() -> None:
        result: object = None
        error: Exception | None = None
        try:
            result = work()
        except Exception as exc:
            error = exc
        try:
            invoker.done.emit(result, error)
        except RuntimeError:
            # The owning widget (and with it the invoker) was destroyed while the work ran.
            logger.debug("Dropping background result: owning widget was destroyed")

    _EXECUTOR.submit(_run)
