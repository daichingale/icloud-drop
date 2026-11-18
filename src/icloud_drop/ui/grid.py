"""日付でまとめたサムネイル。見えている範囲だけ絵を描く。"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractScrollArea, QApplication

from icloud_drop.models import Asset, Group
from icloud_drop.selection import Selection

PAD = 16
GAP = 10
THUMB = 148
CAPTION = 22
HEADER = 36


@dataclass
class _ThumbRow:
    y: int
    height: int
    assets: list[Asset]


@dataclass
class _HeaderRow:
    y: int
    height: int
    title: str


class ThumbnailGrid(QAbstractScrollArea):
    selection_changed = Signal()
    drag_started = Signal(list)
    activated = Signal(str)
    wants_thumbnails = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.viewport().installEventFilter(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setMouseTracking(False)
        self.selection = Selection()
        self.groups: list[Group] = []
        self.flat_ids: list[str] = []
        self._assets: dict[str, Asset] = {}
        self._rows: list[_ThumbRow | _HeaderRow] = []
        self._cache: dict[str, QPixmap] = {}
        self._order: list[str] = []
        self._press_id: str | None = None
        self._press_pos = QPoint()
        self._did_drag = False
        self._layout_width = -1
        self.verticalScrollBar().valueChanged.connect(lambda _value: self._ask_thumbs())
        self.viewport().setAutoFillBackground(False)

    def set_groups(self, groups: list[Group], *, reset_scroll: bool) -> None:
        self.groups = groups
        self.flat_ids = [asset.id for group in groups for asset in group.assets]
        self._assets = {asset.id: asset for group in groups for asset in group.assets}
        visible = set(self.flat_ids)
        self.selection.chosen &= visible
        if self.selection.anchor not in visible:
            self.selection.anchor = None
        self._relayout()
        if reset_scroll:
            self.verticalScrollBar().setValue(0)
        self.viewport().update()
        self._ask_thumbs()
        self.selection_changed.emit()

    def select_ids(self, ids: list[str]) -> None:
        visible = [asset_id for asset_id in ids if asset_id in self._assets]
        self.selection.set_ids(visible)
        self.viewport().update()
        self.selection_changed.emit()

    def select_all(self) -> None:
        self.selection.set_ids(list(self.flat_ids))
        self.viewport().update()
        self.selection_changed.emit()

    def clear_selection(self) -> None:
        self.selection.clear()
        self.viewport().update()
        self.selection_changed.emit()

    def selected_ids_in_order(self) -> list[str]:
        chosen = self.selection.chosen
        return [asset_id for asset_id in self.flat_ids if asset_id in chosen]

    def set_thumbnail(self, asset_id: str, image: QImage) -> None:
        if image.isNull() or asset_id not in self._assets:
            return
        self._cache[asset_id] = QPixmap.fromImage(image)
        self._order.append(asset_id)
        if len(self._order) > 500:
            old = self._order.pop(0)
            if old not in self._order:
                self._cache.pop(old, None)
        self.viewport().update()

    def pixmap_for(self, asset_id: str) -> QPixmap | None:
        return self._cache.get(asset_id)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()
        self._ask_thumbs()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.viewport():
            kind = event.type()
            if kind == QEvent.Type.Paint:
                self.paint_viewport()
                return True
            if kind == QEvent.Type.MouseButtonPress:
                self.on_press(event)
                return True
            if kind == QEvent.Type.MouseMove:
                self.on_move(event)
                return True
            if kind == QEvent.Type.MouseButtonRelease:
                self.on_release(event)
                return True
            if kind == QEvent.Type.MouseButtonDblClick:
                self.on_double(event)
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_A and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.select_all()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.clear_selection()
            return
        super().keyPressEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self.viewport().update()

    def _columns(self) -> int:
        width = max(self.viewport().width(), THUMB + PAD * 2)
        return max(1, (width - PAD * 2 + GAP) // (THUMB + GAP))

    def _relayout(self) -> None:
        cols = self._columns()
        y = PAD
        rows: list[_ThumbRow | _HeaderRow] = []
        for group in self.groups:
            if group.title:
                rows.append(_HeaderRow(y, HEADER, group.title))
                y += HEADER
            assets = group.assets
            for start in range(0, len(assets), cols):
                chunk = assets[start : start + cols]
                rows.append(_ThumbRow(y, THUMB + CAPTION, chunk))
                y += THUMB + CAPTION + GAP
        self._rows = rows
        self._layout_width = self.viewport().width()
        total = y + PAD
        bar = self.verticalScrollBar()
        bar.setRange(0, max(0, total - self.viewport().height()))
        bar.setPageStep(self.viewport().height() or 1)
        self.viewport().update()

    def _cell_rect(self, column: int) -> QRect:
        return QRect(PAD + column * (THUMB + GAP), 0, THUMB, THUMB + CAPTION)

    def hit(self, pos: QPoint) -> str | None:
        y = pos.y() + self.verticalScrollBar().value()
        x = pos.x()
        for row in self._rows:
            if not isinstance(row, _ThumbRow):
                continue
            if y < row.y or y >= row.y + row.height:
                continue
            local_y = y - row.y
            for column, asset in enumerate(row.assets):
                rect = self._cell_rect(column)
                if rect.contains(x, local_y):
                    return asset.id
        return None

    def on_press(self, event) -> None:
        self.setFocus()
        if event.button() != Qt.MouseButton.LeftButton:
            return
        asset_id = self.hit(event.position().toPoint())
        self._press_id = asset_id
        self._press_pos = event.position().toPoint()
        self._did_drag = False
        if asset_id is None:
            if not (event.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)):
                self.clear_selection()
            return
        self.selection.click(
            self.flat_ids,
            asset_id,
            shift=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier),
            ctrl=bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier),
        )
        self.viewport().update()
        self.selection_changed.emit()

    def on_move(self, event) -> None:
        if self._did_drag or self._press_id is None:
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        distance = (event.position().toPoint() - self._press_pos).manhattanLength()
        if distance < QApplication.startDragDistance():
            return
        self._did_drag = True
        ids = self.selected_ids_in_order()
        if self._press_id not in ids:
            ids = [self._press_id]
        self.drag_started.emit(ids)

    def on_release(self, _event) -> None:
        self._did_drag = False
        self._press_id = None

    def on_double(self, event) -> None:
        asset_id = self.hit(event.position().toPoint())
        if asset_id:
            self.activated.emit(asset_id)

    def paint_viewport(self) -> None:
        if self._layout_width != self.viewport().width():
            self._relayout()
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.viewport().rect(), QColor("#f3f0e8"))
        scroll = self.verticalScrollBar().value()
        painter.translate(0, -scroll)
        top = scroll
        bottom = scroll + self.viewport().height()
        font = QFont(painter.font())
        font.setPointSize(10)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        for row in self._rows:
            if row.y + row.height < top - 20:
                continue
            if row.y > bottom + 20:
                break
            if isinstance(row, _HeaderRow):
                painter.setPen(QColor("#3d4a45"))
                painter.drawText(QRect(PAD, row.y, self.viewport().width() - PAD * 2, HEADER), Qt.AlignmentFlag.AlignVCenter, row.title)
                continue
            for column, asset in enumerate(row.assets):
                rect = self._cell_rect(column).translated(0, row.y)
                self._paint_cell(painter, metrics, rect, asset)
        painter.end()

    def _paint_cell(self, painter: QPainter, metrics: QFontMetrics, rect: QRect, asset: Asset) -> None:
        thumb = QRect(rect.x(), rect.y(), THUMB, THUMB)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e6e0d4"))
        painter.drawRoundedRect(thumb, 10, 10)
        pixmap = self._cache.get(asset.id)
        if pixmap is not None and not pixmap.isNull():
            scaled = pixmap.scaled(THUMB - 8, THUMB - 8, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            x = thumb.x() + (THUMB - scaled.width()) // 2
            y = thumb.y() + (THUMB - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            painter.setPen(QColor("#8a8175"))
            painter.drawText(thumb, Qt.AlignmentFlag.AlignCenter, "…")
        if asset.kind == "video":
            self._badge(painter, thumb.adjusted(8, THUMB - 28, -THUMB + 52, -8), "動画")
        elif asset.is_live:
            self._badge(painter, thumb.adjusted(8, THUMB - 28, -THUMB + 58, -8), "Live")
        if asset.id in self.selection:
            painter.setBrush(QColor(47, 111, 106, 70))
            painter.setPen(QPen(QColor("#2f6f6a"), 3))
            painter.drawRoundedRect(thumb.adjusted(1, 1, -1, -1), 10, 10)
        painter.setPen(QColor("#24302c"))
        caption = metrics.elidedText(asset.filename, Qt.TextElideMode.ElideMiddle, THUMB - 4)
        painter.drawText(QRect(rect.x(), rect.y() + THUMB, THUMB, CAPTION), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, caption)

    def _badge(self, painter: QPainter, rect: QRect, text: str) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 24, 28, 180))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QColor("white"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def visible_ids(self) -> list[str]:
        top = self.verticalScrollBar().value() - 300
        bottom = self.verticalScrollBar().value() + self.viewport().height() + 500
        ids: list[str] = []
        for row in self._rows:
            if not isinstance(row, _ThumbRow):
                continue
            if row.y + row.height < top or row.y > bottom:
                continue
            ids.extend(asset.id for asset in row.assets)
        return ids

    def _ask_thumbs(self) -> None:
        missing = [asset_id for asset_id in self.visible_ids() if asset_id not in self._cache]
        if missing:
            self.wants_thumbnails.emit(missing[:80])
