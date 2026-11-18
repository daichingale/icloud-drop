"""キャッシュ済みのファイルを、Qtのドラッグで渡す。Windows以外の確認用。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl, Qt
from PySide6.QtGui import QColor, QDrag, QPainter, QPixmap


def start_file_drag(widget, paths: list[Path]) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    drag = QDrag(widget)
    drag.setMimeData(mime)
    pixmap = QPixmap(128, 40)
    pixmap.fill(QColor("#2f6f6a"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("white"))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, f"{len(paths)} 件")
    painter.end()
    drag.setPixmap(pixmap)
    drag.exec(Qt.DropAction.CopyAction)
