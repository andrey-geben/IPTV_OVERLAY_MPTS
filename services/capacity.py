"""Бюджет нагрузки: подсказки оператору, чтобы не перегрузить железо.

Отвечает на вопрос «сколько ещё можно» в терминах, понятных оператору:
  * сколько каналов с бегущей строкой ещё выдержит CPU;
  * сколько программ суммарно влезает в сеть и в QAM-каналы;
  * хватит ли RAM;
  * что именно сделать, если лимит превышен.

Все оценки — консервативные, с запасом: лучше предупредить раньше.
"""

from __future__ import annotations

import shutil
import subprocess
import time

import psutil

from .encoders import qsv_available

# --- Стоимость на канал (в % одного ядра CPU) ---
CPU_PER_COPY = 1.0          # HLS-прокси без оверлея (-c copy)
CPU_PER_OVERLAY = 18.0      # libx264 ultrafast 720x576
CPU_PER_OVERLAY_QSV = 5.0   # h264_qsv 720x576
CPU_PER_MPTS_PROGRAM = 0.6  # муксинг одной программы в MPTS

RAM_PER_PROCESS_MB = 60
RAM_PER_MPTS_MB = 120

# --- Сеть ---
MBPS_PER_PROGRAM = 3.2      # измерено на живом потоке (2 канала = 6.42 Мбит/с)
QAM_CAPACITY_MBPS = 48.0    # один RF-канал КТВ, 256QAM
RECOMMENDED_PER_GROUP = 12  # программ в одном MPTS с запасом

_state: dict = {"net": None}
_qsv_cached: bool | None = None


def _qsv_available() -> bool:
    return qsv_available()


def _net_rates() -> dict:
    """Скорость сети: считаем дельту между вызовами (раз в ~2 с)."""
    now = time.time()
    counters = psutil.net_io_counters()
    prev = _state.get("net")
    rates = {"rx_mbps": 0.0, "tx_mbps": 0.0}
    if prev:
        dt = now - prev["ts"]
        if dt > 0.2:
            rates["rx_mbps"] = round((counters.bytes_recv - prev["rx"]) * 8 / dt / 1e6, 1)
            rates["tx_mbps"] = round((counters.bytes_sent - prev["tx"]) * 8 / dt / 1e6, 1)
    _state["net"] = {"ts": now, "rx": counters.bytes_recv, "tx": counters.bytes_sent}
    return rates


def evaluate(process_manager=None, mpts_cfg=None, overlay_engine=None,
             link_mbps: float = 1000.0) -> dict:
    """Собирает снимок нагрузки и вердикт «можно ещё / нельзя»."""
    cpu_count = psutil.cpu_count(logical=True) or 4
    cpu_pct = psutil.cpu_percent(interval=None)
    vm = psutil.virtual_memory()
    net = _net_rates()

    # --- что реально запущено ---
    overlay_copy = 0
    overlay_enc = 0
    if overlay_engine is not None and process_manager is not None:
        for info in overlay_engine.active_channels.values():
            if process_manager.status(info["process_name"]).get("running"):
                if info.get("rule_id"):
                    overlay_enc += 1
                else:
                    overlay_copy += 1

    mpts_groups = 0
    mpts_programs = 0
    if mpts_cfg is not None and process_manager is not None:
        for g in mpts_cfg.groups:
            if process_manager.status(f"mpts_{g.id}").get("running"):
                mpts_groups += 1
                mpts_programs += len(g.channel_urls)

    qsv = _qsv_available()
    per_overlay = CPU_PER_OVERLAY_QSV if qsv else CPU_PER_OVERLAY

    # --- оценка занятого CPU (в % одного ядра, затем в % системы) ---
    # проценты одного ядра → доли ядра
    cores_used = (overlay_copy * CPU_PER_COPY
                  + overlay_enc * per_overlay
                  + mpts_programs * CPU_PER_MPTS_PROGRAM) / 100.0
    cores_total = cpu_count
    estimated_pct = min(100.0, cores_used / cores_total * 100.0)

    # --- сколько ещё влезет ---
    headroom_pct = max(0.0, 100.0 - max(cpu_pct, estimated_pct) - 15.0)  # 15% запаса
    headroom_cores = headroom_pct / 100.0 * cores_total
    extra_by_cpu = int(headroom_cores // (per_overlay / 100.0)) if per_overlay else 0
    # у iGPU/NVENC есть предел одновременных сессий кодирования
    gpu_sessions = 6 if qsv else 10_000
    extra_by_gpu = max(0, gpu_sessions - overlay_enc)

    ram_free_mb = vm.available / (1024 * 1024)
    extra_proxies_by_ram = int(max(0, (ram_free_mb - 1024)) // RAM_PER_PROCESS_MB)

    net_used = max(net["rx_mbps"], net["tx_mbps"])
    net_headroom = max(0.0, link_mbps * 0.85 - net_used)
    extra_programs_by_net = int(net_headroom // (MBPS_PER_PROGRAM * 2))
    extra_overlay = max(0, min(extra_by_cpu, extra_by_gpu, extra_proxies_by_ram))

    # --- подсказки ---
    hints: list[dict] = []
    if cpu_pct >= 85 or estimated_pct >= 85:
        hints.append({"level": "err", "text": (
            "CPU почти занят. Снимите бегущую строку с 2–3 каналов "
            "или переведите энкод на h264_qsv (аппаратное кодирование).")})
    elif cpu_pct >= 70:
        hints.append({"level": "warn", "text": (
            "CPU выше 70 %. Новые каналы с тикером лучше не включать — "
            f"запас примерно {extra_overlay} канал(а).")})

    if overlay_enc > 0 and not qsv:
        hints.append({"level": "info", "text": (
            "Бегущая строка кодируется программно (libx264). На этом CPU это "
            f"~{CPU_PER_OVERLAY:.0f} % ядра на канал. Аппаратный h264_qsv "
            "снизит нагрузку в 3–4 раза.")})

    if vm.percent >= 85:
        hints.append({"level": "err", "text": (
            f"RAM занята на {vm.percent:.0f} %. Остановите лишние превью "
            "в мозаике и перезапустите MPTS-группы.")})

    if mpts_groups and mpts_programs:
        per_group = mpts_programs / mpts_groups
        if per_group > RECOMMENDED_PER_GROUP:
            hints.append({"level": "warn", "text": (
                f"В группе в среднем {per_group:.0f} программ — это "
                f"~{per_group * MBPS_PER_PROGRAM:.0f} Мбит/с, больше ёмкости "
                f"QAM (~{QAM_CAPACITY_MBPS:.0f} Мбит/с). Разбейте на группы "
                f"по {RECOMMENDED_PER_GROUP}.")})

    if net_used > link_mbps * 0.7:
        hints.append({"level": "warn", "text": (
            f"Сеть загружена на {net_used / link_mbps * 100:.0f} % "
            f"({net_used:.0f} из {link_mbps:.0f} Мбит/с). Проверьте, что "
            "канал не идёт через Wi-Fi.")})

    if extra_overlay <= 0 and overlay_enc > 0:
        hints.append({"level": "err", "text": (
            "Запаса CPU на новые тикеры нет. Либо снимите строку с каналов, "
            "либо добавьте железо/GPU.")})

    if not hints:
        hints.append({"level": "ok", "text": (
            f"Запас есть: до {extra_overlay} каналов с тикером "
            f"или ~{extra_programs_by_net} программ без оверлея.")})

    worst = "ok"
    for h in hints:
        if h["level"] == "err":
            worst = "err"
            break
        if h["level"] == "warn":
            worst = "warn"

    return {
        "host": {
            "cpu_count": cpu_count,
            "cpu_percent": round(cpu_pct, 1),
            "ram_percent": round(vm.percent, 1),
            "ram_used_gb": round(vm.used / 1e9, 1),
            "ram_total_gb": round(vm.total / 1e9, 1),
            "qsv": qsv,
        },
        "net": {**net, "link_mbps": link_mbps},
        "running": {
            "overlay_copy": overlay_copy,
            "overlay_encode": overlay_enc,
            "mpts_groups": mpts_groups,
            "mpts_programs": mpts_programs,
        },
        "estimate": {
            "cpu_percent": round(estimated_pct, 1),
            "cpu_per_overlay": per_overlay,
            "extra_overlay_channels": extra_overlay,
            "extra_programs_by_net": extra_programs_by_net,
            "extra_proxies_by_ram": extra_proxies_by_ram,
            "bits_per_program_mbps": MBPS_PER_PROGRAM,
            "recommended_per_group": RECOMMENDED_PER_GROUP,
        },
        "hints": hints,
        "verdict": worst,
        "timestamp": time.time(),
    }
