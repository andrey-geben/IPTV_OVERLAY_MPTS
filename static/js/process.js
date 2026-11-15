const POLL_MS = 2000;

function formatUptime(sec) {
    if (sec == null) return "—";
    const p = n => String(n).padStart(2, "0");
    return `${p(Math.floor(sec / 3600))}:${p(Math.floor((sec % 3600) / 60))}:${p(sec % 60)}`;
}
function formatNumber(v, digits = 1) {
    return v == null ? "—" : Number(v).toFixed(digits);
}
function showError(msg) {
    document.getElementById("proc-error-text").textContent = msg;
    document.getElementById("proc-error").style.display = "flex";
}
function clearError() {
    document.getElementById("proc-error").style.display = "none";
}

function updateUi(st) {
    const running = st && st.running === true;

    const led = document.getElementById("proc-led");
    led.className = "led led--big " + (running ? "led--on led--pulse" : "");

    const text = document.getElementById("proc-status-text");
    if (running) {
        text.innerHTML = 'STATUS: <strong style="color:var(--phosphor)">RUNNING</strong>';
    } else if (st && st.exists) {
        text.innerHTML = `STATUS: <strong style="color:var(--red)">STOPPED</strong> (exit=${st.exit_code})`;
    } else {
        text.innerHTML = 'STATUS: <strong style="color:var(--text-dim)">NOT STARTED</strong>';
    }

    document.getElementById("ro-pid").textContent    = running ? (st.pid ?? "—") : "—";
    document.getElementById("ro-uptime").textContent = running ? formatUptime(st.uptime_sec) : "—";
    document.getElementById("ro-cpu").textContent    = running ? formatNumber(st.cpu_percent, 1) : "—";
    document.getElementById("ro-mem").textContent    = running ? formatNumber(st.memory_mb, 1) : "—";
    document.getElementById("proc-command").textContent = (st && st.command) || "—";

    document.getElementById("btn-start").disabled = running;
    document.getElementById("btn-stop").disabled  = !running;
}

async function refreshStatus() {
    try {
        const r = await fetch("/api/process/status");
        const data = await r.json();
        const st = data && data.processes ? data.processes.test_ffmpeg : null;
        updateUi(st);
    } catch (e) {
        showError("Не удалось получить статус: " + e.message);
    }
}

async function doStart() {
    clearError();
    try {
        const r = await fetch("/api/process/start", { method: "POST" });
        const data = await r.json();
        if (!data.ok) showError(data.error || "Ошибка запуска");
    } catch (e) {
        showError("Сетевая ошибка: " + e.message);
    }
    // Дадим серверу полсекунды на старт
    setTimeout(refreshStatus, 500);
}

async function doStop() {
    clearError();
    try {
        const r = await fetch("/api/process/stop", { method: "POST" });
        const data = await r.json();
        if (!data.ok) showError(data.error || "Ошибка остановки");
    } catch (e) {
        showError("Сетевая ошибка: " + e.message);
    }
    setTimeout(refreshStatus, 500);
}

async function refreshLog() {
    const view = document.getElementById("log-view");
    const meta = document.getElementById("log-meta");
    try {
        const r = await fetch("/api/process/log?lines=60");
        const data = await r.json();
        if (!data.ok) {
            view.textContent = "Ошибка: " + (data.error || "неизвестно");
            return;
        }
        if (data.lines.length === 0) {
            view.textContent = data.note || "Лог пуст.";
        } else {
            view.textContent = data.lines.join("\n");
        }
        meta.textContent = `всего строк: ${data.total}`;
    } catch (e) {
        view.textContent = "Сетевая ошибка: " + e.message;
    }
}

document.getElementById("btn-start").addEventListener("click", doStart);
document.getElementById("btn-stop").addEventListener("click", doStop);
document.getElementById("btn-log-refresh").addEventListener("click", refreshLog);

document.getElementById("btn-stop-all")?.addEventListener("click", async () => {
    if (!confirm("⚠ ВНИМАНИЕ!\n\nБудут остановлены ВСЕ FFmpeg-процессы.\n\nСетка вещания PBI ПРЕРВЁТСЯ.\n\nПродолжить?")) return;

    const btn = document.getElementById("btn-stop-all");
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "⏳ ОСТАНОВКА...";

    try {
        const r = await fetch("/api/process/stop-all", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ aggressive: true })
        });
        const data = await r.json();
        if (data.ok) {
            alert(data.message);
            setTimeout(() => location.reload(), 1200);
        } else {
            alert("Ошибка: " + (data.error || "неизвестно"));
        }
    } catch (e) {
        alert("Сетевая ошибка: " + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
});

document.getElementById("btn-restart-all")?.addEventListener("click", async () => {
    if (!confirm("🔄 ПЕРЕЗАПУСК ВСЕХ ПРОЦЕССОВ\n\nЭто займёт 10–20 секунд.\n\nПродолжить?")) return;

    const btn = document.getElementById("btn-restart-all");
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "⏳ ПЕРЕЗАПУСК...";

    try {
        const r = await fetch("/api/process/restart-all", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({})
        });
        const data = await r.json();
        if (data.ok) {
            alert(data.message);
            setTimeout(() => location.reload(), 1200);
        } else {
            alert("Ошибка: " + (data.error || "неизвестно"));
        }
    } catch (e) {
        alert("Сетевая ошибка: " + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
});

// WebSocket: обновление статуса
if (window.socket) {
    window.socket.on("update", (data) => {
        if (data.processes && data.processes.test_ffmpeg) {
            updateUi(data.processes.test_ffmpeg);
        }
    });
    refreshStatus(); // Первичная отрисовка
} else {
    // Fallback: polling
    refreshStatus();
    setInterval(() => { if (!document.hidden) refreshStatus(); }, POLL_MS);
}