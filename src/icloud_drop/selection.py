"""クリック、Shift、Ctrl、全選択。"""

from __future__ import annotations


class Selection:
    def __init__(self) -> None:
        self.chosen: set[str] = set()
        self.anchor: str | None = None

    def __contains__(self, asset_id: str) -> bool:
        return asset_id in self.chosen

    def __len__(self) -> int:
        return len(self.chosen)

    def clear(self) -> None:
        self.chosen.clear()
        self.anchor = None

    def set_ids(self, ids: list[str]) -> None:
        self.chosen = set(ids)
        self.anchor = ids[0] if ids else None

    def click(self, ordered_ids: list[str], asset_id: str, *, shift: bool, ctrl: bool) -> None:
        if asset_id not in ordered_ids:
            return
        anchor_ok = self.anchor in ordered_ids if self.anchor else False
        if shift and anchor_ok and self.anchor is not None:
            start = ordered_ids.index(self.anchor)
            end = ordered_ids.index(asset_id)
            lo, hi = sorted((start, end))
            block = ordered_ids[lo : hi + 1]
            if not ctrl:
                self.chosen.clear()
            self.chosen.update(block)
            return
        if ctrl:
            if asset_id in self.chosen:
                self.chosen.remove(asset_id)
            else:
                self.chosen.add(asset_id)
            self.anchor = asset_id
            return
        self.chosen = {asset_id}
        self.anchor = asset_id
