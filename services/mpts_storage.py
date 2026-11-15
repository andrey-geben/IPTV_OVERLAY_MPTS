# -----подсказка: добавили флаг auto_start в группу MPTS-----
import json
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class MptsGroup:
    id: str
    name: str
    host: str
    port: int
    local_addr: str = ""
    channel_urls: list[str] = field(default_factory=list)
    auto_start: bool = False  # -----подсказка: запускать ли при старте сервера-----

@dataclass
class MptsConfig:
    source_playlist: str = ""
    groups: list[MptsGroup] = field(default_factory=list)

class MptsStorage:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> MptsConfig:
        if not self.path.exists(): return MptsConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            cfg = MptsConfig(source_playlist=data.get("source_playlist", ""))
            for g in data.get("groups", []):
                cfg.groups.append(MptsGroup(
                    id=g["id"], name=g.get("name", g["id"]),
                    host=g.get("host", "239.0.0.1"), port=int(g.get("port", 5000)),
                    local_addr=g.get("local_addr", ""),
                    channel_urls=g.get("channel_urls", []),
                    auto_start=bool(g.get("auto_start", False))
                ))
            return cfg
        except Exception:
            return MptsConfig()

    def save(self, cfg: MptsConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source_playlist": cfg.source_playlist,
            "groups": [
                {
                    "id": g.id, "name": g.name, "host": g.host, "port": g.port,
                    "local_addr": g.local_addr, "channel_urls": g.channel_urls,
                    "auto_start": g.auto_start
                } for g in cfg.groups
            ]
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")