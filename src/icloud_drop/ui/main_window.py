"""サインインと、写真の一覧。"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QImage, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from icloud_drop.credentials import PasswordVault
from icloud_drop.download import (
    CancelToken,
    DragFile,
    ProgressEvent,
    SaveSummary,
    build_drag_plan,
    cache_file,
    ensure_cached,
    save_assets,
)
from icloud_drop.errors import PasswordStoreError, explain_exception
from icloud_drop.filenames import sanitize_filename
from icloud_drop.formatutil import format_ja_datetime, human_size
from icloud_drop.library_index import filter_sort, group_assets, ids_between
from icloud_drop.mock_backend import MockSource
from icloud_drop.models import Album, Asset, AuthResult
from icloud_drop.pyicloud_backend import PyiCloudSource
from icloud_drop.settings import Settings
from icloud_drop.source import PhotoSource
from icloud_drop.ui.grid import ThumbnailGrid
from icloud_drop.ui.qdrag import start_file_drag
from icloud_drop.ui.workers import CallWorker, LibraryWorker, ThumbWorker

LOGGER = logging.getLogger(__name__)


@dataclass
class AppContext:
    data_dir: Path
    settings: Settings
    vault: PasswordVault
    mock_mode: bool
    demo_count: int = 180

    def make_source(self) -> PhotoSource:
        session = self.data_dir / "session"
        if self.mock_mode:
            from icloud_drop.mock_backend import build_demo_library

            return MockSource(session, library=build_demo_library(self.demo_count))
        return PyiCloudSource(session)

    def make_demo(self) -> PhotoSource:
        from icloud_drop.mock_backend import build_demo_library

        return MockSource(self.data_dir / "session", demo=True, library=build_demo_library(self.demo_count))


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.settings = context.settings
        self.vault = context.vault
        self.source: PhotoSource = context.make_source()
        self.prefer_blocking = False
        self.viewing_demo = False
        self.asset_by_id: dict[str, Asset] = {}
        self.assets_by_album: dict[str, list[Asset]] = {}
        self._albums: list[Album] = []
        self._token = 0
        self._library_worker: LibraryWorker | None = None
        self._worker_running = False
        self._pending: tuple[str, bool] | None = None
        self._call_worker: CallWorker | None = None
        self._save_worker: CallWorker | None = None
        self._saving = False
        self._password_note = ""
        self._sync_summary: SaveSummary | None = None
        self._two_sa_mode = False
        self._phones: tuple = ()

        self.setWindowTitle("iCloudの写真")
        self.resize(1180, 760)
        self.setMinimumSize(860, 560)

        root = QWidget()
        root.setObjectName("app")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 12)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self._build_login()
        self._build_code()
        self._build_library()
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.code_page)
        self.stack.addWidget(self.library_page)
        self.stack.setCurrentWidget(self.login_page)

        QShortcut(QKeySequence.StandardKey.SelectAll, self, self.grid.select_all)
        self._restore_form()
        self._thumbs = ThumbWorker(lambda: self.source)
        self._thumbs.ready.connect(self._on_thumb)
        self._thumbs.start()

    # --- 画面の組み立て ---

    def _build_login(self) -> None:
        self.login_page = QWidget()
        outer = QVBoxLayout(self.login_page)
        outer.addStretch(1)
        card = QFrame()
        card.setObjectName("card")
        card.setMaximumWidth(520)
        form = QVBoxLayout(card)
        form.setContentsMargins(28, 28, 28, 28)
        form.setSpacing(10)
        title = QLabel("iCloudの写真")
        title.setObjectName("title")
        subtitle = QLabel("選んだ写真と動画を、パソコンのフォルダへドラッグして保存します。")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        self.apple = QLineEdit()
        self.apple.setPlaceholderText("Apple ID（メールアドレス）")
        self.password = QLineEdit()
        self.password.setPlaceholderText("パスワード")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.returnPressed.connect(self._on_sign_in)
        self.remember = QCheckBox("パスワードをこのパソコンに保存する")
        self.adp = QCheckBox("高度なデータ保護のWebアクセスを使う")
        self.china = QCheckBox("中国本土のiCloud（icloud.com.cn）を使う")
        hint = QLabel(
            "高度なデータ保護をオンにしている場合は、iPhoneで次もオンにしてください。\n"
            "設定 → 自分の名前 → iCloud →「Web上のiCloudデータにアクセス」\n"
            "初回は、iPhoneに届く「許可」を2分以内に押します。"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.practice_label = QLabel("練習用です。パスワードは wrong 以外、確認コードは 123456 です。iCloudには繋がりません。")
        self.practice_label.setObjectName("quiet")
        self.practice_label.setWordWrap(True)
        self.login_error = QLabel("")
        self.login_error.setObjectName("error")
        self.login_error.setWordWrap(True)
        self.login_error.hide()
        self.sign_in_button = QPushButton("サインイン")
        self.sign_in_button.clicked.connect(self._on_sign_in)
        self.demo_button = QPushButton("デモで試す")
        self.demo_button.setObjectName("secondary")
        self.demo_button.clicked.connect(self._on_demo)
        self.login_status = QLabel("")
        self.login_status.setObjectName("quiet")
        self.login_status.setWordWrap(True)
        form.addWidget(title)
        form.addWidget(subtitle)
        form.addWidget(self.apple)
        form.addWidget(self.password)
        form.addWidget(self.remember)
        form.addWidget(self.adp)
        form.addWidget(self.china)
        form.addWidget(hint)
        form.addWidget(self.practice_label)
        form.addWidget(self.login_error)
        form.addWidget(self.sign_in_button)
        form.addWidget(self.demo_button)
        form.addWidget(self.login_status)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        self.practice_label.setVisible(self.source.practice)

    def _build_code(self) -> None:
        self.code_page = QWidget()
        outer = QVBoxLayout(self.code_page)
        outer.addStretch(1)
        card = QFrame()
        card.setObjectName("card")
        card.setMaximumWidth(520)
        form = QVBoxLayout(card)
        form.setContentsMargins(28, 28, 28, 28)
        form.setSpacing(10)
        title = QLabel("確認コード")
        title.setObjectName("title")
        self.code_help = QLabel("信頼できるデバイスに届いた6桁のコードを入力してください。")
        self.code_help.setWordWrap(True)
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("6桁のコード")
        self.code_input.setMaxLength(6)
        self.code_input.returnPressed.connect(self._on_submit_code)
        self.code_button = QPushButton("確認")
        self.code_button.clicked.connect(self._on_submit_code)
        self.sms_button = QPushButton("SMSでコードを受け取る")
        self.sms_button.setObjectName("secondary")
        self.sms_button.clicked.connect(self._on_sms)
        self.approve_button = QPushButton("iPhoneで許可した")
        self.approve_button.setObjectName("secondary")
        self.approve_button.clicked.connect(self._on_approve)
        self.key_button = QPushButton("セキュリティキーで承認")
        self.key_button.setObjectName("secondary")
        self.key_button.clicked.connect(self._on_security_key)
        self.back_button = QPushButton("戻る")
        self.back_button.setObjectName("secondary")
        self.back_button.clicked.connect(lambda: self.stack.setCurrentWidget(self.login_page))
        self.code_error = QLabel("")
        self.code_error.setObjectName("error")
        self.code_error.setWordWrap(True)
        self.code_error.hide()
        self.code_status = QLabel("")
        self.code_status.setObjectName("quiet")
        self.code_status.setWordWrap(True)
        for widget in (
            title,
            self.code_help,
            self.code_input,
            self.code_button,
            self.sms_button,
            self.approve_button,
            self.key_button,
            self.back_button,
            self.code_error,
            self.code_status,
        ):
            form.addWidget(widget)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

    def _build_library(self) -> None:
        self.library_page = QWidget()
        layout = QVBoxLayout(self.library_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.grid = ThumbnailGrid()

        header = QHBoxLayout()
        title = QLabel("iCloudの写真")
        title.setObjectName("title")
        self.who = QLabel("")
        self.who.setObjectName("quiet")
        header.addWidget(title)
        header.addWidget(self.who, 1)
        self.refresh_button = QPushButton("更新")
        self.refresh_button.setObjectName("secondary")
        self.refresh_button.clicked.connect(self._on_refresh)
        self.sign_out_button = QPushButton("サインアウト")
        self.sign_out_button.setObjectName("secondary")
        self.sign_out_button.clicked.connect(self._on_sign_out)
        header.addWidget(self.refresh_button)
        header.addWidget(self.sign_out_button)
        layout.addLayout(header)

        self.banner = QLabel("")
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        layout.addWidget(self.banner)

        tools = QHBoxLayout()
        self.album_combo = QComboBox()
        self.album_combo.setMinimumWidth(180)
        self.album_combo.currentIndexChanged.connect(self._on_album_changed)
        self.kind_group = QButtonGroup(self)
        self.kind_group.setExclusive(True)
        self.kind_buttons: dict[str, QPushButton] = {}
        for key, label in (("all", "すべて"), ("photo", "写真"), ("video", "動画")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("kind", "1")
            self.kind_group.addButton(button)
            self.kind_buttons[key] = button
            tools.addWidget(button)
        self.kind_buttons["all"].setChecked(True)
        self.kind_group.buttonClicked.connect(lambda _button: self.refresh_view(reset_scroll=True))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("新しい順", "newest")
        self.sort_combo.addItem("古い順", "oldest")
        self.sort_combo.addItem("名前順", "name")
        self.sort_combo.currentIndexChanged.connect(lambda _index: self.refresh_view(reset_scroll=True))
        tools.addWidget(self.album_combo, 1)
        tools.addWidget(self.sort_combo)
        layout.addLayout(tools)

        dates = QHBoxLayout()
        self.date_check = QCheckBox("日付を指定")
        self.date_from = QDateEdit()
        self.date_to = QDateEdit()
        for edit in (self.date_from, self.date_to):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy/MM/dd")
            edit.setDate(QDate.currentDate())
            edit.setEnabled(False)
        self.date_check.toggled.connect(self._on_date_toggled)
        self.date_from.dateChanged.connect(lambda _date: self.refresh_view(reset_scroll=False))
        self.date_to.dateChanged.connect(lambda _date: self.refresh_view(reset_scroll=False))
        self.range_button = QPushButton("この期間を選択")
        self.range_button.setObjectName("secondary")
        self.range_button.clicked.connect(self.select_by_date)
        self.all_button = QPushButton("すべて選択")
        self.all_button.setObjectName("secondary")
        self.all_button.clicked.connect(self.grid_select_all)
        self.clear_button = QPushButton("選択解除")
        self.clear_button.setObjectName("secondary")
        self.clear_button.clicked.connect(lambda: self.grid.clear_selection())
        self.count_label = QLabel("0件")
        dates.addWidget(self.date_check)
        dates.addWidget(self.date_from)
        dates.addWidget(QLabel("から"))
        dates.addWidget(self.date_to)
        dates.addWidget(self.range_button)
        dates.addWidget(self.all_button)
        dates.addWidget(self.clear_button)
        dates.addStretch(1)
        dates.addWidget(self.count_label)
        layout.addLayout(dates)

        self.grid.selection_changed.connect(self._update_count)
        self.grid.drag_started.connect(self.begin_drag)
        self.grid.activated.connect(self._preview)
        self.grid.wants_thumbnails.connect(self._thumbs_later)
        layout.addWidget(self.grid, 1)

        footer = QVBoxLayout()
        footer.setSpacing(6)
        actions = QHBoxLayout()
        self.save_button = QPushButton("フォルダに保存")
        self.save_button.clicked.connect(self._on_save)
        self.heic_check = QCheckBox("HEICをJPEGにする")
        self.heic_check.setToolTip("「フォルダに保存」のときだけJPEGにします。ドラッグしたファイルは原本のままです。")
        self.skip_check = QCheckBox("すでに同じファイルがあれば飛ばす")
        self.skip_check.setChecked(True)
        self.live_check = QCheckBox("Live Photoの動画も保存する")
        self.live_check.setChecked(True)
        self.heic_check.setChecked(self.settings.convert_heic)
        self.skip_check.setChecked(self.settings.skip_existing)
        self.live_check.setChecked(self.settings.include_live)
        actions.addWidget(self.save_button)
        actions.addWidget(self.heic_check)
        actions.addWidget(self.skip_check)
        actions.addWidget(self.live_check)
        actions.addStretch(1)
        self.status_label = QLabel("選んだ写真を、エクスプローラーのフォルダへドラッグできます。")
        self.status_label.setObjectName("quiet")
        self.status_label.setWordWrap(True)
        footer.addLayout(actions)
        footer.addWidget(self.status_label)
        layout.addLayout(footer)

        cache_row = QHBoxLayout()
        cache_button = QPushButton("キャッシュを削除")
        cache_button.setObjectName("secondary")
        cache_button.clicked.connect(self._clear_cache)
        cache_row.addWidget(cache_button)
        cache_row.addStretch(1)
        layout.addLayout(cache_row)

    def _thumbs_later(self, ids: list[str]) -> None:
        if hasattr(self, "_thumbs"):
            self._thumbs.request(ids)

    def _restore_form(self) -> None:
        if self.settings.apple_id:
            self.apple.setText(self.settings.apple_id)
        self.remember.setChecked(self.settings.remember_password)
        self.adp.setChecked(self.settings.adp_web_access)
        self.china.setChecked(self.settings.domain == "cn")
        if self.settings.remember_password and self.settings.apple_id:
            try:
                stored = self.vault.load(self.settings.apple_id)
            except Exception:
                stored = None
            if stored:
                self.password.setText(stored)
            else:
                self.login_status.setText("保存していたパスワードは読めませんでした。もう一度入力してください。")

    # --- サインイン ---

    def _domain(self) -> str:
        return "cn" if self.china.isChecked() else "com"

    def _on_sign_in(self) -> None:
        apple_id = self.apple.text().strip()
        password = self.password.text()
        if not apple_id or not password:
            self._show_login_error("Apple IDとパスワードを入力してください。", "")
            return
        self._show_login_error("", "")
        self.login_status.setText("サインインしています…")
        self.sign_in_button.setEnabled(False)

        def run() -> AuthResult:
            return self.source.login(
                apple_id,
                password,
                adp_web_access=self.adp.isChecked(),
                domain=self._domain(),
                on_status=lambda text: None,
            )

        if self.prefer_blocking:
            self.sign_in_button.setEnabled(True)
            self._apply_auth(run(), apple_id, password)
            return
        self._start_call(run, lambda result: self._apply_auth(result, apple_id, password))

    def sign_in_blocking(self, apple_id: str, password: str) -> AuthResult:
        self.apple.setText(apple_id)
        self.password.setText(password)
        result = self.source.login(
            apple_id,
            password,
            adp_web_access=self.adp.isChecked(),
            domain=self._domain(),
        )
        self._apply_auth_blocking(result, apple_id, password)
        return result

    def submit_code_blocking(self, code: str) -> AuthResult:
        result = self.source.submit_code(code)
        self._apply_auth_blocking(result, self.apple.text().strip(), self.password.text())
        return result

    def approve_blocking(self) -> AuthResult:
        result = self.source.approve_on_device()
        self._apply_auth_blocking(result, self.apple.text().strip(), self.password.text())
        return result

    def _apply_auth_blocking(self, result: AuthResult, apple_id: str, password: str) -> None:
        previous = self.prefer_blocking
        self.prefer_blocking = True
        try:
            self._apply_auth(result, apple_id, password)
        finally:
            self.prefer_blocking = previous

    def _on_submit_code(self) -> None:
        code = self.code_input.text().strip()
        if len(code) != 6 or not code.isdigit():
            self._show_code_error("6桁の数字を入力してください。")
            return
        self.code_button.setEnabled(False)
        self.code_status.setText("確認しています…")

        def run() -> AuthResult:
            if self._two_sa_mode:
                return self.source.submit_2sa_code(0, code, on_status=lambda text: None)
            return self.source.submit_code(code, on_status=lambda text: None)

        if self.prefer_blocking:
            self.code_button.setEnabled(True)
            self._apply_auth(run(), self.apple.text().strip(), self.password.text())
            return
        self._start_call(run, lambda result: self._apply_auth(result, self.apple.text().strip(), self.password.text()))

    def _on_sms(self) -> None:
        phones = getattr(self, "_phones", ())
        if not phones:
            self._show_code_error("SMSの送り先がありません。デバイスに届いたコードを入力してください。")
            return

        def run() -> AuthResult:
            return self.source.send_sms(phones[0].id)

        self._start_call(run, self._apply_side_status)

    def _on_approve(self) -> None:
        self.code_status.setText("iPhoneの許可を待っています…")

        def run() -> AuthResult:
            return self.source.approve_on_device(on_status=lambda text: None)

        if self.prefer_blocking:
            self._apply_auth(run(), self.apple.text().strip(), self.password.text())
            return
        self._start_call(run, lambda result: self._apply_auth(result, self.apple.text().strip(), self.password.text()))

    def _on_security_key(self) -> None:
        self.code_status.setText("セキュリティキーに触れてください。")

        def run() -> AuthResult:
            return self.source.approve_security_key(on_status=lambda text: None)

        self._start_call(run, lambda result: self._apply_auth(result, self.apple.text().strip(), self.password.text()))

    def _start_call(self, fn, on_ok) -> None:
        worker = CallWorker(fn)
        self._call_worker = worker

        def ok(result) -> None:
            self.sign_in_button.setEnabled(True)
            self.code_button.setEnabled(True)
            on_ok(result)

        def failed(message: str, hint: str) -> None:
            self.sign_in_button.setEnabled(True)
            self.code_button.setEnabled(True)
            self.login_status.setText("")
            if self.stack.currentWidget() is self.code_page:
                self._show_code_error(message, hint)
            else:
                self._show_login_error(message, hint)

        worker.succeeded.connect(ok)
        worker.failed.connect(failed)
        worker.start()

    def _apply_side_status(self, result: AuthResult) -> None:
        self.code_status.setText(result.message)
        if result.status == "failed":
            self._show_code_error(result.message, result.hint)

    def _apply_auth(self, result: AuthResult, apple_id: str, password: str) -> None:
        self.sign_in_button.setEnabled(True)
        self.code_button.setEnabled(True)
        self._phones = result.phones
        self._two_sa_mode = result.status == "need_2sa"
        if result.status == "failed":
            if self.stack.currentWidget() is self.code_page:
                self._show_code_error(result.message, result.hint)
            else:
                self._show_login_error(result.message, result.hint)
            return
        if result.status in {"need_2fa", "need_2sa", "need_device_approval"}:
            self.code_help.setText(result.message or "確認コードを入力してください。")
            self.sms_button.setVisible(bool(result.phones))
            self.key_button.setVisible(result.security_key)
            self.approve_button.setVisible(True)
            self._show_code_error(result.hint, "")
            if result.hint:
                self.code_error.show()
            self.stack.setCurrentWidget(self.code_page)
            self.code_input.setFocus()
            self.code_status.setText(result.message)
            return
        if result.status == "ok":
            self._remember_password(apple_id, password)
            self.viewing_demo = False
            self.who.setText(apple_id)
            self.banner.hide()
            self.stack.setCurrentWidget(self.library_page)
            self.load_library(blocking=self.prefer_blocking)
            return
        self._show_login_error(result.message or "サインインできませんでした。", result.hint)

    def _remember_password(self, apple_id: str, password: str) -> None:
        self.settings.apple_id = apple_id
        self.settings.remember_password = self.remember.isChecked()
        self.settings.adp_web_access = self.adp.isChecked()
        self.settings.domain = self._domain()
        self.settings.china_domain = self.china.isChecked()
        self._password_note = ""
        if self.remember.isChecked() and password:
            try:
                self.vault.save(apple_id, password)
            except PasswordStoreError as exc:
                self._password_note = str(exc)
        else:
            self.vault.delete(apple_id)
        self._persist_settings()

    def _show_login_error(self, message: str, hint: str) -> None:
        text = message
        if hint:
            text = f"{message}\n{hint}" if message else hint
        self.login_error.setText(text)
        self.login_error.setVisible(bool(text))
        if text:
            self.login_status.setText("")

    def _show_code_error(self, message: str, hint: str = "") -> None:
        text = message
        if hint:
            text = f"{message}\n{hint}" if message else hint
        self.code_error.setText(text)
        self.code_error.setVisible(bool(text))

    def _on_demo(self) -> None:
        self.open_demo(blocking=self.prefer_blocking)

    def open_demo(self, *, blocking: bool = False) -> None:
        self._stop_library_worker()
        try:
            self.source.close()
        except Exception:
            LOGGER.info("前の接続を閉じるときに問題がありました。")
        self.source = self.context.make_demo()
        self.viewing_demo = True
        self.who.setText("サンプル")
        self.banner.setText("サンプルの写真です。自分のiCloudには繋がっていません。ドラッグと保存の練習ができます。")
        self.banner.show()
        self.stack.setCurrentWidget(self.library_page)
        self.load_library(blocking=blocking or self.prefer_blocking, force_albums=True)

    def _on_sign_out(self) -> None:
        answer = QMessageBox.question(
            self,
            "サインアウト",
            "このパソコンに保存したサインイン情報を消して、最初の画面に戻ります。\nダウンロード済みのキャッシュは残ります。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.source.forget()
        except Exception:
            LOGGER.info("サインイン情報の削除で問題がありました。")
        self.vault.delete(self.settings.apple_id)
        self.settings.remember_password = False
        self.remember.setChecked(False)
        self.password.clear()
        self._persist_settings()
        self.viewing_demo = False
        self.asset_by_id.clear()
        self.assets_by_album.clear()
        self.grid.set_groups([], reset_scroll=True)
        self.source = self.context.make_source()
        self.practice_label.setVisible(self.source.practice)
        self.stack.setCurrentWidget(self.login_page)

    # --- 一覧 ---

    def load_library(self, *, blocking: bool = False, force_albums: bool = True) -> None:
        album_id = "all"
        if blocking:
            try:
                albums = self.source.list_albums()
                self._set_albums(albums)
                self.assets_by_album[album_id] = []
                self.asset_by_id = {}
                for batch in self.source.iter_batches(album_id):
                    self._append_batch(album_id, batch)
                self.refresh_view(reset_scroll=True)
                self._set_status(self._password_note or self._count_text())
            except Exception as exc:
                message, hint = explain_exception(exc)
                self._show_banner(message, hint)
            return
        self._request_load(album_id, force_albums)

    def _request_load(self, album_id: str, need_albums: bool) -> None:
        if self._worker_running:
            self._pending = (album_id, need_albums)
            self._set_status("いまの読み込みのあとで切り替えます…")
            return
        self._token += 1
        token = self._token
        self._worker_running = True
        if need_albums:
            self.assets_by_album.clear()
            self.asset_by_id.clear()
        else:
            self.assets_by_album[album_id] = []
        self._set_status("読み込んでいます…")
        worker = LibraryWorker(self.source, album_id, token, need_albums)
        self._library_worker = worker
        worker.albums_ready.connect(self._set_albums)
        worker.batch_ready.connect(self._on_batch)
        worker.failed.connect(self._on_library_failed)
        worker.succeeded.connect(self._on_library_done)
        worker.start()

    def _on_batch(self, token: int, album_id: str, batch: list[Asset]) -> None:
        if token != self._token:
            return
        self._append_batch(album_id, batch)
        if self._current_album() == album_id:
            self.refresh_view(reset_scroll=False)

    def _append_batch(self, album_id: str, batch: list[Asset]) -> None:
        current = self.assets_by_album.setdefault(album_id, [])
        for asset in batch:
            current.append(asset)
            self.asset_by_id[asset.id] = asset

    def _set_albums(self, albums: list[Album]) -> None:
        self._albums = list(albums)
        self.album_combo.blockSignals(True)
        self.album_combo.clear()
        for album in albums:
            self.album_combo.addItem(album.title, album.id)
        self.album_combo.blockSignals(False)

    def _on_library_failed(self, message: str, hint: str) -> None:
        self._worker_running = False
        self._show_banner(message, hint)
        self._set_status("読み込めませんでした。")
        self._continue_pending()

    def _on_library_done(self, token: int) -> None:
        self._worker_running = False
        if token == self._token:
            note = self._password_note
            self._password_note = ""
            self._set_status(note or self._count_text())
            self.refresh_view(reset_scroll=False)
        self._continue_pending()

    def _continue_pending(self) -> None:
        if not self._pending:
            return
        album_id, need = self._pending
        self._pending = None
        self._request_load(album_id, need)

    def _stop_library_worker(self) -> None:
        self._token += 1
        self._pending = None
        self._worker_running = False

    def _on_album_changed(self, _index: int) -> None:
        album_id = self._current_album()
        if album_id in self.assets_by_album and self.assets_by_album[album_id]:
            self.refresh_view(reset_scroll=True)
            return
        if self.prefer_blocking:
            try:
                self.assets_by_album[album_id] = []
                for batch in self.source.iter_batches(album_id):
                    self._append_batch(album_id, batch)
                self.refresh_view(reset_scroll=True)
            except Exception as exc:
                message, hint = explain_exception(exc)
                self._show_banner(message, hint)
            return
        self._request_load(album_id, False)

    def _on_refresh(self) -> None:
        self.assets_by_album.clear()
        self.asset_by_id.clear()
        self.grid.set_groups([], reset_scroll=True)
        if self.prefer_blocking:
            self.load_library(blocking=True)
            return
        self._request_load("all", True)

    def _on_date_toggled(self, checked: bool) -> None:
        self.date_from.setEnabled(checked)
        self.date_to.setEnabled(checked)
        if checked:
            assets = self._album_assets()
            if assets:
                dates = [asset.created.astimezone().date() for asset in assets]
                self.date_from.setDate(QDate(min(dates).year, min(dates).month, min(dates).day))
                self.date_to.setDate(QDate(max(dates).year, max(dates).month, max(dates).day))
        self.refresh_view(reset_scroll=True)

    def _current_album(self) -> str:
        value = self.album_combo.currentData()
        return str(value) if value else "all"

    def _kind(self) -> str:
        for key, button in self.kind_buttons.items():
            if button.isChecked():
                return key
        return "all"

    def _sort(self) -> str:
        return str(self.sort_combo.currentData() or "newest")

    def _album_assets(self) -> list[Asset]:
        return list(self.assets_by_album.get(self._current_album(), []))

    def _date_bounds(self):
        if not self.date_check.isChecked():
            return None, None
        return self.date_from.date().toPython(), self.date_to.date().toPython()

    def visible_assets(self) -> list[Asset]:
        start, end = self._date_bounds()
        return filter_sort(self._album_assets(), kind=self._kind(), date_from=start, date_to=end, sort=self._sort())

    def refresh_view(self, *, reset_scroll: bool) -> None:
        assets = self.visible_assets()
        groups = group_assets(assets, sort=self._sort())
        self.grid.set_groups(groups, reset_scroll=reset_scroll)
        self._update_count()

    def _update_count(self) -> None:
        selected = len(self.grid.selection)
        shown = len(self.grid.flat_ids)
        self.count_label.setText(f"選択 {selected} / 表示 {shown}")

    def _count_text(self) -> str:
        return f"表示 {len(self.grid.flat_ids)} 件。選んだ写真は、フォルダへドラッグできます。"

    def grid_select_all(self) -> None:
        self.grid.select_all()

    def select_by_date(self) -> None:
        if not self.date_check.isChecked():
            self._set_status("先に「日付を指定」をオンにして、始めと終わりを入れてください。")
            return
        start, end = self._date_bounds()
        if start is None or end is None:
            return
        base = filter_sort(self._album_assets(), kind=self._kind(), sort=self._sort())
        ids = ids_between(base, start, end)
        self.refresh_view(reset_scroll=True)
        self.grid.select_ids(ids)
        self._set_status(f"{len(ids)}件を選択しました。")

    def _show_banner(self, message: str, hint: str) -> None:
        text = message if not hint else f"{message}\n{hint}"
        self.banner.setText(text)
        self.banner.setObjectName("error")
        self.banner.show()

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_thumb(self, asset_id: str, data: bytes) -> None:
        image = QImage.fromData(data)
        self.grid.set_thumbnail(asset_id, image)

    def _preview(self, asset_id: str) -> None:
        asset = self.asset_by_id.get(asset_id)
        if asset is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(asset.filename)
        layout = QVBoxLayout(dialog)
        picture = QLabel()
        pixmap = self.grid.pixmap_for(asset_id)
        if pixmap is not None:
            picture.setPixmap(pixmap.scaled(640, 480, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            picture.setText("サムネイルを読み込んでいます。もう一度開いてください。")
        layout.addWidget(picture)
        extra = f"\nLive Photoの動画: {asset.live_filename}" if asset.is_live else ""
        layout.addWidget(QLabel(f"{asset.filename}\n{format_ja_datetime(asset.created)}\n{human_size(asset.size)}{extra}"))
        close = QPushButton("閉じる")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.resize(680, 560)
        dialog.exec()

    # --- ドラッグと保存 ---

    def _options_from_checks(self) -> None:
        self.settings.convert_heic = self.heic_check.isChecked()
        self.settings.skip_existing = self.skip_check.isChecked()
        self.settings.include_live = self.live_check.isChecked()

    def _opener(self, asset_id: str, which: str, start: int):
        return self.source.open_original(asset_id, which, start)

    def _cache_root(self) -> Path:
        path = self.context.data_dir / "cache" / "originals"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _ready_path(self, item: DragFile) -> Path | None:
        asset = self.asset_by_id.get(item.asset_id)
        if asset is None:
            return None
        filename = asset.filename if item.which == "original" else (asset.live_filename or item.filename)
        path = cache_file(self._cache_root(), asset.id, sanitize_filename(filename, fallback=asset.id))
        if path.exists() and item.size and path.stat().st_size == item.size:
            return path
        if path.exists() and not item.size and path.stat().st_size > 0:
            return path
        return None

    def begin_drag(self, ids: list[str]) -> None:
        if self._saving:
            self._set_status("保存が終わるまで待ってから、ドラッグしてください。")
            return
        assets = [self.asset_by_id[asset_id] for asset_id in ids if asset_id in self.asset_by_id]
        if not assets:
            return
        self._options_from_checks()
        plan = build_drag_plan(assets, include_live=self.settings.include_live)
        if sys.platform == "win32":
            self._drag_windows(plan)
            return
        missing = [item for item in plan if self._ready_path(item) is None]
        if missing:
            self._set_status(f"原本を準備しています（{len(missing)}件）。終わったら、もう一度ドラッグしてください。")
            self._precache(plan)
            return
        paths = []
        for item in plan:
            ready = self._ready_path(item)
            if ready is None:
                return
            paths.append(ready)
        start_file_drag(self.grid, paths)
        self._set_status("ドラッグを終了しました。")

    def _drag_windows(self, plan: list[DragFile]) -> None:
        try:
            from icloud_drop.ole_drag import drag_virtual_files
        except Exception:
            LOGGER.exception("ドラッグの準備に失敗しました")
            self._set_status("ドラッグを開始できませんでした。「フォルダに保存」を使ってください。")
            return
        existing: list[str | None] = []
        for item in plan:
            ready = self._ready_path(item)
            existing.append(str(ready) if ready else None)

        def ensure(index: int) -> str:
            item = plan[index]
            asset = self.asset_by_id[item.asset_id]
            path = ensure_cached(asset, item.which, cache_root=self._cache_root(), opener=self._opener)
            return str(path)

        try:
            drag_virtual_files([(item.filename, item.size) for item in plan], ensure, existing)
            self._set_status("ドラッグを終了しました。")
        except Exception:
            LOGGER.exception("ドラッグに失敗しました")
            self._set_status("ドラッグできませんでした。「フォルダに保存」を使ってください。")

    def _precache(self, plan: list[DragFile]) -> None:
        cache_root = self._cache_root()

        def run() -> str:
            for item in plan:
                asset = self.asset_by_id[item.asset_id]
                ensure_cached(asset, item.which, cache_root=cache_root, opener=self._opener)
            return "準備できました。選んだ写真をもう一度フォルダへドラッグしてください。"

        if self.prefer_blocking:
            self._set_status(run())
            return
        worker = CallWorker(run)
        self._call_worker = worker
        worker.succeeded.connect(self._set_status)
        worker.failed.connect(lambda message, hint: self._set_status(message if not hint else f"{message} {hint}"))
        worker.start()

    def _on_save(self) -> None:
        self.save_interactive()

    def save_interactive(self) -> None:
        ids = self.grid.selected_ids_in_order()
        if ids:
            assets = [self.asset_by_id[asset_id] for asset_id in ids if asset_id in self.asset_by_id]
        else:
            visible = self.visible_assets()
            if not visible:
                self._set_status("保存できる写真がありません。")
                return
            answer = QMessageBox.question(
                self,
                "フォルダに保存",
                f"選択されている写真がありません。いま表示中の{len(visible)}件をすべて保存しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            assets = visible
        folder = self._choose_folder()
        if not folder:
            return
        self._save_assets(assets, Path(folder), blocking=self.prefer_blocking)

    def save_to(self, directory: Path, *, blocking: bool = True, only_selected: bool = False) -> SaveSummary:
        if only_selected:
            ids = self.grid.selected_ids_in_order()
            assets = [self.asset_by_id[asset_id] for asset_id in ids]
        else:
            assets = self.visible_assets()
        return self._save_assets(assets, directory, blocking=blocking)

    def _choose_folder(self) -> str:
        start = self.settings.last_folder or str(Path.home())
        dialog = QFileDialog(self, "保存先のフォルダ", start)
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""
        chosen = dialog.selectedFiles()
        if not chosen:
            return ""
        self.settings.last_folder = chosen[0]
        self._persist_settings()
        return chosen[0]

    def _save_assets(self, assets: list[Asset], directory: Path, *, blocking: bool) -> SaveSummary:
        self._options_from_checks()
        self._persist_settings()
        token = CancelToken()
        kwargs = dict(
            dest_dir=directory,
            cache_root=self._cache_root(),
            opener=self._opener,
            include_live=self.settings.include_live,
            convert_heic=self.settings.convert_heic,
            skip_existing=self.settings.skip_existing,
            cancel=token,
        )
        if blocking:
            summary = save_assets(assets, **kwargs)
            self._sync_summary = summary
            self._set_status(self._summary_text(summary))
            return summary
        dialog = _SaveDialog(self)
        dialog.cancel_button.clicked.connect(token.cancel)

        def run() -> SaveSummary:
            def on_progress(event: ProgressEvent) -> None:
                dialog.progress_from_thread.emit(event)

            return save_assets(assets, on_progress=on_progress, **kwargs)

        worker = CallWorker(run)
        self._save_worker = worker
        self._saving = True

        def finished(summary: SaveSummary) -> None:
            self._saving = False
            dialog.accept()
            self._set_status(self._summary_text(summary))
            QMessageBox.information(self, "保存しました", self._summary_text(summary))

        def failed(message: str, hint: str) -> None:
            self._saving = False
            dialog.accept()
            self._show_banner(message, hint)

        dialog.progress_from_thread.connect(dialog.apply_progress)
        worker.succeeded.connect(finished)
        worker.failed.connect(failed)
        worker.start()
        dialog.exec()
        if dialog.was_cancelled:
            token.cancel()
        return SaveSummary()

    def _summary_text(self, summary: SaveSummary) -> str:
        head = "中止しました。" if summary.cancelled else "保存が終わりました。"
        lines = [
            head,
            f"新しく保存: {summary.downloaded}",
            f"すでにあったのでスキップ: {summary.skipped}",
            f"失敗: {summary.failed}",
        ]
        if summary.errors:
            lines.append("")
            lines.extend(summary.errors[:8])
        return "\n".join(lines)

    def _clear_cache(self) -> None:
        answer = QMessageBox.question(
            self,
            "キャッシュを削除",
            "ドラッグ用にためた原本を消します。iCloudの写真は消えません。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        cache = self.context.data_dir / "cache"
        if cache.exists():
            import shutil

            shutil.rmtree(cache, ignore_errors=True)
        self._set_status("キャッシュを削除しました。")

    def _persist_settings(self) -> None:
        self.settings.sort = self._sort() if hasattr(self, "sort_combo") else self.settings.sort
        self.settings.kind = self._kind() if hasattr(self, "kind_buttons") else self.settings.kind
        try:
            self.settings.save(self.context.data_dir / "settings.json")
        except OSError:
            LOGGER.info("設定を保存できませんでした。")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._saving:
            answer = QMessageBox.question(
                self,
                "終了",
                "保存の途中です。終了すると、いまのファイルは途中までしか書けていないことがあります。終了しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._thumbs.stop()
        self._thumbs.wait(1500)
        self._persist_settings()
        event.accept()


class _SaveDialog(QDialog):
    progress_from_thread = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("保存しています")
        self.setModal(True)
        self.was_cancelled = False
        self.label = QLabel("準備しています…")
        self.label.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.cancel_button = QPushButton("中止")
        self.cancel_button.setObjectName("secondary")
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)
        layout.addWidget(self.cancel_button)
        self.resize(460, 140)
        self.cancel_button.clicked.connect(self._cancel)

    def _cancel(self) -> None:
        self.was_cancelled = True
        self.label.setText("中止しています…")

    def apply_progress(self, event: ProgressEvent) -> None:
        total = max(event.total, 1)
        self.bar.setRange(0, total)
        value = event.index if event.state in {"done", "skipped", "failed"} else max(event.index - 1, 0)
        self.bar.setValue(min(value, total))
        self.label.setText(f"{event.index} / {event.total}　{event.filename}")

    def reject(self) -> None:  # noqa: D102
        self.was_cancelled = True
        super().reject()
