let allChannels = [];
let workingUrls = new Set();

async function initPage() {
    // Загружаем сохранённый исходный плейлист
    const r = await fetch("/api/playlist/source");
    const data = await r.json();
    if (data.ok && data.content) {
        document.getElementById("playlist-content").value = data.content;
        await parseContent(data.content);
    }
    // Загружаем рабочий плейлист
    const r2 = await fetch("/api/playlist/working");
    const data2 = await r2.json();
    if (data2.ok) {
        workingUrls = new Set(data2.urls || []);
        renderChannels();
    }
}

async function parseContent(content) {
    if (!content.trim()) return alert("Плейлист пуст");
    const r = await fetch("/api/playlists/parse", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content })
    });
    const data = await r.json();
    if (!data.ok) return alert(data.error);
    allChannels = data.channels;
    await fetch("/api/playlist/source", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content })
    });
    renderChannels();
}

function renderChannels() {
    const tbody = document.getElementById("channels-body");
    if (!tbody) return;
    tbody.innerHTML = "";
    document.getElementById("channels-panel").style.display = "block";
    document.getElementById("parse-meta").textContent = `Найдено каналов: ${allChannels.length}`;

    allChannels.forEach((ch) => {
        const checked = workingUrls.has(ch.url) ? "checked" : "";
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td><input type="checkbox" data-url="${ch.url}" ${checked} class="working-cb"></td>
            <td>${escapeHtml(ch.name)}</td>
            <td>${escapeHtml(ch.group || "")}</td>
            <td class="url">${escapeHtml(ch.url)}</td>
        `;
        tbody.appendChild(tr);
    });

    document.querySelectorAll(".working-cb").forEach(cb => {
        cb.addEventListener("change", updateCount);
    });
    updateCount();
}

function updateCount() {
    const n = document.querySelectorAll(".working-cb:checked").length;
    document.getElementById("working-count").textContent = n;
}

function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

// Обработчики
document.getElementById("btn-parse-text").addEventListener("click", () => {
    parseContent(document.getElementById("playlist-content").value);
});

document.getElementById("btn-parse-file").addEventListener("click", async () => {
    const input = document.getElementById("playlist-file");
    if (!input.files.length) return alert("Файл не выбран");
    const form = new FormData();
    form.append("file", input.files[0]);
    const r = await fetch("/api/playlists/parse-file", { method: "POST", body: form });
    const data = await r.json();
    if (!data.ok) return alert(data.error);
    document.getElementById("playlist-content").value = data.raw_content;
    parseContent(data.raw_content);
});

document.getElementById("btn-parse-url").addEventListener("click", async () => {
    const url = document.getElementById("playlist-url").value.trim();
    if (!url) return alert("URL не указан");
    const r = await fetch("/api/playlists/parse-url", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url })
    });
    const data = await r.json();
    if (!data.ok) return alert(data.error);
    document.getElementById("playlist-content").value = data.raw_content;
    parseContent(data.raw_content);
});

document.getElementById("btn-select-all").addEventListener("click", () => {
    document.querySelectorAll(".working-cb").forEach(cb => cb.checked = true);
    updateCount();
});

document.getElementById("btn-deselect-all").addEventListener("click", () => {
    document.querySelectorAll(".working-cb").forEach(cb => cb.checked = false);
    updateCount();
});

document.getElementById("btn-save-working").addEventListener("click", async () => {
    const urls = [];
    document.querySelectorAll(".working-cb:checked").forEach(cb => urls.push(cb.dataset.url));
    if (!urls.length) return alert("Выберите хотя бы один канал");

    const status = document.getElementById("save-status");
    status.textContent = "Сохранение...";

    const r = await fetch("/api/playlist/working", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ urls })
    });
    const data = await r.json();
    if (data.ok) {
        status.textContent = `Сохранено. Прокси запущены.`;
        document.getElementById("btn-next-mpts").style.display = "inline-block";
    } else {
        status.textContent = "Ошибка: " + data.error;
    }
});

initPage();