(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    applyThemeVars();
    tg.onEvent("themeChanged", applyThemeVars);
    try {
      tg.setHeaderColor("secondary_bg_color");
      tg.setBackgroundColor(tg.themeParams.bg_color || "#0c0f15");
    } catch (e) {
      // older client without these methods -- fine, just skip
    }
  }

  // Splash stays up at least this long so it never flashes-and-vanishes on a fast
  // connection -- feels intentional/branded rather than like a loading glitch.
  const SPLASH_MIN_MS = 550;
  const splashShownAt = Date.now();
  function hideSplash() {
    const el = document.getElementById("splash");
    if (!el) return;
    const wait = Math.max(0, SPLASH_MIN_MS - (Date.now() - splashShownAt));
    setTimeout(() => el.classList.add("hide"), wait);
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
      const err = new Error(body.detail || `HTTP ${resp.status}`);
      err.status = resp.status;
      throw err;
    }
    return resp.json();
  }

  const content = document.getElementById("content");
  const toastEl = document.getElementById("toast");
  const refreshBtn = document.getElementById("refresh-btn");
  const langBtn = document.getElementById("lang-btn");
  const langMenu = document.getElementById("lang-menu");
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
      hi: "Привет",
      tab_vilki: "Вилки",
      tab_news: "Новости",
      tab_settings: "Настройки",
      tab_stats: "Статистика",
      tab_admin: "Админ",
      topbar_title: "🔍 Арбитражный бот",
      refresh_btn: "Обновить",
      share_btn: "Поделиться",
      shared: "Вилка отправлена",
      filter_min_profit: "Мин. %",
      filter_all_sports: "Все виды спорта",
      gate_title: "🔒 Доступ ограничен",
      gate_text: "Чтобы пользоваться ботом и мини-приложением, подпишитесь на канал",
      gate_subscribe: "📢 Подписаться",
      gate_check: "✅ Проверить подписку",
      gate_not_yet: "Вы ещё не подписались",
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
      draws_label: "Ничьи",
      recent_meeting_events: "Последняя встреча — что произошло",
      standings_position: "Место в таблице: ",
      standings_points: "очков",
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
      hi: "Салом",
      tab_vilki: "Арбитраж",
      tab_news: "Хабарҳо",
      tab_settings: "Танзимот",
      tab_stats: "Омор",
      tab_admin: "Админ",
      topbar_title: "🔍 Боти арбитражӣ",
      refresh_btn: "Навсозӣ",
      share_btn: "Мубодила",
      shared: "Вилка фиристода шуд",
      filter_min_profit: "Ҳадди %",
      filter_all_sports: "Ҳамаи намудҳои варзиш",
      gate_title: "🔒 Дастрасӣ маҳдуд аст",
      gate_text: "Барои истифодаи бот ва мини-барнома ба канал обуна шавед",
      gate_subscribe: "📢 Обуна шудан",
      gate_check: "✅ Санҷидани обуна",
      gate_not_yet: "Шумо ҳанӯз обуна нашудаед",
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
      draws_label: "Баробарӣ",
      recent_meeting_events: "Вохӯрии охирин — чӣ рӯй дод",
      standings_position: "Ҷой дар ҷадвал: ",
      standings_points: "хол",
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
    en: {
      hi: "Hi",
      tab_vilki: "Arbs",
      tab_news: "News",
      tab_settings: "Settings",
      tab_stats: "Stats",
      tab_admin: "Admin",
      topbar_title: "🔍 Arbitrage bot",
      refresh_btn: "Refresh",
      share_btn: "Share",
      shared: "Arb shared",
      filter_min_profit: "Min %",
      filter_all_sports: "All sports",
      gate_title: "🔒 Access restricted",
      gate_text: "To use the bot and the mini app, subscribe to the channel",
      gate_subscribe: "📢 Subscribe",
      gate_check: "✅ Check subscription",
      gate_not_yet: "You haven't subscribed yet",
      data_at: "Data as of ",
      msk: " MSK",
      no_vilki: "No suitable arbs right now.",
      check_later: "Check back a little later.",
      found_vilki: "Arbs found: ",
      profit: "Profit: ",
      possible_win: "Possible payout: ",
      odds_warning: "Odds and profit % may change at the bookmaker — double-check before betting.",
      copied: "Copied: ",
      copy_failed: "Couldn't copy",
      no_h2h: "No head-to-head meetings in the database.",
      h2h_title: "Head-to-head (last ",
      h2h_total: "total meetings: ",
      draws_label: "Draws",
      recent_meeting_events: "Last meeting — what happened",
      loading: "Loading…",
      daily_limit_reached: "Today's limit reached",
      analyze_btn: "🔍 Analyze",
      error_prefix: "Error: ",
      no_fresh_news: "No fresh news in the last 24 hours.",
      no_news_data: "No data for the news digest yet.",
      news_meta_line:
        "News on popular matches — no predictions or percentages, just facts for your own analysis. Analysis (head-to-head) — once a day.",
      bankroll: "Bankroll",
      profit_threshold: "Profit threshold",
      profit_threshold_label: "Minimum profit % for an arb to be shown",
      profit_threshold_hint: "A fractional value is allowed, e.g. 0.6",
      period: "Period",
      horizon_24: "Within 24 hours",
      horizon_more24: "More than 24 hours",
      my_bookmakers: "My bookmakers",
      notifications: "Notifications",
      muted_label: "🔕 Silent mode (no sound)",
      save: "Save",
      err_bankroll_positive: "Bankroll must be greater than 0",
      err_threshold_negative: "Profit threshold can't be negative",
      err_need_horizon: "Keep at least one period",
      err_need_bookmaker: "Keep at least one bookmaker",
      settings_saved: "Settings saved ✅",
      save_failed: "Couldn't save: ",
      today: "Today",
      stat_found_vilki: "Arbs found",
      stat_avg_profit: "Average profit",
      stat_best_profit: "Best profit",
      all_time: "All time",
      admin_users: "Users",
      admin_total: "Total",
      admin_on_trial: "On trial",
      admin_has_access: "With access",
      admin_expired: "Access expired",
      admin_notifications_on: "Notifications on",
      admin_referred: "Via referral",
      admin_payments: "Payments",
      admin_units: " pcs · ",
      admin_no_payments: "No payments yet.",
      admin_sources: "Signup sources",
      admin_organic: "organic / untagged",
      admin_no_data: "No data.",
      admin_recent_users: "Recent users",
      admin_no_users: "No users yet.",
    },
  };

  const SUPPORTED_LANGS = ["ru", "en", "tg"];
  let currentLang = "ru";
  try {
    const stored = localStorage.getItem("lang");
    if (SUPPORTED_LANGS.includes(stored)) currentLang = stored;
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
    document.querySelectorAll(".lang-menu-item").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.lang === currentLang);
    });
    applyMainButtonLabel();
  }
  applyStaticLabels();

  function closeLangMenu() {
    langMenu.hidden = true;
  }

  langBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    haptic("light");
    langMenu.hidden = !langMenu.hidden;
  });

  langMenu.querySelectorAll(".lang-menu-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      const chosen = SUPPORTED_LANGS.includes(btn.dataset.lang) ? btn.dataset.lang : "ru";
      closeLangMenu();
      if (chosen === currentLang) return;
      haptic("light");
      currentLang = chosen;
      try {
        localStorage.setItem("lang", currentLang);
      } catch (e) {
        // Per-viewer convenience only -- fine if it doesn't persist.
      }
      applyStaticLabels();
      switchTab(currentTab);
    });
  });

  document.addEventListener("click", (e) => {
    if (!langMenu.hidden && !langMenu.contains(e.target) && e.target !== langBtn) closeLangMenu();
  });

  function haptic(style) {
    if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred(style || "light");
  }
  function hapticNotify(type) {
    if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred(type);
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

  // Order of tabs as swiping should cycle through them -- only the ones actually
  // visible right now (admin-tab stays `hidden` in the DOM for non-admins, so it's
  // naturally excluded without any extra bookkeeping).
  function visibleTabs() {
    return Array.from(document.querySelectorAll(".tab")).filter((b) => !b.hidden);
  }

  // ---------- swipe between tabs ----------
  // Horizontal drag on the content area moves to the next/prev tab, mirroring the tap
  // targets above it. Only acts once the gesture clearly reads as horizontal (delta-x
  // well past delta-y) so it never fights vertical scrolling or pull-to-refresh below.
  (function setupTabSwipe() {
    let startX = 0, startY = 0, tracking = false;
    content.addEventListener(
      "touchstart",
      (e) => {
        if (e.touches.length !== 1) return;
        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
        tracking = true;
      },
      { passive: true }
    );
    content.addEventListener(
      "touchend",
      (e) => {
        if (!tracking) return;
        tracking = false;
        const dx = e.changedTouches[0].clientX - startX;
        const dy = e.changedTouches[0].clientY - startY;
        if (Math.abs(dx) < 55 || Math.abs(dx) < Math.abs(dy) * 1.6) return;
        const tabs = visibleTabs();
        const idx = tabs.findIndex((b) => b.dataset.tab === currentTab);
        if (idx === -1) return;
        // Swipe left (dx<0) advances to the next tab, same direction as a horizontal
        // carousel; swipe right goes back -- matches the RTL-agnostic LTR tab order.
        const nextIdx = dx < 0 ? idx + 1 : idx - 1;
        if (nextIdx < 0 || nextIdx >= tabs.length) return;
        haptic("light");
        switchTab(tabs[nextIdx].dataset.tab);
      },
      { passive: true }
    );
  })();

  // ---------- pull-to-refresh ----------
  // No library -- just enough touch math to drag #content down against a rubber-band
  // resistance curve and fire a manual refresh past a threshold. Only engages when the
  // page is already scrolled to the very top, so it never hijacks normal scrolling.
  (function setupPullToRefresh() {
    const indicator = document.getElementById("ptr-indicator");
    const PTR_THRESHOLD = 64;
    let startY = 0, pulling = false, dragged = 0;

    function atTop() {
      return (window.scrollY || document.documentElement.scrollTop || 0) <= 0;
    }

    content.addEventListener(
      "touchstart",
      (e) => {
        if (e.touches.length !== 1 || !atTop()) return;
        startY = e.touches[0].clientY;
        pulling = true;
        dragged = 0;
      },
      { passive: true }
    );
    content.addEventListener(
      "touchmove",
      (e) => {
        if (!pulling) return;
        const dy = e.touches[0].clientY - startY;
        if (dy <= 0) {
          dragged = 0;
          content.style.transform = "";
          indicator.style.opacity = "0";
          return;
        }
        // Resistance curve -- each extra pixel of real drag moves the content less the
        // further it's already pulled, so it never feels like it's chasing the finger.
        dragged = Math.min(90, Math.sqrt(dy) * 6);
        content.style.transform = `translateY(${dragged}px)`;
        indicator.style.opacity = Math.min(1, dragged / PTR_THRESHOLD).toFixed(2);
        indicator.style.transform = `translateX(-50%) rotate(${dragged * 3}deg)`;
      },
      { passive: true }
    );
    content.addEventListener("touchend", () => {
      if (!pulling) return;
      pulling = false;
      content.style.transform = "";
      if (dragged >= PTR_THRESHOLD) {
        haptic("medium");
        indicator.classList.add("spin");
        loadTab(currentTab, true).finally(() => {
          indicator.classList.remove("spin");
          indicator.style.opacity = "0";
        });
      } else {
        indicator.style.opacity = "0";
      }
      dragged = 0;
    });
  })();

  // ---------- Telegram MainButton ----------
  // Mirrors the 🔄 icon in the topbar (kept as-is for anyone who doesn't notice the
  // native button) -- shows/labels itself per-language and always refreshes whichever
  // tab is currently open.
  if (tg && tg.MainButton) {
    tg.MainButton.onClick(() => loadTab(currentTab, true));
    tg.MainButton.show();
  }
  function applyMainButtonLabel() {
    if (tg && tg.MainButton) tg.MainButton.setText(t("refresh_btn").toUpperCase());
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
      // Subscription revoked mid-session (or the gate was just turned on) -- re-check
      // via /api/me rather than just toasting a raw 403, so the gate screen (with its
      // own subscribe/check buttons) takes over instead of leaving a broken tab up.
      if (e.status === 403) {
        try {
          const me = await api("/api/me");
          meCache = me;
          if (me.channel_required && !me.is_subscribed) {
            renderSubscriptionGate(me.channel_username);
            return;
          }
        } catch (e2) {
          // fall through to the generic toast below
        }
      }
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

  // Stale-while-revalidate cache: last successful /api/vilki payload, so re-opening the
  // Mini App paints real cards instantly (dimmed, see .card.stale) instead of an empty
  // skeleton, while the fresh fetch races in the background and replaces it.
  const VILKI_CACHE_KEY = "vilki_cache_v1";
  function readVilkiCache() {
    try {
      const raw = localStorage.getItem(VILKI_CACHE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }
  function writeVilkiCache(data) {
    try {
      localStorage.setItem(VILKI_CACHE_KEY, JSON.stringify(data));
    } catch (e) {
      // best-effort only
    }
  }

  // Quick filter (min-% + sport) applied client-side on top of the server's own
  // settings-driven filtering -- lets a user narrow the *current* fetch without a trip
  // to Настройки. Persisted per-viewer so it survives a reopen.
  const VILKI_FILTER_KEY = "vilki_filter_v1";
  function readVilkiFilter() {
    try {
      return JSON.parse(localStorage.getItem(VILKI_FILTER_KEY)) || { minPct: 0, sport: "" };
    } catch (e) {
      return { minPct: 0, sport: "" };
    }
  }
  function writeVilkiFilter(f) {
    try {
      localStorage.setItem(VILKI_FILTER_KEY, JSON.stringify(f));
    } catch (e) {
      // best-effort only
    }
  }

  // Tracks the most recently *shown* high-profit match so the success haptic fires only
  // once per new find, not on every 20s poll that still shows the same one.
  let lastHighProfitKey = null;

  function shareVilkaText(m) {
    const legs = m.legs.map((l) => `${l.outcome_name}: ${l.odds} @ ${l.bookmaker}`).join("\n");
    return `${m.game_emoji} ${m.team_a} vs ${m.team_b}\n${t("profit")}${m.profit_pct.toFixed(2)}%\n\n${legs}`;
  }

  function shareVilka(m) {
    haptic("light");
    const text = shareVilkaText(m);
    const url = `https://t.me/share/url?url=&text=${encodeURIComponent(text)}`;
    if (tg && tg.openTelegramLink) tg.openTelegramLink(url);
    else window.open(url, "_blank");
  }

  function renderVilkiCards(matches) {
    return matches.map((m, i) => {
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
          <div class="match-header">
            <span class="emoji-wiggle">${m.game_emoji}</span><span>${esc(m.game_label)}</span>
            <button type="button" class="share-btn" data-share-idx="${i}" title="${t("share_btn")}">📤</button>
          </div>
          <div class="match-teams"><span class="emoji-clash">⚔️</span> ${teamBadge(m.team_a_logo, m.team_a_flag)}${copyable(m.team_a)} vs ${teamBadge(m.team_b_logo, m.team_b_flag)}${copyable(m.team_b)}</div>
          ${m.start_time_label ? `<div class="match-time"><span class="emoji-tick">🕒</span> ${esc(m.start_time_label)}</div>` : ""}
          <div class="${profitClass}">${profitEmoji} ${t("profit")}${m.profit_pct.toFixed(2)}%</div>
          <div class="match-amount"><span class="emoji-bounce">💸</span> ${t("possible_win")}<span class="amount-value">${fmtMoney(m.profit_amount)}</span></div>
          <div class="legs">${legs}</div>
          <div class="odds-warning">⚠️ ${t("odds_warning")}</div>
        </div>`;
    }).join("");
  }

  function renderVilkiFilterBar(sports, filter) {
    const sportChips = sports
      .map(
        (s) =>
          `<button type="button" class="filter-chip${filter.sport === s.emoji ? " selected" : ""}" data-sport="${esc(s.emoji)}">${esc(s.emoji)} ${esc(s.label)}</button>`
      )
      .join("");
    return `
      <div class="filter-bar">
        <input type="number" class="filter-pct" id="filter-pct-input" placeholder="${t("filter_min_profit")}" min="0" step="0.1" value="${filter.minPct || ""}">
        <div class="filter-sport-row">
          <button type="button" class="filter-chip${filter.sport ? "" : " selected"}" data-sport="">${t("filter_all_sports")}</button>
          ${sportChips}
        </div>
      </div>`;
  }

  function applyVilkiFilter(matches, filter) {
    return matches.filter((m) => {
      if (filter.minPct && m.profit_pct < filter.minPct) return false;
      if (filter.sport && m.game_emoji !== filter.sport) return false;
      return true;
    });
  }

  function bindVilkiControls(allMatches, filter) {
    const pctInput = document.getElementById("filter-pct-input");
    if (pctInput) {
      pctInput.addEventListener("change", () => {
        filter.minPct = parseFloat(pctInput.value) || 0;
        writeVilkiFilter(filter);
        renderVilkiBody(allMatches, filter);
      });
    }
    document.querySelectorAll(".filter-chip[data-sport]").forEach((chip) => {
      chip.addEventListener("click", () => {
        haptic("light");
        filter.sport = chip.dataset.sport;
        writeVilkiFilter(filter);
        renderVilkiBody(allMatches, filter);
      });
    });
    content.querySelectorAll(".share-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const m = allMatches[Number(btn.dataset.shareIdx)];
        if (m) shareVilka(m);
      });
    });
  }

  // Renders the meta line + filter bar + filtered cards (or empty state) into
  // #content, and (re)binds their listeners -- shared by both the initial render and
  // every filter-change re-render so the two never drift apart.
  function renderVilkiBody(allMatches, filter, metaLineHtml, isStale) {
    const sportsSeen = new Map();
    allMatches.forEach((m) => {
      if (!sportsSeen.has(m.game_emoji)) sportsSeen.set(m.game_emoji, m.game_label);
    });
    const sports = Array.from(sportsSeen, ([emoji, label]) => ({ emoji, label }));
    const filtered = applyVilkiFilter(allMatches, filter);
    const filterBar = sports.length > 1 || filter.minPct ? renderVilkiFilterBar(sports, filter) : "";

    const body = filtered.length
      ? renderVilkiCards(filtered)
      : `<div class="empty-state"><span class="empty-icon">🔍</span>${t("no_vilki")}<br>${t("check_later")}</div>`;

    content.innerHTML = `${metaLineHtml || ""}${filterBar}<div${isStale ? ' class="stale"' : ""}>${body}</div>`;
    bindVilkiControls(allMatches, filter);

    // Success haptic once per *newly seen* high-profit find, not on every 20s poll
    // that still shows the same one.
    const topHigh = filtered.find((m) => m.profit_pct > HIGH_PROFIT_THRESHOLD);
    const key = topHigh ? `${topHigh.team_a}|${topHigh.team_b}|${topHigh.profit_pct}` : null;
    if (key && key !== lastHighProfitKey && !isStale) hapticNotify("success");
    if (!isStale) lastHighProfitKey = key;
  }

  async function renderVilki() {
    const filter = readVilkiFilter();

    // Stale-while-revalidate: paint the last cached fetch immediately (dimmed) so a
    // reopen never shows an empty skeleton if we already have something to show --
    // the real fetch below replaces it either way, success or failure.
    if (content.querySelector(".skeleton") || !content.innerHTML.trim()) {
      const cached = readVilkiCache();
      if (cached && cached.matches && cached.matches.length) {
        const staleMeta = `<div class="meta-line">${t("data_at")}${cached.checkedAt || "—"}</div>`;
        renderVilkiBody(cached.matches, filter, staleMeta, true);
      }
    }

    const data = await api("/api/vilki");
    const checkedAt = data.updated_at ? fmtMoscowTime(data.updated_at) + t("msk") : "—";
    writeVilkiCache({ matches: data.matches, checkedAt });

    if (!data.matches.length) {
      content.innerHTML = `
        <div class="meta-line">${t("data_at")}${checkedAt}</div>
        <div class="empty-state"><span class="empty-icon">🔍</span>${t("no_vilki")}<br>${t("check_later")}</div>`;
      lastHighProfitKey = null;
      return;
    }

    const metaLine = `<div class="meta-line">${t("found_vilki")}<b>${data.matches.length}</b> (${t("data_at").toLowerCase()}${checkedAt})</div>`;
    renderVilkiBody(data.matches, filter, metaLine, false);
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

  // A real logo image wins over the plain flag emoji when both are available (e.g.
  // football, once API-Football resolves it) -- onerror falls back to hiding a broken
  // image rather than showing the browser's broken-image icon (a CDN url can 404 or
  // change without notice, see team_logos.py's module docstring).
  function teamBadge(logoUrl, flag) {
    if (logoUrl) {
      return `<img class="team-logo" src="${esc(logoUrl)}" alt="" onerror="this.style.display='none'"> `;
    }
    if (flag) {
      return `<span class="team-flag">${flag}</span> `;
    }
    return "";
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

  // Proportional win/draw/loss bar with a legend underneath (colored dot + short name +
  // %) -- percentages computed explicitly (not flex:N tricks) so a 0-count segment
  // reliably collapses to 0 width instead of keeping a content-driven minimum.
  function renderH2hBar(h2h, teamA, teamB) {
    const total = h2h.total || 1;
    const pctA = (h2h.team_a_wins / total) * 100;
    const pctD = (h2h.draws / total) * 100;
    const pctB = (h2h.team_b_wins / total) * 100;
    const seg = (pct, cls) => (pct > 0 ? `<div class="h2h-bar-seg ${cls}" style="width:${pct}%"></div>` : "");
    const legendItem = (dotCls, label, pct) =>
      `<span class="h2h-legend-item"><i class="h2h-dot ${dotCls}"></i>${esc(label)} <b>${Math.round(pct)}%</b></span>`;
    return `
      <div class="h2h-bar">${seg(pctA, "h2h-bar-a")}${seg(pctD, "h2h-bar-draw")}${seg(pctB, "h2h-bar-b")}</div>
      <div class="h2h-legend">
        ${legendItem("h2h-dot-a", teamA, pctA)}
        ${legendItem("h2h-dot-draw", t("draws_label"), pctD)}
        ${legendItem("h2h-dot-b", teamB, pctB)}
      </div>`;
  }

  // Oldest-to-newest reading order (h2h.matches itself is newest-first) -- one colored
  // chip per match, result computed relative to teamA regardless of which side it
  // played on in that particular match.
  function h2hResultForTeamA(hm, teamA) {
    const aWasHome = hm.home === teamA;
    const scoreA = aWasHome ? hm.home_score : hm.away_score;
    const scoreB = aWasHome ? hm.away_score : hm.home_score;
    if (scoreA > scoreB) return "w";
    if (scoreA < scoreB) return "l";
    return "d";
  }

  function renderH2hForm(h2h, teamA) {
    const chips = h2h.matches
      .slice()
      .reverse()
      .map((hm) => {
        const result = h2hResultForTeamA(hm, teamA);
        const letter = result === "w" ? "W" : result === "l" ? "L" : "D";
        return `<span class="h2h-chip h2h-chip-${result}" title="${esc(hm.date)}: ${esc(hm.home)} ${hm.home_score}:${hm.away_score} ${esc(hm.away)}">${letter}</span>`;
      })
      .join("");
    return `<div class="h2h-form">${chips}</div>`;
  }

  // Horizontal 0-90' timeline with axis labels and a halftime guide -- markers
  // positioned by minute (capped at 90 so extra time doesn't push off the track),
  // grouped visually by which side each event belongs to (dot above the track for the
  // home-listed team's events, below for away).
  function renderH2hTimeline(events, homeTeam) {
    const markers = events
      .map((ev) => {
        const pct = Math.min(100, (ev.minute / 90) * 100);
        const side = ev.team === homeTeam ? "h2h-marker-top" : "h2h-marker-bottom";
        return `<div class="h2h-marker ${side}" style="left:${pct}%" title="${ev.minute}' ${esc(ev.player || "")} ${esc(ev.detail || "")} (${esc(ev.team || "")})">${ev.emoji}</div>`;
      })
      .join("");
    return `<div class="h2h-timeline">
        ${markers}
        <div class="h2h-timeline-track"></div>
        <div class="h2h-timeline-half"></div>
        <div class="h2h-timeline-axis"><span>0'</span><span>45'</span><span>90'</span></div>
      </div>`;
  }

  function renderH2hBlock(h2h, teamA, teamB) {
    if (!h2h) return `<div class="news-empty">${t("no_h2h")}</div>`;

    const recentEvents = h2h.recent_meeting_events || [];
    const eventsBlock = recentEvents.length
      ? `<div class="h2h-events">
          <div class="h2h-events-title">${t("recent_meeting_events")}</div>
          ${renderH2hTimeline(recentEvents, h2h.matches[0].home)}
          ${recentEvents
            .map(
              (ev) =>
                `<div class="h2h-event-row"><span class="h2h-event-minute">${ev.minute}'</span> ${ev.emoji} <b>${esc(ev.player || "")}</b> ${esc(ev.detail || "")} <span class="h2h-event-team">— ${esc(ev.team || "")}</span></div>`
            )
            .join("")}
        </div>`
      : "";

    return `<div class="h2h-block">
        <div class="h2h-title">📊 ${t("h2h_title")}${h2h.matches.length} <span class="h2h-title-total">/ ${t("h2h_total")}${h2h.total}</span>)</div>
        ${renderH2hBar(h2h, teamA, teamB)}
        ${renderH2hForm(h2h, teamA)}
        <div class="h2h-matches">${h2h.matches
          .map((hm) => {
            const result = h2hResultForTeamA(hm, teamA);
            const aWasHome = hm.home === teamA;
            const scoreHtml = aWasHome
              ? `<b class="h2h-score-${result}">${hm.home_score}</b>:<b class="h2h-score-${result === "w" ? "l" : result === "l" ? "w" : "d"}">${hm.away_score}</b>`
              : `<b class="h2h-score-${result === "w" ? "l" : result === "l" ? "w" : "d"}">${hm.home_score}</b>:<b class="h2h-score-${result}">${hm.away_score}</b>`;
            return `<div class="h2h-row"><i class="h2h-dot h2h-dot-${result}"></i><span class="h2h-date">${esc(hm.date)}</span> ${esc(hm.home)} ${scoreHtml} ${esc(hm.away)}</div>`;
          })
          .join("")}</div>
        ${eventsBlock}
      </div>`;
  }

  // Recent form (last 5 games, any opponent) + table position -- facts from a separate
  // free-tier source (football-data.org, only Western European top flights, see
  // bot/webapp/football_data.py), no verdict/percentage, same rule as the H2H block.
  // Renders nothing for a team football-data.org doesn't cover (progress is null) rather
  // than an empty/broken-looking block.
  function renderTeamProgressBlock(progress, fallbackName) {
    if (!progress) return "";
    const form = progress.form;
    const standings = progress.standings;
    const formChips = form
      ? `<div class="h2h-form">${form.form_string
          .split("")
          .map((r) => {
            const cls = r === "W" ? "w" : r === "L" ? "l" : "d";
            return `<span class="h2h-chip h2h-chip-${cls}">${r}</span>`;
          })
          .join("")}</div>`
      : "";
    const standingsLine = standings
      ? `<div class="progress-standings">${t("standings_position")}<b>${standings.position}</b> / ${standings.total_teams} · ${standings.points} ${t("standings_points")} (${standings.won}-${standings.draw}-${standings.lost})</div>`
      : "";
    if (!formChips && !standingsLine) return "";
    return `<div class="progress-block">
        <div class="progress-title">${esc(progress.team_name || fallbackName)} <span class="progress-competition">· ${esc(progress.competition_name || "")}</span></div>
        ${standingsLine}
        ${formChips}
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
      const progressHtml =
        renderTeamProgressBlock(result.progress_a, teamA) + renderTeamProgressBlock(result.progress_b, teamB);
      slot.innerHTML = progressHtml + renderH2hBlock(result.h2h, teamA, teamB);
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
      hapticNotify("success");
    } catch (e) {
      toast(t("save_failed") + e.message);
      hapticNotify("error");
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
            (u) => `<div class="admin-user-row">
              <div class="admin-user-top">
                <span>${u.chat_id}${u.acquisition_source ? ` <span class="admin-tag">${esc(u.acquisition_source)}</span>` : ""}</span>
                <span>${u.has_access ? "✅" : "⛔"}</span>
              </div>
              <div class="admin-user-period">${fmtDate(u.access_start)} — ${fmtDate(u.access_end)}</div>
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

  // ---------- Subscription gate ----------
  // Mirrors the button bot UI's SubscriptionGateMiddleware (see
  // handlers/commands.py) -- /api/me reports channel_required/is_subscribed, and every
  // OTHER endpoint independently 403s server-side if not subscribed (see
  // _require_subscribed in webapp/api.py), so this is UX, not the actual enforcement.

  function renderSubscriptionGate(channelUsername) {
    document.querySelectorAll(".tab").forEach((b) => (b.disabled = true));
    content.innerHTML = `
      <div class="empty-state gate-state">
        <span class="empty-icon">🔒</span>
        <div class="gate-title">${t("gate_title")}</div>
        <div class="gate-text">${t("gate_text")} <b>@${esc(channelUsername)}</b></div>
        <a class="save-btn gate-link-btn" href="https://t.me/${esc(channelUsername)}" target="_blank" rel="noopener">${t("gate_subscribe")}</a>
        <button class="gate-check-btn" id="gate-check-btn">${t("gate_check")}</button>
      </div>`;
    document.getElementById("gate-check-btn").addEventListener("click", async () => {
      haptic("light");
      const btn = document.getElementById("gate-check-btn");
      btn.disabled = true;
      btn.textContent = t("loading");
      try {
        const me = await api("/api/me");
        meCache = me;
        if (me.is_subscribed) {
          boot();
        } else {
          toast(t("gate_not_yet"));
          btn.disabled = false;
          btn.textContent = t("gate_check");
        }
      } catch (e) {
        toast(t("error_prefix") + e.message);
        btn.disabled = false;
        btn.textContent = t("gate_check");
      }
    });
  }

  // ---------- init ----------

  // One-off "Привет, <имя>!" toast on every entry, using the Telegram first name.
  function greet() {
    try {
      const u = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
      const name = (u && u.first_name ? String(u.first_name) : "").trim();
      toast(name ? `${t("hi")}, ${name}! 👋` : `${t("hi")}! 👋`);
    } catch (e) {
      // greeting is cosmetic -- never let it break boot
    }
  }

  async function boot() {
    document.querySelectorAll(".tab").forEach((b) => (b.disabled = false));
    let me;
    try {
      me = await api("/api/me");
    } catch (e) {
      toast(t("error_prefix") + e.message);
      content.innerHTML = skeletons.vilki;
      loadTab("vilki"); // best effort -- lets a real endpoint error surface normally
      hideSplash();
      return;
    }
    meCache = me;
    hideSplash();
    greet();
    if (me.channel_required && !me.is_subscribed) {
      renderSubscriptionGate(me.channel_username);
      return;
    }
    // Admin tab is hidden in the markup by default -- only unhidden once /api/me
    // confirms is_admin, so a non-admin never even sees the tab exist.
    if (me.is_admin) document.getElementById("admin-tab").hidden = false;

    content.innerHTML = skeletons.vilki;
    loadTab("vilki");
    if (!refreshTimer) {
      refreshTimer = setInterval(() => {
        if (currentTab === "vilki") loadTab("vilki");
      }, 20000);
    }
  }

  boot();
})();
