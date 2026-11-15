let availableChannels = [];

async function initPage() {
    const r = await fetch("/api/overlay/available-channels");
    const data = await r.json();
    if (data.ok) { availableChannels = data.channels || []; renderChannelsList(); }
    loadRules();
}

function renderChannelsList() {
    const container = document.getElementById("rule-channels-list");
    if (!container) return;
    if (!availableChannels.length) {
        container.innerHTML = '<p class="hint">Рабочий плейлист пуст.</p>'; return;
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
        font_path: document.getElementById("rule-font").value.trim(), // Пустой путь сменится на дефолтный в app.py
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

    try {
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
        } else alert("Ошибка: " + (data.error || "неизвестно"));
    } catch (e) { alert("Сеть: " + e.message); }
});

async function loadRules() {
    const r = await fetch("/api/overlay/rules");
    const data = await r.json();
    const tbody = document.getElementById("rules-body");
    tbody.innerHTML = "";
    if (!data.rules.length) { tbody.innerHTML = '<tr><td colspan="6" class="hint">Нет правил.</td></tr>'; return; }

    data.rules.forEach(rule => {
        const tr = document.createElement("tr");
        const led = rule.enabled ? '<span class="led led--on led--pulse"></span>' : '<span class="led"></span>';
        tr.innerHTML = `
            <td>${led} ${rule.enabled ? "ON" : "OFF"}</td>
            <td>${escapeHtml(rule.name)}</td>
            <td><code>${escapeHtml(rule.text.text)}</code></td>
            <td>${rule.window.start_hhmm}-${rule.window.end_hhmm} (${rule.window.days.length ? rule.window.days.join(",") : "все"})</td>
            <td>${rule.channel_urls.length}</td>
            <td>
                <button class="btn btn--small" onclick='editRule(${JSON.stringify(rule)})'>Изменить</button>
                <button class="btn btn--danger btn--small" onclick="deleteRule('${rule.id}')">Удалить</button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

// -----подсказка: редактирование правила (заполняет форму)-----
window.editRule = function(rule) {
    document.getElementById("rule-id").value = rule.id;
    document.getElementById("rule-name").value = rule.name;
    document.getElementById("rule-text").value = rule.text.text;
    document.getElementById("rule-font").value = rule.text.font_path || "";
    document.getElementById("rule-fontsize").value = rule.text.font_size;
    document.getElementById("rule-fontcolor").value = rule.text.font_color.startsWith("0x") ? "#"+rule.text.font_color.slice(2) : rule.text.font_color;
    document.getElementById("rule-fontcolor-name").value = rule.text.font_color.startsWith("0x") ? "" : rule.text.font_color;
    document.getElementById("rule-speed").value = rule.text.speed;
    document.getElementById("rule-repeats-hour").value = Math.round(3600 / rule.text.repeat_period_sec);
    document.getElementById("rule-yoffset").value = rule.text.y_offset;
    document.getElementById("rule-box").checked = rule.text.box_enabled;
    document.getElementById("rule-boxcolor").value = rule.text.box_color.startsWith("0x") ? "#"+rule.text.box_color.slice(2) : rule.text.box_color;
    document.getElementById("rule-boxcolor-name").value = rule.text.box_color.startsWith("0x") ? "" : rule.text.box_color;
    document.getElementById("rule-boxalpha").value = rule.text.box_alpha;
    document.getElementById("rule-boxalpha-val").textContent = rule.text.box_alpha;
    document.getElementById("rule-start").value = rule.window.start_hhmm;
    document.getElementById("rule-end").value = rule.window.end_hhmm;
    document.getElementById("rule-start-date").value = rule.start_date || "";
    document.getElementById("rule-end-date").value = rule.end_date || "";
    document.getElementById("rule-yearly").checked = rule.is_yearly;

    document.querySelectorAll(".rule-day").forEach(cb => {
        cb.checked = rule.window.days.includes(parseInt(cb.value));
    });
    document.querySelectorAll(".rule-channel-cb").forEach(cb => {
        cb.checked = rule.channel_urls.includes(cb.dataset.url);
    });
    window.scrollTo({ top: 0, behavior: 'smooth' });
};

async function deleteRule(id) {
    if (!confirm("Удалить правило?")) return;
    await fetch(`/api/overlay/rules/${id}`, { method: "DELETE" });
    loadRules();
}
window.deleteRule = deleteRule;

document.getElementById("btn-reset-rule").addEventListener("click", () => {
    document.getElementById("rule-id").value = "";
    document.querySelectorAll(".rule-channel-cb").forEach(cb => cb.checked = false);
});

document.getElementById("rule-fontcolor").addEventListener("input", () => document.getElementById("rule-fontcolor-name").value = "");
document.getElementById("rule-boxcolor").addEventListener("input", () => document.getElementById("rule-boxcolor-name").value = "");

// Модалка символов
const SYMBOLS = { stars: ["★","☆","✪","✦","✧","✶","✷","✸","✹","✵"], hearts: ["♥","♡","♦","♢","♣","♧","♠","♤"], music: ["♪","♫","♬","♩","♭","♮","♯"], arrows: ["→","←","↑","↓","↔","↕","⇒","⇐","⇑","⇓","⇔"], checks: ["✓","✔","✗","✘","☑","☒","☐"], weather: ["☀","☼","☁","☂","☃","❄","❅","❆","☔"], geo: ["●","○","◆","◇","■","□","▲","△","▼","▽","◀","▶","◄","►"], misc: ["§","¶","†","‡","•","‰","°","±","×","÷","©","®","™","☎","☏","☺","☻","♨","☯"] };

function renderSymbols(cat) {
    const grid = document.getElementById("symbols-grid");
    grid.innerHTML = "";
    (SYMBOLS[cat] || []).forEach(sym => {
        const btn = document.createElement("button");
        btn.type = "button"; btn.className = "symbol-btn"; btn.textContent = sym;
        btn.onclick = () => {
            const input = document.getElementById("rule-text");
            const start = input.selectionStart ?? input.value.length;
            input.value = input.value.substring(0, start) + sym + input.value.substring(input.selectionEnd);
            input.focus(); input.setSelectionRange(start + sym.length, start + sym.length);
        };
        grid.appendChild(btn);
    });
}

document.getElementById("btn-symbols").onclick = () => { document.getElementById("symbols-modal").style.display = "flex"; renderSymbols("stars"); };
document.getElementById("symbols-close").onclick = () => document.getElementById("symbols-modal").style.display = "none";
document.querySelectorAll(".symbol-tab").forEach(tab => {
    tab.onclick = () => {
        document.querySelectorAll(".symbol-tab").forEach(t => t.classList.remove("is-active"));
        tab.classList.add("is-active");
        renderSymbols(tab.dataset.cat);
    };
});

initPage();