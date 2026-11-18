// channels.js — управление каналами «папка = канал»
(function () {
    const body = document.getElementById("channels-body");

    function api(path, method) {
        return fetch(path, {
            method: method || "GET",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: method && method !== "GET" ? "{}" : undefined,
        }).then(function (r) { return r.json(); });
    }

    function showError(text) {
        const box = document.getElementById("channels-error");
        const el = document.getElementById("channels-error-text");
        if (!box || !el) return;
        el.textContent = text || "";
        box.style.display = text ? "" : "none";
    }

    function fmtUptime(sec) {
        sec = Number(sec) || 0;
        const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
        return h ? (h + " ч " + m + " мин") : (m + " мин");
    }

    function render(items) {
        if (!body) return;
        body.innerHTML = "";
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="5" class="hint">Каналов пока нет — добавьте папку с видео.</td></tr>';
            return;
        }
        items.forEach(function (ch) {
            const tr = document.createElement("tr");

            const state = ch.running
                ? '<span class="led led--on led--pulse"></span>В эфире ' + fmtUptime(ch.uptime_sec)
                : (ch.ingest_state === "running"
                    ? '<span class="led led--warn led--pulse"></span>Ingest…'
                    : (ch.ingest_state === "error"
                        ? '<span class="led led--err"></span>Ошибка: ' + (ch.ingest_error || "").slice(0, 80)
                        : '<span class="led"></span>Остановлен'));

            tr.innerHTML =
                "<td><b>" + ch.name + "</b><br><span class='hint'>" + ch.id + "</span></td>" +
                "<td class='url'>" + ch.source_dir + "</td>" +
                "<td>" + (ch.ingested || 0) + "</td>" +
                "<td>" + state + "</td>" +
                "<td class='actions'></td>";

            const cell = tr.querySelector("td.actions");
            function btn(label, cls, fn) {
                const b = document.createElement("button");
                b.className = "btn btn--small " + (cls || "");
                b.textContent = label;
                b.style.marginRight = "4px";
                b.onclick = fn;
                cell.appendChild(b);
                return b;
            }

            btn("1. Ingest", "", function () {
                if (!confirm("Обработать все файлы папки? Это займёт время.")) return;
                api("/api/channels/" + ch.id + "/ingest", "POST").then(refresh).catch(function (e) { showError(e.message); });
            });

            if (ch.running) {
                btn("⏹ Стоп", "btn--danger", function () {
                    api("/api/channels/" + ch.id + "/stop", "POST").then(refresh);
                });
            } else {
                btn("▶ В эфир", "btn--primary", function () {
                    api("/api/channels/" + ch.id + "/start", "POST").then(function (res) {
                        if (!res.ok) showError(res.error || "Не удалось запустить");
                        refresh();
                    });
                });
            }

            btn("⤨ Порядок", "", function () {
                api("/api/channels/" + ch.id + "/shuffle", "POST").then(function (res) {
                    if (!res.ok) showError(res.error || "Не удалось перемешать");
                });
            });

            btn("🔗 URL", "", function () {
                api("/api/channels/" + ch.id + "/url").then(function (res) {
                    if (res.ok) {
                        prompt("URL для добавления в MPTS:", res.url);
                    }
                });
            });

            btn("✕", "btn--danger", function () {
                if (!confirm("Удалить канал (файлы ingest останутся на диске)?")) return;
                api("/api/channels/" + ch.id, "DELETE").then(refresh);
            });

            body.appendChild(tr);
        });
    }

    function refresh() {
        return api("/api/channels").then(function (res) {
            if (res && res.channels) render(res.channels);
        }).catch(function (e) { showError(e.message); });
    }

    document.getElementById("btn-channels-refresh").onclick = refresh;
    document.getElementById("btn-channel-add").onclick = function () {
        const src = document.getElementById("channel-src").value.trim();
        const name = document.getElementById("channel-name").value.trim();
        if (!src) { showError("Укажите папку с видео"); return; }
        fetch("/api/channels", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source_dir: src, name: name }),
        }).then(function (r) { return r.json(); }).then(function (res) {
            if (!res.ok) { showError(res.error || "Ошибка"); return; }
            showError("");
            document.getElementById("channel-src").value = "";
            document.getElementById("channel-name").value = "";
            refresh();
        });
    };

    refresh();
    setInterval(refresh, 5000);
})();
