"""
Точка входа Flask-приложения.
"""

import time
import uuid
import hashlib
import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask, render_template, request, jsonify,
    send_from_directory, redirect, url_for, session,
)
from apscheduler.schedulers.background import BackgroundScheduler

from config import Config
from services import (
    ProcessManager, parse_m3u,
    MptsChannel, MptsProfile, build_mpts_command, build_udp_url,
    OverlayStorage, OverlayEngine, OverlayRule, OverlayText, TimeWindow,
    MptsStorage, MptsGroup,
    check_channels_parallel, read_progress_from_log,
    validate_udp_stream, get_process_io,
    AuthManager,
)


app = Flask(__name__)
app.config.from_object(Config)
app.permanent_session_lifetime = Config.PERMANENT_SESSION_LIFETIME

process_manager = ProcessManager(Config.LOGS_DIR)
auth = AuthManager(Config.USERS_FILE)

overlay_storage = OverlayStorage(Config.OVERLAY_CONFIG_PATH)
overlay_cfg = overlay_storage.load()
overlay_engine = OverlayEngine(process_manager, Config.HLS_DIR, overlay_cfg)

mpts_storage = MptsStorage(Config.MPTS_CONFIG_PATH)
mpts_cfg = mpts_storage.load()


# --- Автомиграция: старые группы с proxy URL -> оригинальные URL ---
def _migrate_old_groups():
    working = set(overlay_engine.cfg.working_channel_urls)
    changed = False
    for g in mpts_cfg.groups:
        new_urls = []
        for url in g.channel_urls:
            if "/proxy/hls/" in url:
                parts = url.split("/proxy/hls/")
                if len(parts) == 2:
                    ch_id = parts[1].split("/")[0]
                    found = None
                    for w_url in working:
                        if hashlib.md5(w_url.encode("utf-8")).hexdigest()[:8] == ch_id:
                            found = w_url
                            break
                    new_urls.append(found if found else url)
                else:
                    new_urls.append(url)
            else:
                new_urls.append(url)
        if new_urls != g.channel_urls:
            g.channel_urls = new_urls
            changed = True
    if changed:
        mpts_storage.save(mpts_cfg)
        print("[migration] Старые группы с proxy URL преобразованы в оригиналы")


_migrate_old_groups()

scheduler = BackgroundScheduler()
scheduler.add_job(func=overlay_engine.sync, trigger="interval", seconds=30)
scheduler.start()

TEST_FFMPEG_COMMAND = [
    "ffmpeg", "-re", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
    "-t", "3600", "-f", "null", "-",
]
KNOWN_PROCESSES = ("test_ffmpeg",)
_network_cache = {}


# ============================================================
# Авторизация
# ============================================================

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Требуется авторизация"}), 401
            return redirect(url_for("login_page", next=request.path))
        return f(*args, **kwargs)
    return wrapper


@app.before_request
def enforce_password_change():
    if not session.get("user") or not session["user"].get("must_change_password"):
        return None
    allowed_paths = {"/change-password", "/logout", "/login", "/favicon.ico"}
    if request.path in allowed_paths or request.path.startswith("/static/"):
        return None
    if request.path in ("/api/auth/change-password", "/api/auth/me"):
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
    next_url = request.form.get("next") or "/"

    user = auth.verify(username, password)
    if not user:
        return render_template("login.html", error="Неверный логин или пароль", next=next_url)

    session.permanent = True
    session["user"] = {
        "username": user["username"],
        "role": user.get("role", "user"),
        "must_change_password": user.get("must_change_password", False),
    }
    if user.get("must_change_password"):
        return redirect(url_for("change_password_page"))
    if not next_url.startswith("/"):
        next_url = "/"
    return redirect(next_url)


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
    new_password = (data.get("new_password") or "").strip()
    if len(new_password) < 6:
        return jsonify({"ok": False, "error": "Пароль должен быть не короче 6 символов"}), 400
    username = session["user"]["username"]
    if auth.change_password(username, new_password):
        session["user"]["must_change_password"] = False
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Пользователь не найден"}), 404


# ============================================================
# Страницы
# ============================================================

@app.route("/")
@login_required
def index():
    return redirect(url_for("playlist_page"))


@app.route("/playlist")
@login_required
def playlist_page():
    return render_template("playlist.html")


@app.route("/mpts")
@login_required
def mpts_page():
    return render_template("mpts.html")


@app.route("/mosaic")
@login_required
def mosaic_page():
    return render_template("mosaic.html")


@app.route("/overlay")
@login_required
def overlay_page():
    return render_template("overlay.html")


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


@app.context_processor
def inject_globals():
    return {"config": Config, "current_year": datetime.datetime.now().year}


# ============================================================
# Плейлисты
# ============================================================

@app.route("/api/playlists/parse", methods=["POST"])
@login_required
def api_parse_playlist():
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    if not content.strip():
        return jsonify({"ok": False, "error": "Пустой плейлист"}), 400
    channels = parse_m3u(content)
    return jsonify({"ok": True, "count": len(channels), "channels": channels, "raw_content": content})


@app.route("/api/playlists/parse-file", methods=["POST"])
@login_required
def api_parse_playlist_from_file():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Файл не приложен"}), 400
    f = request.files["file"]
    raw = f.read()
    content = None
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            content = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if content is None:
        return jsonify({"ok": False, "error": "Кодировка не распознана"}), 400
    channels = parse_m3u(content)
    return jsonify({"ok": True, "count": len(channels), "channels": channels, "raw_content": content})


@app.route("/api/playlists/parse-url", methods=["POST"])
@login_required
def api_parse_playlist_from_url():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return jsonify({"ok": False, "error": "Некорректный URL"}), 400
    try:
        import requests
        resp = requests.get(url, timeout=15, headers={"User-Agent": "IPTV-OVERLAY-MPTS/1.0"})
        resp.raise_for_status()
        content = resp.text
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Ошибка скачивания: {exc}"}), 400
    channels = parse_m3u(content)
    return jsonify({"ok": True, "count": len(channels), "channels": channels, "raw_content": content})


# ============================================================
# Рабочий плейлист (используется /playlist, /mpts, /overlay)
# ============================================================

@app.route("/api/playlist/source", methods=["GET", "POST"])
@login_required
def api_playlist_source():
    if request.method == "GET":
        return jsonify({"ok": True, "content": overlay_engine.cfg.source_playlist})
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    if not content.strip():
        return jsonify({"ok": False, "error": "Пустой плейлист"}), 400
    overlay_engine.cfg.source_playlist = content
    overlay_storage.save(overlay_engine.cfg)
    return jsonify({"ok": True})


@app.route("/api/playlist/working", methods=["GET", "POST"])
@login_required
def api_playlist_working():
    if request.method == "GET":
        return jsonify({"ok": True, "urls": overlay_engine.cfg.working_channel_urls})
    data = request.get_json(silent=True) or {}
    urls = data.get("urls", [])
    overlay_engine.cfg.working_channel_urls = urls
    overlay_storage.save(overlay_engine.cfg)
    overlay_engine.sync()
    return jsonify({"ok": True})


# ============================================================
# Overlay — только правила
# ============================================================

@app.route("/api/overlay/rules", methods=["GET", "POST"])
@login_required
def api_overlay_rules():
    if request.method == "GET":
        rules_data = [overlay_storage.rule_to_dict(r) for r in overlay_engine.cfg.rules]
        return jsonify({"ok": True, "rules": rules_data})

    data = request.get_json(silent=True) or {}
    rule_id = data.get("id") or f"rule-{uuid.uuid4().hex[:6]}"
    req_urls = data.get("channel_urls", [])

    # Проверка: каналы должны быть в MPTS
    mpts_channels = set()
    for g in mpts_cfg.groups:
        mpts_channels.update(g.channel_urls)

    missing_from_mpts = [u for u in req_urls if u not in mpts_channels]
    if missing_from_mpts and not data.get("force"):
        return jsonify({
            "ok": False, "require_confirm": True,
            "warning": f"Внимание! {len(missing_from_mpts)} канал(ов) не входят в сетку вещания (MPTS). Строка на них не попадёт в эфир. Продолжить?"
        }), 400

    new_rule = OverlayRule(
        id=rule_id,
        name=data.get("name", "Без названия"),
        text=OverlayText(
            text=data.get("text", "Текст"),
            font_path=data.get("font_path", "C:/Windows/Fonts/seguiemj.ttf"),
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
    overlay_engine.cfg.rules = [r for r in overlay_engine.cfg.rules if r.id != rule_id]
    overlay_storage.save(overlay_engine.cfg)
    overlay_engine.sync()
    return jsonify({"ok": True})


@app.route("/api/overlay/available-channels")
@login_required
def api_overlay_available_channels():
    """Каналы из рабочего плейлиста, доступные для наложения правил."""
    working = set(overlay_engine.cfg.working_channel_urls)
    parsed = parse_m3u(overlay_engine.cfg.source_playlist) if overlay_engine.cfg.source_playlist else []
    channels = [ch for ch in parsed if ch["url"] in working]
    return jsonify({"ok": True, "channels": channels})


# ============================================================
# MPTS
# ============================================================

@app.route("/api/network/interfaces")
@login_required
def api_network_interfaces():
    from services.network_interfaces import get_interfaces
    return jsonify({"ok": True, "interfaces": get_interfaces()})


@app.route("/api/mpts/config", methods=["GET"])
@login_required
def api_mpts_config():
    """Каналы — из рабочего плейлиста (тот же, что в /playlist)."""
    working = set(overlay_engine.cfg.working_channel_urls)
    parsed = parse_m3u(overlay_engine.cfg.source_playlist) if overlay_engine.cfg.source_playlist else []
    channels = [ch for ch in parsed if ch["url"] in working]

    return jsonify({
        "ok": True,
        "channels": channels,
        "groups": [
            {"id": g.id, "name": g.name, "host": g.host, "port": g.port,
             "local_addr": g.local_addr, "channel_urls": g.channel_urls}
            for g in mpts_cfg.groups
        ]
    })


@app.route("/api/mpts/check-channels", methods=["POST"])
@login_required
def api_mpts_check_channels():
    data = request.get_json(silent=True) or {}
    urls = data.get("urls", [])
    if not isinstance(urls, list) or not urls:
        return jsonify({"ok": False, "error": "urls должен быть непустым списком"}), 400
    results = check_channels_parallel(urls, timeout=12)
    return jsonify({"ok": True, "results": results})


@app.route("/api/mpts/groups", methods=["POST"])
@login_required
def api_mpts_create_group():
    data = request.get_json(silent=True) or {}
    group_id = data.get("id") or f"grp-{uuid.uuid4().hex[:6]}"
    new_urls = data.get("channel_urls", [])

    existing = next((g for g in mpts_cfg.groups if g.id == group_id), None)

    if existing and not data.get("force"):
        removed_urls = [u for u in existing.channel_urls if u not in new_urls]
        for url in removed_urls:
            for rule in overlay_engine.cfg.rules:
                if url in rule.channel_urls:
                    return jsonify({
                        "ok": False, "require_confirm": True,
                        "warning": f"Внимание! Вы удаляете канал, на котором активно правило '{rule.name}'. Строка останется, но канал выйдет из эфира MPTS. Продолжить?"
                    }), 400

    if existing:
        existing.name = data.get("name", existing.name)
        existing.host = data.get("host", existing.host)
        existing.port = int(data.get("port", existing.port))
        existing.local_addr = data.get("local_addr", existing.local_addr)
        existing.channel_urls = new_urls
    else:
        mpts_cfg.groups.append(MptsGroup(
            id=group_id, name=data.get("name", "Group"),
            host=data.get("host", "239.0.0.1"),
            port=int(data.get("port", 5000)),
            local_addr=data.get("local_addr", ""),
            channel_urls=new_urls,
        ))
    mpts_storage.save(mpts_cfg)
    return jsonify({"ok": True, "id": group_id})


@app.route("/api/mpts/groups/<group_id>", methods=["DELETE"])
@login_required
def api_mpts_delete_group(group_id):
    process_manager.stop(f"mpts_{group_id}")
    mpts_cfg.groups = [g for g in mpts_cfg.groups if g.id != group_id]
    mpts_storage.save(mpts_cfg)
    return jsonify({"ok": True})


@app.route("/api/mpts/groups/<group_id>/start", methods=["POST"])
@login_required
def api_mpts_group_start(group_id):
    group = next((g for g in mpts_cfg.groups if g.id == group_id), None)
    if not group:
        return jsonify({"ok": False, "error": "Группа не найдена"}), 404

    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", False))

    parsed = parse_m3u(overlay_engine.cfg.source_playlist) if overlay_engine.cfg.source_playlist else []
    working = set(overlay_engine.cfg.working_channel_urls)

    selected = []
    for url in group.channel_urls:
        if url not in working:
            continue
        ch = next((c for c in parsed if c["url"] == url), None)
        if not ch:
            continue
        ch_id = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
        proxy_url = f"{overlay_engine.cfg.public_base_url}/proxy/hls/{ch_id}/index.m3u8"
        selected.append(MptsChannel(name=ch["name"], url=proxy_url))

    if not selected:
        return jsonify({"ok": False, "error": "Нет валидных каналов (все не в рабочем плейлисте)"}), 400

    # Диагностика: проверяем, у каких каналов есть готовые файлы и живы ли процессы
    not_ready_names = []
    dead_names = []
    for ch in selected:
        if "/proxy/hls/" not in ch.url:
            continue
        ch_id = ch.url.split("/proxy/hls/")[1].split("/")[0]
        d = Config.HLS_DIR / f"proxy_{ch_id}"
        has_m3u8 = (d / "index.m3u8").exists()
        has_ts = d.exists() and any(d.glob("*.ts"))
        if has_m3u8 and has_ts:
            continue  # готов
        not_ready_names.append(ch.name)
        # Проверим, жив ли процесс
        st = process_manager.status(f"proxy_{ch_id}")
        if not st.get("running"):
            dead_names.append(ch.name)

    if not_ready_names and not force:
        msg = f"Внимание: {len(not_ready_names)} прокси-поток(ов) ещё не готовы:\n\n"
        msg += "  • " + "\n  • ".join(not_ready_names) + "\n\n"
        if dead_names:
            msg += f"⚠ Из них {len(dead_names)} процессов УПАЛИ. Проверьте логи в data/logs/proxy_*.log\n\n"
        else:
            msg += "⏳ Процессы работают, но HLS ещё формируется (обычно 10–30 секунд для HLS-источников).\n\n"
        msg += "Можно подождать и попробовать снова, ИЛИ запустить MPTS сейчас — он будет повторять подключение до 30 секунд.\n\nПродолжить?"
        return jsonify({"ok": False, "require_confirm": True, "warning": msg}), 200

    profile = MptsProfile(
        channels=selected,
        output_url=build_udp_url(group.host, group.port, local_addr=group.local_addr)
    )
    cmd = build_mpts_command(profile)
    res = process_manager.start(f"mpts_{group_id}", cmd)
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/mpts/groups/<group_id>/stop", methods=["POST"])
@login_required
def api_mpts_group_stop(group_id):
    res = process_manager.stop(f"mpts_{group_id}")
    return jsonify(res), (200 if res.get("ok") else 404)


@app.route("/api/mpts/groups/<group_id>/monitor")
@login_required
def api_mpts_group_monitor(group_id):
    proc_name = f"mpts_{group_id}"
    status = process_manager.status(proc_name)
    if not status.get("running"):
        return jsonify({"ok": True, "running": False, "uptime_sec": 0})

    log_path = Config.LOGS_DIR / f"{proc_name}.log"
    progress = read_progress_from_log(log_path) or {}
    pid = status.get("pid")
    net_sent_mb, net_rate_mbps = None, None
    if pid:
        io = get_process_io(pid)
        if io:
            now = time.time()
            prev = _network_cache.get(pid)
            net_sent_mb = round(io["write_bytes"] / (1024 * 1024), 2)
            if prev:
                dt = now - prev["ts"]
                if dt > 0.1:
                    net_rate_mbps = round((io["write_bytes"] - prev["bytes"]) * 8 / dt / (1024 * 1024), 2)
            _network_cache[pid] = {"bytes": io["write_bytes"], "ts": now}

    return jsonify({
        "ok": True, "running": True, "uptime_sec": status.get("uptime_sec", 0),
        "frame": progress.get("frame", "—"), "fps": progress.get("fps", "—"),
        "bitrate": progress.get("bitrate", "—"), "speed": progress.get("speed", "—"),
        "out_time": progress.get("out_time", "—"), "drop_frames": progress.get("drop_frames", "0"),
        "net_sent_mb": net_sent_mb, "net_rate_mbps": net_rate_mbps,
    })


@app.route("/api/mpts/groups/<group_id>/validate")
@login_required
def api_mpts_group_validate(group_id):
    group = next((g for g in mpts_cfg.groups if g.id == group_id), None)
    if not group:
        return jsonify({"ok": False, "error": "Группа не найдена"}), 404
    if not process_manager.status(f"mpts_{group_id}").get("running"):
        return jsonify({"ok": False, "error": "Поток не запущен"}), 400
    return jsonify({"ok": True, "result": validate_udp_stream(group.host, group.port, timeout_sec=6)})


@app.route("/api/mpts/status")
@login_required
def api_mpts_status():
    return jsonify({"ok": True, "statuses": {g.id: process_manager.status(f"mpts_{g.id}") for g in mpts_cfg.groups}})


# ============================================================
# Мозаика
# ============================================================

@app.route("/api/mosaic/sources")
@login_required
def api_mosaic_sources():
    sources = []
    working = set(overlay_engine.cfg.working_channel_urls)
    parsed = parse_m3u(overlay_engine.cfg.source_playlist) if overlay_engine.cfg.source_playlist else []
    channels_by_url = {c["url"]: c.get("name", c["url"]) for c in parsed}

    active_overlay_urls = set()
    for item in overlay_engine.get_active_snapshot():
        active_overlay_urls.add(item["url"])
        sources.append({
            "id": f"overlay_{item['ch_id']}", "type": "overlay_channel",
            "name": channels_by_url.get(item["url"], item["url"]),
            "url": f"/proxy/hls/{item['ch_id']}/index.m3u8",
        })

    for ch in parsed:
        if ch["url"] in active_overlay_urls:
            continue
        ch_id = hashlib.md5(ch["url"].encode("utf-8")).hexdigest()[:8]
        prev = process_manager.status(f"chprev_{ch_id}")
        sources.append({
            "id": f"raw_{ch_id}", "type": "raw_channel", "name": ch["name"], "ch_id": ch_id,
            "url": f"/mosaic/chprev/{ch_id}/index.m3u8" if prev.get("running") else None,
            "preview_running": prev.get("running", False),
            "in_working": ch["url"] in working,
        })

    for g in mpts_cfg.groups:
        mpts_st = process_manager.status(f"mpts_{g.id}")
        prev = process_manager.status(f"mpts_preview_{g.id}")
        sources.append({
            "id": f"mpts_{g.id}", "type": "mpts_group",
            "name": f"MPTS: {g.name} ({g.host}:{g.port})",
            "url": f"/mosaic/hls/{g.id}/index.m3u8" if prev.get("running") else None,
            "mpts_running": mpts_st.get("running", False),
            "preview_running": prev.get("running", False),
        })

    return jsonify({"ok": True, "sources": sources})


@app.route("/api/mosaic/preview/<group_id>/start", methods=["POST"])
@login_required
def api_mosaic_preview_start(group_id):
    group = next((g for g in mpts_cfg.groups if g.id == group_id), None)
    if not group:
        return jsonify({"ok": False, "error": "Группа не найдена"}), 404
    if not process_manager.status(f"mpts_{group_id}").get("running"):
        return jsonify({"ok": False, "error": "Сначала запустите MPTS"}), 400

    preview_dir = Config.HLS_DIR / f"preview_{group_id}"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for f in preview_dir.glob("*.ts"):
        try: f.unlink(missing_ok=True)
        except: pass
    if (preview_dir / "index.m3u8").exists():
        try: (preview_dir / "index.m3u8").unlink()
        except: pass

    udp_url = f"udp://@{group.host}:{group.port}" if group.host.startswith(("22", "23")) else f"udp://{group.host}:{group.port}"
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", udp_url,
           "-c", "copy", "-f", "hls", "-hls_time", "2", "-hls_list_size", "5",
           "-hls_flags", "delete_segments+append_list",
           "-hls_segment_filename", str(preview_dir / "seg_%03d.ts"),
           str(preview_dir / "index.m3u8")]
    res = process_manager.start(f"mpts_preview_{group_id}", cmd)
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/mosaic/preview/<group_id>/stop", methods=["POST"])
@login_required
def api_mosaic_preview_stop(group_id):
    return jsonify(process_manager.stop(f"mpts_preview_{group_id}"))


@app.route("/api/mosaic/preview-channel/<ch_id>/start", methods=["POST"])
@login_required
def api_mosaic_channel_preview_start(ch_id):
    parsed = parse_m3u(overlay_engine.cfg.source_playlist) if overlay_engine.cfg.source_playlist else []
    target = next((ch for ch in parsed if hashlib.md5(ch["url"].encode("utf-8")).hexdigest()[:8] == ch_id), None)
    if not target:
        return jsonify({"ok": False, "error": "Канал не найден"}), 404

    preview_dir = Config.HLS_DIR / f"chprev_{ch_id}"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for f in preview_dir.glob("*.ts"):
        try: f.unlink(missing_ok=True)
        except: pass
    if (preview_dir / "index.m3u8").exists():
        try: (preview_dir / "index.m3u8").unlink()
        except: pass

    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", target["url"],
           "-c", "copy", "-f", "hls", "-hls_time", "2", "-hls_list_size", "5",
           "-hls_flags", "delete_segments+append_list",
           "-hls_segment_filename", str(preview_dir / "seg_%03d.ts"),
           str(preview_dir / "index.m3u8")]
    res = process_manager.start(f"chprev_{ch_id}", cmd)
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/mosaic/preview-channel/<ch_id>/stop", methods=["POST"])
@login_required
def api_mosaic_channel_preview_stop(ch_id):
    return jsonify(process_manager.stop(f"chprev_{ch_id}"))


# ============================================================
# Общие процессы
# ============================================================

@app.route("/api/process/status")
@login_required
def api_process_status():
    statuses = {name: process_manager.status(name) for name in KNOWN_PROCESSES}
    for item in overlay_engine.get_active_snapshot():
        statuses[item["process_name"]] = process_manager.status(item["process_name"])
    for g in mpts_cfg.groups:
        statuses[f"mpts_{g.id}"] = process_manager.status(f"mpts_{g.id}")
    return jsonify({"ok": True, "any_running": any(s.get("running", False) for s in statuses.values()),
                    "processes": statuses})


@app.route("/api/process/log")
@login_required
def api_process_log():
    name = request.args.get("name", "test_ffmpeg")
    log_path = Config.LOGS_DIR / f"{name}.log"
    if not log_path.exists():
        return jsonify({"ok": True, "total": 0, "lines": []})
    all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return jsonify({"ok": True, "total": len(all_lines),
                    "lines": all_lines[-request.args.get("lines", 50, type=int):]})


@app.route("/api/process/start", methods=["POST"])
@login_required
def api_process_start():
    res = process_manager.start("test_ffmpeg", TEST_FFMPEG_COMMAND)
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/process/stop", methods=["POST"])
@login_required
def api_process_stop():
    res = process_manager.stop("test_ffmpeg")
    return jsonify(res), (200 if res.get("ok") else 404)


# ============================================================
# Раздача HLS
# ============================================================

@app.route("/proxy/hls/<ch_id>/<path:filename>")
def serve_proxy_hls(ch_id, filename):
    directory = Path(Config.HLS_DIR) / f"proxy_{ch_id}"
    resp = send_from_directory(str(directory), filename)
    if filename.endswith(".m3u8"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    elif filename.endswith(".ts"):
        resp.headers["Cache-Control"] = "public, max-age=5"
    return resp


@app.route("/mosaic/hls/<group_id>/<path:filename>")
def serve_mosaic_hls(group_id, filename):
    directory = Path(Config.HLS_DIR) / f"preview_{group_id}"
    resp = send_from_directory(str(directory), filename)
    if filename.endswith(".m3u8"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    elif filename.endswith(".ts"):
        resp.headers["Cache-Control"] = "public, max-age=5"
    return resp


@app.route("/mosaic/chprev/<ch_id>/<path:filename>")
def serve_chprev_hls(ch_id, filename):
    directory = Path(Config.HLS_DIR) / f"chprev_{ch_id}"
    resp = send_from_directory(str(directory), filename)
    if filename.endswith(".m3u8"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    elif filename.endswith(".ts"):
        resp.headers["Cache-Control"] = "public, max-age=5"
    return resp


@app.route("/overlay/hls/<ch_id>/<path:filename>")
def serve_hls(ch_id, filename):
    directory = Path(Config.HLS_DIR) / ch_id
    resp = send_from_directory(str(directory), filename)
    if filename.endswith(".m3u8"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    elif filename.endswith(".ts"):
        resp.headers["Cache-Control"] = "public, max-age=5"
    return resp


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=Config.DEBUG, use_reloader=False)