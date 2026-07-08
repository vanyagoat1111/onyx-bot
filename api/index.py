"""
ONYX WEB — Telegram-бот приёма заявок + выбор услуг и оплата (Vercel, webhook).

Возможности:
  • Анкета клиента (7 шагов), уведомление менеджеру, чек-лист.
  • Выбор услуг с галочками, автоподсчёт суммы, оплата через Продамус (динамическая ссылка).
Состояние хранится в Upstash Redis. Зависимостей нет (только стандартная библиотека).

Переменные окружения:
  BOT_TOKEN, MANAGER_CHAT_ID, WEBHOOK_SECRET, SITE_URL, MANAGER_USERNAME,
  KV_REST_API_URL / KV_REST_API_TOKEN (Upstash, ставит Vercel),
  PRODAMUS_URL     — адрес платёжной формы, напр. https://onyx.payform.ru (после одобрения),
  PAYMENT_URL      — запасная общая ссылка на оплату.
"""

import json
import os
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MANAGER_CHAT_ID = os.environ.get("MANAGER_CHAT_ID", "")
PAYMENT_URL = os.environ.get("PAYMENT_URL", "https://onyx-web.ru/")
SITE_URL = os.environ.get("SITE_URL", "https://onyx-web.ru/")
MANAGER_USERNAME = os.environ.get("MANAGER_USERNAME", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
PRODAMUS_URL = os.environ.get("PRODAMUS_URL", "").rstrip("/")

KV_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL") or ""
KV_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN") or ""
SHEETS_WEBHOOK_URL = os.environ.get("SHEETS_WEBHOOK_URL", "")

_MEM = {}


# ---------------------------------------------------------------------------
# Telegram API
# ---------------------------------------------------------------------------
def tg(method, **params):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        print("TG error:", method, e)
        return None


def send(chat_id, text, reply_markup=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup is not None:
        p["reply_markup"] = reply_markup
    return tg("sendMessage", **p)


def post_to_sheet(row: dict):
    """Отправляет заявку/заказ в Google-таблицу через Apps Script Web App."""
    if not SHEETS_WEBHOOK_URL:
        return
    try:
        data = json.dumps(row, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            SHEETS_WEBHOOK_URL, data=data, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:  # noqa: BLE001
        print("Sheet error:", e)


# ---------------------------------------------------------------------------
# Redis (Upstash) — состояние анкеты и корзина
# ---------------------------------------------------------------------------
def _redis(*cmd):
    if not KV_URL or not KV_TOKEN:
        return None
    data = json.dumps(list(cmd)).encode("utf-8")
    req = urllib.request.Request(
        KV_URL, data=data,
        headers={"Authorization": f"Bearer {KV_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r).get("result")
    except Exception as e:  # noqa: BLE001
        print("Redis error:", cmd[0], e)
        return None


def _get(key):
    if KV_URL:
        raw = _redis("GET", key)
        return json.loads(raw) if raw else None
    return _MEM.get(key)


def _set(key, value, ttl=3600):
    if KV_URL:
        _redis("SET", key, json.dumps(value, ensure_ascii=False), "EX", str(ttl))
    else:
        _MEM[key] = value


def _del(key):
    if KV_URL:
        _redis("DEL", key)
    else:
        _MEM.pop(key, None)


def state_get(uid):
    return _get(f"onyx:state:{uid}")


def state_set(uid, v):
    _set(f"onyx:state:{uid}", v)


def state_del(uid):
    _del(f"onyx:state:{uid}")


def cart_get(uid):
    return _get(f"onyx:cart:{uid}") or []


def cart_set(uid, v):
    _set(f"onyx:cart:{uid}", v)


def save_lead(lead):
    if KV_URL:
        _redis("RPUSH", "onyx:leads", json.dumps(lead, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Каталог услуг (фиксированные цены — идут в автооплату)
# ---------------------------------------------------------------------------
CART_ITEMS = [
    ("launch", "Запуск сайта (разово)", 3990),
    ("service", "Обслуживание (в месяц)", 1990),
    ("catalog", "Каталог товаров/услуг", 7990),
    ("crm", "Интеграция с CRM", 5900),
    ("analytics", "Настройка аналитики", 3990),
    ("booking", "Онлайн-запись", 6990),
    ("tgnotify", "Telegram-уведомления", 3990),
]
ITEM_BY_ID = {cid: (name, price) for cid, name, price in CART_ITEMS}


def cart_total(cart):
    return sum(ITEM_BY_ID[c][1] for c in cart if c in ITEM_BY_ID)


def cart_keyboard(cart):
    rows = []
    for cid, name, price in CART_ITEMS:
        mark = "✅" if cid in cart else "▫️"
        rows.append([{"text": f"{mark} {name} — {price} ₽", "callback_data": f"c:{cid}"}])
    total = cart_total(cart)
    pay_label = f"💳 Оплатить {total} ₽" if total else "💳 Оплатить"
    rows.append([{"text": "🧹 Очистить", "callback_data": "c:clear"},
                 {"text": pay_label, "callback_data": "c:pay"}])
    return {"inline_keyboard": rows}


def cart_text(cart):
    total = cart_total(cart)
    lines = [f"• {ITEM_BY_ID[c][0]} — {ITEM_BY_ID[c][1]} ₽" for c in cart if c in ITEM_BY_ID]
    body = "\n".join(lines) if lines else "Пока ничего не выбрано."
    return (
        "🛒 <b>Выбор услуг ONYX</b>\n\n"
        "Отметьте нужное кнопками ниже — я посчитаю сумму.\n\n"
        f"{body}\n\n<b>Итого: {total} ₽</b>\n\n"
        "Опции с ценой «от …» (доп. страницы, корзина, карты, калькулятор, "
        "индивидуальный дизайн и др.) считаются индивидуально — напишите менеджеру."
    )


def build_payment_link(cart, uid, email=""):
    """Динамическая ссылка Продамуса с выбранными позициями."""
    if not PRODAMUS_URL:
        return None
    params = []
    for i, cid in enumerate([c for c in cart if c in ITEM_BY_ID]):
        name, price = ITEM_BY_ID[cid]
        params.append((f"products[{i}][name]", f"ONYX — {name}"))
        params.append((f"products[{i}][price]", str(price)))
        params.append((f"products[{i}][quantity]", "1"))
    params.append(("do", "pay"))
    params.append(("order_id", f"onyx-{uid}-{int(time.time())}"))
    if email:
        params.append(("customer_email", email))
    query = urllib.parse.urlencode(params)
    return f"{PRODAMUS_URL}/?{query}"


# ---------------------------------------------------------------------------
# Анкета
# ---------------------------------------------------------------------------
class NA:
    pass


def notify_manager(text):
    if MANAGER_CHAT_ID:
        send(MANAGER_CHAT_ID, text)


# ---------------------------------------------------------------------------
# Клавиатуры и тексты
# ---------------------------------------------------------------------------
MAIN_MENU = {
    "keyboard": [
        [{"text": "📝 Оставить заявку"}],
        [{"text": "🛒 Выбрать услуги и оплатить"}],
        [{"text": "💰 Тарифы и оффер"}, {"text": "❓ Частые вопросы"}],
    ],
    "resize_keyboard": True,
}
HAS_SITE_KB = {"keyboard": [[{"text": "Да, есть сайт"}, {"text": "Нет, сайта нет"}]],
               "resize_keyboard": True, "one_time_keyboard": True}
SKIP_KB = {"keyboard": [[{"text": "Пропустить"}]], "resize_keyboard": True, "one_time_keyboard": True}
CONTACT_KB = {"keyboard": [[{"text": "📱 Отправить мой номер", "request_contact": True}]],
              "resize_keyboard": True, "one_time_keyboard": True}
REMOVE = {"remove_keyboard": True}

WELCOME = (
    "👋 <b>Добро пожаловать в ONYX WEB!</b>\n\n"
    "Мы делаем сайты под ключ. <b>Разработка — 0 ₽.</b> "
    "Вы платите только за домен и хостинг, а доп.опции — по желанию.\n\n"
    "Оставьте заявку или выберите услуги для оплаты в меню ниже."
)
TARIFFS = (
    "💰 <b>Оффер ONYX WEB</b>\n\n"
    "• <b>Разработка сайта — 0 ₽</b>\n"
    "• Оплачиваете только домен и хостинг\n"
    "• Доп.опции — по желанию\n\n"
    f"Подробнее: {SITE_URL}\n\n"
    "Нажмите «🛒 Выбрать услуги и оплатить», чтобы собрать заказ."
)
FAQ = (
    "❓ <b>Частые вопросы</b>\n\n"
    "<b>Почему разработка 0 ₽?</b>\nМы зарабатываем на обслуживании, доп.опциях и партнёрских услугах.\n\n"
    "<b>Сколько делается сайт?</b>\nОбычно 1–2 дня после анкеты.\n\n"
    "<b>Что подготовить?</b>\nНажмите «📝 Оставить заявку» — пришлю чек-лист."
)
CHECKLIST = (
    "✅ <b>Чек-лист: что подготовить для сайта</b>\n\n"
    "1️⃣ <b>О компании</b> — название, описание, чем занимаетесь\n"
    "2️⃣ <b>Услуги/товары</b> — список с ценами\n"
    "3️⃣ <b>Контакты</b> — телефон, почта, соцсети, адрес\n"
    "4️⃣ <b>Логотип и фото</b> — если есть\n"
    "5️⃣ <b>Референсы</b> — 2–3 сайта, которые нравятся\n"
    "6️⃣ <b>Тексты</b> — если есть; если нет — поможем\n"
    "7️⃣ <b>Домен</b> — есть ли желаемое имя\n\n"
    "Не переживайте, если чего-то нет — соберём вместе с менеджером 🤝"
)
Q = {
    "niche": "1/7. В какой <b>нише</b> ваш бизнес? (барбершоп, доставка, юрист)",
    "goal": "2/7. Какая <b>задача</b> у сайта? (заявки, каталог, запись, визитка)",
    "has_site": "3/7. У вас уже <b>есть сайт</b>?",
    "references": "4/7. Есть <b>референсы</b>? Ссылки или «Пропустить».",
    "options": "5/7. Нужны <b>доп.опции</b>? (CRM, оплата, каталог) или «Пропустить».",
    "name": "6/7. Как к вам <b>обращаться</b>?",
    "contact": "7/7. Оставьте <b>контакт</b> — телефон или @username.",
}


def finish(chat_id, user, data):
    username = f"@{user.get('username')}" if user.get("username") else "—"
    uid = user.get("id")
    lead = {
        "user_id": uid, "tg_username": username,
        "name": data.get("name", ""), "contact": data.get("contact", ""),
        "niche": data.get("niche", ""), "goal": data.get("goal", ""),
        "has_site": data.get("has_site", ""), "references": data.get("references", ""),
        "options": data.get("options", ""),
    }
    save_lead(lead)
    post_to_sheet({
        "type": "Заявка",
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "name": lead["name"], "contact": lead["contact"],
        "tg": username, "niche": lead["niche"], "goal": lead["goal"],
        "has_site": lead["has_site"], "references": lead["references"],
        "options": lead["options"], "amount": "",
    })
    notify_manager(
        "🔔 <b>Новая заявка ONYX</b>\n\n"
        f"👤 Имя: {lead['name']}\n📞 Контакт: {lead['contact']}\n"
        f"💬 Telegram: {username} (id {uid})\n🏢 Ниша: {lead['niche']}\n"
        f"🎯 Задача: {lead['goal']}\n🌐 Есть сайт: {lead['has_site']}\n"
        f"🎨 Референсы: {lead['references'] or '—'}\n➕ Доп.опции: {lead['options'] or '—'}"
    )
    send(chat_id, "🎉 <b>Спасибо! Заявка принята.</b>\n\nМенеджер скоро свяжется. "
                  "А пока — чек-лист 👇", MAIN_MENU)
    send(chat_id, CHECKLIST)
    if MANAGER_USERNAME:
        send(chat_id, f"Можно написать менеджеру: @{MANAGER_USERNAME}")


# ---------------------------------------------------------------------------
# Обработка сообщений
# ---------------------------------------------------------------------------
def process_message(msg):
    chat_id = msg["chat"]["id"]
    user = msg.get("from", {})
    uid = user.get("id")
    text = (msg.get("text") or "").strip()
    contact = msg.get("contact")

    if text == "/start":
        state_del(uid)
        send(chat_id, WELCOME, MAIN_MENU)
        return
    if text == "/id":
        send(chat_id, f"Ваш chat_id: <code>{chat_id}</code>")
        return
    if text in ("/cancel", "Отмена", "отмена"):
        state_del(uid)
        send(chat_id, "Отменено.", MAIN_MENU)
        return
    if text == "💰 Тарифы и оффер":
        send(chat_id, TARIFFS, MAIN_MENU)
        return
    if text == "❓ Частые вопросы":
        send(chat_id, FAQ, MAIN_MENU)
        return
    if text in ("🛒 Выбрать услуги и оплатить", "/order"):
        cart = cart_get(uid)
        send(chat_id, cart_text(cart), cart_keyboard(cart))
        return
    if text in ("/brief", "📝 Оставить заявку"):
        state_set(uid, {"step": "niche", "data": {}})
        send(chat_id, "Задам 7 коротких вопросов. Можно написать «Отмена».\n\n" + Q["niche"], REMOVE)
        return

    st = state_get(uid)
    if not st:
        send(chat_id, "Выберите действие в меню ниже 👇", MAIN_MENU)
        return

    step, data = st["step"], st["data"]
    order = ["niche", "goal", "has_site", "references", "options", "name", "contact"]
    kb = {"has_site": HAS_SITE_KB, "references": SKIP_KB, "options": SKIP_KB,
          "name": REMOVE, "contact": CONTACT_KB}

    if step in ("references", "options"):
        data[step] = "" if text == "Пропустить" else text
    elif step == "contact":
        data["contact"] = contact.get("phone_number") if contact else text
        state_del(uid)
        finish(chat_id, user, data)
        return
    else:
        data[step] = text

    nxt = order[order.index(step) + 1]
    st["step"] = nxt
    state_set(uid, st)
    send(chat_id, Q[nxt], kb.get(nxt))


# ---------------------------------------------------------------------------
# Обработка нажатий на кнопки (callback)
# ---------------------------------------------------------------------------
def process_callback(cq):
    data = cq.get("data", "")
    uid = cq["from"]["id"]
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    mid = msg.get("message_id")
    tg("answerCallbackQuery", callback_query_id=cq["id"])

    if not data.startswith("c:"):
        return
    action = data[2:]
    cart = cart_get(uid)

    if action == "clear":
        cart = []
    elif action == "pay":
        if not cart:
            tg("answerCallbackQuery", callback_query_id=cq["id"], text="Сначала выберите услуги")
            return
        total = cart_total(cart)
        items = [ITEM_BY_ID[c][0] for c in cart if c in ITEM_BY_ID]
        # уведомляем менеджера о заказе
        uname = f"@{cq['from'].get('username')}" if cq["from"].get("username") else "—"
        notify_manager(
            "🛒 <b>Заказ на оплату</b>\n\n"
            f"💬 {uname} (id {uid})\n"
            + "\n".join(f"• {n}" for n in items)
            + f"\n\n<b>Итого: {total} ₽</b>"
        )
        post_to_sheet({
            "type": "Заказ", "date": time.strftime("%Y-%m-%d %H:%M"),
            "name": "", "contact": "", "tg": uname, "niche": "", "goal": "",
            "has_site": "", "references": "", "options": ", ".join(items),
            "amount": total,
        })
        link = build_payment_link(cart, uid)
        if link:
            send(chat_id, f"К оплате: <b>{total} ₽</b>\nНажмите кнопку — оплата картой или через СБП, "
                          "чек придёт автоматически.",
                 {"inline_keyboard": [[{"text": f"💳 Оплатить {total} ₽", "url": link}]]})
        else:
            send(chat_id, f"Ваш заказ на <b>{total} ₽</b> принят. "
                          "Менеджер пришлёт ссылку на оплату в ближайшее время 🤝", MAIN_MENU)
        return
    elif action in ITEM_BY_ID:
        if action in cart:
            cart.remove(action)
        else:
            cart.append(action)
    else:
        return

    cart_set(uid, cart)
    tg("editMessageText", chat_id=chat_id, message_id=mid,
       text=cart_text(cart), parse_mode="HTML", reply_markup=cart_keyboard(cart))


def process_update(update):
    if update.get("callback_query"):
        process_callback(update["callback_query"])
        return
    msg = update.get("message") or update.get("edited_message")
    if msg:
        process_message(msg)


# ---------------------------------------------------------------------------
# Vercel handler
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
        if WEBHOOK_SECRET:
            if self.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != WEBHOOK_SECRET:
                self._ok(b"forbidden", 403)
                return
        length = int(self.headers.get("content-length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            process_update(json.loads(raw or b"{}"))
        except Exception as e:  # noqa: BLE001
            print("Handler error:", e)
        self._ok()
