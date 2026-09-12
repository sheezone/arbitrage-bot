# Claude memory handoff — Букмекерки arbitrage bot

Перенесите этот файл на MacBook (AirDrop / облако / почта — НЕ через git, тут есть
чувствительные детали типа chat_id и IP сервера) и в начале первой сессии на Mac
скажите Claude: "прочитай этот файл и запомни" — он сохранит это в свою память там.

---

## Как отвечать пользователю

Не проговаривай ход мыслей/процесс исследования в чате — только результат и действия,
коротко. Пользователь явно просил не тратить поинты на нарратив рассуждений.

---

## Проект: Telegram-бот арбитражных ставок (@Lineyka111_bot)

aiogram 3.x бот, ищет арбитраж ("вилки") между букмекерами на CS2/Dota2/LoL/Valorant/
Tennis/Basketball (2-way markets). Код в `E:\букмекерки` (Windows) →
`https://github.com/sheezone/arbitrage-bot` (публичный репо, push по HTTPS).

### Деплой (прод, VPS)
- VDSina VPS, IP `46.17.99.40`, hostname `v3201295.hosted-by-vdsina.ru`, Ubuntu, 11GB RAM
  (не 1GB, старая заметка была неверной — не экономь на фичах из-за RAM без проверки `free -h`).
- systemd-сервис `arbitrage-bot.service`, рабочая директория `/root/arbitrage-bot`.
- SSH-алиас `bukmekerki-new` (настроен в `~/.ssh/config`), работает по 443 порту, иногда
  подвисает — просто повторить.
- Деплой: `git pull && systemctl restart arbitrage-bot && sleep 2 && systemctl status
  arbitrage-bot --no-pager`, затем проверка логов на ошибки (`journalctl -u arbitrage-bot -n 30`),
  игнорируя известный шум: `OddsPapi 429`, `Marathon`, `TelegramForbiddenError: blocked by the user`.
- `.env` на сервере НЕ в git — новые переменные из `.env.example` нужно вручную добавлять
  на сервер через `echo "KEY=value" >> .env`.
- Бот-юзернейм: `@Lineyka111_bot`. Админ chat_id пользователя: `941361300`.

### Источники котировок (10, все в `bot/main.py`)
OddsPapi, Fonbet, PARI, Marathon, Baltbet, The Odds API (только NBA), SureBet
(тестовый публичный токен), Zenit, Leon, OlimpBet, Melbet (headless Chromium).
Полный список отклонённых букмекеров и почему — см. git-историю/старые заметки,
не пытаться заново без новой информации: 1xBet/1xStavka, Лига Ставок, BetBoom,
Tennisi.bet, Мелбет(direct)/BetCity/OlimpBet(direct)/Zenit(direct)/СпортБет/24bet/
Bet-M/Bettery — все либо антибот-защита, либо чистый SPA без серверных данных.

### Billing
`bot/core/billing.py`: TRIAL_DAYS=3 (5 для рефералов), 3 плана (7d/30d/360d, руб + Stars).
Stars работает из коробки, YooKassa не подключена. Админ chat_id обходит подписку.

### Telegram Mini App
`bot/webapp/` — FastAPI JSON API + vanilla JS фронт, авторизация через Telegram initData.
**Критично**: все роуты в `api.py` только `async def` (sqlite3 thread-affinity),
`register_api()` — фабрика, не синглтон. Хостинг через Cloudflare quick tunnel
(`cloudflared`), URL может ротироваться при рестарте процесса — смотреть
`journalctl -u cloudflared-webapp | grep trycloudflare.com`, обновлять `WEBAPP_URL`.

Mini App фичи (на 2026-09-07): флаги команд, реальные лого (футбол через API-Football
с кэшем, NBA через ESPN CDN), переключатель языка ru/tg с иконкой-флагом (показывает
язык, НА который переключит), сплэш-скрин, pull-to-refresh, свайп между вкладками,
шаринг вилки в Telegram, быстрый фильтр (мин.% + вид спорта), Telegram MainButton,
офлайн-кэш последней вилки в localStorage (stale-while-revalidate).

### Локализация (ru/tg — таджикский)
Mini App: полностью через `I18N` словарь в `app.js`. Бот (`bot/handlers/commands.py`):
только дашборд и клавиатура переведены, остальные экраны (поиск, настройки, подписка,
реферал, поддержка, калькулятор, админ) — ещё на русском, переводить по частям.
Колонка `language` в таблице `users` (`ru`/`tg`, default `ru`), `Repository.set_language()`.

### Тесты и правила разработки
`PYTHONIOENCODING=utf-8 python -m pytest -q` (Windows/Git Bash, из-за кириллицы в выводе).
Деплой: commit → push → ssh деплой → грep логов на ошибки, игнорируя известный шум.
"Facts-only" фича анализа матчей (H2H) — НИКОГДА не показывать проценты/вероятности/
рекомендации по ставкам, только факты. Жёсткое ограничение проекта.

### Открытые вопросы/незакрытые темы
- Вопрос про "ЖК" (женские клубы) в вилках — предлагал фильтровать, ответа не было.
- Полный перевод бота на таджикский — продолжать по экрану, следующий шаг не выбран
  (варианты предлагались: поиск, настройки, подписка, реферал).
