"""起動口。設定は data フォルダのJSONだけで、レジストリは使わない。"""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from icloud_drop.credentials import open_vault
from icloud_drop.paths import default_data_dir
from icloud_drop.settings import Settings


def configure_logging(data_dir: Path) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "app.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    def _hook(exc_type, exc, tb) -> None:
        logging.getLogger("icloud_drop").error("予期しないエラー", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="iCloudの写真をフォルダへ保存する")
    parser.add_argument("--mock", action="store_true", help="練習用のサインイン（iCloudには繋がらない）")
    parser.add_argument("--demo", action="store_true", help="サンプル写真をすぐ開く")
    parser.add_argument("--data-dir", default="", help="設定とキャッシュのフォルダ")
    parser.add_argument("--self-check", action="store_true", help="画面を出さずに動作確認だけする")
    parser.add_argument("--demo-count", type=int, default=180, help="サンプルの枚数")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_check:
        from icloud_drop.selfcheck import run_self_check

        return run_self_check()

    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(data_dir)

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from icloud_drop.ui.main_window import AppContext, MainWindow
    from icloud_drop.ui.theme import STYLESHEET

    application = QApplication.instance() or QApplication(sys.argv)
    font = QFont()
    font.setFamilies(
        ["Yu Gothic UI", "Meiryo", "Noto Sans CJK JP", "WenQuanYi Micro Hei", "Droid Sans Fallback", "sans-serif"]
    )
    font.setPointSize(11)
    application.setFont(font)
    application.setStyleSheet(STYLESHEET)

    context = AppContext(
        data_dir=data_dir,
        settings=Settings.load(data_dir / "settings.json"),
        vault=open_vault(data_dir / "secrets"),
        mock_mode=bool(args.mock),
        demo_count=max(1, args.demo_count),
    )
    window = MainWindow(context)
    window.show()
    if args.demo:
        window.open_demo(blocking=False)
    return int(application.exec())


if __name__ == "__main__":
    raise SystemExit(main())
