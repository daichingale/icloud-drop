import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from icloud_drop.credentials import PasswordVault
from icloud_drop.formatutil import local_date
from icloud_drop.settings import Settings
from icloud_drop.ui.main_window import AppContext, MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(tmp_path, qapp, count=16):
    context = AppContext(
        data_dir=tmp_path,
        settings=Settings(),
        vault=PasswordVault(tmp_path / "secrets", dpapi=None, keyring_module=None),
        mock_mode=True,
        demo_count=count,
    )
    window = MainWindow(context)
    window.prefer_blocking = True
    window.resize(1100, 720)
    window.show()
    qapp.processEvents()
    return window


def test_demo_select_save_and_drag_prep(tmp_path, qapp):
    window = _window(tmp_path, qapp)
    try:
        window.open_demo(blocking=True)
        window.grid.viewport().repaint()
        qapp.processEvents()
        assert window.banner.isVisible()
        assert len(window.grid.flat_ids) == 16
        thumb_rows = [row for row in window.grid._rows if getattr(row, "assets", None)]
        assert thumb_rows and max(len(row.assets) for row in thumb_rows) > 1
        window.grid_select_all()
        assert len(window.grid.selection) == 16
        target = tmp_path / "saved"
        summary = window.save_to(target, blocking=True)
        assert summary.failed == 0
        assert summary.downloaded > 0
        names = {path.name for path in target.iterdir()}
        assert "icloud-drop-manifest.json" in names
        assert any(name.startswith("IMG_") for name in names)
        again = window.save_to(target, blocking=True)
        assert again.downloaded == 0
        assert again.skipped > 0

        asset = window.asset_by_id[window.grid.flat_ids[0]]
        day = local_date(asset.created)
        window.date_check.setChecked(True)
        window.date_from.setDate(QDate(day.year, day.month, day.day))
        window.date_to.setDate(QDate(day.year, day.month, day.day))
        window.select_by_date()
        assert 0 < len(window.grid.selection) < 16

        chosen = window.grid.selected_ids_in_order()[:1]
        window.grid.select_ids(chosen)
        window.begin_drag(chosen)
        assert "準備" in window.status_label.text() or "ドラッグ" in window.status_label.text()
        image_bytes = window.source.fetch_thumbnail(chosen[0])
        from PySide6.QtGui import QImage

        assert not QImage.fromData(image_bytes).isNull()
    finally:
        window.close()
        qapp.processEvents()


def test_practice_sign_in_reaches_library(tmp_path, qapp):
    window = _window(tmp_path / "login", qapp, count=12)
    try:
        window._on_sign_in()
        assert window.login_error.isVisible()
        result = window.sign_in_blocking("family@icloud.com", "secret")
        assert result.status == "need_2fa"
        assert window.stack.currentWidget() is window.code_page
        done = window.submit_code_blocking("123456")
        assert done.status == "ok"
        assert window.stack.currentWidget() is window.library_page
        assert len(window.grid.flat_ids) == 12
        window.kind_buttons["video"].click()
        qapp.processEvents()
        assert window.grid.flat_ids
        assert all(window.asset_by_id[asset_id].kind == "video" for asset_id in window.grid.flat_ids)
    finally:
        window.close()
        qapp.processEvents()
