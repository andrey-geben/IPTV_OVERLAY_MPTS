// ============================================================
// MPTS — управление группами каналов
// ============================================================

let allChannels = [];
let allGroups = [];
let lastCheckResults = new Map();
let monitoringGroups = new Set();
let firstRender = true;

// ---------- Утилиты ----------
function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
}

function formatUptime(sec) {
    if (sec == null) return "—";
    const p = n => String(n).padStart(2, "0");
    return `${p(Math.floor(sec / 3600))}:${p(Math.floor((sec % 3600) / 60))}:${p(sec % 60)}`;
}

function getChannelGroups(url) {
    return allGroups.filter(g => g.channel_urls.includes(url)).map(g => g.name);
}

function getSelectedUrls() {
    const urls = [];
    document.querySelectorAll("#channels-body input[type=checkbox]:checked").forEach(cb => {
        urls.push(cb.dataset.url);
    });
    return urls;
}

// ---------- Загрузка конфигурации ----------
async function loadConfig() {
    if (!window.interfacesLoaded) {
        try {
            const ir = await fetch("/api/network/interfaces");
            const idata = await ir.json();
            if (idata.ok) {
                const select = document.getElementById("group-local-addr");
                if (select) {
                    select.innerHTML = '<option value="">Автоматически (по умолчанию ОС)</option>';
                    idata.interfaces.forEach(iface => {
                        const opt = document.createElement("option");
                        opt.value = iface.ip;
                        opt.textContent = `${iface.name} (${iface.ip})`;
                        select.appendChild(opt);
                    });
                }
                window.interfacesLoaded = true;
            }
        } catch (e) {
            console.error("Не удалось загрузить сетевые интерфейсы:", e);
        }
    }

    try {
        const r = await fetch("/api/mpts/config");
        const data = await r.json();
        if (!data.ok) {
            console.error("Ошибка загрузки конфигурации:", data.error);
            return;
        }

        allChannels = data.channels || [];
        allGroups = data.groups || [];

        renderChannels();
        renderGroups();
        refreshStatuses();
    } catch (e) {
        console.error("Ошибка загрузки конфигурации:", e);
    }
}

// ---------- Таблица каналов ----------
function renderChannels() {
    const tbody = document.getElementById("channels-body");
    if (!tbody) return;
    tbody.innerHTML = "";

    const meta = document.getElementById("playlist-meta");
    const panel = document.getElementById("channels-panel");

    if (allChannels.length === 0) {
        if (meta) meta.textContent = "Рабочий плейлист пуст. Загрузите его на странице PLAYLIST.";
        if (panel) panel.style.display = "none";
        return;
    }

    if (panel) panel.style.display = "block";
    if (meta) meta.textContent = `Доступно каналов (в рабочем плейлисте): ${allChannels.length}`;

    allChannels.forEach((ch, i) => {
        const groups = getChannelGroups(ch.url);
        const badges = groups.length
            ? groups.map(g => `<span class="group-badge">${escapeHtml(g)}</span>`).join("")
            : '<span class="hint">—</span>';

        const chk = lastCheckResults.get(ch.url);
        const checkIcon = chk
            ? (chk.alive
                ? '<span class="led led--on" title="OK"></span>'
                : `<span class="led led--err" title="${escapeHtml(chk.error || '')}"></span>`)
            : '<span class="led" title="Не проверен"></span>';

        const tr = document.createElement("tr");
        tr.dataset.index = i;
        tr.innerHTML = `
            <td><input type="checkbox" data-url="${escapeHtml(ch.url)}" ${firstRender ? "checked" : ""}></td>
            <td class="check-cell">${checkIcon}</td>
            <td>${escapeHtml(ch.name)}</td>
            <td class="url">${escapeHtml(ch.url)}</td>
            <td class="groups-cell">${badges}</td>
        `;
        tbody.appendChild(tr);
    });
    firstRender = false;
}

function updateCheckStatus(url, result) {
    lastCheckResults.set(url, result);
    document.querySelectorAll("#channels-body input[type=checkbox]").forEach(cb => {
        if (cb.dataset.url === url) {
            const tr = cb.closest("tr");
            const cell = tr.querySelector(".check-cell");
            cell.innerHTML = result.alive
                ? '<span class="led led--on" title="OK"></span>'
                : `<span class="led led--err" title="${escapeHtml(result.error || '')}"></span>`;
        }
    });
}

// ---------- Проверка каналов ----------
async function checkChannels(urls) {
    if (!urls.length) return [];
    const r = await fetch("/api/mpts/check-channels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ urls })
    });
    const data = await r.json();
    if (!data.ok) throw new Error(data.error || "Ошибка проверки");
    data.results.forEach(res => updateCheckStatus(res.url, res));
    return data.results;
}

// ---------- Отрисовка групп ----------
function renderGroups() {
    const container = document.getElementById("groups-container");
    if (!container) return;
    container.innerHTML = "";

    if (!allGroups.length) {
        container.innerHTML = '<p class="hint">Нет созданных групп.</p>';
        return;
    }

    allGroups.forEach(g => {
        const card = document.createElement("div");
        card.className = "group-card";

        let ipParts = g.host.split('.');
        while (ipParts.length < 4) ipParts.push("0");

        const estimatedBitrate = (g.channel_urls.length * 5).toFixed(1);

        const progNames = g.channel_urls.map((url, idx) => {
            const ch = allChannels.find(c => c.url === url);
            return `<div>Program ${idx + 1}: ${escapeHtml(ch ? ch.name : "Unknown")}</div>`;
        }).join("");

        card.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:start; gap:12px;">
                <div>
                    <h3 style="margin:0; color:var(--amber); font-family:var(--mono);">${escapeHtml(g.name)}</h3>
                    <p class="hint">Каналов: ${g.channel_urls.length} | UDP: ${escapeHtml(g.host)}:${g.port} ${g.local_addr ? '| IF: ' + escapeHtml(g.local_addr) : ''}</p>
                </div>
                <div class="actions">
                    <button class="btn btn--primary btn--small" onclick="startGroup('${g.id}')">▶ ПУСК</button>
                    <button class="btn btn--danger btn--small" onclick="stopGroup('${g.id}')">■ СТОП</button>
                    <button class="btn btn--small" id="btn-monitor-${g.id}" style="display:none;" onclick="toggleMonitor('${g.id}')">👁 МОНИТОР</button>
                    <button class="btn btn--small" onclick="deleteGroup('${g.id}')">🗑</button>
                </div>
            </div>

            <div class="pbi-data" id="pbi-${g.id}" style="display:none; margin-top:10px; padding:10px; background:var(--bg-inset); border:1px solid var(--bezel-light); border-radius:4px;">
                <p class="field-label" style="color:var(--amber); margin-bottom:8px;">ДАННЫЕ ДЛЯ ПАНЕЛИ PBI</p>
                <div class="pbi-row">
                    <div class="pbi-item">
                        <span class="pbi-label">Uni/Multicast IP Address</span>
                        <div class="pbi-ip-group">
                            <input type="text" value="${ipParts[0]}" class="pbi-input ip-cell" readonly onclick="this.select()">
                            <span class="pbi-dot">.</span>
                            <input type="text" value="${ipParts[1]}" class="pbi-input ip-cell" readonly onclick="this.select()">
                            <span class="pbi-dot">.</span>
                            <input type="text" value="${ipParts[2]}" class="pbi-input ip-cell" readonly onclick="this.select()">
                            <span class="pbi-dot">.</span>
                            <input type="text" value="${ipParts[3]}" class="pbi-input ip-cell" readonly onclick="this.select()">
                        </div>
                    </div>
                    <div class="pbi-item">
                        <span class="pbi-label">Uni/Multicast Port</span>
                        <input type="text" value="${g.port}" class="pbi-input" readonly onclick="this.select()">
                    </div>
                    <div class="pbi-item">
                        <span class="pbi-label">TS Bit Rate (Mbps)</span>
                        <input type="text" id="pbi-bitrate-${g.id}" value="${estimatedBitrate}" class="pbi-input" readonly onclick="this.select()">
                    </div>
                </div>
                <div style="margin-top:12px;">
                    <label class="hint">Программы (Service Name):</label>
                    <div style="font-family:var(--mono); font-size:12px; color:var(--phosphor); margin-top:4px;">${progNames}</div>
                </div>
            </div>

            <div id="monitor-${g.id}" class="monitor-panel" style="display:none; margin-top:10px;"></div>
        `;
        container.appendChild(card);
    });
}

// ---------- Управление группами ----------
async function startGroup(id) {
    const r = await fetch(`/api/mpts/groups/${id}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
    });
    let data = await r.json();

    if (!data.ok && data.require_confirm) {
        if (!confirm(data.warning)) {
            refreshStatuses();
            return;
        }
        const r2 = await fetch(`/api/mpts/groups/${id}/start`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ force: true })
        });
        data = await r2.json();
    }

    if (!data.ok) alert(data.error || "Ошибка запуска");
    refreshStatuses();
}

async function stopGroup(id) {
    await fetch(`/api/mpts/groups/${id}/stop`, { method: "POST" });
    monitoringGroups.delete(id);
    const mp = document.getElementById(`monitor-${id}`);
    if (mp) mp.style.display = "none";
    refreshStatuses();
}

async function deleteGroup(id) {
    if (!confirm("Удалить группу и остановить поток?")) return;
    try {
        await fetch(`/api/mpts/groups/${id}`, { method: "DELETE" });
        monitoringGroups.delete(id);
        await loadConfig();
    } catch (e) {
        alert("Ошибка удаления: " + e.message);
    }
}

// ---------- Статусы ----------
async function refreshStatuses() {
    try {
        const r = await fetch("/api/mpts/status");
        const data = await r.json();
        if (!data.ok) return;
        allGroups.forEach(g => {
            const st = data.statuses[g.id] || {};
            const pbiBlock = document.getElementById(`pbi-${g.id}`);
            const monitorBtn = document.getElementById(`btn-monitor-${g.id}`);
            if (st.running) {
                if (pbiBlock) pbiBlock.style.display = "block";
                if (monitorBtn) monitorBtn.style.display = "inline-block";
            } else {
                if (pbiBlock) pbiBlock.style.display = "none";
                if (monitorBtn) monitorBtn.style.display = "none";
            }
        });
    } catch (e) { /* ignore */ }
}

// ---------- Мониторинг ----------
function toggleMonitor(groupId) {
    const mp = document.getElementById(`monitor-${groupId}`);
    if (monitoringGroups.has(groupId)) {
        monitoringGroups.delete(groupId);
        if (mp) mp.style.display = "none";
    } else {
        monitoringGroups.add(groupId);
        if (mp) mp.style.display = "block";
        refreshMonitor();
    }
}

async function refreshMonitor() {
    for (const gId of monitoringGroups) {
        try {
            const r = await fetch(`/api/mpts/groups/${gId}/monitor`);
            const data = await r.json();
            if (!data.ok) continue;
            const panel = document.getElementById(`monitor-${gId}`);
            if (!panel) continue;

            if (!data.running) {
                panel.innerHTML = '<span class="hint">Поток остановлен.</span>';
                continue;
            }

            panel.innerHTML = `
                <div class="monitor-grid">
                    <div class="monitor-cell"><span class="monitor-label">UPTIME</span><span class="monitor-value">${formatUptime(data.uptime_sec)}</span></div>
                    <div class="monitor-cell"><span class="monitor-label">FRAME</span><span class="monitor-value">${data.frame}</span></div>
                    <div class="monitor-cell"><span class="monitor-label">FPS</span><span class="monitor-value">${data.fps}</span></div>
                    <div class="monitor-cell"><span class="monitor-label">ENC BITRATE</span><span class="monitor-value">${data.bitrate}</span></div>
                    <div class="monitor-cell"><span class="monitor-label">NET RATE</span><span class="monitor-value">${data.net_rate_mbps != null ? data.net_rate_mbps + " Mbps" : "—"}</span></div>
                    <div class="monitor-cell"><span class="monitor-label">NET SENT</span><span class="monitor-value">${data.net_sent_mb != null ? data.net_sent_mb + " MB" : "—"}</span></div>
                    <div class="monitor-cell"><span class="monitor-label">SPEED</span><span class="monitor-value">${data.speed}</span></div>
                    <div class="monitor-cell"><span class="monitor-label">DROPS</span><span class="monitor-value">${data.drop_frames}</span></div>
                </div>
                <div style="margin-top:10px; display:flex; gap:10px; align-items:center;">
                    <button class="btn btn--small" onclick="validateStream('${gId}')">🔍 ПРОВЕРИТЬ ПОТОК</button>
                    <span id="validate-status-${gId}" class="hint"></span>
                </div>
                <div id="validate-result-${gId}" style="margin-top:10px;"></div>
            `;

            if (data.bitrate && data.bitrate !== "—") {
                const match = data.bitrate.match(/([\d.]+)/);
                if (match) {
                    const mbps = (parseFloat(match[1]) / 1000).toFixed(1);
                    const bitrateInput = document.getElementById(`pbi-bitrate-${gId}`);
                    if (bitrateInput) bitrateInput.value = mbps;
                }
            }
        } catch (e) { /* ignore */ }
    }
}

async function validateStream(groupId) {
    const statusEl = document.getElementById(`validate-status-${groupId}`);
    const resultEl = document.getElementById(`validate-result-${groupId}`);
    if (!statusEl || !resultEl) return;

    statusEl.textContent = "Читаю поток (до 10 секунд)...";
    resultEl.innerHTML = "";

    try {
        const r = await fetch(`/api/mpts/groups/${groupId}/validate`);
        const data = await r.json();

        if (!data.ok) {
            statusEl.innerHTML = `<span class="led led--err"></span> ${data.error}`;
            return;
        }

        const res = data.result;
        if (!res.alive) {
            statusEl.innerHTML = `<span class="led led--err"></span> Поток недоступен: ${res.error}`;
            return;
        }

        statusEl.innerHTML = `<span class="led led--on"></span> Поток валиден. Программ: ${res.programs.length}`;

        let programsHtml = '<div class="validate-programs">';
        res.programs.forEach(p => {
            programsHtml += `
                <div class="validate-program">
                    <span class="validate-prog-id">Program ${p.index}</span>
                    <span class="validate-prog-name">${p.name}</span>
                    <span class="validate-prog-streams">${p.streams.join(", ")}</span>
                </div>
            `;
        });
        programsHtml += "</div>";
        resultEl.innerHTML = programsHtml;
    } catch (e) {
        statusEl.innerHTML = `<span class="led led--err"></span> Ошибка: ${e.message}`;
    }
}

// ============================================================
// ЯВНО на window для inline onclick
// ============================================================
window.startGroup = startGroup;
window.stopGroup = stopGroup;
window.deleteGroup = deleteGroup;
window.toggleMonitor = toggleMonitor;
window.validateStream = validateStream;

// ============================================================
// Обработчики кнопок
// ============================================================
document.getElementById("btn-select-all")?.addEventListener("click", () => {
    document.querySelectorAll("#channels-body input[type=checkbox]").forEach(cb => cb.checked = true);
});
document.getElementById("btn-deselect-all")?.addEventListener("click", () => {
    document.querySelectorAll("#channels-body input[type=checkbox]").forEach(cb => cb.checked = false);
});

document.getElementById("btn-check-selected")?.addEventListener("click", async () => {
    const urls = getSelectedUrls();
    if (!urls.length) return alert("Сначала выберите каналы");
    const statusEl = document.getElementById("check-status");
    if (statusEl) statusEl.textContent = `Проверяю ${urls.length} каналов...`;
    try {
        const results = await checkChannels(urls);
        const alive = results.filter(r => r.alive).length;
        const dead = results.length - alive;
        if (statusEl) statusEl.textContent = `Готово. Живых: ${alive}, недоступных: ${dead}`;
    } catch (e) {
        if (statusEl) statusEl.textContent = "Ошибка проверки: " + e.message;
    }
});

document.getElementById("btn-save-group")?.addEventListener("click", async () => {
    const urls = getSelectedUrls();
    if (!urls.length) return alert("Выберите каналы для группы");

    const btn = document.getElementById("btn-save-group");
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Проверка каналов...";

    try {
        const results = await checkChannels(urls);
        const dead = results.filter(r => !r.alive);

        if (dead.length > 0) {
            const names = dead.map(d => {
                const ch = allChannels.find(c => c.url === d.url);
                return `  • ${ch ? ch.name : d.url} — ${d.error || "нет сигнала"}`;
            }).join("\n");
            const proceed = confirm(
                `⚠ Внимание!\n\n${dead.length} из ${results.length} каналов недоступны:\n\n${names}\n\nПродолжить сохранение группы?`
            );
            if (!proceed) {
                btn.disabled = false;
                btn.textContent = originalText;
                return;
            }
        }

        const name = document.getElementById("group-name").value;
        const host = document.getElementById("group-host").value;
        const port = parseInt(document.getElementById("group-port").value);
        const localAddr = document.getElementById("group-local-addr") ? document.getElementById("group-local-addr").value : "";

        const payload = { name, host, port, local_addr: localAddr, channel_urls: urls, force: false };

        let r = await fetch("/api/mpts/groups", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        let data = await r.json();

        if (!data.ok && data.require_confirm) {
            if (confirm(data.warning)) {
                payload.force = true;
                r = await fetch("/api/mpts/groups", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                data = await r.json();
            } else {
                btn.disabled = false;
                btn.textContent = originalText;
                return;
            }
        }

        if (data.ok) {
            loadConfig();
        } else {
            alert(data.error);
        }
    } catch (e) {
        alert("Ошибка: " + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
});

// ---------- STOP ALL ----------
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

// ---------- RESTART ALL ----------
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

// ---------- Автообновление ----------
setInterval(() => { if (!document.hidden) { refreshStatuses(); refreshMonitor(); } }, 2500);

// ---------- Инициализация ----------
loadConfig();