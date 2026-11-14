/*
 * Логика страницы /process.
 *
 * Опрашивает /api/process/status каждые 2 секунды и обновляет:
 *   - большой LED и текст статуса
 *   - табло PID / UPTIME / CPU / MEM
 *   - команду запуска
 *   - активность кнопок ПУСК/СТОП
 *
 * Кнопки вызывают POST /api/process/start и /api/process/stop.
 * После действия — немедленный рефреш статуса (не ждём 2 сек).
 */

const POLL_MS = 2000;

// ---------- Хелперы форматирования ----------

function formatUptime(sec) {
    if (sec == null) return "—";
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    const p = n => String(n).padStart(2, "0");
    return `${p(h)}:${p(m)}:${p(s)}`;
}

function formatNumber(v, digits = 1) {
    if (v == null) return "—";
    return Number(v).toFixed(digits);
}

// ---------- Обновление UI по статусу ----------

function updateUi(st) {
    const running = st && st.running === true;

    // LED и текст
    const led = document.getElementById("proc-led");
    led.className = "led led--big " + (running ? "led--on led--pulse" : "");

    const text = document.getElementById("proc-status-text");
    if (running) {
        text.innerHTML = 'STATUS: <strong style="color:var(--phosphor)">RUNNING</strong>';
    } else if (st && st.exists) {
        // Был запущен, но процесс уже завершился сам
        text.innerHTML = `STATUS: <strong style="color:var(--red)">STOPPED</strong> (exit=${st.exit_code})`;
    } else {
        text.innerHTML = 'STATUS: <strong style="color:var(--text-dim)">NOT STARTED</strong>';
    }

    // Табло
    document.getElementById("ro-pid").textContent    = running ? (st.pid ?? "—") : "—";
    document.getElementById("ro-uptime").textContent = running ? formatUptime(st.uptime_sec) : "—";
    document.getElementById("ro-cpu").textContent    = running ? formatNumber(st.cpu_percent, 1) : "—";
    document.getElementById("ro-mem").textContent    = running ? formatNumber(st.memory_mb, 1) : "—";

    // Команда
    document.getElementById("proc-command").textContent = (st && st.command) || "—";

    // Кнопки
    document.getElementById("btn-start").disabled = running;
    document.getElementById("btn-stop").disabled  = !running;
}

// ---------- Опрос статуса ----------

async function refreshStatus() {
    try {
        const r = await fetch("/api/process/status");
        const data = await r.json();
        // Статус test_ffmpeg теперь лежит в data.processes.test_ffmpeg
        const st = data && data.processes ? data.processes.test_ffmpeg : null;
        updateUi(st);
    } catch (e) {
        showError("Не удалось получить статус: " + e.message);
    }
}

function startPolling() {
    refreshStatus();
    setInterval(() => {
        if (!document.hidden) refreshStatus();
    }, POLL_MS);
}

// ---------- Действия ----------

async function doStart() {
    clearError();
    try {
        const r = await fetch("/api/process/start", { method: "POST" });
        const data = await r.json();
        if (!data.ok) showError(data.error || "Ошибка запуска");
    } catch (e) {
        showError("Сетевая ошибка: " + e.message);
    }
    refreshStatus();
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
    refreshStatus();
}

// ---------- Лог ----------

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

// ---------- Ошибки ----------

function showError(msg) {
    document.getElementById("proc-error-text").textContent = msg;
    document.getElementById("proc-error").style.display = "flex";
}
function clearError() {
    document.getElementById("proc-error").style.display = "none";
}

// ---------- Инициализация ----------

document.getElementById("btn-start").addEventListener("click", doStart);
document.getElementById("btn-stop").addEventListener("click", doStop);
document.getElementById("btn-log-refresh").addEventListener("click", refreshLog);

startPolling();