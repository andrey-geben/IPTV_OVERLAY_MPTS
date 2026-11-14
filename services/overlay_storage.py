"""
Хранилище правил Overlay в JSON-файле.
"""

import json
from pathlib import Path

from .overlay_models import OverlayConfig, OverlayRule, OverlayText, TimeWindow


def _rule_to_dict(rule: OverlayRule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "text": {
            "text": rule.text.text,
            "font_path": rule.text.font_path,
            "font_size": rule.text.font_size,
            "font_color": rule.text.font_color,
            "font_alpha": rule.text.font_alpha,
            "speed": rule.text.speed,
            "repeat_period_sec": rule.text.repeat_period_sec,
            "box_enabled": rule.text.box_enabled,
            "box_color": rule.text.box_color,
            "box_alpha": rule.text.box_alpha,
            "y_offset": rule.text.y_offset,
            "start_date": rule.start_date,
            "end_date": rule.end_date,
            "is_yearly": rule.is_yearly,
        },
        "window": {
            "days": rule.window.days,
            "start_hhmm": rule.window.start_hhmm,
            "end_hhmm": rule.window.end_hhmm,
        },
        "channel_urls": rule.channel_urls,
        "enabled": rule.enabled,
        "priority": rule.priority,
    }


def _rule_from_dict(d: dict) -> OverlayRule:
    t = d.get("text", {})
    w = d.get("window", {})
    return OverlayRule(
        id=d["id"],
        name=d.get("name", d["id"]),
        text=OverlayText(
            text=t.get("text", "Бегущая строка"),
            font_path=t.get("font_path", "C:/Windows/Fonts/arial.ttf"),
            font_size=int(t.get("font_size", 36)),
            font_color=t.get("font_color", "white"),
            font_alpha=float(t.get("font_alpha", 1.0)),
            speed=int(t.get("speed", 100)),
            repeat_period_sec=int(t.get("repeat_period_sec", 10)),
            box_enabled=bool(t.get("box_enabled", True)),
            box_color=t.get("box_color", "black"),
            box_alpha=float(t.get("box_alpha", 0.7)),
            y_offset=int(t.get("y_offset", 50)),
            start_date=d.get("start_date", ""),
            end_date=d.get("end_date", ""),
            is_yearly=bool(d.get("is_yearly", False)),
        ),
        window=TimeWindow(
            days=list(w.get("days", list(range(7)))),
            start_hhmm=w.get("start_hhmm", "00:00"),
            end_hhmm=w.get("end_hhmm", "23:59"),
        ),
        channel_urls=list(d.get("channel_urls", [])),
        enabled=bool(d.get("enabled", True)),
        priority=int(d.get("priority", 0)),
    )


class OverlayStorage:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> OverlayConfig:
        if not self.path.exists():
            return OverlayConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return OverlayConfig()

        cfg = OverlayConfig(
            hls_time_sec=int(data.get("hls_time_sec", 2)),
            hls_list_size=int(data.get("hls_list_size", 5)),
            public_base_url=data.get("public_base_url", "http://127.0.0.1:5000"),
            source_playlist=data.get("source_playlist", ""),
            working_channel_urls=list(data.get("working_channel_urls", [])),
        )
        for rd in data.get("rules", []):
            try:
                cfg.rules.append(_rule_from_dict(rd))
            except Exception:
                continue
        return cfg

    def save(self, cfg: OverlayConfig) -> None:
        payload = {
            "hls_time_sec": cfg.hls_time_sec,
            "hls_list_size": cfg.hls_list_size,
            "public_base_url": cfg.public_base_url,
            "source_playlist": cfg.source_playlist,
            "working_channel_urls": cfg.working_channel_urls,
            "rules": [_rule_to_dict(r) for r in cfg.rules],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def rule_to_dict(rule: OverlayRule) -> dict:
        return _rule_to_dict(rule)