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
      // Deeper diagnostic than just "empty" -- confirmed both Desktop and mobile fail
      // the same way, so the next question is whether Telegram is putting tgWebAppData
      // in the URL AT ALL (hash/search below) or unsafeInitData has anything either
      // (unsigned, never used for auth, but tells us if Telegram sent *something*).
      const unsafeUser = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
      const hashParams = new URLSearchParams(location.hash.slice(1));
      const rawTgWebAppData = hashParams.get("tgWebAppData");
      throw new Error(
        `Нет initData (tg=${tg ? "есть" : "нет"}, version=${tg ? tg.version : "?"}, ` +
          `unsafeUser=${unsafeUser ? "id=" + unsafeUser.id : "нет"}, hash_len=${location.hash.length}, ` +
          `tgWebAppData_present=${rawTgWebAppData !== null}, tgWebAppData_len=${
            rawTgWebAppData ? rawTgWebAppData.length : 0
          }, hash_keys=${Array.from(hashParams.keys()).join(",")})`
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
  const langBtn = document.getElementById("lang-btn");
  const topbarTitleEl = document.getElementById("topbar-title");

  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    setTimeout(() => toastEl.classList.remove("show"), 2200);
  }

  let currentTab = "vilki";
  let meCache = null;
  let bookmakersCache = null;
  let refreshTimer = null;

  const HIGH_PROFIT_THRESHOLD = 10;
  const BANKROLL_PRESETS = [500, 1000, 5000, 10000];

  // ---------- i18n ----------
  // Frontend chrome only (labels/buttons/messages) -- backend error strings (e.g. HTTP
  // exception details from api.py) stay Russian regardless, that's a separate, much
  // bigger scope the user didn't ask for. Tajik strings are a best-effort translation,
  // not reviewed by a native speaker -- flag any that read oddly and they can be fixed.
  const I18N = {
    ru: {
      tab_vilki: "Вилки",
      tab_news: "Новости",
      tab_settings: "Настройки",
      tab_stats: "Статистика",
      tab_admin: "Админ",
      topbar_title: "🔍 Арбитражный бот",
      data_at: "Данные на ",
      msk: " МСК",
      no_vilki: "Сейчас подходящих вилок нет.",
      check_later: "Загляните чуть позже.",
      found_vilki: "Найдено вилок: ",
      profit: "Прибыль: ",
      possible_win: "Возможный выигрыш: ",
      odds_warning: "Коэффициенты и % прибыли могут измениться у букмекера — проверяйте перед ставкой.",
      copied: "Скопировано: ",
      copy_failed: "Не удалось скопировать",
      no_h2h: "Личных встреч в базе не нашлось.",
      h2h_title: "Личные встречи (последние ",
      h2h_total: "всего встреч: ",
      loading: "Загрузка…",
      daily_limit_reached: "Лимит на сегодня исчерпан",
      analyze_btn: "🔍 Проанализировать",
      error_prefix: "Ошибка: ",
      no_fresh_news: "За последние 24 часа свежих новостей не нашлось.",
      no_news_data: "Пока нет данных для новостной сводки.",
      news_meta_line:
        "Новости по популярным матчам — без прогнозов и процентов, только факты для собственного анализа. Анализ (личные встречи) — 1 раз в день.",
      bankroll: "Банкролл",
      profit_threshold: "Порог прибыли",
      profit_threshold_label: "Минимальный % прибыли для показа вилки",
      profit_threshold_hint: "Можно указать дробное значение, например 0.6",
      period: "Период",
      horizon_24: "До 24 часов",
      horizon_more24: "Более 24 часов",
      my_bookmakers: "Мои букмекеры",
      notifications: "Уведомления",
      muted_label: "🔕 Тихий режим (без звука)",
      save: "Сохранить",
      err_bankroll_positive: "Банкролл должен быть больше 0",
      err_threshold_negative: "Порог прибыли не может быть отрицательным",
      err_need_horizon: "Нужно оставить хотя бы один период",
      err_need_bookmaker: "Нужно оставить хотя бы одного букмекера",
      settings_saved: "Настройки сохранены ✅",
      save_failed: "Не удалось сохранить: ",
      today: "Сегодня",
      stat_found_vilki: "Найдено вилок",
      stat_avg_profit: "Средняя прибыль",
      stat_best_profit: "Лучшая прибыль",
      all_time: "За всё время",
      admin_users: "Пользователи",
      admin_total: "Всего",
      admin_on_trial: "На пробном",
      admin_has_access: "С доступом",
      admin_expired: "Доступ истёк",
      admin_notifications_on: "Уведомления вкл",
      admin_referred: "По рефералке",
      admin_payments: "Оплаты",
      admin_units: " шт · ",
      admin_no_payments: "Платежей ещё не было.",
      admin_sources: "Источники регистраций",
      admin_organic: "органика / без метки",
      admin_no_data: "Нет данных.",
      admin_recent_users: "Последние пользователи",
      admin_no_users: "Пользователей ещё нет.",
    },
    tg: {
      tab_vilki: "Арбитраж",
      tab_news: "Хабарҳо",
      tab_settings: "Танзимот",
      tab_stats: "Омор",
      tab_admin: "Админ",
      topbar_title: "🔍 Боти арбитражӣ",
      data_at: "Маълумот аз соати ",
      msk: " МСК",
      no_vilki: "Ҳоло вилкаҳои мувофиқ нест.",
      check_later: "Баъд аз каме вақт дубора нигаред.",
      found_vilki: "Вилкаҳои ёфтшуда: ",
      profit: "Фоида: ",
      possible_win: "Бурди эҳтимолӣ: ",
      odds_warning: "Коэффисиентҳо ва фоизи фоида дар назди букмекер метавонанд тағйир ёбанд — пеш аз гузоштани бет санҷед.",
      copied: "Нусхабардорӣ шуд: ",
      copy_failed: "Нусхабардорӣ ба амал наомад",
      no_h2h: "Дар пойгоҳи додаҳо вохӯриҳои шахсӣ ёфт нашуданд.",
      h2h_title: "Вохӯриҳои шахсӣ (охирин ",
      h2h_total: "ҳамагӣ вохӯриҳо: ",
      loading: "Боргирӣ…",
      daily_limit_reached: "Лимити имрӯза тамом шуд",
      analyze_btn: "🔍 Таҳлил кардан",
      error_prefix: "Хато: ",
      no_fresh_news: "Дар 24 соати охир хабарҳои нав ёфт нашуданд.",
      no_news_data: "Ҳоло барои хулосаи хабарҳо маълумот нест.",
      news_meta_line:
        "Хабарҳо оид ба бозиҳои маъмул — бидуни пешгӯӣ ва фоиз, танҳо далелҳо барои таҳлили худ. Таҳлил (вохӯриҳои шахсӣ) — 1 бор дар як рӯз.",
      bankroll: "Бонкролл",
      profit_threshold: "Ҳадди фоида",
      profit_threshold_label: "Фоизи ҳадди ақали фоида барои нишон додани вилка",
      profit_threshold_hint: "Метавонед қимати касрӣ нависед, масалан 0.6",
      period: "Давра",
      horizon_24: "То 24 соат",
      horizon_more24: "Зиёда аз 24 соат",
      my_bookmakers: "Букмекерҳои ман",
      notifications: "Огоҳиномаҳо",
      muted_label: "🔕 Реҷаи ором (бе садо)",
      save: "Нигоҳ доштан",
      err_bankroll_positive: "Бонкролл бояд аз 0 зиёд бошад",
      err_threshold_negative: "Ҳадди фоида наметавонад манфӣ бошад",
      err_need_horizon: "Бояд ҳадди ақал як давра боқӣ монад",
      err_need_bookmaker: "Бояд ҳадди ақал як букмекер боқӣ монад",
      settings_saved: "Танзимот нигоҳ дошта шуд ✅",
      save_failed: "Нигоҳ доштан ноком шуд: ",
      today: "Имрӯз",
      stat_found_vilki: "Вилкаҳои ёфтшуда",
      stat_avg_profit: "Фоидаи миёна",
      stat_best_profit: "Беҳтарин фоида",
      all_time: "Дар тамоми давра",
      admin_users: "Корбарон",
      admin_total: "Ҳамагӣ",
      admin_on_trial: "Дар озмоишӣ",
      admin_has_access: "Бо дастрасӣ",
      admin_expired: "Дастрасӣ тамом шуд",
      admin_notifications_on: "Огоҳиномаҳо фаъол",
      admin_referred: "Тавассути реферал",
      admin_payments: "Пардохтҳо",
      admin_units: " адад · ",
      admin_no_payments: "Пардохт ҳанӯз набудааст.",
      admin_sources: "Манбаъҳои сабтном",
      admin_organic: "органикӣ / бе тег",
      admin_no_data: "Маълумот нест.",
      admin_recent_users: "Корбарони охирин",
      admin_no_users: "Корбарон ҳанӯз нест.",
    },
  };

  let currentLang = "ru";
  try {
    currentLang = localStorage.getItem("lang") === "tg" ? "tg" : "ru";
  } catch (e) {
    // localStorage can throw (private mode, blocked site data) -- ru default is fine.
  }

  function t(key) {
    return (I18N[currentLang] && I18N[currentLang][key]) || I18N.ru[key] || key;
  }

  const TIME_HORIZON_LABELS = () => ({ 1: t("horizon_24"), 2: t("horizon_more24") });

  function applyStaticLabels() {
    topbarTitleEl.textContent = t("topbar_title");
    document.querySelector('[data-tab="vilki"]').textContent = t("tab_vilki");
    document.querySelector('[data-tab="news"]').textContent = t("tab_news");
    document.querySelector('[data-tab="settings"]').textContent = t("tab_settings");
    document.querySelector('[data-tab="stats"]').textContent = t("tab_stats");
    document.getElementById("admin-tab").textContent = t("tab_admin");
  }
  applyStaticLabels();

  langBtn.addEventListener("click", () => {
    haptic("light");
    currentLang = currentLang === "ru" ? "tg" : "ru";
    try {
      localStorage.setItem("lang", currentLang);
    } catch (e) {
      // Per-viewer convenience only -- fine if it doesn't persist.
    }
    applyStaticLabels();
    switchTab(currentTab);
  });

  function haptic(style) {
    if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred(style || "light");
  }

  const skeletons = {
    vilki: `<div class="skeleton skeleton-card"></div><div class="skeleton skeleton-card"></div><div class="skeleton skeleton-card"></div>`,
    news: `<div class="skeleton skeleton-card"></div><div class="skeleton skeleton-card"></div>`,
    settings: `<div class="skeleton skeleton-card" style="height:280px"></div>`,
    stats: `<div class="stat-grid"><div class="skeleton skeleton-stat"></div><div class="skeleton skeleton-stat"></div></div>`,
    admin: `<div class="skeleton skeleton-card" style="height:280px"></div>`,
  };

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.tab === currentTab) return;
      haptic("light");
      switchTab(btn.dataset.tab);
    });
  });
  refreshBtn.addEventListener("click", () => {
    haptic("light");
    loadTab(currentTab, true);
  });

  function switchTab(tab) {
    currentTab = tab;
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    content.innerHTML = skeletons[tab] || "";
    loadTab(tab);
  }

  async function loadTab(tab, manual) {
    if (manual) refreshBtn.classList.add("spinning");
    try {
      if (tab === "vilki") await renderVilki();
      else if (tab === "news") await renderNews();
      else if (tab === "settings") await renderSettings();
      else if (tab === "stats") await renderStats();
      else if (tab === "admin") await renderAdmin();
    } catch (e) {
      toast(t("error_prefix") + e.message);
    } finally {
      if (manual) refreshBtn.classList.remove("spinning");
    }
  }

  // ---------- Вилки ----------

  // Space-grouped thousands, period decimal (matches format_amount on the bot side) --
  // a bankroll of 1 000 000 (a real preset) otherwise renders as one unbroken digit run.
  function fmtMoney(n) {
    return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).replace(/,/g, " ");
  }

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
    const checkedAt = data.updated_at ? fmtMoscowTime(data.updated_at) + t("msk") : "—";

    if (!data.matches.length) {
      content.innerHTML = `
        <div class="meta-line">${t("data_at")}${checkedAt}</div>
        <div class="empty-state"><span class="empty-icon">🔍</span>${t("no_vilki")}<br>${t("check_later")}</div>`;
      return;
    }

    const cards = data.matches.map((m, i) => {
      const isHigh = m.profit_pct > HIGH_PROFIT_THRESHOLD;
      const profitClass = isHigh ? "match-profit high" : "match-profit";
      const profitEmoji = isHigh ? `<span class="emoji-shake">‼️</span>` : `<span class="emoji-pulse">🚀</span>`;
      const legs = m.legs
        .map(
          // Outcome name (e.g. a double-chance "Team A или Team B") gets its own line,
          // however long -- odds/bookmaker/stake always stay together on one line below
          // it. Previously all of it was one flex row with the stake pinned to the
          // right; a long name wrapping pushed "odds @ bookmaker" onto its own visual
          // line with the stake next to it, which read like a separate 3rd bet.
          (leg) => `
        <div class="leg-row">
          <div class="leg-outcome">${copyable(leg.outcome_name)}</div>
          <div class="leg-details">
            <span><b>${leg.odds}</b> @ ${
              leg.bookmaker_url
                ? `<a href="${esc(leg.bookmaker_url)}" target="_blank" rel="noopener">${esc(leg.bookmaker)}</a>`
                : esc(leg.bookmaker)
            }</span>
            <span class="leg-stake">${fmtMoney(leg.stake)}</span>
          </div>
        </div>`
        )
        .join("");
      return `
        <div class="card${isHigh ? " high-profit" : ""}" style="animation-delay:${Math.min(i * 45, 360)}ms">
          <div class="match-header"><span class="emoji-wiggle">${m.game_emoji}</span><span>${esc(m.game_label)}</span></div>
          <div class="match-teams"><span class="emoji-clash">⚔️</span> ${copyable(m.team_a)} vs ${copyable(m.team_b)}</div>
          ${m.start_time_label ? `<div class="match-time"><span class="emoji-tick">🕒</span> ${esc(m.start_time_label)}</div>` : ""}
          <div class="${profitClass}">${profitEmoji} ${t("profit")}${m.profit_pct.toFixed(2)}%</div>
          <div class="match-amount"><span class="emoji-bounce">💸</span> ${t("possible_win")}<span class="amount-value">${fmtMoney(m.profit_amount)}</span></div>
          <div class="legs">${legs}</div>
          <div class="odds-warning">⚠️ ${t("odds_warning")}</div>
        </div>`;
    });

    content.innerHTML = `
      <div class="meta-line">${t("found_vilki")}<b>${data.matches.length}</b> (${t("data_at").toLowerCase()}${checkedAt})</div>
      ${cards.join("")}`;
  }

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  // Team/outcome names are highlighted and tap-to-copy -- handy for pasting into a
  // bookmaker's own search box. data-copy carries the raw text (HTML-escaped as an
  // attribute); the visible text is separately esc()'d same as everywhere else.
  function copyable(text) {
    // esc() alone doesn't escape " -- fine for element content but not for a quoted
    // attribute value, so quotes get an extra pass here.
    const attr = esc(text).replace(/"/g, "&quot;");
    return `<span class="copyable" data-copy="${attr}">${esc(text)}</span>`;
  }

  async function copyToClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      // Clipboard API can be unavailable/blocked in some WebView contexts -- fall back
      // to the classic hidden-textarea + execCommand trick rather than just failing.
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
        return true;
      } catch (e2) {
        return false;
      }
    }
  }

  // ---------- Новости ----------
  // Real headlines for a few popular matches -- deliberately NOT win-probability
  // predictions/percentages, see bot/webapp/news.py's module docstring for why.

  function fmtNewsTime(ts) {
    if (!ts) return "";
    try {
      return new Date(ts * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    } catch (e) {
      return "";
    }
  }

  function renderH2hBlock(h2h, teamA, teamB) {
    if (!h2h) return `<div class="news-empty">${t("no_h2h")}</div>`;
    return `<div class="h2h-block">
        <div class="h2h-title">📊 ${t("h2h_title")}${h2h.matches.length})</div>
        <div class="h2h-record">${esc(teamA)} ${h2h.team_a_wins} — ${h2h.draws} — ${h2h.team_b_wins} ${esc(teamB)} <span class="h2h-total">(${t("h2h_total")}${h2h.total})</span></div>
        <div class="h2h-matches">${h2h.matches
          .map(
            (hm) => `<div class="h2h-row"><span class="h2h-date">${esc(hm.date)}</span> ${esc(hm.home)} ${hm.home_score}:${hm.away_score} ${esc(hm.away)}</div>`
          )
          .join("")}</div>
      </div>`;
  }

  // Gated to 1 analysis/day/user (see /api/analysis) -- deliberately not fetched for
  // all 3 matches up front, only for the one the user actually clicks on.
  async function onAnalyzeClick(btn) {
    const teamA = btn.dataset.teamA;
    const teamB = btn.dataset.teamB;
    const slot = document.getElementById(btn.dataset.slotId);
    haptic("light");
    btn.disabled = true;
    btn.textContent = t("loading");
    try {
      const result = await api(`/api/analysis?team_a=${encodeURIComponent(teamA)}&team_b=${encodeURIComponent(teamB)}`);
      slot.innerHTML = renderH2hBlock(result.h2h, teamA, teamB);
      btn.remove();
      // Admins have no daily quota server-side (see /api/analysis) -- leave every other
      // button clickable for them instead of locking the rest of the page.
      if (!meCache || !meCache.is_admin) {
        if (meCache) meCache.analysis_available = false;
        document.querySelectorAll(".analyze-btn").forEach((b) => {
          if (b !== btn) {
            b.disabled = true;
            b.textContent = t("daily_limit_reached");
          }
        });
      }
    } catch (e) {
      btn.disabled = false;
      btn.textContent = t("analyze_btn");
      toast(t("error_prefix") + e.message);
    }
  }

  content.addEventListener("click", (e) => {
    const btn = e.target.closest(".analyze-btn");
    if (btn) onAnalyzeClick(btn);

    const copyEl = e.target.closest(".copyable");
    if (copyEl) {
      const text = copyEl.dataset.copy;
      copyToClipboard(text).then((ok) => {
        haptic(ok ? "light" : "medium");
        toast(ok ? `${t("copied")}${text}` : t("copy_failed"));
      });
    }
  });

  async function renderNews() {
    const [data, me] = await Promise.all([api("/api/news"), api("/api/me")]);
    meCache = me;

    if (!data.matches.length) {
      content.innerHTML = `<div class="empty-state"><span class="empty-icon">📰</span>${t("no_news_data")}</div>`;
      return;
    }

    const cards = data.matches.map((m, i) => {
      const headlines = m.headlines.length
        ? m.headlines
            .map(
              (h) => `
          <div class="news-row">
            <a href="${esc(h.link)}" target="_blank" rel="noopener">${esc(h.title)}</a>
            <div class="news-meta">${esc(h.source || "")}${h.published_at ? " · " + fmtNewsTime(h.published_at) : ""}</div>
          </div>`
            )
            .join("")
        : `<div class="news-empty">${t("no_fresh_news")}</div>`;

      // Full analysis (H2H) is fetched only on click, gated to 1/day/user -- see
      // /api/analysis and onAnalyzeClick above.
      const slotId = `analysis-slot-${i}`;
      let analyzeBlock = "";
      if (m.can_analyze) {
        analyzeBlock = me.analysis_available
          ? `<button type="button" class="analyze-btn" data-team-a="${esc(m.team_a)}" data-team-b="${esc(m.team_b)}" data-slot-id="${slotId}">${t("analyze_btn")}</button>`
          : `<button type="button" class="analyze-btn" disabled>${t("daily_limit_reached")}</button>`;
      }

      return `
        <div class="card" style="animation-delay:${Math.min(i * 45, 360)}ms">
          <div class="match-header"><span class="emoji-wiggle">${m.game_emoji}</span><span>${esc(m.game_label)}</span></div>
          <div class="match-teams"><span class="emoji-clash">⚔️</span> ${copyable(m.team_a)} vs ${copyable(m.team_b)}</div>
          ${m.start_time_label ? `<div class="match-time"><span class="emoji-tick">🕒</span> ${esc(m.start_time_label)}</div>` : ""}
          <div class="news-list">${headlines}</div>
          ${analyzeBlock}
          <div id="${slotId}"></div>
        </div>`;
    });

    content.innerHTML = `
      <div class="meta-line">${t("news_meta_line")}</div>
      ${cards.join("")}`;
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

    const horizonRows = Object.entries(TIME_HORIZON_LABELS())
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
      <div class="section-title">${t("bankroll")}</div>
      <div class="field">
        <input type="number" id="bankroll-input" value="${me.bankroll}" min="1" step="1">
        <div class="preset-row">${presetRow}</div>
      </div>

      <div class="section-title">${t("profit_threshold")}</div>
      <div class="field">
        <label>${t("profit_threshold_label")}</label>
        <input type="number" id="threshold-input" value="${me.min_profit_pct}" min="0" step="0.1" placeholder="0.6">
        <div class="field-hint">${t("profit_threshold_hint")}</div>
      </div>

      <div class="section-title">${t("period")}</div>
      ${horizonRows}

      <div class="section-title">${t("my_bookmakers")}</div>
      <div class="chip-grid" id="bk-grid">${bkChips}</div>

      <div class="section-title">${t("notifications")}</div>
      <div class="toggle-row">
        <span>${t("muted_label")}</span>
        <label class="switch">
          <input type="checkbox" id="muted-input" ${me.muted ? "checked" : ""}>
          <span class="slider"></span>
        </label>
      </div>

      <button class="save-btn" id="save-settings-btn">${t("save")}</button>
    `;

    document.querySelectorAll(".preset-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        haptic("light");
        document.getElementById("bankroll-input").value = btn.dataset.preset;
      });
    });
    document.getElementById("bk-grid").addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip) return;
      haptic("light");
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

    if (!bankroll || bankroll <= 0) return toast(t("err_bankroll_positive"));
    if (isNaN(minProfitPct) || minProfitPct < 0) return toast(t("err_threshold_negative"));
    if (!horizons.length) return toast(t("err_need_horizon"));
    if (!bookmakers.length) return toast(t("err_need_bookmaker"));

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
      toast(t("settings_saved"));
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    } catch (e) {
      toast(t("save_failed") + e.message);
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("error");
    }
  }

  // ---------- Статистика ----------

  function animateValue(el, target, suffix, decimals) {
    const duration = 500;
    const start = performance.now();
    function tick(now) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // ease-out-cubic
      const current = target * eased;
      el.textContent = (decimals ? current.toFixed(decimals) : Math.round(current)) + suffix;
      if (t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  async function renderStats() {
    const s = await api("/api/stats");
    content.innerHTML = `
      <div class="section-title">${t("today")}</div>
      <div class="stat-grid stat-grid-3">
        <div class="stat-card" style="animation-delay:0ms"><div class="stat-value" data-v="${s.today_count}" data-suf="" data-dec="0">0</div><div class="stat-label">${t("stat_found_vilki")}</div></div>
        <div class="stat-card" style="animation-delay:40ms"><div class="stat-value" data-v="${s.today_avg_profit}" data-suf="%" data-dec="2">0%</div><div class="stat-label">${t("stat_avg_profit")}</div></div>
        <div class="stat-card" style="animation-delay:80ms"><div class="stat-value" data-v="${s.today_best_profit}" data-suf="%" data-dec="2">0%</div><div class="stat-label">${t("stat_best_profit")}</div></div>
      </div>
      <div class="section-title">${t("all_time")}</div>
      <div class="stat-grid">
        <div class="stat-card" style="animation-delay:120ms"><div class="stat-value" data-v="${s.alltime_count}" data-suf="" data-dec="0">0</div><div class="stat-label">${t("stat_found_vilki")}</div></div>
        <div class="stat-card" style="animation-delay:160ms"><div class="stat-value" data-v="${s.alltime_avg_profit}" data-suf="%" data-dec="2">0%</div><div class="stat-label">${t("stat_avg_profit")}</div></div>
      </div>
    `;
    content.querySelectorAll(".stat-value").forEach((el) => {
      animateValue(el, parseFloat(el.dataset.v) || 0, el.dataset.suf, Number(el.dataset.dec));
    });
  }

  // ---------- Админ ----------

  function fmtDate(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit" });
    } catch (e) {
      return "";
    }
  }

  async function renderAdmin() {
    const s = await api("/api/admin/stats");

    const paymentsRows = s.payments.length
      ? s.payments
          .map(
            (p) =>
              `<div class="admin-row"><span>${esc(p.provider)} · ${esc(p.currency)}</span><span>${p.count}${t("admin_units")}${fmtMoney(p.total)}</span></div>`
          )
          .join("")
      : `<div class="news-empty">${t("admin_no_payments")}</div>`;

    const sourcesRows = s.acquisition_sources.length
      ? s.acquisition_sources
          .map(
            (a) => `<div class="admin-row"><span>${esc(a.source || t("admin_organic"))}</span><span>${a.count}</span></div>`
          )
          .join("")
      : `<div class="news-empty">${t("admin_no_data")}</div>`;

    const usersRows = s.recent_users.length
      ? s.recent_users
          .map(
            (u) => `<div class="admin-row">
              <span>${u.chat_id}${u.acquisition_source ? ` <span class="admin-tag">${esc(u.acquisition_source)}</span>` : ""}</span>
              <span>${u.has_access ? "✅" : "⛔"} ${fmtDate(u.trial_started_at)}</span>
            </div>`
          )
          .join("")
      : `<div class="news-empty">${t("admin_no_users")}</div>`;

    content.innerHTML = `
      <div class="section-title">${t("admin_users")}</div>
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value" data-v="${s.total_users}" data-suf="" data-dec="0">0</div><div class="stat-label">${t("admin_total")}</div></div>
        <div class="stat-card"><div class="stat-value" data-v="${s.on_trial}" data-suf="" data-dec="0">0</div><div class="stat-label">${t("admin_on_trial")}</div></div>
        <div class="stat-card"><div class="stat-value" data-v="${s.has_access}" data-suf="" data-dec="0">0</div><div class="stat-label">${t("admin_has_access")}</div></div>
        <div class="stat-card"><div class="stat-value" data-v="${s.expired}" data-suf="" data-dec="0">0</div><div class="stat-label">${t("admin_expired")}</div></div>
        <div class="stat-card"><div class="stat-value" data-v="${s.active_notifications}" data-suf="" data-dec="0">0</div><div class="stat-label">${t("admin_notifications_on")}</div></div>
        <div class="stat-card"><div class="stat-value" data-v="${s.referred_count}" data-suf="" data-dec="0">0</div><div class="stat-label">${t("admin_referred")}</div></div>
      </div>

      <div class="section-title">${t("admin_payments")}</div>
      <div class="card">${paymentsRows}</div>

      <div class="section-title">${t("admin_sources")}</div>
      <div class="card">${sourcesRows}</div>

      <div class="section-title">${t("admin_recent_users")}</div>
      <div class="card">${usersRows}</div>
    `;
    content.querySelectorAll(".stat-value").forEach((el) => {
      animateValue(el, parseFloat(el.dataset.v) || 0, el.dataset.suf, Number(el.dataset.dec));
    });
  }

  // ---------- init ----------

  content.innerHTML = skeletons.vilki;
  loadTab("vilki");
  refreshTimer = setInterval(() => {
    if (currentTab === "vilki") loadTab("vilki");
  }, 20000);

  // Admin tab is hidden in the markup by default -- only unhidden once /api/me confirms
  // is_admin, so a non-admin never even sees the tab exist.
  api("/api/me")
    .then((me) => {
      meCache = me;
      if (me.is_admin) document.getElementById("admin-tab").hidden = false;
    })
    .catch(() => {});
})();
