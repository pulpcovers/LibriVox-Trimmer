import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


logger = logging.getLogger(__name__)


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(object)


class Worker(QRunnable):
    def __init__(
        self,
        function: Callable[..., Any],
        *,
        accepts_progress: bool = False,
    ) -> None:
        super().__init__()
        self.function = function
        self.accepts_progress = accepts_progress
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            if self.accepts_progress:
                result = self.function(self.signals.progress.emit)
            else:
                result = self.function()
            self.signals.finished.emit(result)
        except Exception as error:
            logger.exception("Background task failed")
            self.signals.failed.emit(str(error))