"""利用者向けのエラー文言。パスワードは含めない。"""

from __future__ import annotations

ADP_HINT = (
    "iPhoneで「設定」→ 一番上の自分の名前 →「iCloud」→"
    "「Web上のiCloudデータにアクセス」をオンにしてください。"
    "高度なデータ保護を使っている場合は、サインイン画面の"
    "「高度なデータ保護のWebアクセスを使う」にチェックを入れて、もう一度サインインしてください。"
    "初回は、iPhoneに届く許可の通知を2分以内に承認してください。"
)

SESSION_HINT = "一度サインアウトして、パスワードからやり直してください。クッキーが古くなっていることがあります。"


class ICloudError(Exception):
    """アプリ側で扱うiCloud由来のエラー。"""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class AccessDeniedError(ICloudError):
    def __init__(self, message: str = "iCloudの写真にアクセスできませんでした。", *, hint: str = ADP_HINT) -> None:
        super().__init__(message, hint=hint)


class TransientError(ICloudError):
    """混み合っているなど、少し待てば直ることがあるエラー。"""


class PasswordStoreError(ICloudError):
    def __init__(self, message: str = "パスワードを保存できませんでした。次回も入力してください。") -> None:
        super().__init__(message)


class CancelledError(ICloudError):
    def __init__(self) -> None:
        super().__init__("中止しました。")


def explain_exception(exc: BaseException) -> tuple[str, str]:
    """例外を、画面に出してよい短い説明とヒントに分ける。"""
    if isinstance(exc, ICloudError):
        return str(exc), exc.hint

    code = str(getattr(exc, "code", "") or "")
    text = str(exc)
    blob = f"{code} {text}".lower()

    if (
        "access_denied" in blob
        or "private db access" in blob
        or "web access to icloud data is disabled" in blob
        or "advanced data protection" in blob
    ):
        return "iCloudの写真にアクセスできませんでした。", ADP_HINT

    if "timed out waiting for the icloud web access approval" in blob:
        return "iPhoneでの承認が時間内に終わりませんでした。", "通知を開いて「許可」を押し、もう一度試してください。"

    if "not finished indexing" in blob or "indexing" in blob and "not" in blob:
        return "iCloud側で写真の整理が終わっていません。", "数分待ってから「更新」を押してください。"

    if "cannot log in" in blob or "failed login" in blob or "invalid" in blob and "password" in blob:
        return "サインインできませんでした。Apple IDとパスワードを確認してください。", ""

    if "security key" in blob or "fido" in blob:
        return "セキュリティキーでの確認に失敗しました。", "キーを挿して触れてもう一度試すか、6桁のコードが使えるアカウントでサインインしてください。"

    # 生の例外文は長いことがあるので、画面には短くする。詳細はログへ。
    short = text.strip().splitlines()[0] if text.strip() else exc.__class__.__name__
    if len(short) > 180:
        short = short[:180] + "…"
    return f"処理に失敗しました。{short}", ""
