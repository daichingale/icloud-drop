"""設定はJSONファイルだけ。レジストリには書かない。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Settings:
    apple_id: str = ""
    remember_password: bool = False
    adp_web_access: bool = False
    domain: str = "com"
    convert_heic: bool = False
    skip_existing: bool = True
    include_live: bool = True
    sort: str = "newest"
    kind: str = "all"
    album_id: str = "all"
    last_folder: str = ""
    china_domain: bool = False

    @classmethod
    def load(cls, path: Path) -> Settings:
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        # 万一パスワードが混ざっていても、読み込まない。
        raw.pop("password", None)
        known = {name: raw[name] for name in cls.__dataclass_fields__ if name in raw}
        settings = cls(**known)
        if settings.domain not in {"com", "cn"}:
            settings.domain = "com"
        if settings.sort not in {"newest", "oldest", "name"}:
            settings.sort = "newest"
        if settings.kind not in {"all", "photo", "video"}:
            settings.kind = "all"
        return settings

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload.pop("password", None)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
