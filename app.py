# ============================================================
# ИМПОРТЫ И ИНИЦИАЛИЗАЦИЯ
# ============================================================
import os
import time
import uuid
import hashlib
import datetime
import threading
from functools import wraps
from pathlib import Path
import requests

from flask import (
    Flask, render_template, request, jsonify,
    send_from_directory, redirect, url_for, session,
)
from flask_socketio import SocketIO
from apscheduler.schedulers.background import BackgroundScheduler

from config import Config
from services import (
    ProcessManager, parse_m3u,
    MptsChannel, MptsProfile, build_mpts_command, build_udp_url,
    OverlayStorage, OverlayEngine, OverlayRule, OverlayText, TimeWindow,
    MptsStorage, MptsGroup, check_channels_parallel, read_progress_from_log,
    validate_udp_stream, get_process_io, AuthManager
)

from services.hls_server import start_hls_server
from services.capacity import evaluate as evaluate_capacity
from services.channel_player import ChannelStore, ChannelPlayer

app = Flask(__name__)
app.config.from_object(Config)
app.permanent_session_lifetime = Config.PERMANENT_SESSION_LIFETIME

# ------------ подсказка -----------------
# async_mode="threading" позволяет SocketIO работать без eventlet/gevent.
# Для локального Flask-сервера это проще и не требует monkey-patching.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

process_manager = ProcessManager(Config.LOGS_DIR)
auth = AuthManager(Config.USERS_FILE)

overlay_storage = OverlayStorage(Config.OVERLAY_CONFIG_PATH)
overlay_cfg = overlay_storage.load()
overlay_engine = OverlayEngine(process_manager, Config.HLS_DIR, overlay_cfg)

mpts_storage = MptsStorage(Config.MPTS_CONFIG_PATH)
channel_store = ChannelStore(Config.CHANNELS_CONFIG_PATH)
channel_player = ChannelPlayer(process_manager, Config.HLS_DIR)
mpts_cfg = mpts_storage.load()

# ------------ подсказка -----------------
# APScheduler вызывает overlay_engine.sync() каждые 30 секунд:
# подхватывает изменения расписания оверлеев, рестартит упавшие прокси.
# Это независимо от пользовательских действий на /playlist и /overlay.
scheduler = BackgroundScheduler()
scheduler.add_job(func=overlay_engine.sync, trigger="interval", seconds=30)
scheduler.start()

TEST_FFMPEG_COMMAND = [
    "ffmpeg", "-re", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
    "-t", "3600", "-f", "null", "-"
]

# ------------ подсказка -----------------
# Кэш для расчёта сетевой скорости (Мбит/с). Хранит пары {pid: (ts, bytes)}.
# Обновляется в _collect_ws_payload и api_mpts_group_monitor.
_network_cache = {}


def _get_default_font():
    # ------------ подсказка -----------------
    # Если пользователь не указал шрифт — ставим DejaVuSans. Он содержит
    # базовый набор эмодзи и спецсимволов, которые иначе не отрисуются.
    # Переносимый путь: шрифт лежит в static/fonts и попадает в сборку.
    try:
        from config import Config as _Cfg
        p = str(_Cfg.DEFAULT_OVERLAY_FONT)
        if os.path.isfile(p):
            return p
    except Exception:
        pass
    # Запасной вариант для Windows, если DejaVu не нашли
    for fallback in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
        if os.path.isfile(fallback):
            return fallback
    return "DejaVuSans.ttf"


def _sanitize_local_addr(addr: str) -> str:
    # ------------ подсказка -----------------
    # Loopback (127.x.x.x, localhost) НЕ умеет отправлять UDP-мультикаст:
    # FFmpeg падает на setsockopt(IP_MULTICAST_IF), процесс умирает в первую
    # секунду, MPTS не работает. Поэтому вычищаем loopback из local_addr
    # ещё на уровне приложения — и при сохранении группы, и при старте.
    # Пустая строка = FFmpeg сам выберет правильный интерфейс.
    addr = (addr or "").strip()
    if not addr:
        return ""
    if addr.lower() == "localhost" or addr.startswith("127."):
        return ""
    return addr


def _auto_start_mpts_groups():
    # ------------ подсказка -----------------
    # Автозапуск групп MPTS при старте сервера, если у группы
    # auto_start=True. Вызывается из точки входа (внизу файла).
    print("[startup] Checking for auto_start MPTS groups...")
    for g in mpts_cfg.groups:
        if g.auto_start:
            print(f"[startup] Auto-starting MPTS group: {g.name}")
            try:
                _start_mpts_group_internal(g.id)
            except Exception as e:
                print(f"[startup] Error starting {g.name}: {e}")


def _collect_ws_payload() -> dict:
    # ------------ подсказка -----------------
    # Собираем всё, что нужно фронту для обновления UI каждые 2 секунды:
    # - статусы ВСЕХ процессов (test, overlay, mpts, mosaic-preview)
    # - прогресс кодирования (frame/fps/bitrate/speed) из лога
    # - сетевую скорость (Мбит/с) через psutil
    statuses = {p["name"]: process_manager.status(p["name"]) for p in process_manager.list_all()}
    for item in overlay_engine.get_active_snapshot():
        statuses[item["process_name"]] = process_manager.status(item["process_name"])
    for g in mpts_cfg.groups:
        statuses[f"mpts_{g.id}"] = process_manager.status(f"mpts_{g.id}")

    mpts_statuses, monitors = {}, {}
    for g in mpts_cfg.groups:
        pname = f"mpts_{g.id}"
        st = statuses.get(pname, {})
        mpts_statuses[g.id] = st
        if st.get("running"):
            log_path = Config.LOGS_DIR / f"{pname}.log"
            # ------------ подсказка -----------------
            # read_progress_from_log парсит -progress pipe:1 из лога FFmpeg
            # и достаёт frame/fps/bitrate/speed/out_time.
            progress = read_progress_from_log(log_path) or {}
            pid = st.get("pid")
            net_sent_mb, net_rate_mbps = None, None
            if pid:
                io = get_process_io(pid)
                if io:
                    now = time.time()
                    prev = _network_cache.get(pid)
                    net_sent_mb = round(io["write_bytes"] / (1024 * 1024), 2)
                    # ------------ подсказка -----------------
                    # Скорость = дельта байт × 8 / дельта времени / 1024².
                    if prev:
                        dt = now - prev["ts"]
                        if dt > 0.1:
                            net_rate_mbps = round((io["write_bytes"] - prev["bytes"]) * 8 / dt / (1024 * 1024), 2)
                    _network_cache[pid] = {"bytes": io["write_bytes"], "ts": now}
            monitors[g.id] = {
                "uptime_sec": st.get("uptime_sec", 0),
                "frame": progress.get("frame", "—"),
                "fps": progress.get("fps", "—"),
                "bitrate": progress.get("bitrate", "—"),
                "speed": progress.get("speed", "—"),
                "out_time": progress.get("out_time", "—"),
                "drop_frames": progress.get("drop_frames", "0"),
                "net_sent_mb": net_sent_mb,
                "net_rate_mbps": net_rate_mbps,
            }
    return {
        "processes": statuses,
        "mpts": mpts_statuses,
        "monitors": monitors,
        "capacity": evaluate_capacity(process_manager, mpts_cfg, overlay_engine),
        "timestamp": time.time(),
    }


def _ws_broadcast_loop() -> None:
    # ------------ подсказка -----------------
    # Фоновый поток, который каждые 2 сек рассылает всем подключённым
    # клиентам событие "update". daemon=True — чтобы поток умирал вместе
    # с Flask, а не висел после Ctrl+C.
    while True:
        try:
            socketio.emit("update", _collect_ws_payload())
        except Exception as exc:
            print(f"[ws] broadcast error: {exc}")
        time.sleep(2)


threading.Thread(target=_ws_broadcast_loop, daemon=True).start()


# ============================================================
# АВТОРИЗАЦИЯ
# ============================================================
def login_required(f):
    # ------------ подсказка -----------------
    # Универсальный декоратор:
    # - для /api/* возвращает 401 JSON (фронт сам покажет логин);
    # - для обычных страниц редиректит на /login?next=<текущий путь>.
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Требуется авторизация"}), 401
            return redirect(url_for("login_page", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def role_required(*allowed_roles: str):
    # ------------ подсказка -----------------
    # role_required("operator") — доступ для operator и admin.
    # role_required("admin")    — только admin.
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not session.get("user"):
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Требуется авторизация"}), 401
                return redirect(url_for("login_page", next=request.path))
            user_role = session["user"].get("role", "viewer")
            # admin всегда допущен: role_required("operator") = operator + admin
            if user_role not in allowed_roles and user_role != "admin":
                if request.path.startswith("/api/"):
                    return jsonify({
                        "ok": False,
                        "error": f"Недостаточно прав (ваша роль: {user_role})"
                    }), 403
                return "Доступ запрещён", 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


@app.before_request
def enforce_password_change():
    # ------------ подсказка -----------------
    # Если у пользователя must_change_password=True — пускаем только на
    # смену пароля и logout. Всё остальное (страницы → редирект, API → 403).
    if not session.get("user") or not session["user"].get("must_change_password"):
        return None
    allowed = {
        "/change-password", "/logout", "/login", "/favicon.ico",
        "/api/auth/change-password", "/api/auth/me",
    }
    if request.path in allowed or request.path.startswith("/static/"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Требуется смена пароля"}), 403
    return redirect(url_for("change_password_page"))


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "GET":
        if session.get("user"):
            return redirect(url_for("playlist_page"))
        return render_template("login.html", error=None, next=request.args.get("next", "/"))

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    user = auth.verify(username, password)
    if not user:
        return render_template("login.html", error="Неверный логин или пароль", next="/")

    session.permanent = True
    session["user"] = {
        "username": user["username"],
        "role": user.get("role", "user"),
        "must_change_password": user.get("must_change_password", False),
    }
    if user.get("must_change_password"):
        return redirect(url_for("change_password_page"))
    return redirect("/")


@app.route("/logout")
def logout_page():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/api/auth/me")
@login_required
def api_auth_me():
    return jsonify({"ok": True, "user": session.get("user")})


@app.route("/api/auth/change-password", methods=["POST"])
@login_required
def api_auth_change_password():
    data = request.get_json(silent=True) or {}
    new_pwd = (data.get("new_password") or "").strip()
    if len(new_pwd) < 6:
        return jsonify({"ok": False, "error": "Пароль < 6 символов"}), 400
    if auth.change_password(session["user"]["username"], new_pwd):
        # ------------ подсказка -----------------
        # Снимаем флаг must_change_password прямо в сессии — иначе
        # before_request продолжит редиректить на /change-password.
        session["user"]["must_change_password"] = False
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Ошибка"}), 404


@app.context_processor
def inject_globals():
    # ------------ подсказка -----------------
    # Доступно во всех Jinja-шаблонах: {{ config.X }} и {{ current_year }}.
    return {"config": Config, "current_year": datetime.datetime.now().year}


# ============================================================
# СТРАНИЦЫ
# ============================================================
@app.route("/")
@login_required
def index():
    return redirect(url_for("playlist_page"))


@app.route("/playlist")
@login_required
def playlist_page():
    return render_template("playlist.html")


@app.route("/overlay")
@login_required
def overlay_page():
    return render_template("overlay.html")


@app.route("/mpts")
@login_required
def mpts_page():
    return render_template("mpts.html")


@app.route("/mosaic")
@login_required
def mosaic_page():
    return render_template("mosaic.html")


@app.route("/process")
@login_required
def process_page():
    return render_template("process.html")


@app.route("/styleguide")
@login_required
def styleguide():
    return render_template("styleguide.html")


@app.route("/about")
@login_required
def about_page():
    return render_template("about.html")


@app.route("/change-password", methods=["GET"])
@login_required
def change_password_page():
    return render_template("change_password.html")

# ============================================================
# API — ПЛЕЙЛИСТЫ
# ============================================================
@app.route("/api/playlists/parse", methods=["POST"])
@login_required
def api_parse_playlist():
    # ------------ подсказка -----------------
    # Парсит M3U-контент, присланный текстом (textarea на /playlist).
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    if not content.strip():
        return jsonify({"ok": False, "error": "Пусто"}), 400
    channels = parse_m3u(content)
    return jsonify({"ok": True, "count": len(channels), "channels": channels, "raw_content": content})


@app.route("/api/playlists/parse-file", methods=["POST"])
@login_required
def api_parse_playlist_from_file():
    # ------------ подсказка -----------------
    # Загрузка M3U файлом. Пробуем кодировки по очереди: utf-8-sig (с BOM),
    # utf-8, cp1251 (Windows-1251, часто встречается в русских плейлистах).
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Нет файла"}), 400
    raw = request.files["file"].read()
    content = None
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            content = raw.decode(enc)
            break
        except Exception:
            continue
    if not content:
        return jsonify({"ok": False, "error": "Кодировка"}), 400
    channels = parse_m3u(content)
    return jsonify({"ok": True, "count": len(channels), "channels": channels, "raw_content": content})


@app.route("/api/playlists/parse-url", methods=["POST"])
@login_required
def api_parse_playlist_from_url():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not url.startswith(("http://", "https://")):
        return jsonify({"ok": False, "error": "URL"}), 400
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        channels = parse_m3u(resp.text)
        return jsonify({"ok": True, "count": len(channels), "channels": channels, "raw_content": resp.text})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/playlist/source", methods=["GET", "POST"])
@login_required
def api_playlist_source():
    # ------------ подсказка -----------------
    # Исходный (полный) M3U от провайдера. GET возвращает, POST сохраняет.
    if request.method == "GET":
        return jsonify({"ok": True, "content": overlay_engine.cfg.source_playlist})
    # ------------ подсказка -----------------
    # POST: только operator и admin. Viewer не должен менять плейлист.
    if request.method == "POST":
        if session["user"].get("role") not in ("admin", "operator"):
            return jsonify({"ok": False, "error": "Недостаточно прав"}), 403
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    if not content.strip():
        return jsonify({"ok": False, "error": "Пусто"}), 400
    overlay_engine.cfg.source_playlist = content
    overlay_storage.save(overlay_engine.cfg)
    return jsonify({"ok": True})


@app.route("/api/playlist/working", methods=["GET", "POST"])
@role_required("operator")
def api_playlist_working():
    # ------------ подсказка -----------------
    # GET — вернуть текущий рабочий плейлист.
    # POST — сохранить и запустить overlay-прокси через sync().
    # Доступ: operator и admin.
    if request.method == "GET":
        return jsonify({"ok": True, "urls": overlay_engine.cfg.working_channel_urls})

    data = request.get_json(silent=True) or {}
    urls = data.get("urls", [])
    # ------------ подсказка -----------------
    # Валидация: только список непустых строк. Иначе save() или sync()
    # упадут на None/dict/мусоре (классическая ошибка от JS).
    if not isinstance(urls, list):
        return jsonify({"ok": False, "error": "urls должен быть списком"}), 400
    urls = [u for u in urls if isinstance(u, str) and u.strip()]
    overlay_engine.cfg.working_channel_urls = urls

    try:
        overlay_storage.save(overlay_engine.cfg)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "stage": "save", "error": f"{type(e).__name__}: {e}"}), 500

    try:
        overlay_engine.sync()
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "stage": "sync", "error": f"{type(e).__name__}: {e}"}), 500

    return jsonify({"ok": True, "count": len(urls)})


# ============================================================
# API — OVERLAY
# ============================================================
def _find_emoji(text: str) -> list[str]:
    # ------------ подсказка -----------------
    # Ищем эмодзи, которые не отрисуются в drawtext даже с DejaVuSans.
    # safe_cp — whitelist «безопасных» символов (звёзды, сердца, снежинки,
    # галочки и т.п.), которые DejaVuSans умеет рисовать.
    if not text:
        return []
    found, seen = [], set()
    for ch in text:
        cp = ord(ch)
        is_emoji = (
            0x1F300 <= cp <= 0x1F9FF or
            0x1F600 <= cp <= 0x1F64F or
            0x1F680 <= cp <= 0x1F6FF or
            0x2600 <= cp <= 0x27BF
        )
        safe_cp = {
            0x2605, 0x2606, 0x2665, 0x2661, 0x266A, 0x266B,
            0x2713, 0x2714, 0x2717, 0x2718, 0x263A, 0x263B,
            0x2600, 0x263C, 0x2601, 0x2602, 0x2603,
            0x2744, 0x2745, 0x2746, 0x2668, 0x2702, 0x2708, 0x26A1,
        }
        if is_emoji and cp not in safe_cp and ch not in seen:
            seen.add(ch)
            found.append(ch)
    return found


@app.route("/api/overlay/available-channels")
@login_required
def api_overlay_available_channels():
    # ------------ подсказка -----------------
    # Только те каналы, что в рабочем плейлисте — по ним можно создать правило.
    working = set(overlay_engine.cfg.working_channel_urls)
    parsed = parse_m3u(overlay_engine.cfg.source_playlist) if overlay_engine.cfg.source_playlist else []
    return jsonify({"ok": True, "channels": [ch for ch in parsed if ch["url"] in working]})


@app.route("/api/overlay/rules", methods=["GET", "POST"])
@login_required
def api_overlay_rules():
    # ------------ подсказка -----------------
    # GET — список всех правил.
    # POST — создать или обновить правило. Требует force=True, если:
    # * в тексте есть «опасные» эмодзи;
    # * есть каналы, которых нет ни в одной MPTS-группе.
    if request.method == "GET":
        return jsonify({"ok": True, "rules": [overlay_storage.rule_to_dict(r) for r in overlay_engine.cfg.rules]})

    # ------------ подсказка -----------------
    # POST: только operator и admin.
    if session["user"].get("role") not in ("admin", "operator"):
        return jsonify({"ok": False, "error": "Недостаточно прав"}), 403

    data = request.get_json(silent=True) or {}
    rule_id = data.get("id") or f"rule-{uuid.uuid4().hex[:6]}"
    req_urls = data.get("channel_urls", [])
    rule_text_value = data.get("text", "")

    warnings = []
    emoji_chars = _find_emoji(rule_text_value)
    if emoji_chars:
        warnings.append(f"⚠ В тексте есть эмодзи: {' '.join(emoji_chars[:5])}. Замените их.")
    mpts_channels = {u for g in mpts_cfg.groups for u in g.channel_urls}
    missing = [u for u in req_urls if u not in mpts_channels]
    if missing:
        warnings.append(f"⚠ {len(missing)} канал(ов) нет в MPTS.")

    if warnings and not data.get("force"):
        return jsonify({
            "ok": False,
            "require_confirm": True,
            "warning": "Внимание!\n\n" + "\n\n".join(warnings) + "\n\nСохранить?",
        }), 400

    # ------------ подсказка -----------------
    # Шрифт: если пусто или arial — принудительно DejaVuSans.
    font_path = data.get("font_path", "").strip()
    if not font_path or "arial" in font_path.lower():
        font_path = _get_default_font()

    new_rule = OverlayRule(
        id=rule_id,
        name=data.get("name", "Без названия"),
        text=OverlayText(
            text=rule_text_value or "Текст",
            font_path=font_path,
            font_size=int(data.get("font_size", 36)),
            font_color=data.get("font_color", "white"),
            font_alpha=float(data.get("font_alpha", 1.0)),
            speed=int(data.get("speed", 100)),
            repeat_period_sec=int(data.get("repeat_period_sec", 60)),
            box_enabled=bool(data.get("box_enabled", True)),
            box_color=data.get("box_color", "black"),
            box_alpha=float(data.get("box_alpha", 0.7)),
            y_offset=int(data.get("y_offset", 50)),
        ),
        window=TimeWindow(
            days=[int(d) for d in data.get("days", [])],
            start_hhmm=data.get("start_hhmm", "00:00"),
            end_hhmm=data.get("end_hhmm", "23:59"),
        ),
        channel_urls=req_urls,
        enabled=data.get("enabled", True),
        priority=int(data.get("priority", 0)),
        start_date=data.get("start_date", ""),
        end_date=data.get("end_date", ""),
        is_yearly=bool(data.get("is_yearly", False)),
    )

    # ------------ подсказка -----------------
    # upsert: ищем по id. Если нашли — заменяем, иначе добавляем.
    existing_idx = next((i for i, r in enumerate(overlay_engine.cfg.rules) if r.id == rule_id), None)
    if existing_idx is not None:
        overlay_engine.cfg.rules[existing_idx] = new_rule
    else:
        overlay_engine.cfg.rules.append(new_rule)

    overlay_storage.save(overlay_engine.cfg)
    overlay_engine.sync()
    return jsonify({"ok": True, "rule": overlay_storage.rule_to_dict(new_rule)})


@app.route("/api/overlay/rules/<rule_id>", methods=["DELETE"])
@login_required
def api_overlay_rule_delete(rule_id):
    if session["user"].get("role") not in ("admin", "operator"):
        return jsonify({"ok": False, "error": "Недостаточно прав"}), 403
    overlay_engine.cfg.rules = [r for r in overlay_engine.cfg.rules if r.id != rule_id]
    overlay_storage.save(overlay_engine.cfg)
    overlay_engine.sync()
    return jsonify({"ok": True})


# ============================================================
# API — MPTS
# ============================================================
def _mpts_input_url(url: str, ch_id: str) -> str:
    """Откуда MPTS читает канал.

    Есть правила overlay — берём HLS-прокси (там живёт тикер).
    Нет правил — читаем источник напрямую: экономим процесс, диск и сеть.
    """
    try:
        has_rules = any(url in rule.channel_urls for rule in overlay_engine.cfg.rules)
    except Exception:
        has_rules = True  # при сомнении ведём себя как раньше
    if has_rules:
        return f"http://127.0.0.1:{Config.HLS_HTTP_PORT}/proxy/hls/{ch_id}/index.m3u8"
    return url


def _start_mpts_group_internal(group_id: str) -> dict:
    # ------------ подсказка -----------------
    # Внутренняя логика запуска MPTS. Используется из API и из автозапуска.
    # Ключевые фиксы против прошлых падений:
    # 1) URL = http://127.0.0.1:5000/... (не file://) — переживает
    #    микросекунду, когда overlay перезаписывает index.m3u8.
    # 2) Ждём HLS до 120 сек (OVERLAY-прокси стартует 60-90 сек).
    # 3) local_addr прогоняется через _sanitize_local_addr — убирает
    #    loopback, который валит мультикаст.
    # 4) После start() ждём 5 сек и проверяем, жив ли FFmpeg. Если упал —
    #    возвращаем 400 с последней строкой лога, а не молчаливый 200.
    group = next((g for g in mpts_cfg.groups if g.id == group_id), None)
    if not group:
        return {"ok": False, "error": "Группа не найдена"}

    parsed = parse_m3u(overlay_engine.cfg.source_playlist) if overlay_engine.cfg.source_playlist else []
    working = set(overlay_engine.cfg.working_channel_urls)

    selected = []
    for url in group.channel_urls:
        if url not in working:
            continue
        ch = next((c for c in parsed if c["url"] == url), None)
        if not ch:
            # канал из папки («папка = канал») — имени в плейлисте нет
            own_name = _channel_name_by_url(url)
            if own_name:
                ch = {"name": own_name, "url": url}
        if not ch:
            continue
        ch_id = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
        local_path = Config.HLS_DIR / f"proxy_{ch_id}" / "index.m3u8"
        # ------------ подсказка -----------------
        # Для MPTS используем HTTP, а не file:// — file:// не умеет
        # переподключаться, если файл исчез на микросекунду при перезаписи.
        http_url = _mpts_input_url(ch["url"], ch_id)
        selected.append({
            "name": ch["name"],
            "path": local_path,
            "url": http_url,
        })

    if not selected:
        return {"ok": False, "error": "Нет валидных каналов"}

    # ------------ подсказка -----------------
    # Ждём, пока все index.m3u8 появятся и станут > 100 байт.
    # 120 сек — потому что OVERLAY-прокси тратит 30 сек на analyzeduration,
    # плюс буферизация и накопление первых сегментов.
    deadline = time.time() + 120
    missing = []
    while time.time() < deadline:
        missing = [
            item["name"] for item in selected
            if not item["path"].exists() or item["path"].stat().st_size < 100
        ]
        if not missing:
            break
        time.sleep(1.0)

    if missing:
        return {
            "ok": False,
            "error": (
                f"HLS не готов: {', '.join(missing[:3])}"
                f"{'...' if len(missing) > 3 else ''}. "
                f"Подождите 30-60 секунд."
            ),
        }

    channels = [MptsChannel(name=i["name"], url=i["url"]) for i in selected]
    profile = MptsProfile(
        channels=channels,
        output_url=build_udp_url(
            group.host,
            group.port,
            ttl=getattr(Config, "MPTS_TTL", 8),
            # ------------ подсказка -----------------
            # Санитайзер: даже если в конфиге остался 127.0.0.1 — на выход
            # пойдёт пустая строка (FFmpeg сам выберет интерфейс).
            local_addr=_sanitize_local_addr(group.local_addr),
        ),
    )
    cmd = build_mpts_command(profile)
    proc_name = f"mpts_{group_id}"

    if process_manager.status(proc_name).get("running"):
        return {"ok": False, "error": "Группа уже запущена"}

    print(f"[mpts] === START {group.name} ({len(channels)} ch) ===")
    print(f"[mpts] CMD: {' '.join(cmd)}")
    res = process_manager.start(proc_name, cmd)
    if not res.get("ok"):
        return res

    # ------------ подсказка -----------------
    # Live-check: ждём 5 секунд. Если FFmpeg жив — считаем, что стартовал
    # успешно. Если упал — достаём последнюю строку лога, чтобы фронт мог
    # показать её в alert вместо «молчаливого 200 и моргающей панели».
    time.sleep(5.0)
    if process_manager.status(proc_name).get("running"):
        return res

    log_path = Config.LOGS_DIR / f"{proc_name}.log"
    last_line = ""
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            last_line = lines[-1] if lines else ""
        except Exception:
            pass
    return {
        "ok": False,
        "error": f"FFmpeg упал через 5 сек: {last_line or 'см. logs'}",
    }


@app.route("/api/mpts/split", methods=["POST"])
@role_required("operator")
def api_mpts_split():
    """Раскладывает рабочие каналы по MPTS-группам.

    apply=false — только предпросмотр, конфиг не меняется.
    apply=true  — останавливает текущие группы и записывает новые.
    """
    data = request.get_json(silent=True) or {}
    per = max(1, min(15, int(data.get("per_group") or 10)))
    start_ip = (data.get("start_ip") or "239.0.0.11").strip()
    port = int(data.get("port") or 5000)
    auto_start = bool(data.get("auto_start"))
    apply_now = bool(data.get("apply"))
    local_addr = data.get("local_addr") or ""

    try:
        prefix, last = start_ip.rsplit(".", 1)
        last = int(last)
    except ValueError:
        return jsonify({"ok": False, "error": "Некорректный IP"}), 400

    seen, urls = set(), []
    for g in mpts_cfg.groups:
        for u in g.channel_urls:
            if u not in seen:
                seen.add(u)
                urls.append(u)
    if not urls:
        return jsonify({"ok": False, "error": "Сначала выберите каналы в рабочем плейлисте"}), 400

    if local_addr is None or local_addr == "":
        local_addr = next((g.local_addr for g in mpts_cfg.groups if g.local_addr), "")

    groups, chunks = [], [urls[i:i + per] for i in range(0, len(urls), per)]
    for i, chunk in enumerate(chunks):
        groups.append({
            "name": f"MPTS {i + 1} ({len(chunk)} кан.)",
            "host": f"{prefix}.{last + i}",
            "port": port,
            "local_addr": local_addr,
            "channel_urls": chunk,
            "auto_start": auto_start,
        })

    mbps = round(len(urls) * 3.2, 0)
    per_mbps = round(per * 3.2, 0)
    warnings = []
    if per > 12:
        warnings.append(f"{per} программ — ~{per_mbps:.0f} Мбит/с, это уже на пределе QAM (~48 Мбит/с)")

    if not apply_now:
        return jsonify({
            "ok": True, "preview": True, "total_channels": len(urls),
            "total_mbps": mbps, "per_group_mbps": per_mbps,
            "groups": groups, "warnings": warnings,
        })

    stopped = 0
    for g in list(mpts_cfg.groups):
        if process_manager.status(f"mpts_{g.id}").get("running"):
            process_manager.stop(f"mpts_{g.id}")
            stopped += 1

    new_groups = []
    for g in groups:
        new_groups.append(MptsGroup(
            id=f"grp-{uuid.uuid4().hex[:6]}", name=g["name"], host=g["host"],
            port=g["port"], local_addr=g["local_addr"],
            channel_urls=g["channel_urls"], auto_start=g["auto_start"],
        ))
    mpts_cfg.groups = new_groups
    mpts_storage.save(mpts_cfg)

    return jsonify({
        "ok": True, "preview": False, "created": len(new_groups),
        "stopped": stopped, "total_channels": len(urls),
        "total_mbps": mbps, "warnings": warnings,
    })


@app.route("/api/mpts/config", methods=["GET"])
@login_required
def api_mpts_config():
    # ------------ подсказка -----------------
    # Фронту /mpts нужны:
    # - channels: каналы из рабочего плейлиста (для выбора в группу);
    # - groups: все созданные группы с их URL/host/port/local_addr.
    working = set(overlay_engine.cfg.working_channel_urls)
    parsed = parse_m3u(overlay_engine.cfg.source_playlist) if overlay_engine.cfg.source_playlist else []
    channels = [ch for ch in parsed if ch["url"] in working]
    return jsonify({
        "ok": True,
        "channels": channels,
        "groups": [
            {
                "id": g.id,
                "name": g.name,
                "host": g.host,
                "port": g.port,
                "local_addr": g.local_addr,
                "channel_urls": g.channel_urls,
                "auto_start": g.auto_start,
            }
            for g in mpts_cfg.groups
        ],
    })


@app.route("/api/mpts/check-channels", methods=["POST"])
@login_required
def api_mpts_check_channels():
    # ------------ подсказка -----------------
    # Параллельная проверка доступности URL (запускается из UI перед созданием
    # группы). Возвращает список {url, ok, error?, latency_ms?}.
    urls = (request.get_json(silent=True) or {}).get("urls", [])
    if not isinstance(urls, list) or not urls:
        return jsonify({"ok": False, "error": "urls"}), 400
    return jsonify({"ok": True, "results": check_channels_parallel(urls, timeout=12)})


@app.route("/api/mpts/groups", methods=["POST"])
@role_required("operator")
def api_mpts_create_group():
    # ------------ подсказка -----------------
    # Создание/обновление группы. Если удаляем канал, у которого есть
    # активное правило overlay — просим force=True (чтобы пользователь
    # случайно не сломал эфир). Доступ: operator и admin.
    data = request.get_json(silent=True) or {}
    group_id = data.get("id") or f"grp-{uuid.uuid4().hex[:6]}"
    new_urls = data.get("channel_urls", [])
    existing = next((g for g in mpts_cfg.groups if g.id == group_id), None)

    if existing and not data.get("force"):
        removed = [u for u in existing.channel_urls if u not in new_urls]
        for url in removed:
            for rule in overlay_engine.cfg.rules:
                if url in rule.channel_urls:
                    return jsonify({
                        "ok": False,
                        "require_confirm": True,
                        "warning": f"Вы удаляете канал с активной строкой '{rule.name}'. Продолжить?",
                    }), 400

    if existing:
        existing.name = data.get("name", existing.name)
        existing.host = data.get("host", existing.host)
        existing.port = int(data.get("port", existing.port))
        # ------------ подсказка -----------------
        # Санитайзер local_addr: 127.0.0.1 / localhost → "".
        # Иначе FFmpeg упадёт на setsockopt(IP_MULTICAST_IF) при старте.
        existing.local_addr = _sanitize_local_addr(data.get("local_addr", existing.local_addr))
        existing.channel_urls = new_urls
        existing.auto_start = bool(data.get("auto_start", existing.auto_start))
    else:
        mpts_cfg.groups.append(MptsGroup(
            id=group_id,
            name=data.get("name", "Group"),
            host=data.get("host", "239.0.0.1"),
            port=int(data.get("port", 5000)),
            # ------------ подсказка -----------------
            # Санитайзер и для новой группы — на случай, если фронт
            # пришлёт 127.0.0.1 из автозаполнения.
            local_addr=_sanitize_local_addr(data.get("local_addr", "")),
            channel_urls=new_urls,
            auto_start=bool(data.get("auto_start", False)),
        ))
    mpts_storage.save(mpts_cfg)
    return jsonify({"ok": True, "id": group_id})


@app.route("/api/mpts/groups/<group_id>", methods=["DELETE"])
@role_required("operator")
def api_mpts_delete_group(group_id):
    # ------------ подсказка -----------------
    # Сначала останавливаем процесс, потом удаляем группу из конфига.
    # Иначе FFmpeg останется сиротой и продолжит вещать в UDP.
    process_manager.stop(f"mpts_{group_id}")
    mpts_cfg.groups = [g for g in mpts_cfg.groups if g.id != group_id]
    mpts_storage.save(mpts_cfg)
    return jsonify({"ok": True})


@app.route("/api/mpts/groups/<group_id>/start", methods=["POST"])
@role_required("operator")
def api_mpts_group_start(group_id):
    # ------------ подсказка -----------------
    # Тонкая обёртка: вся логика в _start_mpts_group_internal.
    # Возвращаем 200 только при ok:true, иначе 400 с телом ошибки.
    res = _start_mpts_group_internal(group_id)
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/mpts/groups/<group_id>/stop", methods=["POST"])
@role_required("operator")
def api_mpts_group_stop(group_id):
    res = process_manager.stop(f"mpts_{group_id}")
    return jsonify(res), (200 if res.get("ok") else 404)


@app.route("/api/mpts/groups/<group_id>/monitor")
@login_required
def api_mpts_group_monitor(group_id):
    # ------------ подсказка -----------------
    # Точечный опрос состояния одной группы (для карточки на /mpts).
    # Дублирует часть _collect_ws_payload, но для конкретной группы.
    proc_name = f"mpts_{group_id}"
    status = process_manager.status(proc_name)
    if not status.get("running"):
        return jsonify({"ok": True, "running": False, "uptime_sec": 0})

    progress = read_progress_from_log(Config.LOGS_DIR / f"{proc_name}.log") or {}
    pid = status.get("pid")
    net_sent, net_rate = None, None
    if pid:
        io = get_process_io(pid)
        if io:
            now = time.time()
            prev = _network_cache.get(pid)
            net_sent = round(io["write_bytes"] / (1024 * 1024), 2)
            if prev:
                dt = now - prev["ts"]
                if dt > 0.1:
                    net_rate = round((io["write_bytes"] - prev["bytes"]) * 8 / dt / (1024 * 1024), 2)
            _network_cache[pid] = {"bytes": io["write_bytes"], "ts": now}

    return jsonify({
        "ok": True,
        "running": True,
        "uptime_sec": status.get("uptime_sec", 0),
        "frame": progress.get("frame", "—"),
        "fps": progress.get("fps", "—"),
        "bitrate": progress.get("bitrate", "—"),
        "speed": progress.get("speed", "—"),
        "out_time": progress.get("out_time", "—"),
        "drop_frames": progress.get("drop_frames", "0"),
        "net_sent_mb": net_sent,
        "net_rate_mbps": net_rate,
    })


@app.route("/api/mpts/groups/<group_id>/validate")
@login_required
def api_mpts_group_validate(group_id):
    # ------------ подсказка -----------------
    # Проверка, что UDP-поток реально идёт (ffprobe слушает multicast).
    group = next((g for g in mpts_cfg.groups if g.id == group_id), None)
    if not group:
        return jsonify({"ok": False, "error": "Нет группы"}), 404
    if not process_manager.status(f"mpts_{group_id}").get("running"):
        return jsonify({"ok": False, "error": "Не запущен"}), 400
    return jsonify({"ok": True, "result": validate_udp_stream(group.host, group.port, timeout_sec=6)})


@app.route("/api/mpts/status")
@login_required
def api_mpts_status():
    return jsonify({"ok": True, "statuses": {g.id: process_manager.status(f"mpts_{g.id}") for g in mpts_cfg.groups}})


# ============================================================
# КАНАЛЫ «папка = канал»
# ============================================================
@app.route("/channels")
@login_required
def channels_page():
    # Каналы теперь живут на первой странице вместе с плейлистом
    return redirect(url_for("playlist_page"))


@app.route("/api/channels", methods=["GET"])
@login_required
def api_channels_list():
    items = []
    for ch in channel_store.channels:
        st = channel_player.status(ch)
        files = len(list(Path(ch.output_dir).glob("*.ts"))) if Path(ch.output_dir).exists() else 0
        items.append({
            "id": ch.id, "name": ch.name, "source_dir": ch.source_dir,
            "output_dir": ch.output_dir, "ingested": files,
            "ingest_state": ch.ingest_state, "ingest_error": ch.ingest_error,
            "enabled": ch.enabled, **st,
        })
    return jsonify({"ok": True, "channels": items})


@app.route("/api/channels", methods=["POST"])
@role_required("operator")
def api_channels_create():
    data = request.get_json(silent=True) or {}
    src = (data.get("source_dir") or "").strip()
    if not src or not Path(src).is_dir():
        return jsonify({"ok": False, "error": "Папка не найдена"}), 400
    ch = channel_store.add(data.get("name", ""), src)
    return jsonify({"ok": True, "id": ch.id})


@app.route("/api/channels/<ch_id>", methods=["DELETE"])
@role_required("operator")
def api_channels_delete(ch_id):
    ch = channel_store.get(ch_id)
    if not ch:
        return jsonify({"ok": False, "error": "Канал не найден"}), 404
    if channel_player.status(ch)["running"]:
        channel_player.stop(ch)
    channel_store.remove(ch_id)
    return jsonify({"ok": True})


@app.route("/api/channels/<ch_id>/ingest", methods=["POST"])
@role_required("operator")
def api_channels_ingest(ch_id):
    ch = channel_store.get(ch_id)
    if not ch:
        return jsonify({"ok": False, "error": "Канал не найден"}), 404
    return jsonify(channel_player.start_ingest(ch))


@app.route("/api/channels/<ch_id>/start", methods=["POST"])
@role_required("operator")
def api_channels_start(ch_id):
    ch = channel_store.get(ch_id)
    if not ch:
        return jsonify({"ok": False, "error": "Канал не найден"}), 404
    res = channel_player.start(ch)
    if res.get("ok"):
        ch.enabled = True
        channel_store.save()
    return jsonify(res)


@app.route("/api/channels/<ch_id>/stop", methods=["POST"])
@role_required("operator")
def api_channels_stop(ch_id):
    ch = channel_store.get(ch_id)
    if not ch:
        return jsonify({"ok": False, "error": "Канал не найден"}), 404
    ch.enabled = False
    channel_store.save()
    return jsonify(channel_player.stop(ch))


@app.route("/api/channels/<ch_id>/shuffle", methods=["POST"])
@role_required("operator")
def api_channels_shuffle(ch_id):
    ch = channel_store.get(ch_id)
    if not ch:
        return jsonify({"ok": False, "error": "Канал не найден"}), 404
    try:
        path, total = channel_player.build_playlist(ch)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "duration_sec": round(total, 1), "playlist": str(path)})


@app.route("/api/channels/<ch_id>/url", methods=["GET"])
@login_required
def api_channels_url(ch_id):
    """URL для добавления канала в MPTS."""
    ch = channel_store.get(ch_id)
    if not ch:
        return jsonify({"ok": False, "error": "Канал не найден"}), 404
    url = f"http://127.0.0.1:{Config.HLS_HTTP_PORT}/proxy/hls/{ch.id}/index.m3u8"
    return jsonify({"ok": True, "url": url})


def _channel_name_by_url(url: str) -> str | None:
    for ch in channel_store.channels:
        if ch.id in url:
            return ch.name
    return None


@app.route("/api/system/capacity")
@login_required
def api_system_capacity():
    """Снимок нагрузки + подсказки оператору (бюджет CPU/RAM/сети)."""
    from services.capacity import evaluate
    return jsonify({"ok": True, **evaluate(process_manager, mpts_cfg, overlay_engine)})


@app.route("/api/network/interfaces")
@login_required
def api_network_interfaces():
    from services.network_interfaces import get_interfaces
    return jsonify({"ok": True, "interfaces": get_interfaces()})


# ============================================================
# API — УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ (только admin)
# ============================================================
@app.route("/api/users", methods=["GET"])
@role_required("admin")
def api_users_list():
    return jsonify({"ok": True, "users": auth.list_users()})


@app.route("/api/users", methods=["POST"])
@role_required("admin")
def api_users_create():
    data = request.get_json(silent=True) or {}
    ok, err = auth.create_user(
        data.get("username", ""),
        data.get("password", ""),
        data.get("role", "viewer"),
    )
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True})


@app.route("/api/users/<username>", methods=["DELETE"])
@role_required("admin")
def api_users_delete(username):
    ok, err = auth.delete_user(username)
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True})


@app.route("/api/users/<username>/role", methods=["POST"])
@role_required("admin")
def api_users_change_role(username):
    data = request.get_json(silent=True) or {}
    ok, err = auth.change_role(username, data.get("role", "viewer"))
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True})


@app.route("/api/users/<username>/reset-password", methods=["POST"])
@role_required("admin")
def api_users_reset_password(username):
    data = request.get_json(silent=True) or {}
    ok, err = auth.reset_password(username, data.get("password", ""))
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True})

# ============================================================
# API — МОЗАИКА
# ============================================================
@app.route("/api/mosaic/sources")
@login_required
def api_mosaic_sources():
    sources = []
    working = set(overlay_engine.cfg.working_channel_urls)
    parsed = parse_m3u(overlay_engine.cfg.source_playlist) if overlay_engine.cfg.source_playlist else []
    channels_by_url = {c["url"]: c.get("name", c["url"]) for c in parsed}
    active_overlay_urls = set()
    for url, info in overlay_engine.active_channels.items():
        active_overlay_urls.add(url)
        sources.append({
            "id": f"overlay_{info['ch_id']}",
            "type": "overlay_channel",
            "name": channels_by_url.get(url, url),
            "url": f"/proxy/hls/{info['ch_id']}/index.m3u8",
        })
    for ch in parsed:
        if ch["url"] in active_overlay_urls:
            continue
        ch_id = hashlib.md5(ch["url"].encode("utf-8")).hexdigest()[:8]
        proc_status = process_manager.status(f"chprev_{ch_id}")
        playlist_file = Config.HLS_DIR / f"chprev_{ch_id}" / "index.m3u8"
        ready = False
        if proc_status.get("running") and playlist_file.exists():
            try:
                stat = playlist_file.stat()
                ready = (time.time() - stat.st_mtime) < 10 and stat.st_size > 100
            except OSError:
                pass
        sources.append({
            "id": f"raw_{ch_id}",
            "type": "raw_channel",
            "name": ch["name"],
            "ch_id": ch_id,
            "url": f"/mosaic/chprev/{ch_id}/index.m3u8" if ready else None,
            "preview_running": ready,
            "in_working": ch["url"] in working,
        })
    for g in mpts_cfg.groups:
        mpts_status = process_manager.status(f"mpts_{g.id}")
        preview_status = process_manager.status(f"mpts_preview_{g.id}")
        playlist_file = Config.HLS_DIR / f"preview_{g.id}" / "index.m3u8"
        preview_ready = False
        if preview_status.get("running") and playlist_file.exists():
            try:
                stat = playlist_file.stat()
                preview_ready = (time.time() - stat.st_mtime) < 10 and stat.st_size > 100
            except OSError:
                pass
        sources.append({
            "id": f"mpts_{g.id}",
            "type": "mpts_group",
            "name": f"MPTS: {g.name} ({g.host}:{g.port})",
            "url": f"/mosaic/hls/{g.id}/index.m3u8" if preview_ready else None,
            "mpts_running": mpts_status.get("running", False),
            "preview_running": preview_ready,
        })
    return jsonify({"ok": True, "sources": sources})


@app.route("/api/mosaic/preview/<group_id>/start", methods=["POST"])
@role_required("operator")
def api_mosaic_preview_start(group_id):
    group = next((g for g in mpts_cfg.groups if g.id == group_id), None)
    if not group:
        return jsonify({"ok": False, "error": "Нет группы"}), 404
    if not process_manager.status(f"mpts_{group_id}").get("running"):
        return jsonify({"ok": False, "error": "Запустите MPTS"}), 400
    preview_dir = Config.HLS_DIR / f"preview_{group_id}"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for f in preview_dir.glob("*.ts"):
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass
    if (preview_dir / "index.m3u8").exists():
        try:
            (preview_dir / "index.m3u8").unlink()
        except Exception:
            pass
    udp_url = f"udp://@{group.host}:{group.port}" if group.host.startswith(("22", "23")) else f"udp://{group.host}:{group.port}"
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", udp_url, "-c", "copy",
        "-f", "hls", "-hls_time", "2", "-hls_list_size", "5",
        "-hls_flags", "delete_segments+append_list",
        "-hls_segment_filename", str(preview_dir / "seg_%03d.ts"),
        str(preview_dir / "index.m3u8"),
    ]
    return jsonify(process_manager.start(f"mpts_preview_{group_id}", cmd))


@app.route("/api/mosaic/preview/<group_id>/stop", methods=["POST"])
@role_required("operator")
def api_mosaic_preview_stop(group_id):
    return jsonify(process_manager.stop(f"mpts_preview_{group_id}"))


@app.route("/api/mosaic/preview-channel/<ch_id>/start", methods=["POST"])
@role_required("operator")
def api_mosaic_channel_preview_start(ch_id):
    parsed = parse_m3u(overlay_engine.cfg.source_playlist) if overlay_engine.cfg.source_playlist else []
    target = next((ch for ch in parsed if hashlib.md5(ch["url"].encode("utf-8")).hexdigest()[:8] == ch_id), None)
    if not target:
        return jsonify({"ok": False, "error": "Нет канала"}), 404
    preview_dir = Config.HLS_DIR / f"chprev_{ch_id}"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for f in preview_dir.glob("*.ts"):
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass
    if (preview_dir / "index.m3u8").exists():
        try:
            (preview_dir / "index.m3u8").unlink()
        except Exception:
            pass
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", target["url"], "-c", "copy",
        "-f", "hls", "-hls_time", "2", "-hls_list_size", "5",
        "-hls_flags", "delete_segments+append_list",
        "-hls_segment_filename", str(preview_dir / "seg_%03d.ts"),
        str(preview_dir / "index.m3u8"),
    ]
    return jsonify(process_manager.start(f"chprev_{ch_id}", cmd))


@app.route("/api/mosaic/preview-channel/<ch_id>/stop", methods=["POST"])
@role_required("operator")
def api_mosaic_channel_preview_stop(ch_id):
    return jsonify(process_manager.stop(f"chprev_{ch_id}"))


# ============================================================
# API — ОБЩИЕ ПРОЦЕССЫ
# ============================================================
@app.route("/api/process/status")
@login_required
def api_process_status():
    statuses = {p["name"]: process_manager.status(p["name"]) for p in process_manager.list_all()}
    for item in overlay_engine.get_active_snapshot():
        statuses[item["process_name"]] = process_manager.status(item["process_name"])
    for g in mpts_cfg.groups:
        statuses[f"mpts_{g.id}"] = process_manager.status(f"mpts_{g.id}")
    return jsonify({
        "ok": True,
        "any_running": any(s.get("running", False) for s in statuses.values()),
        "processes": statuses,
    })


@app.route("/api/process/log")
@login_required
def api_process_log():
    name = request.args.get("name", "test_ffmpeg")
    log_path = Config.LOGS_DIR / f"{name}.log"
    if not log_path.exists():
        return jsonify({"ok": True, "total": 0, "lines": []})
    all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return jsonify({
        "ok": True,
        "total": len(all_lines),
        "lines": all_lines[-request.args.get("lines", 50, type=int):],
    })


@app.route("/api/process/start", methods=["POST"])
@role_required("operator")
def api_process_start():
    return jsonify(process_manager.start("test_ffmpeg", TEST_FFMPEG_COMMAND))


@app.route("/api/process/stop", methods=["POST"])
@role_required("operator")
def api_process_stop():
    return jsonify(process_manager.stop("test_ffmpeg"))


@app.route("/api/process/stop-all", methods=["POST"])
@role_required("admin")
def api_process_stop_all():
    import sys
    import subprocess as sp
    result = {"ok": True, "stopped": [], "orphans_killed": 0}
    try:
        overlay_engine.stop_all()
        result["stopped"].append("overlay_proxies")
    except Exception:
        pass
    try:
        result["stopped"].extend(process_manager.stop_all())
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            cp = sp.run(["taskkill", "/IM", "ffmpeg.exe", "/F"], capture_output=True, text=True, timeout=5)
            result["orphans_killed"] = cp.stdout.count("SUCCESS")
        except Exception:
            pass
    result["message"] = f"Остановлено: {len(result['stopped'])}. Убито: {result['orphans_killed']}."
    return jsonify(result)


@app.route("/api/process/restart-all", methods=["POST"])
@role_required("admin")
def api_process_restart_all():
    import sys
    import subprocess as sp
    result = {"ok": True, "started_mpts": 0, "errors": []}
    was_running = [g.id for g in mpts_cfg.groups if process_manager.status(f"mpts_{g.id}").get("running")]
    try:
        overlay_engine.stop_all()
    except Exception:
        pass
    try:
        process_manager.stop_all()
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            sp.run(["taskkill", "/IM", "ffmpeg.exe", "/F"], capture_output=True)
        except Exception:
            pass
    time.sleep(1.5)
    try:
        overlay_engine.sync()
    except Exception:
        pass
    for gid in was_running:
        try:
            res = _start_mpts_group_internal(gid)
            if res.get("ok"):
                result["started_mpts"] += 1
        except Exception:
            pass
    result["message"] = f"Перезапущено MPTS: {result['started_mpts']}"
    return jsonify(result)


# ============================================================
# РАЗДАЧА HLS (с защитой от PermissionError)
# ============================================================
@app.route("/overlay/hls/<ch_id>/<path:filename>")
def serve_hls(ch_id, filename):
    try:
        resp = send_from_directory(str(Path(Config.HLS_DIR) / ch_id), filename)
        if filename.endswith(".m3u8"):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp
    except PermissionError:
        return "Locked", 503, {"Retry-After": "1"}
    except FileNotFoundError:
        return "Not found", 404


@app.route("/proxy/hls/<ch_id>/<path:filename>")
def serve_proxy_hls(ch_id, filename):
    try:
        resp = send_from_directory(str(Path(Config.HLS_DIR) / f"proxy_{ch_id}"), filename)
        if filename.endswith(".m3u8"):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        elif filename.endswith(".ts"):
            resp.headers["Cache-Control"] = "public, max-age=5"
        return resp
    except PermissionError:
        return "Locked", 503, {"Retry-After": "1"}
    except FileNotFoundError:
        return "Not found", 404


@app.route("/mosaic/hls/<group_id>/<path:filename>")
def serve_mosaic_hls(group_id, filename):
    try:
        resp = send_from_directory(str(Path(Config.HLS_DIR) / f"preview_{group_id}"), filename)
        if filename.endswith(".m3u8"):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        elif filename.endswith(".ts"):
            resp.headers["Cache-Control"] = "public, max-age=5"
        return resp
    except PermissionError:
        return "Locked", 503, {"Retry-After": "1"}
    except FileNotFoundError:
        return "Not found", 404


@app.route("/mosaic/chprev/<ch_id>/<path:filename>")
def serve_chprev_hls(ch_id, filename):
    try:
        resp = send_from_directory(str(Path(Config.HLS_DIR) / f"chprev_{ch_id}"), filename)
        if filename.endswith(".m3u8"):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        elif filename.endswith(".ts"):
            resp.headers["Cache-Control"] = "public, max-age=5"
        return resp
    except PermissionError:
        return "Locked", 503, {"Retry-After": "1"}
    except FileNotFoundError:
        return "Not found", 404


# ============================================================
# ТОЧКА ВХОДА
# ============================================================
if __name__ == "__main__":
    # ------------ подсказка -----------------
    # Выводим список маршрутов при старте для отладки.
    print("=" * 70)
    print("ЗАГРУЖЕНЫ МАРШРУТЫ:")
    for r in sorted(app.url_map.iter_rules(), key=lambda x: x.rule):
        print(f"  {r.rule:55} -> {r.endpoint}")
    print("=" * 70)

    # Автозапуск групп MPTS
    start_hls_server(Config.HLS_DIR, Config.HLS_HTTP_PORT)
    _auto_start_mpts_groups()

    socketio.run(app, host=Config.WEB_HOST, port=5000, debug=Config.DEBUG, use_reloader=False, allow_unsafe_werkzeug=True)
