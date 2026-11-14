let availableChannels = [];

async function initPage() {
    // Загружаем доступные каналы (из рабочего плейлиста)
    const r = await fetch("/api/overlay/available-channels");
    const data = await r.json();
    if (data.ok) {
        availableChannels = data.channels || [];
        renderChannelsList();
    }
    loadRules();
}

function renderChannelsList() {
    const container = document.getElementById("rule-channels-list");
    if (!container) return;
    if (!availableChannels.length) {
        container.innerHTML = '<p class="hint">Рабочий плейлист пуст. Загрузите его на странице PLAYLIST.</p>';
        return;
    }
    container.innerHTML = "";
    availableChannels.forEach(ch => {
        const label = document.createElement("label");
        label.className = "rule-channel-item";
        label.innerHTML = `<input type="checkbox" class="rule-channel-cb" data-url="${ch.url}"> ${escapeHtml(ch.name)}`;
        container.appendChild(label);
    });
}

function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

document.getElementById("btn-save-rule").addEventListener("click", async () => {
    const urls = [];
    document.querySelectorAll(".rule-channel-cb:checked").forEach(cb => urls.push(cb.dataset.url));
    if (!urls.length) return alert("Выберите хотя бы один канал");

    const days = [];
    document.querySelectorAll(".rule-day:checked").forEach(cb => days.push(parseInt(cb.value)));

    const repeats = parseInt(document.getElementById("rule-repeats-hour").value) || 60;
    const period = Math.max(1, Math.round(3600 / repeats));

    const fontColorName = document.getElementById("rule-fontcolor-name").value.trim();
    const fontColorHex = document.getElementById("rule-fontcolor").value;
    const boxColorName = document.getElementById("rule-boxcolor-name").value.trim();
    const boxColorHex = document.getElementById("rule-boxcolor").value;

    const payload = {
        id: document.getElementById("rule-id").value || null,
        name: document.getElementById("rule-name").value,
        text: document.getElementById("rule-text").value,
        font_path: document.getElementById("rule-font").value,
        font_size: parseInt(document.getElementById("rule-fontsize").value),
        font_color: fontColorName || fontColorHex,
        font_alpha: 1.0,
        speed: parseInt(document.getElementById("rule-speed").value),
        repeat_period_sec: period,
        y_offset: parseInt(document.getElementById("rule-yoffset").value),
        box_enabled: document.getElementById("rule-box").checked,
        box_color: boxColorName || boxColorHex,
        box_alpha: parseFloat(document.getElementById("rule-boxalpha").value),
        days: days,
        start_hhmm: document.getElementById("rule-start").value,
        end_hhmm: document.getElementById("rule-end").value,
        start_date: document.getElementById("rule-start-date").value,
        end_date: document.getElementById("rule-end-date").value,
        is_yearly: document.getElementById("rule-yearly").checked,
        channel_urls: urls,
        force: false,
    };

    let r = await fetch("/api/overlay/rules", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    });
    let data = await r.json();

    if (!data.ok && data.require_confirm) {
        if (!confirm(data.warning)) return;
        payload.force = true;
        r = await fetch("/api/overlay/rules", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        data = await r.json();
    }

    if (data.ok) {
        alert("Правило сохранено!");
        loadRules();
        document.getElementById("rule-id").value = "";
    } else {
        alert("Ошибка: " + data.error);
    }
});

async function loadRules() {
    const r = await fetch("/api/overlay/rules");
    const data = await r.json();
    const tbody = document.getElementById("rules-body");
    tbody.innerHTML = "";
    if (!data.rules.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="hint">Нет правил.</td></tr>';
        return;
    }
    data.rules.forEach(rule => {
        const tr = document.createElement("tr");
        const led = rule.enabled ? '<span class="led led--on led--pulse"></span>' : '<span class="led"></span>';
        tr.innerHTML = `
            <td>${led} ${rule.enabled ? "ON" : "OFF"}</td>
            <td>${escapeHtml(rule.name)}</td>
            <td><code>${escapeHtml(rule.text.text)}</code></td>
            <td>${rule.window.start_hhmm}-${rule.window.end_hhmm} (${rule.window.days.length ? rule.window.days.join(",") : "все"})</td>
            <td>${rule.channel_urls.length}</td>
            <td><button class="btn btn--danger btn--small" onclick="deleteRule('${rule.id}')">Удалить</button></td>
        `;
        tbody.appendChild(tr);
    });
}

async function deleteRule(id) {
    if (!confirm("Удалить правило?")) return;
    await fetch(`/api/overlay/rules/${id}`, { method: "DELETE" });
    loadRules();
}

document.getElementById("btn-reset-rule").addEventListener("click", () => {
    document.getElementById("rule-id").value = "";
    document.querySelectorAll(".rule-channel-cb").forEach(cb => cb.checked = false);
});

document.getElementById("rule-fontcolor").addEventListener("input", () => {
    document.getElementById("rule-fontcolor-name").value = "";
});
document.getElementById("rule-boxcolor").addEventListener("input", () => {
    document.getElementById("rule-boxcolor-name").value = "";
});

initPage();