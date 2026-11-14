let allSources = [];
let selectedIds = new Set();
let currentPage = 0;
let gridSize = 2;
let viewMode = "mosaic";
let singleViewId = null;
const players = new Map();

function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
}

// ============================================================
// Сравнение источников (по id + url)
// ============================================================
function sourcesDiffer(a, b) {
    if (a.length !== b.length) return true;
    for (let i = 0; i < a.length; i++) {
        if (a[i].id !== b[i].id) return true;
        if ((a[i].url || "") !== (b[i].url || "")) return true;
        if (a[i].preview_running !== b[i].preview_running) return true;
    }
    return false;
}

// ============================================================
// Боковая панель
// ============================================================
function renderSidebar() {
    const list = document.getElementById("sources-list");
    if (!list) return;
    list.innerHTML = "";

    if (!allSources.length) {
        list.innerHTML = '<p class="hint">Нет доступных источников.</p>';
        return;
    }

    allSources.forEach(src => {
        const row = document.createElement("div");
        row.className = "source-row";
        let statusLed, statusText, extraBtn = "";

        if (src.type === "overlay_channel") {
            statusLed = '<span class="led led--on"></span>'; statusText = "OVERLAY";
        } else if (src.type === "raw_channel") {
            if (src.preview_running) {
                statusLed = '<span class="led led--on led--pulse"></span>'; statusText = "PREVIEW";
            } else {
                statusLed = '<span class="led"></span>'; statusText = "—";
                extraBtn = `<button class="btn btn--small" onclick="startChannelPreview('${src.ch_id}', event)">▶</button>`;
            }
        } else if (src.type === "mpts_group") {
            if (src.preview_running) {
                statusLed = '<span class="led led--on led--pulse"></span>'; statusText = "PREVIEW";
            } else if (src.mpts_running) {
                statusLed = '<span class="led led--warn"></span>'; statusText = "MPTS";
                extraBtn = `<button class="btn btn--small" onclick="startPreview('${src.id.replace("mpts_","")}', event)">▶</button>`;
            } else {
                statusLed = '<span class="led"></span>'; statusText = "OFF";
            }
        }

        const viewBtn = src.url
            ? `<button class="btn btn--small" onclick="openSingle('${src.id}')">▢</button>` : "";
        const stopBtn = ((src.type === "mpts_group" && src.preview_running)
            ? `<button class="btn btn--small" onclick="stopPreview('${src.id.replace("mpts_","")}', event)">■</button>`
            : (src.type === "raw_channel" && src.preview_running)
            ? `<button class="btn btn--small" onclick="stopChannelPreview('${src.ch_id}', event)">■</button>`
            : "");

        row.innerHTML = `
            <label class="source-label">
                <input type="checkbox" data-id="${src.id}" ${selectedIds.has(src.id) ? "checked" : ""}>
                ${statusLed}<span class="source-name">${escapeHtml(src.name)}</span>
            </label>
            <span class="source-status">${statusText}</span>
            ${extraBtn}${viewBtn}${stopBtn}`;
        list.appendChild(row);
    });

    list.querySelectorAll("input[type=checkbox]").forEach(cb => {
        cb.addEventListener("change", () => {
            if (cb.checked) selectedIds.add(cb.dataset.id);
            else selectedIds.delete(cb.dataset.id);
            renderView();
        });
    });
}

// ============================================================
// Загрузка (однократная, без наложения)
// ============================================================
let isLoadingSources = false;
async function loadSources() {
    if (isLoadingSources) return;
    isLoadingSources = true;
    try {
        const r = await fetch("/api/mosaic/sources");
        const data = await r.json();
        if (!data.ok) return;

        const oldSources = allSources;
        const hadSelection = allSources.length > 0;
        const previousSelected = new Set(selectedIds);

        allSources = data.sources;

        if (!hadSelection) allSources.forEach(s => selectedIds.add(s.id));
        else {
            const validIds = new Set(allSources.map(s => s.id));
            selectedIds = new Set([...previousSelected].filter(id => validIds.has(id)));
        }

        const changed = sourcesDiffer(oldSources, allSources);

        renderSidebar();
        if (changed) renderView();
    } catch (e) {
        console.error("Failed to load sources:", e);
    } finally {
        isLoadingSources = false;
        setTimeout(loadSources, 10000); // ✅ планируем следующий вызов только после завершения
    }
}

// ============================================================
// Рендер области просмотра
// ============================================================
function renderView() {
    if (viewMode === "single" && singleViewId) renderSingle();
    else renderMosaic();
}

function renderMosaic() {
    const single = document.getElementById("single-view");
    const grid = document.getElementById("mosaic-grid");
    if (single) single.style.display = "none";
    if (!grid) return;
    grid.style.display = "grid";

    const selected = allSources.filter(s => selectedIds.has(s.id) && s.url);
    if (!selected.length) {
        destroyAllPlayers();
        grid.innerHTML = '<p class="hint" style="grid-column:1/-1; text-align:center; padding:40px;">Выберите источники.</p>';
        const pager = document.getElementById("mosaic-pager");
        if (pager) pager.style.display = "none";
        return;
    }

    const perPage = gridSize * gridSize;
    const totalPages = Math.max(1, Math.ceil(selected.length / perPage));
    if (currentPage >= totalPages) currentPage = totalPages - 1;
    const start = currentPage * perPage;
    const pageItems = selected.slice(start, start + perPage);
    const neededKeys = new Set(pageItems.map(s => s.id));

    // 1. Удаляем плееры, которых больше нет на странице
    for (const [key, player] of players.entries()) {
        if (!neededKeys.has(key)) {
            if (player.hls) player.hls.destroy();
            if (player.cell && player.cell.parentElement) player.cell.remove();
            players.delete(key);
        }
    }

    // 2. Очищаем grid (без удаления video из DOM — они переедут обратно)
    while (grid.firstChild) grid.removeChild(grid.firstChild);

    // 3. Добавляем нужные ячейки
    pageItems.forEach(src => {
        let player = players.get(src.id);

        if (player && player.url === src.url) {
            // Плеер уже работает — переиспользуем ячейку
            grid.appendChild(player.cell);
        } else {
            // Создаём новую
            if (player) {
                if (player.hls) player.hls.destroy();
                if (player.cell && player.cell.parentElement) player.cell.remove();
                players.delete(src.id);
            }
            const cell = document.createElement("div");
            cell.className = "mosaic-cell";

            const video = document.createElement("video");
            video.muted = true;
            video.playsInline = true;
            video.autoplay = true;

            const label = document.createElement("div");
            label.className = "mosaic-label";
            label.textContent = src.name;

            cell.appendChild(video);
            cell.appendChild(label);
            cell.addEventListener("dblclick", () => openSingle(src.id));
            grid.appendChild(cell);

            attachPlayer(video, src.url, src.id);
            player = players.get(src.id);
            if (player) player.cell = cell;
        }
    });

    grid.style.gridTemplateColumns = `repeat(${gridSize}, 1fr)`;

    const pager = document.getElementById("mosaic-pager");
    if (totalPages > 1 && pager) {
        pager.style.display = "flex";
        document.getElementById("mosaic-page-info").textContent = `${currentPage + 1} / ${totalPages}`;
    } else if (pager) pager.style.display = "none";
}

function renderSingle() {
    const grid = document.getElementById("mosaic-grid");
    const single = document.getElementById("single-view");
    if (grid) grid.style.display = "none";
    if (!single) return;
    single.style.display = "block";

    const src = allSources.find(s => s.id === singleViewId);
    if (!src || !src.url) {
        document.getElementById("single-view-name").textContent = "Источник недоступен";
        const wrap = document.getElementById("single-view-video-wrap");
        if (wrap) wrap.innerHTML = "";
        return;
    }

    document.getElementById("single-view-name").textContent = src.name;
    const wrap = document.getElementById("single-view-video-wrap");
    destroyAllPlayers();
    if (wrap) wrap.innerHTML = "";

    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.autoplay = true;
    video.controls = true;
    if (wrap) wrap.appendChild(video);
    attachPlayer(video, src.url, src.id);
}

// ============================================================
// hls.js — live
// ============================================================
function attachPlayer(videoEl, hlsUrl, key) {
    if (typeof Hls !== "undefined" && Hls.isSupported()) {
        const hls = new Hls({
            liveSyncDurationCount: 3,
            liveMaxLatencyDurationCount: 10,
            startPosition: -1,
            maxBufferLength: 10,
            maxMaxBufferLength: 20,
            manifestLoadingMaxRetry: 3,
            manifestLoadingRetryDelay: 1000,
            levelLoadingMaxRetry: 3,
            fragLoadingMaxRetry: 3,
            fragLoadingRetryDelay: 1000,
        });
        hls.loadSource(hlsUrl + "?t=" + Date.now());
        hls.attachMedia(videoEl);
        hls.on(Hls.Events.ERROR, (evt, data) => {
            if (data.fatal) {
                if (data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
                else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
            }
        });
        players.set(key, { hls, videoEl, url: hlsUrl, cell: null });
    } else if (videoEl.canPlayType("application/vnd.apple.mpegurl")) {
        videoEl.src = hlsUrl;
        players.set(key, { hls: null, videoEl, url: hlsUrl, cell: null });
    }
}

function destroyAllPlayers() {
    players.forEach(p => {
        if (p.hls) p.hls.destroy();
        if (p.cell && p.cell.parentElement) p.cell.remove();
    });
    players.clear();
}

// ============================================================
// Превью
// ============================================================
async function startPreview(groupId, evt) {
    if (evt) evt.preventDefault();
    try {
        const r = await fetch(`/api/mosaic/preview/${groupId}/start`, { method: "POST" });
        const data = await r.json();
        if (!data.ok) { alert("Ошибка: " + data.error); return; }
        setTimeout(loadSources, 3000);
    } catch (e) { alert(e.message); }
}
async function stopPreview(groupId, evt) {
    if (evt) evt.preventDefault();
    await fetch(`/api/mosaic/preview/${groupId}/stop`, { method: "POST" });
    setTimeout(loadSources, 500);
}
async function startChannelPreview(chId, evt) {
    if (evt) evt.preventDefault();
    try {
        const r = await fetch(`/api/mosaic/preview-channel/${chId}/start`, { method: "POST" });
        const data = await r.json();
        if (!data.ok) { alert("Ошибка: " + data.error); return; }
        setTimeout(loadSources, 3000);
    } catch (e) { alert(e.message); }
}
async function stopChannelPreview(chId, evt) {
    if (evt) evt.preventDefault();
    await fetch(`/api/mosaic/preview-channel/${chId}/stop`, { method: "POST" });
    setTimeout(loadSources, 500);
}

// ============================================================
// Одиночный режим
// ============================================================
function openSingle(sourceId) {
    singleViewId = sourceId;
    viewMode = "single";
    document.querySelectorAll(".mosaic-mode-btn").forEach(b => {
        b.classList.toggle("is-active", b.dataset.mode === "single");
    });
    renderView();
}

const btnClose = document.getElementById("single-view-close");
if (btnClose) {
    btnClose.addEventListener("click", () => {
        singleViewId = null;
        viewMode = "mosaic";
        document.querySelectorAll(".mosaic-mode-btn").forEach(b => {
            b.classList.toggle("is-active", b.dataset.mode === "mosaic");
        });
        renderView();
    });
}

document.querySelectorAll(".mosaic-mode-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        viewMode = btn.dataset.mode;
        document.querySelectorAll(".mosaic-mode-btn").forEach(b => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        if (viewMode === "single" && !singleViewId) {
            const first = allSources.find(s => s.url);
            if (first) singleViewId = first.id;
        }
        renderView();
    });
});

document.querySelectorAll(".mosaic-grid-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        gridSize = parseInt(btn.dataset.grid);
        currentPage = 0;
        document.querySelectorAll(".mosaic-grid-btn").forEach(b => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        renderView();
    });
});

const btnSelectAll = document.getElementById("mosaic-select-all");
if (btnSelectAll) btnSelectAll.addEventListener("click", () => {
    allSources.forEach(s => selectedIds.add(s.id)); renderSidebar(); renderView();
});

const btnDeselectAll = document.getElementById("mosaic-deselect-all");
if (btnDeselectAll) btnDeselectAll.addEventListener("click", () => {
    selectedIds.clear(); renderSidebar(); renderView();
});

const btnRefresh = document.getElementById("mosaic-refresh");
if (btnRefresh) btnRefresh.addEventListener("click", loadSources);

const btnPrev = document.getElementById("mosaic-prev");
if (btnPrev) btnPrev.addEventListener("click", () => {
    if (currentPage > 0) { currentPage--; renderView(); }
});

const btnNext = document.getElementById("mosaic-next");
if (btnNext) btnNext.addEventListener("click", () => {
    const selected = allSources.filter(s => selectedIds.has(s.id) && s.url);
    const perPage = gridSize * gridSize;
    const totalPages = Math.max(1, Math.ceil(selected.length / perPage));
    if (currentPage < totalPages - 1) { currentPage++; renderView(); }
});

window.openSingle = openSingle;
window.startPreview = startPreview;
window.stopPreview = stopPreview;
window.startChannelPreview = startChannelPreview;
window.stopChannelPreview = stopChannelPreview;

// ============================================================
// Старт
// ============================================================
loadSources();