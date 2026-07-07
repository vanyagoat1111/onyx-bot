"""
ONYX WEB — Telegram-бот приёма заявок для Vercel (webhook, serverless).

Работает как функция: Telegram присылает каждое сообщение POST-запросом сюда.
Состояние анкеты хранится в Upstash Redis (бесплатно, подключается в Vercel → Storage).
Зависимостей нет — только стандартная библиотека Python.

Переменные окружения (Vercel → Settings → Environment Variables):
  BOT_TOKEN            — токен бота от @BotFather (обязательно)
  MANAGER_CHAT_ID      — куда слать заявки (обязательно; узнать: команда /id боту)
  PAYMENT_URL          — ссылка на оплату домена/хостинга
  SITE_URL             — адрес сайта
  MANAGER_USERNAME     — @username менеджера без @ (необязательно)
  WEBHOOK_SECRET       — секрет для защиты webhook (необязательно, но желательно)
  KV_REST_API_URL / KV_REST_API_TOKEN — база Upstash (подставляются Vercel автоматически)
"""

import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MANAGER_CHAT_ID = os.environ.get("MANAGER_CHAT_ID", "")
PAYMENT_URL = os.environ.get("PAYMENT_URL", "https://onyx-web.ru/")
SITE_URL = os.environ.get("SITE_URL", "https://onyx-web.ru/")
MANAGER_USERNAME = os.environ.get("MANAGER_USERNAME", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# Upstash Redis REST (Vercel KV) — имена переменных могут отличаться, берём любые.
KV_URL = (
    os.environ.get("KV_REST_API_URL")
    or os.environ.get("UPSTASH_REDIS_REST_URL")
    or ""
)
KV_TOKEN = (
    os.environ.get("KV_REST_API_TOKEN")
    or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    or ""
)

# Запасное хранилище в памяти (если база не подключена). В serverless между
# запросами не сохраняется — анкета будет работать плохо. Нужно подключить Redis.
_MEM = {}


# ---------------------------------------------------------------------------
# Telegram API
# ---------------------------------------------------------------------------
def tg(method: str, **params):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        print("TG error:", method, e)
        return None


def send(chat_id, text, reply_markup=None):
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup is not None:
        params["reply_markup"] = reply_markup
    return tg("sendMessage", **params)


# ---------------------------------------------------------------------------
# Состояние анкеты (Upstash Redis REST)
# ---------------------------------------------------------------------------
def _redis(*cmd):
    if not KV_URL or not KV_TOKEN:
        return None
    data = json.dumps(list(cmd)).encode("utf-8")
    req = urllib.request.Request(
        KV_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {KV_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r).get("result")
    except Exception as e:  # noqa: BLE001
        print("Redis error:", cmd[0], e)
        return None


def state_get(uid):
    key = f"onyx:state:{uid}"
    if KV_URL:
        raw = _redis("GET", key)
        return json.loads(raw) if raw else None
    return _MEM.get(key)


def state_set(uid, value):
    key = f"onyx:state:{uid}"
    if KV_URL:
        _redis("SET", key, json.dumps(value, ensure_ascii=False), "EX", "3600")
    else:
        _MEM[key] = value


def state_del(uid):
    key = f"onyx:state:{uid}"
    if KV_URL:
        _redis("DEL", key)
    else:
        _MEM.pop(key, None)


def save_lead(lead: dict):
    if KV_URL:
        _redis("RPUSH", "onyx:leads", json.dumps(lead, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------
MAIN_MENU = {
    "keyboard": [
        [{"text": "📝 Оставить заявку"}],
        [{"text": "💰 Тарифы и оффер"}, {"text": "❓ Частые вопросы"}],
        [{"text": "💳 Оплатить домен/хостинг"}],
    ],
    "resize_keyboard": True,
}
HAS_SITE_KB = {
    "keyboard": [[{"text": "Да, есть сайт"}, {"text": "Нет, сайта нет"}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}
SKIP_KB = {
    "keyboard": [[{"text": "Пропустить"}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}
CONTACT_KB = {
    "keyboard": [[{"text": "📱 Отправить мой номер", "request_contact": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}
REMOVE = {"remove_keyboard": True}
PAY_KB = {"inline_keyboard": [[{"text": "💳 Перейти к оплате", "url": PAYMENT_URL}]]}


# ---------------------------------------------------------------------------
# Тексты
# ---------------------------------------------------------------------------
WELCOME = (
    "👋 <b>Добро пожаловать в ONYX WEB!</b>\n\n"
    "Мы делаем сайты под ключ. <b>Разработка — 0 ₽.</b> "
    "Вы платите только за домен и хостинг, а доп.опции — по желанию.\n\n"
    "Чтобы начать, оставьте заявку — я задам несколько коротких вопросов "
    "и передам всё менеджеру. Это займёт пару минут."
)
TARIFFS = (
    "💰 <b>Оффер ONYX WEB</b>\n\n"
    "• <b>Разработка сайта — 0 ₽</b>\n"
    "• Вы оплачиваете только домен и хостинг\n"
    "• Доп.опции (по желанию): подключение CRM, формы заявок, "
    "мультиязычность, доработки, продвижение\n\n"
    f"Подробнее на сайте: {SITE_URL}\n\n"
    "Готовы начать? Нажмите «📝 Оставить заявку»."
)
FAQ = (
    "❓ <b>Частые вопросы</b>\n\n"
    "<b>Почему разработка 0 ₽?</b>\n"
    "Мы зарабатываем на обслуживании, доп.опциях и партнёрских услугах, "
    "поэтому сам сайт делаем бесплатно.\n\n"
    "<b>Сколько стоит домен и хостинг?</b>\n"
    "Зависит от проекта — точную сумму назовёт менеджер после заявки.\n\n"
    "<b>Сколько делается сайт?</b>\n"
    "Обычно от 1 до нескольких дней после заполнения анкеты.\n\n"
    "<b>Что мне нужно подготовить?</b>\n"
    "Нажмите «📝 Оставить заявку» — я пришлю чек-лист."
)
CHECKLIST = (
    "✅ <b>Чек-лист: что подготовить для сайта</b>\n\n"
    "1️⃣ <b>О компании</b> — название, короткое описание, чем занимаетесь\n"
    "2️⃣ <b>Услуги/товары</b> — список с ценами (если есть)\n"
    "3️⃣ <b>Контакты</b> — телефон, почта, соцсети, адрес\n"
    "4️⃣ <b>Логотип и фото</b> — если есть (можно прислать позже)\n"
    "5️⃣ <b>Референсы</b> — 2–3 сайта, которые нравятся\n"
    "6️⃣ <b>Тексты</b> — если есть готовые; если нет — поможем составить\n"
    "7️⃣ <b>Домен</b> — есть ли желаемое имя сайта\n\n"
    "Не переживайте, если чего-то нет — соберём вместе с менеджером 🤝"
)

Q = {
    "niche": "1/7. В какой <b>нише</b> ваш бизнес? (например: барбершоп, доставка еды, юрист)",
    "goal": "2/7. Какая <b>задача</b> у сайта? (привлекать клиентов, каталог, запись онлайн, визитка)",
    "has_site": "3/7. У вас уже <b>есть сайт</b>?",
    "references": "4/7. Есть <b>референсы</b> — сайты или стиль, который нравится? Пришлите ссылки или «Пропустить».",
    "options": "5/7. Нужны <b>доп.опции</b>? (CRM, форма заявок, онлайн-оплата, мультиязычность). Перечислите или «Пропустить».",
    "name": "6/7. Как к вам <b>обращаться</b>?",
    "contact": "7/7. Оставьте <b>контакт</b> — телефон или @username. Можно нажать кнопку ниже.",
}


# ---------------------------------------------------------------------------
# Завершение анкеты
# ---------------------------------------------------------------------------
def finish(chat_id, user, data):
    username = f"@{user.get('username')}" if user.get("username") else "—"
    uid = user.get("id")

    lead = {
        "user_id": uid,
        "tg_username": username,
        "name": data.get("name", ""),
        "contact": data.get("contact", ""),
        "niche": data.get("niche", ""),
        "goal": data.get("goal", ""),
        "has_site": data.get("has_site", ""),
        "references": data.get("references", ""),
        "options": data.get("options", ""),
    }
    save_lead(lead)

    manager_text = (
        "🔔 <b>Новая заявка ONYX</b>\n\n"
        f"👤 Имя: {lead['name']}\n"
        f"📞 Контакт: {lead['contact']}\n"
        f"💬 Telegram: {username} (id {uid})\n"
        f"🏢 Ниша: {lead['niche']}\n"
        f"🎯 Задача: {lead['goal']}\n"
        f"🌐 Есть сайт: {lead['has_site']}\n"
        f"🎨 Референсы: {lead['references'] or '—'}\n"
        f"➕ Доп.опции: {lead['options'] or '—'}"
    )
    if MANAGER_CHAT_ID:
        send(MANAGER_CHAT_ID, manager_text)

    send(
        chat_id,
        "🎉 <b>Спасибо! Заявка принята.</b>\n\n"
        "Менеджер свяжется с вами в ближайшее время. "
        "А пока — вот чек-лист, что подготовить для сайта 👇",
        MAIN_MENU,
    )
    send(chat_id, CHECKLIST)
    if MANAGER_USERNAME:
        send(chat_id, f"Можно написать менеджеру напрямую: @{MANAGER_USERNAME}")


# ---------------------------------------------------------------------------
# Обработка входящего обновления
# ---------------------------------------------------------------------------
def process_update(update: dict):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    user = msg.get("from", {})
    uid = user.get("id")
    text = (msg.get("text") or "").strip()
    contact = msg.get("contact")

    # Команды и кнопки меню
    if text == "/start":
        state_del(uid)
        send(chat_id, WELCOME, MAIN_MENU)
        return
    if text == "/id":
        send(chat_id, f"Ваш chat_id: <code>{chat_id}</code>\nВпишите его в MANAGER_CHAT_ID.")
        return
    if text in ("/cancel", "Отмена", "отмена"):
        state_del(uid)
        send(chat_id, "Заявка отменена. Можно начать заново.", MAIN_MENU)
        return
    if text == "💰 Тарифы и оффер":
        send(chat_id, TARIFFS, MAIN_MENU)
        return
    if text == "❓ Частые вопросы":
        send(chat_id, FAQ, MAIN_MENU)
        return
    if text == "💳 Оплатить домен/хостинг":
        send(
            chat_id,
            "💳 <b>Оплата домена и хостинга</b>\n\n"
            "Нажмите кнопку ниже. Если суммы ещё не согласованы — сначала оставьте "
            "заявку, менеджер пришлёт точный счёт.",
            PAY_KB,
        )
        return
    if text in ("/brief", "📝 Оставить заявку"):
        state_set(uid, {"step": "niche", "data": {}})
        send(
            chat_id,
            "Отлично! Задам 7 коротких вопросов. В любой момент можно написать «Отмена».\n\n"
            + Q["niche"],
            REMOVE,
        )
        return

    # Анкета
    st = state_get(uid)
    if not st:
        send(chat_id, "Нажмите «📝 Оставить заявку», чтобы начать, или /start.", MAIN_MENU)
        return

    step = st["step"]
    data = st["data"]

    if step == "niche":
        data["niche"] = text
        st["step"] = "goal"
        state_set(uid, st)
        send(chat_id, Q["goal"])
    elif step == "goal":
        data["goal"] = text
        st["step"] = "has_site"
        state_set(uid, st)
        send(chat_id, Q["has_site"], HAS_SITE_KB)
    elif step == "has_site":
        data["has_site"] = text
        st["step"] = "references"
        state_set(uid, st)
        send(chat_id, Q["references"], SKIP_KB)
    elif step == "references":
        data["references"] = "" if text == "Пропустить" else text
        st["step"] = "options"
        state_set(uid, st)
        send(chat_id, Q["options"], SKIP_KB)
    elif step == "options":
        data["options"] = "" if text == "Пропустить" else text
        st["step"] = "name"
        state_set(uid, st)
        send(chat_id, Q["name"], REMOVE)
    elif step == "name":
        data["name"] = text
        st["step"] = "contact"
        state_set(uid, st)
        send(chat_id, Q["contact"], CONTACT_KB)
    elif step == "contact":
        data["contact"] = contact.get("phone_number") if contact else text
        state_del(uid)
        finish(chat_id, user, data)


# ---------------------------------------------------------------------------
# Vercel HTTP handler
# ---------------------------------------------------------------------------
class handler(BaseHTTPRequestHandler):
    def _ok(self, body=b"ok", code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._ok("ONYX bot webhook is running".encode("utf-8"))

    def do_POST(self):
        # Проверка секрета (если задан)
        if WEBHOOK_SECRET:
            got = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if got != WEBHOOK_SECRET:
                self._ok(b"forbidden", 403)
                return
        length = int(self.headers.get("content-length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            update = json.loads(raw or b"{}")
            process_update(update)
        except Exception as e:  # noqa: BLE001
            print("Handler error:", e)
        self._ok()
