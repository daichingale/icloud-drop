"""ドラッグの入口。Windows以外では、ここで止めて本体を読み込まない。"""

from __future__ import annotations

import sys
from collections.abc import Callable


def ole_available() -> bool:
    return sys.platform == "win32"


def drag_virtual_files(
    entries: list[tuple[str, int]],
    ensure_path: Callable[[int], str],
    existing_paths: list[str | None] | None = None,
) -> None:
    if not ole_available():
        raise OSError("エクスプローラーへのドラッグはWindowsだけで使えます。")
    from icloud_drop.ole_windows import drag_virtual_files as start

    start(entries, ensure_path, existing_paths)
