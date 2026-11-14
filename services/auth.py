"""
Простая авторизация через JSON-файл пользователей.
Пароли хранятся как pbkdf2:sha256-хэш (werkzeug.security).
"""

import json
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


class AuthManager:
    def __init__(self, users_path: Path) -> None:
        self.path = Path(users_path)
        self._ensure_default_user()

    def _ensure_default_user(self) -> None:
        """Создаёт админа по умолчанию при первом запуске."""
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("users"):
                    return
            except Exception:
                pass

        default = {
            "users": [
                {
                    "username": "admin",
                    "password_hash": generate_password_hash("admin"),
                    "role": "admin",
                    "must_change_password": True,
                }
            ]
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(default, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"users": []}

    def _save(self, data: dict) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def verify(self, username: str, password: str) -> dict | None:
        """Возвращает профиль пользователя при успешной проверке, иначе None."""
        for u in self._load().get("users", []):
            if u.get("username") == username:
                if check_password_hash(u.get("password_hash", ""), password):
                    return u
                return None
        return None

    def change_password(self, username: str, new_password: str) -> bool:
        data = self._load()
        for u in data.get("users", []):
            if u.get("username") == username:
                u["password_hash"] = generate_password_hash(new_password)
                u["must_change_password"] = False
                self._save(data)
                return True
        return False