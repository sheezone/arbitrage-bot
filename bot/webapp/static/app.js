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
  const calcBtn = document.getElementById("calc-btn");
  const calcModal = document.getElementById("calc-modal");
  const calcBody = document.getElementById("calc-body");
  const analysisModal = document.getElementById("analysis-modal");
  const analysisBody = document.getElementById("analysis-body");
  const topbarTitleEl = document.getElementById("topbar-title");

  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    setTimeout(() => toastEl.classList.remove("show"), 2200);
  }

  let currentTab = "vilki";
  let swipeActive = false; // true while a horizontal tab-swipe owns the gesture
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
      disclaimer: "18+. Информационно-аналитический сервис сравнения коэффициентов лицензированных БК. Не букмекер, ставки не принимает, доход не гарантирует. Не является призывом к участию в азартных играх.",
      profit: "Расчётная разница: ",
      possible_win: "Расчётный результат: ",
      odds_warning: "Расчёт по текущим коэффициентам, не гарантия дохода. Котировки могут измениться, БК может не принять ставку. 18+.",
      copied: "Скопировано: ",
      copy_failed: "Не удалось скопировать",
      no_h2h: "Личных встреч в базе не нашлось.",
      h2h_title: "Личные встречи (последние ",
      h2h_total: "всего встреч: ",
      draws_label: "Ничьи",
      recent_meeting_events: "Последняя встреча — что произошло",
      form_title: "📈 Форма команд (последние матчи)",
      form_none: "Данных по форме нет.",
      implied_title: "Оценка шансов",
      implied_note: "по коэффициентам букмекеров — не прогноз",
      standing_label: "в таблице",
      pts_short: "очк.",
      goals_short: "мячи",
      tab_calc: "Калькулятор",
      calc_intro: "Расчёт распределения ставки на два исхода по коэффициентам двух БК. Ставки вы делаете сами; это не гарантия дохода.",
      calc_bankroll: "Банкролл (сумма на ставку)",
      calc_odds_a: "Коэффициент, исход 1",
      calc_odds_b: "Коэффициент, исход 2",
      calc_run: "Рассчитать",
      calc_is_arb: "✅ Это вилка",
      calc_not_arb: "⚠️ Не вилка — при таких коэффициентах убыток",
      calc_margin: "Расчётная разница",
      calc_stake_on: "Ставка на исход",
      calc_payout: "Возврат при любом исходе",
      calc_err_odds: "Коэффициенты должны быть больше 1",
            an_title: "Анализ матча",
      sort_pct: "% ↓",
      sort_time: "⏱ раньше",
      copy_all: "Копировать",
      copied_all: "Скопировано ✅",
      age_now: "обновлено {n} сек назад",
      age_min: "обновлено {n} мин назад",
      hint_swipe: "Листайте вкладки свайпом влево-вправо. 🧮 вверху — калькулятор.",
      hint_ok: "Понятно",
      hist_recent: "Недавние — могли измениться",
      hist_seen: "видели {n} назад",
      u_sec: "сек", u_min: "мин", u_hr: "ч",
an_news: "Новости (травмы, форма, дисквалификации)",
      an_lineups: "Составы и расстановка",
      an_lineups_none: "Составы станут известны ближе к началу матча. Структурные составы/травмы доступны только на платном источнике данных.",
      loading: "Загрузка…",
      daily_limit_reached: "Лимит на сегодня исчерпан",
      analyze_btn: "🔍 Проанализировать",
      error_prefix: "Ошибка: ",
      no_fresh_news: "За последние 24 часа свежих новостей не нашлось.",
      no_news_data: "Пока нет данных для новостной сводки.",
      news_meta_line:
        "Новости по популярным матчам — только факты (заголовки), без прогнозов. «Проанализировать» добавляет форму команд, личные встречи и оценку шансов по коэффициентам БК — 1 раз в день.",
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
      disclaimer: "18+. Хидмати иттилоотӣ-таҳлилии муқоисаи коэффисиентҳои БК-и иҷозатдор. Букмекер нест, ставка қабул намекунад, даромадро кафолат намедиҳад. Даъват ба бозии қиморӣ нест.",
      profit: "Фарқи ҳисобӣ: ",
      possible_win: "Натиҷаи ҳисобӣ: ",
      odds_warning: "Ҳисоб аз рӯи коэффисиентҳои ҷорӣ, кафолати даромад нест. Котировкаҳо тағйир ёфта метавонанд. 18+.",
      copied: "Нусхабардорӣ шуд: ",
      copy_failed: "Нусхабардорӣ ба амал наомад",
      no_h2h: "Дар пойгоҳи додаҳо вохӯриҳои шахсӣ ёфт нашуданд.",
      h2h_title: "Вохӯриҳои шахсӣ (охирин ",
      h2h_total: "ҳамагӣ вохӯриҳо: ",
      draws_label: "Баробарӣ",
      recent_meeting_events: "Вохӯрии охирин — чӣ рӯй дод",
      form_title: "📈 Шакли дастаҳо (бозиҳои охирин)",
      form_none: "Маълумоти шакл нест.",
      implied_title: "Арзёбии шансҳо",
      implied_note: "аз рӯи коэффисиентҳои букмекерҳо — на пешгӯӣ",
      standing_label: "дар ҷадвал",
      pts_short: "хол",
      goals_short: "тӯбҳо",
      tab_calc: "Ҳисобкунак",
      calc_intro: "Ҳисоби тақсими ставка ба ду натиҷа аз рӯи коэффисиентҳои ду БК. Ставкаро худатон мегузоред; ин кафолати даромад нест.",
      calc_bankroll: "Бонкролл (маблағ барои ставка)",
      calc_odds_a: "Коэффисиент, натиҷаи 1",
      calc_odds_b: "Коэффисиент, натиҷаи 2",
      calc_run: "Ҳисоб кардан",
      calc_is_arb: "✅ Ин вилка аст",
      calc_not_arb: "⚠️ Вилка нест — бо чунин коэффисиентҳо зарар",
      calc_margin: "Фарқи ҳисобӣ",
      calc_stake_on: "Ставка ба натиҷа",
      calc_payout: "Бозгашт дар ҳар натиҷа",
      calc_err_odds: "Коэффисиентҳо бояд аз 1 зиёд бошанд",
            an_title: "Таҳлили бозӣ",
      sort_pct: "% ↓",
      sort_time: "⏱ пештар",
      copy_all: "Нусха",
      copied_all: "Нусхабардорӣ шуд ✅",
      age_now: "{n} сон пеш нав шуд",
      age_min: "{n} дақ пеш нав шуд",
      hint_swipe: "Бахшҳоро бо свайп чап-рост варақ занед. 🧮 боло — ҳисобкунак.",
      hint_ok: "Фаҳмидам",
      hist_recent: "Наздикӣ — метавонанд тағйир ёбанд",
      hist_seen: "{n} пеш дида шуд",
      u_sec: "сон", u_min: "дақ", u_hr: "соат",
an_news: "Хабарҳо (ҷароҳатҳо, шакл, дисквалификатсия)",
      an_lineups: "Ҳайат ва ҷобаҷогузорӣ",
      an_lineups_none: "Ҳайатҳо наздик ба оғози бозӣ маълум мешаванд. Ҳайат/ҷароҳатҳои сохторӣ танҳо дар манбаи пулакӣ дастрасанд.",
      loading: "Боргирӣ…",
      daily_limit_reached: "Лимити имрӯза тамом шуд",
      analyze_btn: "🔍 Таҳлил кардан",
      error_prefix: "Хато: ",
      no_fresh_news: "Дар 24 соати охир хабарҳои нав ёфт нашуданд.",
      no_news_data: "Ҳоло барои хулосаи хабарҳо маълумот нест.",
      news_meta_line:
        "Хабарҳо оид ба бозиҳои маъмул — танҳо далелҳо (сарлавҳаҳо), бидуни пешгӯӣ. «Таҳлил» шакли дастаҳо, вохӯриҳои шахсӣ ва арзёбии шансро аз рӯи коэффисиентҳо илова мекунад — 1 бор дар рӯз.",
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
      disclaimer: "18+. An information and analytics service comparing licensed bookmakers' odds. Not a bookmaker, takes no bets, guarantees no income. Not a call to gamble.",
      profit: "Calc. margin: ",
      possible_win: "Calc. result: ",
      odds_warning: "A model figure at current odds, not guaranteed income. Odds may move, the bookmaker may refuse the bet. 18+.",
      copied: "Copied: ",
      copy_failed: "Couldn't copy",
      no_h2h: "No head-to-head meetings in the database.",
      h2h_title: "Head-to-head (last ",
      h2h_total: "total meetings: ",
      draws_label: "Draws",
      recent_meeting_events: "Last meeting — what happened",
      form_title: "📈 Team form (recent matches)",
      form_none: "No form data.",
      implied_title: "Chance estimate",
      implied_note: "from bookmaker odds — not a prediction",
      standing_label: "in the table",
      pts_short: "pts",
      goals_short: "goals",
      tab_calc: "Calculator",
      calc_intro: "Splits a stake across two outcomes by two bookmakers' odds. You place the bets yourself; this is not guaranteed income.",
      calc_bankroll: "Bankroll (total stake)",
      calc_odds_a: "Odds, outcome 1",
      calc_odds_b: "Odds, outcome 2",
      calc_run: "Calculate",
      calc_is_arb: "✅ It's an arb",
      calc_not_arb: "⚠️ Not an arb — a loss at these odds",
      calc_margin: "Calc. margin",
      calc_stake_on: "Stake on outcome",
      calc_payout: "Return on either outcome",
      calc_err_odds: "Odds must be greater than 1",
            an_title: "Match analysis",
      sort_pct: "% ↓",
      sort_time: "⏱ sooner",
      copy_all: "Copy",
      copied_all: "Copied ✅",
      age_now: "updated {n}s ago",
      age_min: "updated {n}m ago",
      hint_swipe: "Swipe left/right to change tabs. 🧮 up top is the calculator.",
      hint_ok: "Got it",
      hist_recent: "Recent — may have changed",
      hist_seen: "seen {n} ago",
      u_sec: "s", u_min: "m", u_hr: "h",
an_news: "News (injuries, form, suspensions)",
      an_lineups: "Lineups & formation",
      an_lineups_none: "Lineups appear closer to kickoff. Structured lineups/injuries are only available on a paid data source.",
      loading: "Loading…",
      daily_limit_reached: "Today's limit reached",
      analyze_btn: "🔍 Analyze",
      error_prefix: "Error: ",
      no_fresh_news: "No fresh news in the last 24 hours.",
      no_news_data: "No data for the news digest yet.",
      news_meta_line:
        "News on popular matches — facts only (headlines), no predictions. \"Analyze\" adds team form, head-to-head and an odds-implied chance estimate — once a day.",
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
    calcBtn.title = t("tab_calc");
    document.getElementById("calc-modal-title").textContent = t("tab_calc");
    document.getElementById("analysis-modal-title").textContent = t("an_title");
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

  // Shaped skeletons that echo the real layout so content doesn't jump when it loads.
  const skVilka = `<div class="sk-vilka">
      <div class="skeleton sk-line w40"></div>
      <div class="skeleton sk-line w75"></div>
      <div class="skeleton sk-pill"></div>
      <div class="skeleton sk-block"></div>
    </div>`;
  const skeletons = {
    vilki: skVilka.repeat(3),
    news: skVilka.repeat(2),
    settings: `<div class="skeleton sk-block" style="height:120px;margin-bottom:12px"></div>`.repeat(3),
    stats: `<div class="stat-grid"><div class="skeleton skeleton-stat"></div><div class="skeleton skeleton-stat"></div></div>`,
    admin: `<div class="skeleton sk-block" style="height:120px;margin-bottom:12px"></div>`.repeat(2),
  };

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.tab === currentTab) return;
      haptic("light");
      const tabs = visibleTabs();
      const from = tabs.findIndex((b) => b.dataset.tab === currentTab);
      const to = tabs.findIndex((b) => b.dataset.tab === btn.dataset.tab);
      animatedSwitch(btn.dataset.tab, to > from ? -1 : 1);
    });
  });
  refreshBtn.addEventListener("click", () => {
    haptic("light");
    loadTab(currentTab, true);
  });

  function switchTab(tab) {
    currentTab = tab;
    try {
      localStorage.setItem("last_tab", tab);
    } catch (e) {
      /* per-viewer convenience only */
    }
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    content.innerHTML = skeletons[tab] || "";
    loadTab(tab);
  }

  // Slide the current page off in `dir` (-1 = new page enters from the right / moving
  // forward, +1 = from the left), swap content while it's hidden, then ease the new
  // page in. Used by both the swipe gesture and a tab-bar tap so navigation always
  // feels like flipping. Transform + opacity together (partial travel) so the swap in
  // the middle is invisible and the motion stays light rather than a hard full-width
  // slam. One easing curve throughout; double-rAF between the swap and the slide-in so
  // the browser paints the off-screen start before animating from it.
  const SWIPE_EASE = "cubic-bezier(0.22, 1, 0.36, 1)";
  const REDUCE_MOTION = (() => {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  })();
  let animating = false;
  function animatedSwitch(tab, dir) {
    if (REDUCE_MOTION) {
      content.style.transform = "";
      content.style.opacity = "";
      if (tab !== currentTab) switchTab(tab);
      return;
    }
    if (animating || tab === currentTab) {
      if (tab === currentTab) {
        content.style.transition = `transform 0.22s ${SWIPE_EASE}, opacity 0.22s ${SWIPE_EASE}`;
        content.style.transform = "";
        content.style.opacity = "";
      }
      return;
    }
    animating = true;
    const w = content.clientWidth || window.innerWidth;
    const travel = Math.round(w * 0.55);
    content.style.transition = `transform 0.2s ${SWIPE_EASE}, opacity 0.18s linear`;
    content.style.transform = `translateX(${dir * -travel}px)`;
    content.style.opacity = "0";
    let swapped = false;
    const finish = () => {
      if (swapped) return;
      swapped = true;
      content.removeEventListener("transitionend", finish);
      switchTab(tab);
      content.style.transition = "none";
      content.style.transform = `translateX(${dir * travel}px)`;
      content.style.opacity = "0";
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          content.style.transition = `transform 0.28s ${SWIPE_EASE}, opacity 0.22s linear`;
          content.style.transform = "";
          content.style.opacity = "1";
          setTimeout(() => {
            content.style.transition = "";
            content.style.opacity = "";
            animating = false;
          }, 300);
        });
      });
    };
    content.addEventListener("transitionend", finish);
    setTimeout(finish, 230); // fallback if transitionend is missed
  }

  // Order of tabs as swiping should cycle through them -- only the ones actually
  // visible right now (admin-tab stays `hidden` in the DOM for non-admins, so it's
  // naturally excluded without any extra bookkeeping).
  function visibleTabs() {
    return Array.from(document.querySelectorAll(".tab")).filter((b) => !b.hidden);
  }

  // ---------- swipe between tabs ----------
  // The content area follows the finger horizontally (rubber-banding at the first/last
  // tab), and on release either flips to the neighbouring tab or snaps back -- so pages
  // feel paged through, not just tapped. `touch-action: pan-y` on #content keeps the
  // browser owning vertical scroll, so this never has to preventDefault.
  (function setupTabSwipe() {
    let startX = 0, startY = 0, startT = 0, axis = null, dxNow = 0;

    function canScrollXUnder(target) {
      let el = target;
      while (el && el !== content) {
        if (el.scrollWidth - el.clientWidth > 4) return true;
        el = el.parentElement;
      }
      return false;
    }

    content.addEventListener("touchstart", (e) => {
      if (e.touches.length !== 1 || animating || !calcModal.hidden || !analysisModal.hidden) return;
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      startT = Date.now();
      axis = null;
      dxNow = 0;
    }, { passive: true });

    content.addEventListener("touchmove", (e) => {
      if (startT === 0) return;
      const dx = e.touches[0].clientX - startX;
      const dy = e.touches[0].clientY - startY;
      if (axis === null) {
        if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
        // vertical, or starting inside something that scrolls sideways -> let it be
        if (Math.abs(dy) > Math.abs(dx) || canScrollXUnder(e.target)) {
          axis = "y";
          return;
        }
        axis = "x";
        swipeActive = true;
        content.style.transition = "none";
      }
      if (axis !== "x") return;
      const tabs = visibleTabs();
      const idx = tabs.findIndex((b) => b.dataset.tab === currentTab);
      const w = content.clientWidth || window.innerWidth;
      const atEnd = (dx < 0 && idx >= tabs.length - 1) || (dx > 0 && idx <= 0);
      dxNow = atEnd ? dx * 0.28 : dx; // rubber-band past the ends
      // Slight fade as the page is dragged, so it connects to the release animation.
      content.style.transform = `translateX(${dxNow}px)`;
      content.style.opacity = String(1 - Math.min(0.3, Math.abs(dxNow) / w));
    }, { passive: true });

    function endSwipe() {
      if (startT === 0) return;
      const wasX = axis === "x";
      const dx = dxNow;
      const elapsed = Date.now() - startT;
      startT = 0;
      axis = null;
      swipeActive = false;
      if (!wasX) return;
      const w = content.clientWidth || window.innerWidth;
      const flick = Math.abs(dx) > 45 && elapsed < 250;
      const tabs = visibleTabs();
      const idx = tabs.findIndex((b) => b.dataset.tab === currentTab);
      const dir = dx < 0 ? -1 : 1; // dx<0 -> go to next tab (enters from right)
      const nextIdx = dx < 0 ? idx + 1 : idx - 1;
      if ((Math.abs(dx) > w * 0.25 || flick) && nextIdx >= 0 && nextIdx < tabs.length) {
        haptic("light");
        animatedSwitch(tabs[nextIdx].dataset.tab, dir);
      } else {
        content.style.transition = `transform 0.24s ${SWIPE_EASE}, opacity 0.24s ${SWIPE_EASE}`;
        content.style.transform = "";
        content.style.opacity = "";
        setTimeout(() => (content.style.transition = ""), 260);
      }
    }
    content.addEventListener("touchend", endSwipe, { passive: true });
    content.addEventListener("touchcancel", endSwipe, { passive: true });
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
        if (!pulling || swipeActive) return;
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

  // Telegram MainButton (the big bottom "ОБНОВИТЬ") intentionally NOT used -- the 🔄
  // icon in the topbar covers refresh and the bottom bar just crowded the screen.
  if (tg && tg.MainButton) tg.MainButton.hide();
  function applyMainButtonLabel() {}

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

  // Rolling history of arbs the Mini App has seen -- a single /api/vilki fetch only
  // ever shows the *current* scan, so a vilka that dropped out of the latest cycle
  // would otherwise vanish. History keeps the last HISTORY_MAX by lastSeen, drops
  // anything not seen for HISTORY_TTL_MS, and is shown (dimmed, with an age label)
  // below the live list.
  const VILKI_HISTORY_KEY = "vilki_history_v2";
  const HISTORY_MAX = 60;
  const HISTORY_TTL_MS = 4 * 3600 * 1000;
  const HISTORY_SHOW = 15;

  function vilkiKey(m) {
    const legs = (m.legs || []).map((l) => l.outcome_name).sort().join("|");
    return `${m.game}|${m.team_a}|${m.team_b}|${legs}`;
  }
  function readVilkiHistory() {
    try {
      return JSON.parse(localStorage.getItem(VILKI_HISTORY_KEY)) || {};
    } catch (e) {
      return {};
    }
  }
  function mergeVilkiHistory(matches) {
    const now = Date.now();
    const hist = readVilkiHistory();
    matches.forEach((m) => {
      const k = vilkiKey(m);
      const prev = hist[k];
      hist[k] = { match: m, firstSeen: (prev && prev.firstSeen) || now, lastSeen: now };
    });
    let entries = Object.entries(hist).filter(([, v]) => now - v.lastSeen <= HISTORY_TTL_MS);
    entries.sort((a, b) => b[1].lastSeen - a[1].lastSeen);
    entries = entries.slice(0, HISTORY_MAX);
    try {
      localStorage.setItem(VILKI_HISTORY_KEY, JSON.stringify(Object.fromEntries(entries)));
    } catch (e) {
      // storage full / blocked -- history is a nice-to-have
    }
  }

  // Quick filter (min-% + sport) applied client-side on top of the server's own
  // settings-driven filtering -- lets a user narrow the *current* fetch without a trip
  // to Настройки. Persisted per-viewer so it survives a reopen.
  const VILKI_FILTER_KEY = "vilki_filter_v1";
  function readVilkiFilter() {
    try {
      const f = JSON.parse(localStorage.getItem(VILKI_FILTER_KEY)) || {};
      return { minPct: f.minPct || 0, sport: f.sport || "", sort: f.sort || "pct" };
    } catch (e) {
      return { minPct: 0, sport: "", sort: "pct" };
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

  function vilkaCopyText(m) {
    const legs = m.legs
      .map((l) => `${l.outcome_name}: ${l.odds} @ ${l.bookmaker} — ${fmtMoney(l.stake)}`)
      .join("\n");
    return `${m.game_emoji} ${m.team_a} vs ${m.team_b}\n${t("profit")}${m.profit_pct.toFixed(2)}% · ${fmtMoney(m.profit_amount)}\n\n${legs}`;
  }

  function renderVilkiCards(matches, opts) {
    const hist = opts && opts.historical;
    return matches
      .map((m, i) => {
        const isHigh = m.profit_pct > HIGH_PROFIT_THRESHOLD;
        const pillCls = isHigh ? "profit-pill high" : m.profit_pct >= 3 ? "profit-pill mid" : "profit-pill";
        const legs = m.legs
          .map((leg) => {
            const bk = leg.bookmaker_url
              ? `<a href="${esc(leg.bookmaker_url)}" target="_blank" rel="noopener">${esc(leg.bookmaker)}</a>`
              : esc(leg.bookmaker);
            return `<div class="leg">
              <div class="leg-name">${copyable(leg.outcome_name)}</div>
              <div class="leg-price"><b>${leg.odds}</b> · <span class="leg-bk">${bk}</span></div>
              <div class="leg-stk">${fmtMoney(leg.stake)}</div>
            </div>`;
          })
          .join("");
        const idxAttr = hist ? `data-hist-idx="${i}"` : "";
        const shareAttr = hist ? "" : `data-share-idx="${i}"`;
        const copyAttr = hist ? `data-histcopy-idx="${i}"` : `data-copyall-idx="${i}"`;
        return `
        <div class="card${isHigh ? " high-profit" : ""}${hist ? " card-hist" : ""}" ${idxAttr} style="animation-delay:${Math.min(i * 45, 360)}ms">
          <div class="card-top">
            <span class="emoji-wiggle">${m.game_emoji}</span>
            <span class="game-label">${esc(m.game_label)}</span>
            ${hist
              ? `<span class="hist-age">${esc(t("hist_seen").replace("{n}", shortAge(Date.now() - (m._lastSeen || Date.now()))))}</span>`
              : `<button type="button" class="share-btn" ${shareAttr} title="${t("share_btn")}">📤</button>`}
          </div>
          <div class="match-teams"><span class="emoji-clash">⚔️</span> ${teamBadge(m.team_a_logo, m.team_a_flag)}${copyable(m.team_a)} vs ${teamBadge(m.team_b_logo, m.team_b_flag)}${copyable(m.team_b)}</div>
          ${m.start_time_label ? `<div class="match-time"><span class="emoji-tick">🕒</span> ${esc(m.start_time_label)}</div>` : ""}
          <div><span class="${pillCls}">${isHigh ? "‼️" : "🚀"} ${m.profit_pct.toFixed(2)}%</span></div>
          <div class="card-payout">${t("possible_win")}<b>${fmtMoney(m.profit_amount)}</b></div>
          <div class="legs-tbl">${legs}</div>
          <div class="card-foot">
            <div class="odds-warning">⚠️ ${t("odds_warning")}</div>
            <button type="button" class="copyall-btn" ${copyAttr}>${t("copy_all")}</button>
          </div>
        </div>`;
      })
      .join("");
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
        <div class="filter-sort">
          <button type="button" class="sort-chip${filter.sort === "pct" ? " selected" : ""}" data-sort="pct">${t("sort_pct")}</button>
          <button type="button" class="sort-chip${filter.sort === "time" ? " selected" : ""}" data-sort="time">${t("sort_time")}</button>
        </div>
        <div class="filter-sport-row">
          <button type="button" class="filter-chip${filter.sport ? "" : " selected"}" data-sport="">${t("filter_all_sports")}</button>
          ${sportChips}
        </div>
      </div>`;
  }

  function applyVilkiFilter(matches, filter) {
    const out = matches.filter((m) => {
      if (filter.minPct && m.profit_pct < filter.minPct) return false;
      if (filter.sport && m.game_emoji !== filter.sport) return false;
      return true;
    });
    if (filter.sort === "time") {
      out.sort((a, b) => (a.start_time_utc || "9") .localeCompare(b.start_time_utc || "9") || b.profit_pct - a.profit_pct);
    } else {
      out.sort((a, b) => b.profit_pct - a.profit_pct);
    }
    return out;
  }

  function bindVilkiControls(allMatches, shownMatches, histMatches, filter) {
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
    document.querySelectorAll(".sort-chip[data-sort]").forEach((chip) => {
      chip.addEventListener("click", () => {
        haptic("light");
        filter.sort = chip.dataset.sort;
        writeVilkiFilter(filter);
        renderVilkiBody(allMatches, filter);
      });
    });
    content.querySelectorAll(".share-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const m = shownMatches[Number(btn.dataset.shareIdx)];
        if (m) shareVilka(m);
      });
    });
    content.querySelectorAll(".copyall-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const isHist = btn.dataset.histcopyIdx != null;
        const m = isHist
          ? (histMatches || [])[Number(btn.dataset.histcopyIdx)]
          : shownMatches[Number(btn.dataset.copyallIdx)];
        if (!m) return;
        copyToClipboard(vilkaCopyText(m)).then((ok) => {
          haptic(ok ? "light" : "medium");
          toast(ok ? t("copied_all") : t("copy_failed"));
        });
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
    const filterBar = renderVilkiFilterBar(sports, filter);

    // Recent arbs from history that aren't in the current live scan -- shown dimmed
    // below, marked "видели N назад". Not on a stale (cached) render, to avoid mixing
    // two flavours of "old".
    let histMatches = [];
    if (!isStale) {
      const liveKeys = new Set(allMatches.map(vilkiKey));
      const hist = readVilkiHistory();
      histMatches = Object.values(hist)
        .filter((v) => !liveKeys.has(vilkiKey(v.match)))
        .map((v) => ({ ...v.match, _lastSeen: v.lastSeen }));
      histMatches = applyVilkiFilter(histMatches, filter)
        .sort((a, b) => b._lastSeen - a._lastSeen)
        .slice(0, HISTORY_SHOW);
    }

    const body = filtered.length
      ? renderVilkiCards(filtered)
      : `<div class="empty-state"><span class="empty-icon">🔍</span>${t("no_vilki")}<br>${t("check_later")}
           <button type="button" class="empty-cta" id="empty-refresh">🔄 ${t("refresh_btn")}</button></div>`;

    const histBlock = histMatches.length
      ? `<div class="hist-divider">${t("hist_recent")}</div><div class="hist-wrap">${renderVilkiCards(histMatches, { historical: true })}</div>`
      : "";

    content.innerHTML = `${metaLineHtml || ""}${filterBar}<div${isStale ? ' class="stale"' : ""}>${body}</div>${histBlock}<div class="meta-line disclaimer">${t("disclaimer")}</div>`;
    bindVilkiControls(allMatches, filtered, histMatches, filter);
    const er = document.getElementById("empty-refresh");
    if (er) er.addEventListener("click", () => { haptic("light"); loadTab("vilki", true); });

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
    mergeVilkiHistory(data.matches);
    lastVilkiFetchTs = Date.now();

    if (!data.matches.length) {
      content.innerHTML = `
        <div class="meta-line">${t("data_at")}${checkedAt}<span class="data-age" id="data-age"></span></div>
        <div class="empty-state"><span class="empty-icon">🔍</span>${t("no_vilki")}<br>${t("check_later")}
          <button type="button" class="empty-cta" id="empty-refresh">🔄 ${t("refresh_btn")}</button></div>`;
      const er = document.getElementById("empty-refresh");
      if (er) er.addEventListener("click", () => { haptic("light"); loadTab("vilki", true); });
      lastHighProfitKey = null;
      return;
    }

    const metaLine = `<div class="meta-line">${t("found_vilki")}<b>${data.matches.length}</b> (${t("data_at").toLowerCase()}${checkedAt})<span class="data-age" id="data-age"></span></div>`;
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

  // Clean vertical timeline: a rail on the left, one row per event with a minute pill,
  // an icon on the rail, then player + team. Home-team events sit on an accent dot,
  // away on a red one, so a match can be read at a glance without a cramped 0-90 track.
  function h2hEventIcon(ev) {
    const d = (ev.detail || "").toLowerCase();
    if (d.includes("red")) return "🟥";
    if (d.includes("own")) return "🥅";
    if (d.includes("penalt") && ev.emoji === "⚽") return "🎯";
    return ev.emoji || "▪️";
  }

  // API-Football sends event `detail` as an English enum -- localise it to the Mini
  // App's language. Keyword-matched (order matters: "own goal" before "goal", etc.).
  const EVENT_DETAIL = {
    ru: {
      own_goal: "Автогол", missed_pen: "Нереализованный пенальти", penalty: "Гол с пенальти",
      goal: "Гол", "2yellow": "Вторая жёлтая", yellow: "Жёлтая карточка", red: "Красная карточка",
      sub: "Замена", goal_cancelled: "Гол отменён (VAR)", goal_confirmed: "Гол засчитан (VAR)",
      pen_confirmed: "Пенальти назначен (VAR)", pen_cancelled: "Пенальти отменён (VAR)",
      card_upgrade: "Карточка изменена (VAR)",
    },
    en: {
      own_goal: "Own goal", missed_pen: "Missed penalty", penalty: "Penalty goal", goal: "Goal",
      "2yellow": "Second yellow", yellow: "Yellow card", red: "Red card", sub: "Substitution",
      goal_cancelled: "Goal cancelled (VAR)", goal_confirmed: "Goal confirmed (VAR)",
      pen_confirmed: "Penalty confirmed (VAR)", pen_cancelled: "Penalty cancelled (VAR)",
      card_upgrade: "Card upgraded (VAR)",
    },
    tg: {
      own_goal: "Голи худӣ", missed_pen: "Пеналтии аз даст рафта", penalty: "Гол аз пеналти",
      goal: "Гол", "2yellow": "Зарди дуюм", yellow: "Корти зард", red: "Корти сурх", sub: "Иваз",
      goal_cancelled: "Гол бекор шуд (VAR)", goal_confirmed: "Гол қабул шуд (VAR)",
      pen_confirmed: "Пеналти таъин шуд (VAR)", pen_cancelled: "Пеналти бекор шуд (VAR)",
      card_upgrade: "Корт тағйир ёфт (VAR)",
    },
  };
  const _EVENT_DETAIL_KEYS = [
    ["own goal", "own_goal"], ["missed penalty", "missed_pen"], ["penalty confirmed", "pen_confirmed"],
    ["penalty cancelled", "pen_cancelled"], ["goal cancelled", "goal_cancelled"],
    ["goal confirmed", "goal_confirmed"], ["card upgrade", "card_upgrade"],
    ["second yellow", "2yellow"], ["yellow card", "yellow"], ["red card", "red"],
    ["substitution", "sub"], ["penalty", "penalty"], ["normal goal", "goal"], ["goal", "goal"],
  ];
  function evDetail(raw) {
    if (!raw) return "";
    const d = raw.toLowerCase();
    const M = EVENT_DETAIL[currentLang] || EVENT_DETAIL.ru;
    for (const [kw, key] of _EVENT_DETAIL_KEYS) if (d.includes(kw)) return M[key] || raw;
    return raw;
  }
  function renderH2hTimeline(events, homeTeam) {
    const rows = events
      .slice()
      .sort((a, b) => (a.minute || 0) - (b.minute || 0))
      .map((ev) => {
        const home = ev.team === homeTeam;
        return `<div class="tl-row">
            <span class="tl-min">${ev.minute || 0}'</span>
            <span class="tl-node ${home ? "tl-home" : "tl-away"}">${h2hEventIcon(ev)}</span>
            <span class="tl-body"><b>${esc(ev.player || "")}</b>${
              ev.detail ? ` <span class="tl-detail">${esc(evDetail(ev.detail))}</span>` : ""
            }<span class="tl-team">${esc(ev.team || "")}</span></span>
          </div>`;
      })
      .join("");
    return `<div class="tl">${rows}</div>`;
  }

  // Market-implied win split -- (1/odds) per outcome, normalised. Labelled as coming
  // from bookmaker odds, not presented as an independent prediction.
  function renderImpliedBar(implied, teamA, teamB) {
    if (!implied || !implied.outcomes || !implied.outcomes.length) return "";
    const by = {};
    implied.outcomes.forEach((o) => (by[o.outcome] = o.prob));
    let rows;
    if (by[teamA] != null && by[teamB] != null) {
      rows = [[teamA, by[teamA], "h2h-bar-a", "h2h-dot-a"], [teamB, by[teamB], "h2h-bar-b", "h2h-dot-b"]];
    } else {
      rows = implied.outcomes.map((o, i) => [
        o.outcome, o.prob, i % 2 ? "h2h-bar-b" : "h2h-bar-a", i % 2 ? "h2h-dot-b" : "h2h-dot-a",
      ]);
    }
    const bar = rows.map(([, p, cls]) => `<div class="h2h-bar-seg ${cls}" style="width:${(p * 100).toFixed(1)}%"></div>`).join("");
    const legend = rows
      .map(([name, p, , dot]) => `<span class="h2h-legend-item"><i class="h2h-dot ${dot}"></i>${esc(name)} <b>${Math.round(p * 100)}%</b></span>`)
      .join("");
    return `<div class="impl-block">
        <div class="impl-sub">🎲 ${t("implied_title")}</div>
        <div class="h2h-bar">${bar}</div>
        <div class="h2h-legend">${legend}</div>
        <div class="impl-note">${t("implied_note")}</div>
      </div>`;
  }

  function renderTeamForm(form) {
    if (!form) return "";
    const chip = (r) => `h2h-chip-${r === "W" ? "w" : r === "L" ? "l" : "d"}`;
    const chips = (form.matches || [])
      .slice()
      .reverse() // matches are newest-first; show oldest→newest like the H2H strip
      .map((m) => {
        const ha = m.home_away === "home" ? "🏠" : m.home_away === "away" ? "✈️" : "";
        return `<span class="h2h-chip ${chip(m.result)}" title="${esc(`${m.date} ${ha} ${m.gf}:${m.ga} ${m.opponent}`)}">${m.result}</span>`;
      })
      .join("");
    const st = form.standing;
    const stLine =
      st && st.rank
        ? `<div class="form-standing">🏆 ${st.rank} ${t("standing_label")} · ${st.points != null ? st.points : "—"} ${t("pts_short")}</div>`
        : "";
    const rows = (form.matches || [])
      .map((m) => {
        const d = m.result === "W" ? "w" : m.result === "L" ? "l" : "d";
        const ha = m.home_away === "home" ? "🏠" : m.home_away === "away" ? "✈️" : "";
        return `<div class="h2h-row"><i class="h2h-dot h2h-dot-${d === "w" ? "a" : d === "l" ? "b" : "draw"}"></i><span class="h2h-date">${esc(m.date)}</span> ${ha} <b>${m.gf}:${m.ga}</b> ${esc(m.opponent)}</div>`;
      })
      .join("");
    return `<div class="form-team">
        <div class="form-team-head"><b>${esc(form.team)}</b><span class="h2h-form">${chips}</span></div>
        <div class="form-rec">${form.wins}–${form.draws}–${form.losses} · ${t("goals_short")} ${form.gf}:${form.ga}</div>
        ${stLine}
        <div class="h2h-matches">${rows}</div>
      </div>`;
  }

  function renderFormBlock(result, teamA, teamB) {
    const impl = renderImpliedBar(result.implied, teamA, teamB);
    const fa = renderTeamForm(result.form_a);
    const fb = renderTeamForm(result.form_b);
    if (!impl && !fa && !fb) return "";
    return `<div class="h2h-block form-block">
        <div class="h2h-title">${t("form_title")}</div>
        ${impl}
        ${fa || `<div class="news-empty">${t("form_none")}</div>`}
        ${fb}
      </div>`;
  }

  function renderH2hBlock(h2h, teamA, teamB) {
    if (!h2h) return `<div class="news-empty">${t("no_h2h")}</div>`;

    const recentEvents = h2h.recent_meeting_events || [];
    const eventsBlock = recentEvents.length
      ? `<div class="h2h-events">
          <div class="h2h-events-title">${t("recent_meeting_events")}</div>
          ${renderH2hTimeline(recentEvents, h2h.matches[0].home)}
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

  function closeAnalysis() {
    analysisModal.hidden = true;
  }
  document.getElementById("analysis-close").addEventListener("click", closeAnalysis);
  analysisModal.querySelector(".modal-backdrop").addEventListener("click", closeAnalysis);

  // Draws an 11-a-side pitch with player dots laid out by `formation` ("4-3-3").
  // Only used when lineup data is actually present (no free football source has it
  // pre-match yet -- see /api/analysis); the section is hidden otherwise.
  function renderPitch(lineup, flip) {
    if (!lineup || !Array.isArray(lineup.starters) || lineup.starters.length < 11) return "";
    const rows = String(lineup.formation || "4-3-3").split("-").map((n) => parseInt(n, 10)).filter(Boolean);
    let players = lineup.starters.slice();
    const gk = players.shift();
    const bands = [[gk]];
    rows.forEach((count) => bands.push(players.splice(0, count)));
    const n = bands.length;
    const dots = bands
      .map((band, bi) => {
        const y = flip ? 100 - ((bi + 0.5) / n) * 100 : ((bi + 0.5) / n) * 100;
        return band
          .map((p, pi) => {
            const x = ((pi + 0.5) / band.length) * 100;
            const label = esc((p && (p.shirt || p.name)) ? String(p.shirt || "").trim() || "" : "");
            const name = esc(p && p.name ? p.name.split(" ").slice(-1)[0] : "");
            return `<div class="pitch-player" style="left:${x}%;top:${y}%">
                <span class="pitch-dot">${label}</span><span class="pitch-name">${name}</span>
              </div>`;
          })
          .join("");
      })
      .join("");
    return `<div class="pitch"><div class="pitch-lines"></div>${dots}</div>`;
  }

  function renderNewsBlock(news) {
    if (!news || !news.length) return `<div class="news-empty">${t("no_fresh_news")}</div>`;
    return (
      `<div class="an-news">` +
      news
        .slice(0, 8)
        .map(
          (h) => `<div class="news-row">
            <a href="${esc(h.link)}" target="_blank" rel="noopener">${esc(h.title)}</a>
            <div class="news-meta">${esc(h.source || "")}${h.published_at ? " · " + fmtNewsTime(h.published_at) : ""}</div>
          </div>`
        )
        .join("") +
      `</div>`
    );
  }

  function renderAnalysis(result, teamA, teamB) {
    const secForm = renderFormBlock(result, teamA, teamB);
    const secH2h = renderH2hBlock(result.h2h, teamA, teamB);
    const pitchA = renderPitch(result.lineup_a, false);
    const pitchB = renderPitch(result.lineup_b, true);
    const lineupSec = pitchA || pitchB
      ? `<div class="h2h-block"><div class="h2h-title">📋 ${t("an_lineups")}</div>
           <div class="pitch-wrap">${pitchB}${pitchA}</div></div>`
      : `<div class="h2h-block"><div class="h2h-title">📋 ${t("an_lineups")}</div>
           <div class="news-empty">${t("an_lineups_none")}</div></div>`;
    return `
      <div class="an-head">
        <b>${esc(teamA)}</b> <span class="an-vs">vs</span> <b>${esc(teamB)}</b>
      </div>
      ${secForm}
      ${secH2h}
      <div class="h2h-block"><div class="h2h-title">📰 ${t("an_news")}</div>${renderNewsBlock(result.news)}</div>
      ${lineupSec}
    `;
  }

  // Gated to 1 analysis/day/user (see /api/analysis) -- deliberately not fetched for
  // all 3 matches up front, only for the one the user actually clicks on. Opens a
  // full-screen modal.
  async function onAnalyzeClick(btn) {
    const teamA = btn.dataset.teamA;
    const teamB = btn.dataset.teamB;
    haptic("light");
    analysisBody.innerHTML = `<div class="sk-analysis">
        <div class="skeleton sk-line w40"></div>
        <div class="skeleton sk-block"></div>
        <div class="skeleton sk-block"></div>
        <div class="skeleton sk-block"></div>
      </div>`;
    analysisModal.hidden = false;
    try {
      const result = await api(`/api/analysis?team_a=${encodeURIComponent(teamA)}&team_b=${encodeURIComponent(teamB)}`);
      analysisBody.innerHTML = renderAnalysis(result, teamA, teamB);
      btn.remove();
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
      closeAnalysis();
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

      // Full analysis is fetched only on click, gated to 1/day/user, and opens in a
      // full-screen modal -- see /api/analysis and onAnalyzeClick above.
      let analyzeBlock = "";
      if (m.can_analyze) {
        analyzeBlock = me.analysis_available
          ? `<button type="button" class="analyze-btn" data-team-a="${esc(m.team_a)}" data-team-b="${esc(m.team_b)}">${t("analyze_btn")}</button>`
          : `<button type="button" class="analyze-btn" disabled>${t("daily_limit_reached")}</button>`;
      }

      return `
        <div class="card" style="animation-delay:${Math.min(i * 45, 360)}ms">
          <div class="match-header"><span class="emoji-wiggle">${m.game_emoji}</span><span>${esc(m.game_label)}</span></div>
          <div class="match-teams"><span class="emoji-clash">⚔️</span> ${copyable(m.team_a)} vs ${copyable(m.team_b)}</div>
          ${m.start_time_label ? `<div class="match-time"><span class="emoji-tick">🕒</span> ${esc(m.start_time_label)}</div>` : ""}
          <div class="news-list">${headlines}</div>
          ${analyzeBlock}
        </div>`;
    });

    content.innerHTML = `
      <div class="meta-line">${t("news_meta_line")}</div>
      ${cards.join("")}`;
  }

  // ---------- Калькулятор ----------
  // Pure client-side, no API -- mirrors the bot's _calculator_result_view math
  // (bot/core/arbitrage.py): arb_ratio = 1/oddsA + 1/oddsB; margin% = (1/ratio - 1)*100;
  // stake_i = bankroll * (1/odds_i) / ratio. Last inputs persisted per viewer.
  const CALC_KEY = "calc_v1";
  function readCalc() {
    try {
      return JSON.parse(localStorage.getItem(CALC_KEY)) || {};
    } catch (e) {
      return {};
    }
  }
  function writeCalc(v) {
    try {
      localStorage.setItem(CALC_KEY, JSON.stringify(v));
    } catch (e) {
      /* best-effort */
    }
  }

  function renderCalc() {
    const saved = readCalc();
    calcBody.innerHTML = `
      <div class="meta-line">${t("calc_intro")}</div>
      <div class="field">
        <label>${t("calc_bankroll")}</label>
        <input type="number" id="calc-bankroll" min="1" step="1" value="${saved.bankroll || 1000}">
        <div class="preset-row">${BANKROLL_PRESETS.map((p) => `<button type="button" class="preset-btn" data-calc-preset="${p}">${p}</button>`).join("")}</div>
      </div>
      <div class="field">
        <label>${t("calc_odds_a")}</label>
        <input type="number" id="calc-odds-a" min="1" step="0.01" placeholder="2.10" value="${saved.oddsA || ""}">
      </div>
      <div class="field">
        <label>${t("calc_odds_b")}</label>
        <input type="number" id="calc-odds-b" min="1" step="0.01" placeholder="2.05" value="${saved.oddsB || ""}">
      </div>
      <button class="save-btn" id="calc-run">${t("calc_run")}</button>
      <div id="calc-result"></div>
    `;

    calcBody.querySelectorAll("[data-calc-preset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        haptic("light");
        calcBody.querySelector("#calc-bankroll").value = btn.dataset.calcPreset;
      });
    });
    calcBody.querySelector("#calc-run").addEventListener("click", runCalc);
  }

  function openCalc() {
    haptic("light");
    renderCalc();
    calcModal.hidden = false;
  }
  function closeCalc() {
    calcModal.hidden = true;
  }
  calcBtn.addEventListener("click", openCalc);
  document.getElementById("calc-close").addEventListener("click", closeCalc);
  calcModal.querySelector(".modal-backdrop").addEventListener("click", closeCalc);

  function runCalc() {
    const bankroll = parseFloat(document.getElementById("calc-bankroll").value);
    const oddsA = parseFloat(document.getElementById("calc-odds-a").value);
    const oddsB = parseFloat(document.getElementById("calc-odds-b").value);
    const out = document.getElementById("calc-result");

    if (!bankroll || bankroll <= 0) {
      toast(t("err_bankroll_positive"));
      return;
    }
    if (!(oddsA > 1) || !(oddsB > 1)) {
      toast(t("calc_err_odds"));
      return;
    }
    haptic("light");
    writeCalc({ bankroll, oddsA, oddsB });

    const ratio = 1 / oddsA + 1 / oddsB;
    const marginPct = (1 / ratio - 1) * 100;
    const stakeA = (bankroll * (1 / oddsA)) / ratio;
    const stakeB = (bankroll * (1 / oddsB)) / ratio;
    const payout = stakeA * oddsA; // equal for either outcome by construction
    const isArb = marginPct > 0;

    out.innerHTML = `
      <div class="h2h-block">
        <div class="h2h-title">${isArb ? t("calc_is_arb") : t("calc_not_arb")}</div>
        <div class="calc-margin ${isArb ? "pos" : "neg"}">${t("calc_margin")}: <b>${marginPct.toFixed(2)}%</b></div>
        <div class="h2h-matches">
          <div class="h2h-row"><span class="h2h-date">${t("calc_stake_on")} 1</span> <b>${fmtMoney(stakeA)}</b> @ ${oddsA}</div>
          <div class="h2h-row"><span class="h2h-date">${t("calc_stake_on")} 2</span> <b>${fmtMoney(stakeB)}</b> @ ${oddsB}</div>
        </div>
        <div class="calc-payout">${t("calc_payout")}: <b>${fmtMoney(payout)}</b> (${fmtMoney(payout - bankroll)})</div>
        <div class="impl-note">${t("odds_warning")}</div>
      </div>`;
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
      <div class="settings-section">
        <div class="section-title">${t("bankroll")}</div>
        <div class="field">
          <input type="number" id="bankroll-input" value="${me.bankroll}" min="1" step="1">
          <div class="preset-row">${presetRow}</div>
        </div>
      </div>

      <div class="settings-section">
        <div class="section-title">${t("profit_threshold")}</div>
        <div class="field">
          <label>${t("profit_threshold_label")}</label>
          <input type="number" id="threshold-input" value="${me.min_profit_pct}" min="0" step="0.1" placeholder="0.6">
          <div class="field-hint">${t("profit_threshold_hint")}</div>
        </div>
      </div>

      <div class="settings-section">
        <div class="section-title">${t("period")}</div>
        ${horizonRows}
      </div>

      <div class="settings-section">
        <div class="section-title">${t("my_bookmakers")}</div>
        <div class="chip-grid" id="bk-grid">${bkChips}</div>
      </div>

      <div class="settings-section">
        <div class="section-title">${t("notifications")}</div>
        <div class="toggle-row">
          <span>${t("muted_label")}</span>
          <label class="switch">
            <input type="checkbox" id="muted-input" ${me.muted ? "checked" : ""}>
            <span class="slider"></span>
          </label>
        </div>
      </div>

      <div class="save-bar"><button class="save-btn" id="save-settings-btn">${t("save")}</button></div>
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

    // Reopen on the tab the user left, if it still exists / is allowed.
    let startTab = "vilki";
    try {
      const saved = localStorage.getItem("last_tab");
      const ok = Array.from(document.querySelectorAll(".tab")).some(
        (b) => !b.hidden && b.dataset.tab === saved
      );
      if (ok) startTab = saved;
    } catch (e) {
      /* default vilki */
    }
    currentTab = startTab;
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === startTab));
    content.innerHTML = skeletons[startTab] || "";
    loadTab(startTab);
    if (!refreshTimer) {
      refreshTimer = setInterval(() => {
        if (currentTab === "vilki") loadTab("vilki");
      }, 20000);
    }
    startDataAgeTicker();
    maybeShowHint();
  }

  // "обновлено N назад" -- refreshes the #data-age span every few seconds so the
  // freshness of the Вилки list is always visible without another fetch.
  let lastVilkiFetchTs = 0;
  function ageText(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return t("age_now").replace("{n}", s);
    const m = Math.round(s / 60);
    return t("age_min").replace("{n}", m);
  }
  function shortAge(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return `${s} ${t("u_sec")}`;
    const m = Math.round(s / 60);
    if (m < 60) return `${m} ${t("u_min")}`;
    return `${Math.round(m / 60)} ${t("u_hr")}`;
  }
  function startDataAgeTicker() {
    setInterval(() => {
      const el = document.getElementById("data-age");
      if (el && lastVilkiFetchTs) el.textContent = " · " + ageText(Date.now() - lastVilkiFetchTs);
    }, 5000);
  }

  function maybeShowHint() {
    try {
      if (localStorage.getItem("hint_v1")) return;
    } catch (e) {
      return;
    }
    setTimeout(showHintBar, 1600); // let the greeting toast clear first
  }
  function showHintBar() {
    const bar = document.createElement("div");
    bar.className = "hint-bar";
    bar.innerHTML = `<span>${esc(t("hint_swipe"))}</span><button type="button">${esc(t("hint_ok"))}</button>`;
    bar.querySelector("button").addEventListener("click", () => {
      bar.remove();
      try {
        localStorage.setItem("hint_v1", "1");
      } catch (e) {
        /* ignore */
      }
    });
    document.body.appendChild(bar);
    setTimeout(() => bar.isConnected && bar.remove(), 12000);
  }

  boot();
})();
