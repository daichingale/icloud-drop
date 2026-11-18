"""画面なしの自己チェック。配布したexeが動くかの確認に使う。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from icloud_drop.download import build_drag_plan, save_assets
from icloud_drop.dragdrop import parse_file_group_descriptor, pack_file_group_descriptor
from icloud_drop.mock_backend import MockSource, build_demo_library


def run_self_check() -> int:
    library = build_demo_library(count=12)
    with tempfile.TemporaryDirectory(prefix="icloud-drop-") as raw:
        root = Path(raw)
        source = MockSource(root / "session", demo=True, library=library)
        albums = source.list_albums()
        if not albums or albums[0].id != "all":
            print("自己チェック失敗: アルバムを読めませんでした。")
            return 1
        assets = []
        for batch in source.iter_batches("all", batch_size=5):
            assets.extend(batch)
        if len(assets) != 12:
            print("自己チェック失敗: 写真の枚数が合いません。")
            return 1
        plan = build_drag_plan(assets, include_live=True)
        packed = pack_file_group_descriptor([(item.filename, item.size) for item in plan])
        parsed = parse_file_group_descriptor(packed)
        if parsed[0][0] != plan[0].filename:
            print("自己チェック失敗: ドラッグ用のファイル名が合いません。")
            return 1
        summary = save_assets(
            assets[:4],
            dest_dir=root / "out",
            cache_root=root / "cache",
            opener=source.open_original,
            include_live=True,
            convert_heic=True,
            skip_existing=True,
        )
        if summary.failed or summary.downloaded == 0:
            print("自己チェック失敗: 保存できませんでした。")
            return 1
        again = save_assets(
            assets[:4],
            dest_dir=root / "out",
            cache_root=root / "cache",
            opener=source.open_original,
            include_live=True,
            convert_heic=True,
            skip_existing=True,
        )
        if again.downloaded != 0 or again.skipped == 0:
            print("自己チェック失敗: 2回目の保存で同じファイルを取り直しています。")
            return 1
    print("自己チェック: OK")
    return 0
