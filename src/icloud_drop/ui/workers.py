"""通信や保存で画面を止めないための作業スレッド。"""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from icloud_drop.errors import explain_exception


class CallWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str, str)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.succeeded.emit(self._fn())
        except Exception as exc:
            message, hint = explain_exception(exc)
            self.failed.emit(message, hint)


class LibraryWorker(QThread):
    albums_ready = Signal(object)
    batch_ready = Signal(int, str, object)
    failed = Signal(str, str)
    succeeded = Signal(int)

    def __init__(self, source, album_id: str, token: int, need_albums: bool) -> None:
        super().__init__()
        self.source = source
        self.album_id = album_id
        self.token = token
        self.need_albums = need_albums

    def run(self) -> None:
        try:
            if self.need_albums:
                self.albums_ready.emit(self.source.list_albums())
            for batch in self.source.iter_batches(self.album_id):
                self.batch_ready.emit(self.token, self.album_id, batch)
            self.succeeded.emit(self.token)
        except Exception as exc:
            message, hint = explain_exception(exc)
            self.failed.emit(message, hint)


class ThumbWorker(QThread):
    ready = Signal(str, bytes)

    def __init__(self, source_getter) -> None:
        super().__init__()
        self.source_getter = source_getter
        self._lock = threading.Lock()
        self._queue: list[str] = []
        self._seen: set[str] = set()
        self._wake = threading.Event()
        self._stop = False

    def request(self, ids: list[str]) -> None:
        with self._lock:
            for asset_id in reversed(ids):
                if asset_id in self._seen:
                    continue
                self._queue.append(asset_id)
                self._seen.add(asset_id)
            if len(self._queue) > 240:
                dropped = self._queue[:-240]
                self._queue = self._queue[-240:]
                for asset_id in dropped:
                    self._seen.discard(asset_id)
        self._wake.set()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    def forget(self, asset_id: str) -> None:
        with self._lock:
            self._seen.discard(asset_id)

    def run(self) -> None:
        while not self._stop:
            self._wake.wait(0.3)
            self._wake.clear()
            while not self._stop:
                with self._lock:
                    if not self._queue:
                        break
                    asset_id = self._queue.pop()
                try:
                    data = self.source_getter().fetch_thumbnail(asset_id)
                except Exception:
                    with self._lock:
                        self._seen.discard(asset_id)
                    continue
                self.ready.emit(asset_id, data)
