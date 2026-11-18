"""落ち着いた色。画面の文言はウィジェット側で日本語にする。"""

STYLESHEET = """
QWidget { color: #1c2430; font-size: 13px; }
QMainWindow, #app { background: #f3f0e8; }
#card {
    background: #fffdf8;
    border: 1px solid #e4ddd0;
    border-radius: 18px;
}
#title { font-size: 26px; font-weight: 600; }
#subtitle { color: #5c6570; }
#quiet { color: #5c6570; }
#hint {
    background: #f7f1e2;
    border-radius: 10px;
    padding: 8px;
    color: #5a4b32;
}
#error {
    background: #fde8e4;
    border-radius: 10px;
    padding: 8px;
    color: #7a2e24;
}
#banner {
    background: #f7f1e2;
    border-radius: 10px;
    padding: 8px;
    color: #5a4b32;
}
QLineEdit, QComboBox, QDateEdit {
    background: white;
    border: 1px solid #ddd6c8;
    border-radius: 8px;
    padding: 6px 8px;
    min-height: 22px;
}
QPushButton {
    background: #2f6f6a;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 14px;
    min-height: 20px;
}
QPushButton:hover { background: #275e5a; }
QPushButton:disabled { background: #c5d0ce; color: #f7fafa; }
QPushButton#secondary {
    background: #e7e2d6;
    color: #1c2430;
}
QPushButton#secondary:hover { background: #ddd6c6; }
QPushButton[kind="1"] {
    background: #e7e2d6;
    color: #1c2430;
}
QPushButton[kind="1"]:checked {
    background: #2f6f6a;
    color: white;
}
QCheckBox { spacing: 8px; }
QProgressBar {
    border: none;
    background: #e6e1d6;
    border-radius: 6px;
    min-height: 12px;
    text-align: center;
}
QProgressBar::chunk { background: #2f6f6a; border-radius: 6px; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 4px; }
QScrollBar::handle:vertical { background: #cfc6b6; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
