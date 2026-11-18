"""ダウンロードの読み取り口。テストではメモリ上のバイト列を使う。"""

from __future__ import annotations

from typing import Protocol


class BinaryStream(Protocol):
    status: int

    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


class ResponseStream:
    """requests の応答を、少しずつ読む。"""

    def __init__(self, response: object) -> None:
        self._response = response
        self.status = int(getattr(response, "status_code", 200))
        self._iter = response.iter_content(chunk_size=256 * 1024)  # type: ignore[attr-defined]
        self._extra = b""

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunks = [self._extra]
            self._extra = b""
            for chunk in self._iter:
                if chunk:
                    chunks.append(chunk)
            return b"".join(chunks)
        while len(self._extra) < size:
            try:
                chunk = next(self._iter)
            except StopIteration:
                break
            if chunk:
                self._extra += chunk
        taken = self._extra[:size]
        self._extra = self._extra[size:]
        return taken

    def close(self) -> None:
        close = getattr(self._response, "close", None)
        if close:
            close()


class ByteStream:
    def __init__(self, data: bytes, start: int = 0, *, status: int | None = None) -> None:
        if start < 0:
            start = 0
        self._data = data[start:]
        self._pos = 0
        if status is None:
            self.status = 206 if start else 200
        else:
            self.status = status
        # status 200 で start>0 のときは、サーバが範囲を無視して全体を返した扱い。
        if self.status == 200:
            self._data = data
            self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._data) - self._pos
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        return None
