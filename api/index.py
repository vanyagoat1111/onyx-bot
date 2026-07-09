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
ADMIN_IDS = set(int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit())


def is_admin(uid):
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
    rows.append([{"text": "ℹ️ Об услугах", "callback_data": "c:info"},
                 {"text": "🏠 Меню", "callback_data": "b:home"}])
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


# ------------------------- Данные: пользователи и заказы -------------------------
YEAR = 60 * 60 * 24 * 365

STATUS = {
    "new": "🆕 Новый",
    "wait_pay": "⏳ Ожидает оплаты",
    "invoice": "🧾 Ожидает счёта",
    "paid": "✅ Оплачен",
    "in_work": "🛠 В работе",
    "review": "🔎 На проверке",
    "done": "🎉 Готов",
    "canceled": "❌ Отменён",
}


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


def order_new(uid, items, total, payment_type, status="new", extra=None):
    oid = next_order_id()
    order = {"id": oid, "uid": uid, "items": items, "total": total,
             "payment_type": payment_type, "status": status,
             "paid": False, "created": now_str()}
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
    return oid


def order_get(oid):
    return _get(f"onyx:order:{oid}")


def order_save(order):
    _set(f"onyx:order:{order['id']}", order, ttl=YEAR)


# ------------------------- Админ / подписки / рефералы / услуги -------------------------
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}
_BOT_USERNAME = [os.environ.get("BOT_USERNAME", "") or None]
PRCY_API_KEY = os.environ.get("PRCY_API_KEY", "")


def is_admin(uid):
    return uid in ADMIN_IDS


def bot_username():
    if _BOT_USERNAME[0]:
        return _BOT_USERNAME[0]
    me = tg("getMe")
    _BOT_USERNAME[0] = (me or {}).get("result", {}).get("username", "onyx_bot")
    return _BOT_USERNAME[0]


def subscribe(uid):
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
    return bool(p.get("name") and p.get("contact"))


def set_order_status(oid, status_key):
    o = order_get(oid)
    if not o:
        return None
    o["status"] = status_key
    if status_key == "paid":
        o["paid"] = True
    order_save(o)
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


def run_subscription_reminders():
    today = time.strftime("%Y-%m-%d")
    n = 0
    for uid in all_subscribers():
        p = user_get(uid)
        sub = p.get("subscription") if p else None
        if sub and sub.get("active") and sub.get("next") and sub["next"] <= today:
            send(uid, f"🔔 Напоминание: пора продлить обслуживание сайта — {sub.get('price', 1990)} ₽/мес.")
            notify_manager(f"🔔 Подписка к оплате: id {uid} ({p.get('name', '')})")
            sub["next"] = add_days(today, 30)
            p["subscription"] = sub
            user_save(uid, p)
            n += 1
    return n


# Описания услуг (Этап 3)
DESC = {
    "launch": "Домен, хостинг, SSL, публикация и техподдержка сайта.",
    "service": "Ежемесячное обслуживание: мелкие правки, бэкапы, контроль работы.",
    "pages": "Добавление отдельных страниц к сайту (цена за одну).",
    "catalog": "Каталог ваших товаров или услуг на сайте.",
    "crm": "Связь сайта с CRM — заявки попадают в систему автоматически.",
    "cart": "Корзина и оформление заказов прямо на сайте.",
    "maps": "Интерактивная карта (Яндекс/Google) с вашим адресом.",
    "calc": "Калькулятор расчёта стоимости ваших услуг.",
    "docs": "Правовые документы: политика, оферта, согласия.",
    "design": "Уникальный дизайн под ваш бренд по вашему промпту.",
    "analytics": "Яндекс Метрика и Google Analytics.",
    "booking": "Онлайн-запись для салонов, клиник, студий.",
    "tgnotify": "Новые заявки сразу приходят в Telegram.",
}


def services_info_text():
    parts = ["ℹ️ <b>Услуги ONYX — что входит</b>"]
    for cid, name, price in CART_ITEMS:
        parts.append(f"<b>{name} — {price} ₽</b>\n{DESC.get(cid, '')}")
    return "\n\n".join(parts)


ADMIN_HELP = (
    "🔐 <b>Админ-команды</b>\n\n"
    "/orders — последние заказы\n"
    "/order N — детали заказа №N\n"
    "/status N код — сменить статус заказа\n\n"
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
        lines.append(f"№{oid} — {o.get('total', 0)} ₽ — {st} — {o.get('payment_type', '')}")
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
        f"Клиент: {u.get('name', '—')} / {u.get('contact', '—')} / @{u.get('username', '—')} (id {o.get('uid')})",
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
    if key == "paid":
        o["paid"] = True
    order_save(o)
    cuid = o.get("uid")
    if cuid:
        send(cuid, f"🔔 Статус вашего заказа <b>№{oid}</b>: {STATUS[key]}")
        if key == "done":
            send(cuid, "Ваш сайт готов! 🎉 Пожалуйста, оцените нашу работу:", rating_kb())
    return f"✅ Заказ №{oid} → {STATUS[key]}. Клиент уведомлён."


def render_cabinet(uid):
    p = user_get(uid)
    if not p:
        return ("👤 <b>Личный кабинет</b>\n\n"
                "Здесь появятся ваши данные, заказы и статусы. "
                "Оставьте заявку или соберите заказ — и профиль создастся автоматически.")
    lines = ["👤 <b>Личный кабинет</b>", ""]
    lines.append(f"Имя: {p.get('name', '—')}")
    lines.append(f"Контакт: {p.get('contact', '—')}")
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
            items = ", ".join(o.get("items", [])) or "—"
            lines.append(f"• №{oid} — {items} — {o.get('total', 0)} ₽ — {st}")
    lines.append("")
    sub = p.get("subscription")
    if sub and sub.get("active"):
        lines.append(f"🔔 <b>Подписка:</b> {sub.get('plan', 'Обслуживание')} — активна")
    else:
        lines.append("🔔 <b>Подписка:</b> нет активной")
    return "\n".join(lines)


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
    [{"text": "🌐 Получить сайт"}, {"text": "👤 Мой кабинет"}],
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
    upsert_user(uid, name=data.get('name'), contact=data.get('contact'), username=user.get('username'))
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
    if cart_get(uid):
        send(chat_id, "🛒 У вас есть выбранные услуги. Перейти к оплате?",
             {"inline_keyboard": [[{"text": "💳 К оплате", "callback_data": "cart:open"}]]})


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
        oid = order_new(user.get("id"), items, total, "Юрлицо", status="invoice",
                        extra={"company": data.get("company"), "inn": data.get("inn"), "email": data.get("email")})
        cart_set(user.get("id"), [])
        extra = f"\nЗаказ №{oid}: " + ", ".join(items) + f" — {total} ₽"
        comment += extra
    upsert_user(user.get("id"), contact=data.get("contact") or data.get("email"), username=user.get("username"))
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
    subscribe(uid)

    if text == "🏠 Главное меню":
        state_del(uid); main_menu(chat_id); return
    if text.startswith("/start"):
        state_del(uid)
        parts = text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        register_user(uid, user.get("username"))
        if payload.startswith("ref"):
            rid = payload[3:]
            if rid.isdigit() and int(rid) != uid:
                p = user_get(uid)
                if not p.get("referred_by"):
                    p["referred_by"] = int(rid); user_save(uid, p)
                    rp = user_get(int(rid))
                    if rp:
                        rp["referrals"] = rp.get("referrals", 0) + 1; user_save(int(rid), rp)
                        notify_manager(f"🤝 Новый реферал у id {rid}: id {uid}")
        start_flow(chat_id); return
    if text == "/id":
        send(chat_id, f"Ваш chat_id: <code>{chat_id}</code>"); return
    if text in ("/cancel", "Отмена", "отмена"):
        state_del(uid); main_menu(chat_id, "Отменено."); return

    if is_admin(uid) and text.startswith("/"):
        low = text.strip()
        if low == "/admin":
            send(chat_id,
                 "🛠 <b>Админ-команды</b>\n"
                 "/orders — последние заказы\n"
                 "/order &lt;№&gt; — детали заказа\n"
                 "/status &lt;№&gt; &lt;ключ&gt; — сменить статус\n"
                 "ключи: new, wait_pay, invoice, paid, in_work, review, done, canceled\n"
                 "/sub &lt;uid&gt; on|off — подписка обслуживания\n"
                 "/broadcast &lt;текст&gt; — рассылка всем")
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
                lines.append(f"№{oid} · id {o['uid']} · {', '.join(o.get('items', []))} · {o.get('total', 0)} ₽ · {STATUS.get(o.get('status'), o.get('status'))}")
            send(chat_id, "\n".join(lines)); return
        if low.startswith("/order "):
            try:
                oid = int(low.split()[1])
            except Exception:
                send(chat_id, "Формат: /order &lt;№&gt;"); return
            o = order_get(oid)
            if not o:
                send(chat_id, "Заказ не найден."); return
            send(chat_id, f"📦 <b>Заказ №{oid}</b>\nКлиент id: {o['uid']}\nУслуги: {', '.join(o.get('items', []))}\nСумма: {o.get('total', 0)} ₽\nТип: {o.get('payment_type', '')}\nСтатус: {STATUS.get(o.get('status'), o.get('status'))}\nСоздан: {o.get('created', '')}")
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
                    send(o["uid"], "🎉 Ваш сайт готов! Пожалуйста, оцените наш сервис:", rating_kb())
            except Exception as e:
                print("notify client err", e)
            return
        if low.startswith("/sub "):
            parts = low.split()
            if len(parts) < 3 or parts[2] not in ("on", "off"):
                send(chat_id, "Формат: /sub &lt;uid&gt; on|off"); return
            try:
                tuid = int(parts[1])
            except Exception:
                send(chat_id, "uid должен быть числом."); return
            p = user_get(tuid)
            if not p:
                send(chat_id, "Пользователь не найден."); return
            if parts[2] == "on":
                today = time.strftime("%Y-%m-%d")
                p["subscription"] = {"active": True, "plan": "Обслуживание", "price": 1990,
                                     "since": today, "next": add_days(today, 30)}
                send(chat_id, f"✅ Подписка включена для id {tuid}.")
                try:
                    send(tuid, "✅ Подключено обслуживание сайта — 1 990 ₽/мес. Спасибо!")
                except Exception:
                    pass
            else:
                if p.get("subscription"):
                    p["subscription"]["active"] = False
                send(chat_id, f"Подписка выключена для id {tuid}.")
            user_save(tuid, p); return
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

    MENU_TRIGGERS = {"🌐 Получить сайт", "🔍 Бесплатный аудит", "🛒 Тарифы и услуги",
                     "📋 Что подготовить", "📊 Статус заказа", "💬 Вопрос менеджеру",
                     "👨‍💻 Разработчику", "🤝 Стать партнёром", "⭐ Оценить сервис",
                     "🔗 Сайт ONYX", "👤 Мой кабинет"}
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
    if text == "👤 Мой кабинет":
        send(chat_id, render_cabinet(uid), MAIN_MENU); return
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
        pu = user_get(uid)
        ref_link = f"https://t.me/{bot_username()}?start=ref{uid}"
        cnt = pu.get("referrals", 0)
        send(chat_id, PARTNER_INFO + f"\n\n🔗 Ваша ссылка: {ref_link}\n👥 Приведено: {cnt}",
             {"inline_keyboard": [[{"text": "✍️ Оставить контакт", "callback_data": "pt:start"}]]}); return
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
    if data == "cart:open":
        send(chat_id, cart_text(cart_get(uid)), cart_kb(cart_get(uid))); return
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
        if action == "info":
            send(chat_id, services_info_text()); return
        if action == "clear":
            cart = []
        elif action == "pay":
            if not cart:
                answer_cb(cq["id"], "Сначала выберите услуги"); return
            if not anketa_done(uid):
                send(chat_id, "Перед оплатой заполните короткую анкету (2 минуты) — так менеджер сразу подготовит всё под ваш проект.",
                     {"inline_keyboard": [[{"text": "📝 Заполнить анкету", "callback_data": "brief:start"}]]}); return
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
            upsert_user(uid, username=user.get("username"))
            oid = order_new(uid, items, total, "Физлицо", status="wait_pay")
            cart_set(uid, [])
            notify_manager(f"🛒 <b>Заказ №{oid} (физлицо)</b>\n💬 " + uname + f" (id {uid})\n" +
                           "\n".join(f"• {n}" for n in items) + f"\n\n<b>Итого: {total} ₽</b>")
            post_to_sheet(sheet_row("Заказ (физлицо)", tg_=uname, options=", ".join(items),
                                    amount=total, comment=f"Заказ №{oid}"))
            link = build_payment_link(cart, uid)
            if link:
                send(chat_id, f"Заказ <b>№{oid}</b> на <b>{total} ₽</b>.\nОплата картой или через СБП, чек придёт автоматически.",
                     {"inline_keyboard": [[{"text": f"💳 Оплатить {total} ₽", "url": link}]]})
            else:
                send(chat_id, f"Заказ <b>№{oid}</b> на <b>{total} ₽</b> принят. Менеджер пришлёт ссылку на оплату 🤝", MAIN_MENU)
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
        if "cron" in self.path:
            try:
                n = run_subscription_reminders()
            except Exception as e:
                print("cron err", e); n = -1
            self._ok(f"cron ok: {n}".encode("utf-8")); return
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
