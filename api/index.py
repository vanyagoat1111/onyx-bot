"""
ONYX WEB — Telegram-бот (Vercel, webhook).
Меню, строгая анкета с навигацией, корзина, оплата (физ/юр), формы, выгрузка в Google Sheets.
Только стандартная библиотека Python.
"""
import json, os, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MANAGER_CHAT_ID = os.environ.get("MANAGER_CHAT_ID", "")
SITE_URL = os.environ.get("SITE_URL", "https://onyx-web.ru/")
MANAGER_USERNAME = os.environ.get("MANAGER_USERNAME", "onyxcoop")
DEVELOPER_USERNAME = os.environ.get("DEVELOPER_USERNAME", "softstaticg")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
PRODAMUS_URL = os.environ.get("PRODAMUS_URL", "").rstrip("/")
CHECKLIST_PDF_URL = os.environ.get("CHECKLIST_PDF_URL", "")
SHEETS_WEBHOOK_URL = os.environ.get("SHEETS_WEBHOOK_URL", "")

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


def send(chat_id, text, reply_markup=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup is not None:
        p["reply_markup"] = reply_markup
    return tg("sendMessage", **p)


def answer_cb(cq_id, text=None):
    p = {"callback_query_id": cq_id}
    if text:
        p["text"] = text
    tg("answerCallbackQuery", **p)


def post_to_sheet(row):
    if not SHEETS_WEBHOOK_URL:
        return
    try:
        data = json.dumps(row, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(SHEETS_WEBHOOK_URL, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("Sheet error:", e)


def sheet_row(type_, name="", contact="", tg_="", niche="", goal="", has_site="",
              references="", options="", amount="", comment=""):
    return {"type": type_, "date": time.strftime("%Y-%m-%d %H:%M"), "name": name,
            "contact": contact, "tg": tg_, "niche": niche, "goal": goal,
            "has_site": has_site, "references": references, "options": options,
            "amount": amount, "comment": comment}


def notify_manager(text):
    if MANAGER_CHAT_ID:
        send(MANAGER_CHAT_ID, text)


# ------------------------- Redis / state -------------------------
def _redis(*cmd):
    if not KV_URL or not KV_TOKEN:
        return None
    data = json.dumps(list(cmd)).encode("utf-8")
    req = urllib.request.Request(KV_URL, data=data,
        headers={"Authorization": f"Bearer {KV_TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r).get("result")
    except Exception as e:
        print("Redis error:", cmd[0], e)
        return None


def _get(k):
    if KV_URL:
        raw = _redis("GET", k)
        return json.loads(raw) if raw else None
    return _MEM.get(k)


def _set(k, v, ttl=3600):
    if KV_URL:
        _redis("SET", k, json.dumps(v, ensure_ascii=False), "EX", str(ttl))
    else:
        _MEM[k] = v


def _del(k):
    if KV_URL:
        _redis("DEL", k)
    else:
        _MEM.pop(k, None)


def state_get(uid): return _get(f"onyx:state:{uid}")
def state_set(uid, v): _set(f"onyx:state:{uid}", v)
def state_del(uid): _del(f"onyx:state:{uid}")
def cart_get(uid): return _get(f"onyx:cart:{uid}") or []
def cart_set(uid, v): _set(f"onyx:cart:{uid}", v)


# ------------------------- Каталог -------------------------
CART_ITEMS = [
    ("launch", "Запуск сайта (разово)", 3990),
    ("service", "Обслуживание / мес", 1990),
    ("pages", "Доп. страница (от)", 1500),
    ("catalog", "Каталог товаров/услуг", 7990),
    ("crm", "Интеграция с CRM", 5900),
    ("cart", "Корзина для заказов (от)", 10790),
    ("maps", "Карты и геосервисы (от)", 3900),
    ("calc", "Калькулятор стоимости (от)", 7990),
    ("docs", "Правовые документы (от)", 2900),
    ("design", "Индивидуальный дизайн (от)", 15000),
    ("analytics", "Настройка аналитики", 3990),
    ("booking", "Онлайн-запись", 6990),
    ("tgnotify", "Telegram-уведомления", 3990),
]
ITEM = {c: (n, p) for c, n, p in CART_ITEMS}


def cart_total(cart): return sum(ITEM[c][1] for c in cart if c in ITEM)


def cart_kb(cart):
    rows = []
    for cid, name, price in CART_ITEMS:
        mark = "✅" if cid in cart else "▫️"
        rows.append([{"text": f"{mark} {name} — {price} ₽", "callback_data": f"c:{cid}"}])
    total = cart_total(cart)
    rows.append([{"text": "🧹 Очистить", "callback_data": "c:clear"},
                 {"text": (f"💳 Оплатить {total} ₽" if total else "💳 Оплатить"), "callback_data": "c:pay"}])
    rows.append([{"text": "🏠 Главное меню", "callback_data": "b:home"}])
    return {"inline_keyboard": rows}


def cart_text(cart):
    total = cart_total(cart)
    lines = [f"• {ITEM[c][0]} — {ITEM[c][1]} ₽" for c in cart if c in ITEM]
    body = "\n".join(lines) if lines else "Пока ничего не выбрано."
    return ("🛒 <b>Тарифы и услуги ONYX</b>\n\nОтметьте нужное — посчитаю сумму.\n\n"
            f"{body}\n\n<b>Итого: {total} ₽</b>\n\n"
            "Цены «от …» уточняются индивидуально с менеджером.")


def build_payment_link(cart, uid):
    if not PRODAMUS_URL:
        return None
    params = []
    for i, cid in enumerate([c for c in cart if c in ITEM]):
        name, price = ITEM[cid]
        params.append((f"products[{i}][name]", f"ONYX — {name}"))
        params.append((f"products[{i}][price]", str(price)))
        params.append((f"products[{i}][quantity]", "1"))
    params.append(("do", "pay"))
    params.append(("order_id", f"onyx-{uid}-{int(time.time())}"))
    return f"{PRODAMUS_URL}/?{urllib.parse.urlencode(params)}"


# ------------------------- Тексты -------------------------
WELCOME = (
    "👋 <b>ONYX WEB — сайты для бизнеса</b>\n\n"
    "<b>Разработка — 0 ₽.</b> Вы платите только за домен, хостинг и доп.опции."
)
WELCOME_KB = {"inline_keyboard": [[{"text": "✅ Заполнить заявку на сайт", "callback_data": "brief:start"}]]}
CHECKLIST = (
    "📋 <b>Что подготовить для создания сайта?</b>\n\n"
    "1️⃣ <b>О компании</b> — название, короткое описание, чем занимаетесь\n"
    "2️⃣ <b>Услуги/товары</b> — список с ценами (если есть)\n"
    "3️⃣ <b>Контакты</b> — телефон, почта, соцсети, адрес\n"
    "4️⃣ <b>Логотип и фото</b> — если есть (можно позже)\n"
    "5️⃣ <b>Референсы</b> — 2–3 сайта, которые нравятся\n"
    "6️⃣ <b>Тексты</b> — если есть; если нет — поможем составить\n"
    "7️⃣ <b>Домен</b> — есть ли желаемое имя сайта\n\n"
    "Не переживайте, если чего-то нет — соберём вместе с менеджером 🤝"
)
TARIFFS_INFO = (
    "💰 <b>Оффер ONYX WEB</b>\n\n"
    "• Разработка сайта — <b>0 ₽</b>\n"
    "• Запуск (разово) — 3 990 ₽\n"
    "• Обслуживание — 1 990 ₽ / мес\n"
    "• Доп.опции — по желанию (см. «🛒 Тарифы и услуги»)\n\n"
    f"Подробнее: {SITE_URL}"
)
PARTNER_INFO = (
    "🤝 <b>Партнёрская программа ONYX</b>\n\n"
    "Рекомендуйте наш бесплатный сайт своим клиентам и получайте <b>20%</b> "
    "на регулярной основе с каждого приведённого клиента.\n\n"
    "Плюс: скидки на наши услуги и поток клиентов к вам.\n\n"
    "Оставьте контакт — расскажем детали:"
)


# ------------------------- Главное меню -------------------------
MAIN_MENU = {"keyboard": [
    [{"text": "🌐 Получить сайт"}],
    [{"text": "🔍 Бесплатный аудит"}, {"text": "🛒 Тарифы и услуги"}],
    [{"text": "📋 Что подготовить"}, {"text": "📊 Статус заказа"}],
    [{"text": "💬 Вопрос менеджеру"}, {"text": "👨‍💻 Разработчику"}],
    [{"text": "🤝 Стать партнёром"}, {"text": "⭐ Оценить сервис"}],
    [{"text": "🔗 Сайт ONYX"}],
], "resize_keyboard": True}


def main_menu(chat_id, text=WELCOME):
    send(chat_id, text, MAIN_MENU)


# ------------------------- Анкета -------------------------
BRIEF_STEPS = [
    {"key": "biz", "q": "Какой у вас бизнес?", "opts": ["Услуги", "Товары / магазин", "Общепит", "Красота и здоровье", "Образование", "Другое"]},
    {"key": "have", "q": "Что уже есть?", "opts": ["Ничего нет", "Только соцсети", "Есть старый сайт"]},
    {"key": "goal", "q": "Главная цель сайта?", "opts": ["Заявки и клиенты", "Каталог услуг", "Сайт-визитка", "Онлайн-запись", "Интернет-магазин"]},
    {"key": "content", "q": "Есть тексты / контент для сайта?", "opts": ["Да, всё есть", "Частично", "Нет, нужна помощь"]},
    {"key": "brand", "q": "Есть логотип или фирменный стиль?", "opts": ["Да, есть", "Нет"]},
    {"key": "deadline", "q": "Когда нужен сайт?", "opts": ["Срочно (1–2 дня)", "В течение недели", "Не спешу"]},
    {"key": "budget", "q": "Бюджет на доп.опции?", "opts": ["До 5 000 ₽", "5 000–15 000 ₽", "Обсудим с менеджером"]},
    {"key": "name", "q": "Как к вам обращаться? Напишите имя.", "text": True},
    {"key": "contact", "q": "Оставьте контакт: телефон или @username.", "text": True, "contact": True},
]
NAV_KB = {"keyboard": [[{"text": "⬅️ Назад"}, {"text": "🏠 Главное меню"}]], "resize_keyboard": True}


def send_brief_step(chat_id, st):
    i = st["i"]
    step = BRIEF_STEPS[i]
    head = f"<b>Вопрос {i+1} из {len(BRIEF_STEPS)}</b>\n{step['q']}"
    if step.get("text"):
        rows = []
        if step.get("contact"):
            rows.append([{"text": "📱 Отправить мой номер", "request_contact": True}])
        rows.append([{"text": "⬅️ Назад"}, {"text": "🏠 Главное меню"}])
        send(chat_id, head, {"keyboard": rows, "resize_keyboard": True})
    else:
        kb = [[{"text": o, "callback_data": f"b:o:{idx}"}] for idx, o in enumerate(step["opts"])]
        nav = []
        if i > 0:
            nav.append({"text": "⬅️ Назад", "callback_data": "b:back"})
        nav.append({"text": "🏠 Меню", "callback_data": "b:home"})
        kb.append(nav)
        send(chat_id, head, {"inline_keyboard": kb})


def finish_brief(chat_id, user, data):
    username = f"@{user.get('username')}" if user.get("username") else "—"
    uid = user.get("id")
    opts = f"Бюджет: {data.get('budget','')}; срок: {data.get('deadline','')}; логотип: {data.get('brand','')}"
    notify_manager(
        "🔔 <b>Новая заявка ONYX</b>\n\n"
        f"👤 Имя: {data.get('name','')}\n📞 Контакт: {data.get('contact','')}\n"
        f"💬 Telegram: {username} (id {uid})\n"
        f"🏢 Бизнес: {data.get('biz','')}\n📦 Что есть: {data.get('have','')}\n"
        f"🎯 Цель: {data.get('goal','')}\n📝 Контент: {data.get('content','')}\n"
        f"⏱ Срок: {data.get('deadline','')}\n💰 Бюджет: {data.get('budget','')}\n"
        f"🎨 Логотип: {data.get('brand','')}"
    )
    post_to_sheet(sheet_row("Заявка", name=data.get("name", ""), contact=data.get("contact", ""),
                            tg_=username, niche=data.get("biz", ""), goal=data.get("goal", ""),
                            has_site=data.get("have", ""), references=data.get("content", ""),
                            options=opts))
    send(chat_id, "🎉 <b>Спасибо! Заявка принята.</b>\nМенеджер свяжется с вами в ближайшее время 🤝", MAIN_MENU)


def brief_text_input(chat_id, user, st, text, contact):
    if text == "🏠 Главное меню":
        state_del(user["id"]); main_menu(chat_id); return
    if text == "⬅️ Назад":
        st["i"] = max(0, st["i"] - 1); state_set(user["id"], st); send_brief_step(chat_id, st); return
    step = BRIEF_STEPS[st["i"]]
    st["data"][step["key"]] = contact.get("phone_number") if (contact and step.get("contact")) else text
    st["i"] += 1
    if st["i"] >= len(BRIEF_STEPS):
        state_del(user["id"]); finish_brief(chat_id, user, st["data"])
    else:
        state_set(user["id"], st); send_brief_step(chat_id, st)


# ------------------------- Формы-захваты -------------------------
CAP = {
    "audit": {"steps": [("site", "Пришлите ссылку на ваш сайт для аудита:"),
                        ("contact", "Оставьте контакт для ответа (телефон / @username):")],
              "type": "Аудит", "done": "🔍 Спасибо! Подготовим мини-аудит и свяжемся с вами."},
    "status": {"steps": [("order", "Напишите ваше имя или телефон, по которому оставляли заявку:")],
               "type": "Статус", "done": "📊 Спасибо! Менеджер уточнит статус вашего заказа и напишет."},
    "ask": {"steps": [("question", "Напишите ваш вопрос — передам менеджеру:")],
            "type": "Вопрос", "done": "💬 Спасибо! Менеджер ответит вам в ближайшее время."},
    "partner": {"steps": [("about", "Коротко о вас / вашем бизнесе:"),
                          ("contact", "Оставьте контакт (телефон / @username):")],
                "type": "Партнёр", "done": "🤝 Спасибо! Обсудим партнёрство — менеджер свяжется."},
    "legal": {"steps": [("company", "Название компании / ИП:"),
                        ("inn", "ИНН компании:"),
                        ("email", "E-mail для выставления счёта:")],
              "type": "Счёт юрлицу", "done": "🧾 Спасибо! Выставим счёт и пришлём на указанную почту."},
}


def send_cap_step(chat_id, st):
    kind = st["kind"]; i = st["i"]
    _, q = CAP[kind]["steps"][i]
    rows = []
    if CAP[kind]["steps"][i][0] == "contact":
        rows.append([{"text": "📱 Отправить мой номер", "request_contact": True}])
    rows.append([{"text": "🏠 Главное меню"}])
    send(chat_id, q, {"keyboard": rows, "resize_keyboard": True})


def finish_cap(chat_id, user, st):
    kind = st["kind"]; data = st["data"]
    username = f"@{user.get('username')}" if user.get("username") else "—"
    cfg = CAP[kind]
    comment = " | ".join(f"{k}: {v}" for k, v in data.items() if not k.startswith("_"))
    extra = ""
    if kind == "legal" and st.get("cart"):
        items = [ITEM[c][0] for c in st["cart"] if c in ITEM]
        total = cart_total(st["cart"])
        extra = "\nЗаказ: " + ", ".join(items) + f" — {total} ₽"
        comment += extra
    notify_manager(f"📥 <b>{cfg['type']}</b>\n💬 {username} (id {user.get('id')})\n{comment}")
    amount = cart_total(st["cart"]) if (kind == "legal" and st.get("cart")) else ""
    post_to_sheet(sheet_row(cfg["type"], tg_=username, contact=data.get("contact", ""),
                            comment=comment, amount=amount))
    send(chat_id, cfg["done"], MAIN_MENU)


def cap_text_input(chat_id, user, st, text, contact):
    if text == "🏠 Главное меню":
        state_del(user["id"]); main_menu(chat_id); return
    key = CAP[st["kind"]]["steps"][st["i"]][0]
    st["data"][key] = contact.get("phone_number") if (contact and key == "contact") else text
    st["i"] += 1
    if st["i"] >= len(CAP[st["kind"]]["steps"]):
        state_del(user["id"]); finish_cap(chat_id, user, st)
    else:
        state_set(user["id"], st); send_cap_step(chat_id, st)


def start_cap(chat_id, uid, kind, cart=None):
    st = {"flow": "cap", "kind": kind, "i": 0, "data": {}}
    if cart:
        st["cart"] = cart
    state_set(uid, st)
    send_cap_step(chat_id, st)


# ------------------------- Прочие экраны -------------------------
def send_checklist(chat_id, with_brief_button=True):
    btn = {"inline_keyboard": [[{"text": "✅ Заполнить заявку на сайт", "callback_data": "brief:start"}]]} if with_brief_button else None
    if CHECKLIST_PDF_URL:
        tg("sendDocument", chat_id=chat_id, document=CHECKLIST_PDF_URL,
           caption="📋 Что подготовить для создания сайта — смотрите в чек-листе 👇",
           reply_markup=btn)
    else:
        send(chat_id, "📋 Чек-лист скоро будет доступен. Нажмите «Заполнить заявку» — менеджер поможет.", btn)


def start_flow(chat_id):
    # 1) PDF-чек-лист + постоянное меню
    if CHECKLIST_PDF_URL:
        tg("sendDocument", chat_id=chat_id, document=CHECKLIST_PDF_URL,
           caption="📋 Чек-лист для бизнеса от ONYX", reply_markup=MAIN_MENU)
        send(chat_id, WELCOME, WELCOME_KB)
    else:
        send(chat_id, WELCOME, MAIN_MENU)


def rating_kb():
    return {"inline_keyboard": [[{"text": f"{'⭐'*n}", "callback_data": f"r:{n}"} for n in range(1, 6)]]}


# ------------------------- Обработка сообщений -------------------------
def process_message(msg):
    chat_id = msg["chat"]["id"]
    user = msg.get("from", {})
    uid = user.get("id")
    text = (msg.get("text") or "").strip()
    contact = msg.get("contact")

    if text == "🏠 Главное меню":
        state_del(uid); main_menu(chat_id); return
    if text == "/start":
        state_del(uid); start_flow(chat_id); return
    if text == "/id":
        send(chat_id, f"Ваш chat_id: <code>{chat_id}</code>"); return
    if text in ("/cancel", "Отмена", "отмена"):
        state_del(uid); main_menu(chat_id, "Отменено."); return

    MENU_TRIGGERS = {"🌐 Получить сайт", "🔍 Бесплатный аудит", "🛒 Тарифы и услуги",
                     "📋 Что подготовить", "📊 Статус заказа", "💬 Вопрос менеджеру",
                     "👨‍💻 Разработчику", "🤝 Стать партнёром", "⭐ Оценить сервис", "🔗 Сайт ONYX"}
    st = state_get(uid)
    if st and st.get("flow") in ("brief", "cap") and text in MENU_TRIGGERS:
        state_del(uid); st = None
    if st and st.get("flow") == "brief":
        step = BRIEF_STEPS[st["i"]]
        if step.get("text"):
            brief_text_input(chat_id, user, st, text, contact); return
        send(chat_id, "Пожалуйста, выберите вариант кнопкой выше 👆"); return
    if st and st.get("flow") == "cap":
        cap_text_input(chat_id, user, st, text, contact); return

    # Меню
    if text in ("🌐 Получить сайт", "/brief"):
        st = {"flow": "brief", "i": 0, "data": {}}
        state_set(uid, st); send_brief_step(chat_id, st); return
    if text == "📋 Что подготовить":
        send_checklist(chat_id); return
    if text == "🛒 Тарифы и услуги":
        send(chat_id, cart_text(cart_get(uid)), cart_kb(cart_get(uid))); return
    if text == "🔍 Бесплатный аудит":
        start_cap(chat_id, uid, "audit"); return
    if text == "📊 Статус заказа":
        start_cap(chat_id, uid, "status"); return
    if text == "💬 Вопрос менеджеру":
        start_cap(chat_id, uid, "ask"); return
    if text == "👨‍💻 Разработчику":
        send(chat_id, "👨‍💻 Написать разработчику бота:",
             {"inline_keyboard": [[{"text": "Открыть чат", "url": f"https://t.me/{DEVELOPER_USERNAME}"}]]}); return
    if text == "🤝 Стать партнёром":
        send(chat_id, PARTNER_INFO, {"inline_keyboard": [[{"text": "✍️ Оставить контакт", "callback_data": "pt:start"}]]}); return
    if text == "⭐ Оценить сервис":
        send(chat_id, "Оцените наш сервис:", rating_kb()); return
    if text == "🔗 Сайт ONYX":
        send(chat_id, "🔗 Наш сайт:", {"inline_keyboard": [[{"text": "Открыть onyx-web.ru", "url": SITE_URL}]]}); return

    main_menu(chat_id, "Выберите действие в меню ниже 👇")


# ------------------------- Обработка кнопок -------------------------
def process_callback(cq):
    data = cq.get("data", "")
    user = cq["from"]; uid = user["id"]
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    mid = msg.get("message_id")
    answer_cb(cq["id"])

    if data == "b:home":
        state_del(uid); main_menu(chat_id); return
    if data == "brief:start":
        st = {"flow": "brief", "i": 0, "data": {}}
        state_set(uid, st); send_brief_step(chat_id, st); return
    if data == "pt:start":
        start_cap(chat_id, uid, "partner"); return
    if data.startswith("r:"):
        n = data[2:]
        uname = f"@{user.get('username')}" if user.get("username") else "—"
        notify_manager(f"⭐ <b>Оценка сервиса: {n}/5</b>\n💬 {uname} (id {uid})")
        post_to_sheet(sheet_row("Оценка", tg_=uname, comment=f"{n}/5"))
        send(chat_id, "Спасибо за оценку! 🙏", MAIN_MENU); return

    # анкета — выбор варианта / назад
    if data.startswith("b:o:") or data == "b:back":
        st = state_get(uid)
        if not st or st.get("flow") != "brief":
            return
        if data == "b:back":
            st["i"] = max(0, st["i"] - 1); state_set(uid, st); send_brief_step(chat_id, st); return
        idx = int(data.split(":")[2])
        step = BRIEF_STEPS[st["i"]]
        st["data"][step["key"]] = step["opts"][idx]
        st["i"] += 1
        if st["i"] >= len(BRIEF_STEPS):
            state_del(uid); finish_brief(chat_id, user, st["data"])
        else:
            state_set(uid, st); send_brief_step(chat_id, st)
        return

    # корзина
    if data.startswith("c:"):
        action = data[2:]
        cart = cart_get(uid)
        if action == "clear":
            cart = []
        elif action == "pay":
            if not cart:
                answer_cb(cq["id"], "Сначала выберите услуги"); return
            send(chat_id, "Как будете оплачивать?", {"inline_keyboard": [
                [{"text": "👤 Как физлицо (карта / СБП)", "callback_data": "pm:fiz"}],
                [{"text": "🏢 Как юрлицо (счёт на реквизиты)", "callback_data": "pm:ur"}],
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

    # способ оплаты
    if data in ("pm:fiz", "pm:ur"):
        cart = cart_get(uid)
        if not cart:
            answer_cb(cq["id"], "Корзина пуста"); return
        total = cart_total(cart)
        items = [ITEM[c][0] for c in cart if c in ITEM]
        uname = f"@{user.get('username')}" if user.get("username") else "—"
        if data == "pm:fiz":
            notify_manager("🛒 <b>Заказ (физлицо)</b>\n💬 " + uname + f" (id {uid})\n" +
                           "\n".join(f"• {n}" for n in items) + f"\n\n<b>Итого: {total} ₽</b>")
            post_to_sheet(sheet_row("Заказ (физлицо)", tg_=uname, options=", ".join(items), amount=total))
            link = build_payment_link(cart, uid)
            if link:
                send(chat_id, f"К оплате: <b>{total} ₽</b>\nОплата картой или через СБП, чек придёт автоматически.",
                     {"inline_keyboard": [[{"text": f"💳 Оплатить {total} ₽", "url": link}]]})
            else:
                send(chat_id, f"Ваш заказ на <b>{total} ₽</b> принят. Менеджер пришлёт ссылку на оплату 🤝", MAIN_MENU)
        else:
            start_cap(chat_id, uid, "legal", cart=cart)
        return


def process_update(update):
    if update.get("callback_query"):
        process_callback(update["callback_query"]); return
    msg = update.get("message") or update.get("edited_message")
    if msg:
        process_message(msg)


# ------------------------- Vercel handler -------------------------
class handler(BaseHTTPRequestHandler):
    def _ok(self, body=b"ok", code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._ok("ONYX bot webhook is running".encode("utf-8"))

    def do_POST(self):
        if WEBHOOK_SECRET and self.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != WEBHOOK_SECRET:
            self._ok(b"forbidden", 403); return
        length = int(self.headers.get("content-length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            process_update(json.loads(raw or b"{}"))
        except Exception as e:
            print("Handler error:", e)
        self._ok()
