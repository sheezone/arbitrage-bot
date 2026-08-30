(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    applyThemeVars();
    tg.onEvent("themeChanged", applyThemeVars);
  }

  function applyThemeVars() {
    const tp = tg.themeParams || {};
    const root = document.documentElement.style;
    const map = {
      bg_color: "--tg-theme-bg-color",
      secondary_bg_color: "--tg-theme-secondary-bg-color",
      text_color: "--tg-theme-text-color",
      hint_color: "--tg-theme-hint-color",
      link_color: "--tg-theme-link-color",
      button_color: "--tg-theme-button-color",
      button_text_color: "--tg-theme-button-text-color",
    };
    for (const [key, cssVar] of Object.entries(map)) {
      if (tp[key]) root.setProperty(cssVar, tp[key]);
    }
  }

  async function api(path, options) {
    // Read tg.initData fresh on every call rather than once at script load -- some
    // clients populate it a beat after telegram-web-app.js first runs, so a value
    // captured too early could stay empty for the rest of the page's life.
    const initData = tg ? tg.initData : "";
    if (!initData) {
      throw new Error(
        `Нет initData от Telegram (tg=${tg ? "есть" : "нет"}, platform=${tg ? tg.platform : "?"}, version=${
          tg ? tg.version : "?"
        }). Откройте приложение через кнопку у бота, не по прямой ссылке.`
      );
    }
    const resp = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        Authorization: "tma " + initData,
        ...(options && options.headers),
      },
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  const content = document.getElementById("content");
  const toastEl = document.getElementById("toast");
  const refreshBtn = document.getElementById("refresh-btn");

  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    setTimeout(() => toastEl.classList.remove("show"), 2200);
  }

  let currentTab = "vilki";
  let meCache = null;
  let bookmakersCache = null;
  let refreshTimer = null;

  const TIME_HORIZON_LABELS = { 1: "До 24 часов", 2: "Более 24 часов" };
  const HIGH_PROFIT_THRESHOLD = 10;
  const BANKROLL_PRESETS = [500, 1000, 5000, 10000];

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
  refreshBtn.addEventListener("click", () => loadTab(currentTab, true));

  function switchTab(tab) {
    currentTab = tab;
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    loadTab(tab);
  }

  async function loadTab(tab, manual) {
    if (manual) refreshBtn.classList.add("spinning");
    try {
      if (tab === "vilki") await renderVilki();
      else if (tab === "settings") await renderSettings();
      else if (tab === "stats") await renderStats();
    } catch (e) {
      toast("Ошибка: " + e.message);
    } finally {
      if (manual) refreshBtn.classList.remove("spinning");
    }
  }

  // ---------- Вилки ----------

  function fmtMoscowTime(iso) {
    if (!iso) return "";
    try {
      return new Date(iso * 1000).toLocaleTimeString("ru-RU", { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit" });
    } catch (e) {
      return "";
    }
  }

  async function renderVilki() {
    const data = await api("/api/vilki");
    const checkedAt = data.updated_at ? fmtMoscowTime(data.updated_at) + " МСК" : "—";

    if (!data.matches.length) {
      content.innerHTML = `
        <div class="meta-line">Данные на ${checkedAt}</div>
        <div class="empty-state">Сейчас подходящих вилок нет.<br>Загляните чуть позже.</div>`;
      return;
    }

    const cards = data.matches.map((m) => {
      const profitClass = m.profit_pct > HIGH_PROFIT_THRESHOLD ? "match-profit high" : "match-profit";
      const legs = m.legs
        .map(
          (leg) => `
        <div class="leg-row">
          <span>${esc(leg.outcome_name)}: <b>${leg.odds}</b> @ ${
            leg.bookmaker_url
              ? `<a href="${esc(leg.bookmaker_url)}" target="_blank" rel="noopener">${esc(leg.bookmaker)}</a>`
              : esc(leg.bookmaker)
          }</span>
          <span class="leg-stake">${leg.stake.toFixed(2)}</span>
        </div>`
        )
        .join("");
      return `
        <div class="card">
          <div class="match-header"><span>${m.game_emoji}</span><span>${esc(m.game_label)}</span></div>
          <div class="match-teams">⚔️ ${esc(m.team_a)} vs ${esc(m.team_b)}</div>
          ${m.start_time_label ? `<div class="match-time">🕒 ${esc(m.start_time_label)}</div>` : ""}
          <div class="${profitClass}">🚀 Прибыль: ${m.profit_pct.toFixed(2)}%</div>
          <div class="match-amount">💸 Возможный выигрыш: ${m.profit_amount.toFixed(2)}</div>
          <div class="legs">${legs}</div>
        </div>`;
    });

    content.innerHTML = `
      <div class="meta-line">Найдено вилок: <b>${data.matches.length}</b> (данные на ${checkedAt})</div>
      ${cards.join("")}`;
  }

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  // ---------- Настройки ----------

  async function renderSettings() {
    const [me, bk] = await Promise.all([
      api("/api/me"),
      bookmakersCache ? Promise.resolve(bookmakersCache) : api("/api/bookmakers"),
    ]);
    meCache = me;
    bookmakersCache = bk;
    const selectedBk = me.allowed_bookmakers.length ? new Set(me.allowed_bookmakers) : new Set(bk.bookmakers.map((b) => b.key));

    const presetRow = BANKROLL_PRESETS.map(
      (p) => `<button type="button" class="preset-btn" data-preset="${p}">${p}</button>`
    ).join("");

    const horizonRows = Object.entries(TIME_HORIZON_LABELS)
      .map(
        ([days, label]) => `
        <div class="toggle-row">
          <span>${label}</span>
          <label class="switch">
            <input type="checkbox" data-horizon="${days}" ${me.time_horizons.includes(Number(days)) ? "checked" : ""}>
            <span class="slider"></span>
          </label>
        </div>`
      )
      .join("");

    const bkChips = bk.bookmakers
      .map(
        (b) => `<button type="button" class="chip ${selectedBk.has(b.key) ? "selected" : ""}" data-bk="${b.key}">${esc(b.label)}</button>`
      )
      .join("");

    content.innerHTML = `
      <div class="section-title">Банкролл</div>
      <div class="field">
        <input type="number" id="bankroll-input" value="${me.bankroll}" min="1" step="1">
        <div class="preset-row">${presetRow}</div>
      </div>

      <div class="section-title">Порог прибыли</div>
      <div class="field">
        <label>Минимальный % прибыли для показа вилки</label>
        <input type="number" id="threshold-input" value="${me.min_profit_pct}" min="0" step="0.1">
      </div>

      <div class="section-title">Период</div>
      ${horizonRows}

      <div class="section-title">Мои букмекеры</div>
      <div class="chip-grid" id="bk-grid">${bkChips}</div>

      <div class="section-title">Уведомления</div>
      <div class="toggle-row">
        <span>🔕 Тихий режим (без звука)</span>
        <label class="switch">
          <input type="checkbox" id="muted-input" ${me.muted ? "checked" : ""}>
          <span class="slider"></span>
        </label>
      </div>

      <button class="save-btn" id="save-settings-btn">Сохранить</button>
    `;

    document.querySelectorAll(".preset-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.getElementById("bankroll-input").value = btn.dataset.preset;
      });
    });
    document.getElementById("bk-grid").addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip) return;
      chip.classList.toggle("selected");
    });
    document.getElementById("save-settings-btn").addEventListener("click", saveSettings);
  }

  async function saveSettings() {
    const bankroll = parseFloat(document.getElementById("bankroll-input").value);
    const minProfitPct = parseFloat(document.getElementById("threshold-input").value);
    const horizons = Array.from(document.querySelectorAll("[data-horizon]"))
      .filter((el) => el.checked)
      .map((el) => Number(el.dataset.horizon));
    const bookmakers = Array.from(document.querySelectorAll("#bk-grid .chip.selected")).map((el) => el.dataset.bk);
    const muted = document.getElementById("muted-input").checked;

    if (!bankroll || bankroll <= 0) return toast("Банкролл должен быть больше 0");
    if (isNaN(minProfitPct) || minProfitPct < 0) return toast("Порог прибыли не может быть отрицательным");
    if (!horizons.length) return toast("Нужно оставить хотя бы один период");
    if (!bookmakers.length) return toast("Нужно оставить хотя бы одного букмекера");

    try {
      await api("/api/settings", {
        method: "POST",
        body: JSON.stringify({
          bankroll,
          min_profit_pct: minProfitPct,
          time_horizons: horizons,
          allowed_bookmakers: bookmakers,
          muted,
        }),
      });
      toast("Настройки сохранены");
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    } catch (e) {
      toast("Не удалось сохранить: " + e.message);
    }
  }

  // ---------- Статистика ----------

  async function renderStats() {
    const s = await api("/api/stats");
    content.innerHTML = `
      <div class="section-title">Сегодня</div>
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">${s.today_count}</div><div class="stat-label">Найдено вилок</div></div>
        <div class="stat-card"><div class="stat-value">${s.today_avg_profit.toFixed(2)}%</div><div class="stat-label">Средняя прибыль</div></div>
        <div class="stat-card"><div class="stat-value">${s.today_best_profit.toFixed(2)}%</div><div class="stat-label">Лучшая прибыль</div></div>
      </div>
      <div class="section-title">За всё время</div>
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">${s.alltime_count}</div><div class="stat-label">Найдено вилок</div></div>
        <div class="stat-card"><div class="stat-value">${s.alltime_avg_profit.toFixed(2)}%</div><div class="stat-label">Средняя прибыль</div></div>
      </div>
    `;
  }

  // ---------- init ----------

  loadTab("vilki");
  refreshTimer = setInterval(() => {
    if (currentTab === "vilki") loadTab("vilki");
  }, 20000);
})();
