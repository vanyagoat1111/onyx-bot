"""
ONYX WEB — Telegram-бот (Vercel, webhook).
Меню, строгая анкета с навигацией, корзина, оплата (физ/юр), формы, выгрузка в Google Sheets.
Только стандартная библиотека Python.
"""
import json, os, time, re, urllib.parse, urllib.request, hmac, hashlib, copy
from http.server import BaseHTTPRequestHandler

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MANAGER_CHAT_ID = os.environ.get("MANAGER_CHAT_ID", "")
SITE_URL = os.environ.get("SITE_URL", "https://onyx-web.ru/")
MANAGER_USERNAME = os.environ.get("MANAGER_USERNAME", "ONYXCOOP")
DEVELOPER_USERNAME = os.environ.get("DEVELOPER_USERNAME", "softstaticg")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
# Онлайн-оплата (Prodamus/ЮKassa и т.п.) ОТКЛЮЧЕНА. Оплата у клиентов — только по счёту.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")  # напр. https://onyx-bot-4xn3.vercel.app
CHECKLIST_PDF_URL = os.environ.get("CHECKLIST_PDF_URL", "")
CHECKLIST_URL = os.environ.get("CHECKLIST_URL", "")
SHEETS_WEBHOOK_URL = os.environ.get("SHEETS_WEBHOOK_URL", "")


def _parse_admin_ids():
    raw = (os.environ.get("ADMIN_IDS", "") + "," + os.environ.get("ADMIN_TELEGRAM_IDS", ""))
    return {int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()}


ADMIN_IDS = _parse_admin_ids()


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


def log_error(kind, detail="", notify=False):
    """Записать ошибку в кольцевой лог (для админ-раздела «Ошибки») и опц. уведомить админов."""
    try:
        line = f"{time.strftime('%d.%m %H:%M')} · {kind}: {str(detail)[:180]}"
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
    Используется только там, где не нужна reply-клавиатура (ReplyKeyboardMarkup) —
    её нельзя навесить через editMessageText, только через новый send()."""
    if mid:
        r = tg("editMessageText", chat_id=chat_id, message_id=mid, text=text,
               parse_mode="HTML", reply_markup=kb)
        if r and r.get("ok"):
            return r
    return send(chat_id, text, kb)


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


def notify_admins(text):
    """Уведомить всех админов; если админов нет — упасть на менеджера."""
    sent = False
    for aid in ADMIN_IDS:
        try:
            send(aid, text); sent = True
        except Exception as e:
            print("notify_admins err", e)
    if not sent and MANAGER_CHAT_ID:
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
# unit: "" — разово (без пометки), "разово" — явная пометка, "мес" — в месяц
# approx=True — цена «от …». mandatory=True — обязательная услуга для новых клиентов.
SERVICES = [
    {"id": "launch", "name": "Запуск сайта", "price": 3990, "unit": "разово", "mandatory": True,
     "short": "Домен, хостинг, SSL, публикация и техподдержка сайта.",
     "why": "Без запуска сайт не выйдет в интернет — это база, на которой держится всё остальное."},
    {"id": "pages", "name": "Дополнительные страницы", "price": 1500, "approx": True,
     "short": "Добавление отдельных страниц (о компании, услуги, галерея и т.д.). Цена за страницу.",
     "why": "Подробнее раскрывает услуги и повышает доверие к компании."},
    {"id": "design", "name": "Индивидуальный дизайн", "price": 15000, "approx": True,
     "short": "Уникальный дизайн под ваш бренд по вашему промпту.",
     "why": "Сайт выглядит дороже шаблонного и выделяет вас среди конкурентов."},
    {"id": "crm", "name": "Подключение CRM", "price": 5900,
     "short": "Связь сайта с вашей CRM — заявки попадают в систему автоматически.",
     "why": "Ни одна заявка не теряется, менеджер сразу видит нового клиента."},
    {"id": "pay", "name": "Онлайн-оплата", "price": 4990,
     "short": "Приём онлайн-оплаты прямо на сайте (карты, СБП).",
     "why": "Клиент платит сразу на сайте — быстрее сделки и меньше отказов."},
    {"id": "booking", "name": "Онлайн-запись", "price": 6990,
     "short": "Онлайн-запись для салонов, клиник и студий (подключаются сторонние сервисы).",
     "why": "Клиенты записываются сами 24/7 — разгружаете администратора."},
    {"id": "cart", "name": "Корзина / интернет-магазин", "price": 10790, "approx": True,
     "short": "Корзина и оформление заказов прямо на сайте.",
     "why": "Превращает сайт в интернет-магазин — продажи без участия менеджера."},
    {"id": "catalog", "name": "Каталог товаров", "price": 7990,
     "short": "Каталог ваших товаров или услуг на сайте.",
     "why": "Клиент видит ассортимент и цены — меньше вопросов, больше заявок."},
    {"id": "seo", "name": "SEO-настройка", "price": 6990,
     "short": "Базовая SEO-настройка: структура, мета-теги, индексация в поисковиках.",
     "why": "Сайт находят в Яндексе и Google — бесплатный поток клиентов из поиска."},
    {"id": "analytics", "name": "Настройка аналитики", "price": 3990,
     "short": "Подключение Яндекс Метрики и Google Analytics.",
     "why": "Видно, откуда приходят клиенты и где уходят — решения по цифрам, а не на ощущениях."},
    {"id": "tgnotify", "name": "Telegram-уведомления", "price": 3990,
     "short": "Новые заявки сразу приходят в Telegram владельца.",
     "why": "Реагируете на заявку за минуты, пока клиент ещё «горячий»."},
    {"id": "calc", "name": "Калькулятор стоимости", "price": 7990, "approx": True,
     "short": "Интерактивный калькулятор расчёта стоимости услуг.",
     "why": "Клиент сам считает цену и оставляет заявку с уже понятным бюджетом."},
    {"id": "multilang", "name": "Мультиязычность", "price": 2490,
     "short": "Несколько языков на сайте (например, RU / EN).",
     "why": "Расширяет аудиторию и работает на клиентов из других стран."},
    {"id": "maps", "name": "Карты и геосервисы", "price": 3900, "approx": True,
     "short": "Интерактивная карта (Яндекс / Google) с вашим адресом.",
     "why": "Клиенту проще вас найти — важно для офлайн-бизнеса."},
    {"id": "docs", "name": "Правовые документы", "price": 2900, "approx": True,
     "short": "Политика конфиденциальности, оферта, согласия на обработку данных.",
     "why": "Защищает от штрафов по 152-ФЗ и повышает доверие к сайту."},
    {"id": "smm", "name": "SMM ONYX", "price": 50000, "unit": "мес", "approx": True,
     "short": "Ведение соцсетей вашего бизнеса под ключ в ONYX SMM.",
     "why": "Постоянный поток аудитории и заявок из соцсетей без вашего участия."},
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
                      "Приступаем к работе — команда ONYX свяжется с вами по старту 🚀", MAIN_MENU)
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
    purchased = p.get("purchased_services") or []
    if "launch" in purchased:
        return True
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
            "Разработка сайта — <b>0 ₽</b>. Ниже — запуск и опции для развития.\n"
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
    rows.append([{"text": "🏠 Меню", "callback_data": "b:home"}])
    return {"inline_keyboard": rows}


# --- Карточка одной услуги ---
def service_card_text(cid, uid):
    s = SERVICE.get(cid)
    if not s:
        return "Услуга не найдена."
    in_cart = cid in cart_get(uid)
    lines = [f"<b>{s['name']}</b>",
             f"💰 Стоимость: <b>{fmt_price(s)}</b>", "",
             s["short"], "",
             f"🎯 <b>Зачем бизнесу:</b> {s['why']}"]
    if s.get("mandatory") and cid in mandatory_ids(uid):
        lines.append("\n🔒 Обязательная услуга для запуска нового сайта.")
    if in_cart:
        lines.append("\n✅ Уже в корзине.")
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
        line = f"• {s['name']} — {fmt_price(s)}"
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
    "created": {"label": "🆕 Заказ создан",
                "desc": "Ваш заказ оформлен. Как только оплата будет подтверждена, мы приступим к работе.",
                "eta": "—"},
    "waiting_payment": {"label": "⏳ Ожидает оплаты",
                        "desc": "Ждём оплату по заказу. После оплаты сразу берём проект в работу.",
                        "eta": "—"},
    "waiting_invoice": {"label": "🧾 Ожидает оплаты по счёту",
                        "desc": "Мы готовим счёт для вашей организации. После оплаты счёта начнём работу.",
                        "eta": "—"},
    "paid_waiting_start": {"label": "💰 Оплачен, ожидает старта",
                           "desc": "Оплата получена, заказ в очереди на старт. Мы начнём работу в ближайшее время.",
                           "eta": "до 1 рабочего дня"},
    "questionnaire_review": {"label": "📋 Изучаем анкету",
                             "desc": "Наша команда изучает вашу анкету и готовит структуру будущего сайта под ваш бизнес.",
                             "eta": "до 1 рабочего дня"},
    "in_production": {"label": "🛠 Сайт в разработке",
                      "desc": "Ваш сайт сейчас находится в разработке. Мы собираем структуру, тексты и основные блоки сайта. Обычно этот этап занимает 1–3 рабочих дня.",
                      "eta": "1–3 рабочих дня"},
    "design_review": {"label": "🎨 Проверяем дизайн и структуру",
                      "desc": "Сайт собран — проверяем дизайн, структуру и удобство на всех устройствах.",
                      "eta": "до 1 рабочего дня"},
    "domain_setup": {"label": "🌐 Подключаем домен",
                     "desc": "Мы подключаем домен и проверяем корректную работу сайта. Обычно этот этап занимает до 24 часов.",
                     "eta": "до 24 часов"},
    "final_check": {"label": "✅ Финальная проверка",
                    "desc": "Финальная проверка сайта перед публикацией: скорость, формы заявок, отображение на телефоне.",
                    "eta": "до 24 часов"},
    "completed": {"label": "🎉 Сайт готов",
                  "desc": "Ваш сайт готов и опубликован! Проверьте его и поделитесь впечатлением.",
                  "eta": "—"},
    "paused": {"label": "⏸ Проект на паузе",
               "desc": "Проект временно на паузе. Мы свяжемся с вами по дальнейшим шагам.",
               "eta": "—"},
    "cancelled": {"label": "❌ Заказ отменён",
                  "desc": "Заказ отменён. Если это ошибка — напишите в поддержку, мы поможем.",
                  "eta": "—"},
}

# Старые ключи статусов -> новые (для показа старых заказов в новом разделе)
STATUS_ALIAS = {
    "new": "created", "wait_pay": "waiting_payment", "invoice": "waiting_invoice",
    "paid": "paid_waiting_start", "in_work": "in_production", "review": "design_review",
    "done": "completed", "canceled": "cancelled",
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
    return PROJECT_STATUS.get(key, {}).get("eta", "—")


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


# ------------------------- Админ / подписки / рефералы / услуги -------------------------
ADMIN_IDS = _parse_admin_ids()
_BOT_USERNAME = [os.environ.get("BOT_USERNAME", "") or None]
PRCY_API_KEY = os.environ.get("PRCY_API_KEY", "")
# PR-CY: двухшаговый API (POST задача -> GET результат). Эндпоинт и имя инструмента вынесены в env,
# т.к. точный toolName для «Анализа сайта» указан в личной документации PR-CY (по вашему ключу).
PRCY_API_URL = os.environ.get("PRCY_API_URL", "https://apis.pr-cy.ru/api/v2.1.0/tool-tasks/")
PRCY_TOOL_NAME = os.environ.get("PRCY_TOOL_NAME", "analysis")
PRCY_WAIT_SEC = int(os.environ.get("PRCY_WAIT_SEC", "8") or 8)
# AI-резюме: по умолчанию Anthropic; для OpenAI задайте AI_API_URL с /chat/completions
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_API_URL = os.environ.get("AI_API_URL", "https://api.anthropic.com/v1/messages")
AI_MODEL = os.environ.get("AI_MODEL", "claude-sonnet-5")


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
# Подписки, автоплатежи и напоминания об оплате удалены. Оплата — только по счёту, разово.


def services_info_text():
    parts = ["ℹ️ <b>Услуги ONYX — что входит</b>"]
    for s in SERVICES:
        parts.append(f"<b>{s['name']} — {fmt_price(s)}</b>\n{s['short']}")
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
    "Если у вас возник вопрос по сайту, заказу, оплате или работе сервиса, "
    "вы можете написать нам.\n\n"
    f"Служба поддержки ONYX: @{MANAGER_USERNAME}\n"
    f"Разработчик вашего проекта: @{DEVELOPER_USERNAME}"
)


def support_kb():
    return {"inline_keyboard": [
        [{"text": "🆘 Написать в поддержку", "url": f"https://t.me/{MANAGER_USERNAME}"}],
        [{"text": "📨 Создать обращение", "callback_data": "sup:new"}],
        [{"text": "🗂 Мои обращения", "callback_data": "sup:my"}],
        [{"text": "❓ Помощь / FAQ", "callback_data": "sup:faq"}],
        [{"text": "👨‍💻 Написать разработчику", "url": f"https://t.me/{DEVELOPER_USERNAME}"}],
        [{"text": "🏠 Назад в меню", "callback_data": "b:home"}],
    ]}
# ONYX_INFO — удалён: раздел заменён (см. «Поддержка» / «Информативный ONYX»).
CABINET_KB = {"inline_keyboard": [
    [{"text": "👤 Мой профиль", "callback_data": "cab:profile"}],
    [{"text": "🛍 Мои покупки", "callback_data": "cab:orders"}],
    [{"text": "⭐ Оценить сервис", "callback_data": "cab:review"}],
    [{"text": "🆘 Поддержка", "callback_data": "cab:support"}],
    [{"text": "ℹ️ Информативный ONYX", "callback_data": "cab:info"}],
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
        lines.append("— пока нет")
    lines.append("")

    lines.append("✅ <b>Купленные услуги:</b>")
    if purchased:
        for cid in purchased:
            lines.append(f"• {SERVICE.get(cid, {}).get('name', cid)}")
    else:
        lines.append("— пока нет")
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
            items = ", ".join(SERVICE.get(c, {}).get("name", c) for c in o.get("items", [])) or "—"
            lines.append(f"№{oid} — {items} — {fmt_amount(o.get('total', 0))} — {ps_label(key)}")
    return "\n".join(lines), sub_kb


# ------------------------- Этап 7: Бесплатный аудит (PR-CY + AI) -------------------------
AUDIT_STATUS = ("created", "waiting_prcy", "prcy_received", "ai_summary_ready", "sent_to_client", "failed")

AUDIT_INTRO = ("🔍 <b>Бесплатный аудит сайта</b>\n\n"
               "Отправьте ссылку на ваш сайт, и мы подготовим краткий аудит: что мешает сайту "
               "приводить заявки, какие слабые места есть сейчас и что можно улучшить.")

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
def _prcy_req(url, method="GET", payload=None):
    if not PRCY_API_KEY:
        return None
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/vnd.api+json",
        "Api-Key": PRCY_API_KEY,
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def prcy_create_task(url):
    """POST: создать задачу анализа. Возвращает task_id или None."""
    try:
        res = _prcy_req(PRCY_API_URL, "POST", {"data": {
            "type": "toolTasks",
            "attributes": {"toolName": PRCY_TOOL_NAME,
                           "params": {"domain": url_domain(url)}},
        }})
        return ((res or {}).get("data") or {}).get("id")
    except Exception as e:
        print("PRCY create err:", e)
        return None


def prcy_fetch_task(task_id):
    """GET: забрать результат. (готово?, данные)"""
    try:
        base = PRCY_API_URL.rstrip("/")
        res = _prcy_req(f"{base}/{task_id}?include=tests")
        attrs = ((res or {}).get("data") or {}).get("attributes") or {}
        if attrs.get("isUpdating"):
            return False, None
        return True, res
    except Exception as e:
        print("PRCY fetch err:", e)
        return False, None


def prcy_weak_points(raw):
    """Извлечь проблемные тесты из ответа PR-CY. Только то, что реально пришло."""
    points = []
    for t in (raw or {}).get("included", []) or []:
        attrs = t.get("attributes") or t
        name = attrs.get("name") or t.get("id") or ""
        status = str(attrs.get("status", "")).lower()
        if status and status not in ("success", "ok", "info"):
            points.append({"name": name, "status": status,
                           "results": attrs.get("results")})
    return points


# ---- Рекомендации и цены (только из нашего прайса, без выдумок) ----
AUDIT_RULES = [
    (("mobile", "adapt", "viewport", "responsive"), "Сайт неудобен на телефоне",
     "Большинство клиентов заходят со смартфона. Если сайт «едет» или мелкий текст — человек уходит, не дочитав.",
     ["design"]),
    (("speed", "load", "performance", "pagespeed", "ttfb"), "Сайт медленно загружается",
     "Каждая лишняя секунда загрузки увеличивает число тех, кто закрывает страницу до её открытия.",
     ["launch"]),
    (("ssl", "https", "certificate", "security"), "Проблемы с безопасностью (SSL/HTTPS)",
     "Браузер помечает такой сайт как небезопасный — это сразу убивает доверие и заявки.",
     ["launch"]),
    (("meta", "title", "description", "h1", "seo", "index", "robots", "sitemap"), "Слабая SEO-основа",
     "Сайт хуже находится в Яндексе и Google — вы теряете бесплатный поток клиентов из поиска.",
     ["seo"]),
    (("counter", "metrika", "analytics", "goal"), "Не подключена аналитика",
     "Без аналитики не видно, откуда приходят клиенты и где они уходят — решения принимаются вслепую.",
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
    ("Первый экран не объясняет ценность", "За 5 секунд клиент должен понять, кто вы и чем полезны — иначе он закроет сайт.", ["pages"]),
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
                        ["launch"]))
    if not out:
        for title, why, svcs in AUDIT_FALLBACK:
            out.append((title, why, svcs))
            services += svcs
    out = out[:5]
    services = [s for s in dict.fromkeys(services) if s in SERVICE]
    return out, services


def audit_price(services):
    """Стоимость в ONYX по нашему прайсу + ориентировочное сравнение с рынком."""
    svc = [s for s in services if s in SERVICE] or ["launch"]
    total = sum(SERVICE[s]["price"] for s in svc)
    onyx = f"от {fmt_amount(total)} (разработка сайта — 0 ₽, вы платите только за нужные опции)"
    market = (f"ориентировочно {fmt_amount(total * 3)} – {fmt_amount(total * 6)} — "
              "в большинстве студий разработка оплачивается отдельно")
    return svc, onyx, market


# ---- AI-резюме ----
def ai_summarize(url, points_raw, weak, services):
    """Короткое резюме. Если AI_API_KEY не задан — детерминированная заглушка без выдумок."""
    weak_txt = "; ".join(t for t, _, _ in weak)
    if not AI_API_KEY:
        return (f"Мы посмотрели сайт {url_domain(url)}. Это предварительная оценка по открытым техническим "
                f"данным. Основное, что может мешать заявкам: {weak_txt.lower()}. "
                "Точную картину покажет ручная проверка нашей командой.")
    facts = json.dumps(points_raw, ensure_ascii=False)[:4000]
    prompt = (
        "Ты — эксперт веб-студии ONYX. Напиши КРАТКОЕ резюме аудита сайта простыми словами "
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
    return {"inline_keyboard": [
        [{"text": "📩 Получить предложение", "callback_data": "audit:offer"}],
        [{"text": "🛠 Заказать исправления", "callback_data": "audit:fix"}],
        [{"text": "🌐 Заказать новый сайт", "callback_data": "brief:start"}],
        [{"text": "🆘 Написать в поддержку", "callback_data": "myorder:support"},
         {"text": "🏠 Назад в меню", "callback_data": "b:home"}],
    ]}


def render_audit(a, weak, services, onyx_price, market):
    lines = [f"🔍 <b>Мы проверили ваш сайт: {url_domain(a['website_url'])}</b>", "",
             "<b>Краткий вывод:</b>", a.get("ai_summary", ""), "",
             "<b>Что может мешать заявкам:</b>"]
    for i, (title, why, _s) in enumerate(weak, 1):
        lines.append(f"\n{i}. <b>{title}</b>\nПочему это важно: {why}")
    lines.append("\n<b>Что может сделать ONYX:</b>")
    for s in services[:6]:
        lines.append(f"— {SERVICE[s]['name'].lower()}")
    lines.append(f"\n<b>Стоимость в ONYX:</b>\n{onyx_price}")
    lines.append(f"\n<b>В других студиях подобные доработки могут стоить:</b>\n{market}")
    lines.append("\n<i>Это предварительная оценка по открытым данным — не гарантия и не точный расчёт.</i>")
    lines.append("\nХотите, чтобы мы подготовили предложение по улучшению вашего сайта?")
    return "\n".join(lines)


def audit_finish(a, raw):
    """PR-CY данные получены -> слабые места -> AI -> отправка клиенту."""
    a["prcy_raw_result"] = json.dumps(raw, ensure_ascii=False)[:45000] if raw else ""
    a["status"] = "prcy_received"
    audit_save(a, to_sheet=False)

    points = prcy_weak_points(raw)
    weak, services = audit_recommendations(points)
    services, onyx_price, market = audit_price(services)
    a["ai_summary"] = ai_summarize(a["website_url"], points, weak, services)
    a["weak_points"] = "; ".join(t for t, _, _ in weak)
    a["recommended_services"] = ", ".join(SERVICE[s]["name"] for s in services)
    a["estimated_onyx_price"] = onyx_price
    a["market_price_comparison"] = market
    a["status"] = "ai_summary_ready"
    audit_save(a, to_sheet=False)

    send(a["telegram_id"], render_audit(a, weak, services, onyx_price, market), audit_result_kb())
    a["status"] = "sent_to_client"
    audit_save(a)
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
    notify_admins(f"🔍 <b>Новый аудит №{a['audit_id']}</b>\n"
                  f"Сайт: {a['website_url']}\n"
                  f"Клиент: id {a['telegram_id']} {('@' + a['username']) if a.get('username') else ''}\n"
                  f"Слабые места: {a['weak_points']}\n"
                  f"Рекомендуем: {a['recommended_services']}\n"
                  f"Оценка ONYX: {onyx_price}")
    return a


def audit_fail(a, reason=""):
    a["status"] = "failed"
    audit_save(a)
    send(a["telegram_id"], AUDIT_UNAVAILABLE,
         {"inline_keyboard": [[{"text": "🌐 Заказать сайт", "callback_data": "brief:start"}],
                              [{"text": "🏠 Назад в меню", "callback_data": "b:home"}]]})
    create_task("audit", a.get("audit_id", ""), f"Сделать аудит вручную: {a.get('website_url', '')}",
                description=f"Причина: {reason or 'PR-CY недоступен'}",
                telegram_id=a.get("telegram_id"), priority="high", notify=False)
    notify_admins(f"⚠️ <b>Аудит №{a['audit_id']} — нужен ручной разбор</b>\n"
                  f"Сайт: {a['website_url']}\n"
                  f"Клиент: id {a['telegram_id']} {('@' + a['username']) if a.get('username') else ''}\n"
                  f"Причина: {reason or 'PR-CY недоступен'}")
    return a


def audit_start(chat_id, uid, url, username=""):
    a = audit_new(uid, username, url)
    lead_touch(uid, username=username, status="audit_requested", action="audit_requested")
    send(chat_id, f"🔎 Проверяем сайт <b>{url_domain(url)}</b>… Это займёт до минуты.")
    if not PRCY_API_KEY:
        audit_fail(a, "PRCY_API_KEY не задан")
        return a
    task_id = prcy_create_task(url)
    if not task_id:
        audit_fail(a, "PR-CY не принял задачу")
        return a
    a["prcy_task_id"] = task_id
    a["status"] = "waiting_prcy"
    audit_save(a, to_sheet=False)
    # Ограниченное ожидание внутри запроса (Vercel обрывает долгие вызовы)
    deadline = time.time() + PRCY_WAIT_SEC
    while time.time() < deadline:
        time.sleep(2)
        ready, raw = prcy_fetch_task(task_id)
        if ready and raw:
            return audit_finish(a, raw)
    # Не успели — дожмём в cron
    audits_pending_add(a["audit_id"])
    audit_save(a)
    send(chat_id, "⏳ Анализ занимает чуть больше времени — пришлём результат сюда, как только будет готов.")
    return a


def run_pending_audits():
    """Cron: дожать аудиты, которые PR-CY не успел посчитать в момент запроса."""
    n = 0
    for aid in audits_pending_all():
        a = audit_get(aid)
        if not a or a.get("status") in ("sent_to_client", "failed"):
            audits_pending_remove(aid)
            continue
        task_id = a.get("prcy_task_id")
        if not task_id:
            audits_pending_remove(aid)
            audit_fail(a, "нет task_id")
            continue
        ready, raw = prcy_fetch_task(task_id)
        if ready and raw:
            audit_finish(a, raw)
            audits_pending_remove(aid)
            n += 1
        elif days_between(a.get("created_at", "")[:10].replace(".", "-"), today_str()) != 0:
            pass  # ждём следующий запуск
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
    names = ", ".join(SERVICE.get(c, {}).get("name", c) for c in o.get("items", [])) or "—"
    pay = PAY_STATUS_RU.get(o.get("payment_status", "pending"), o.get("payment_status", "—"))
    lines = [
        f"📦 <b>Заказ №{o['id']}</b>",
        f"📅 Создан: {o.get('created', '—')}",
        f"🧩 Услуги: {names}",
        f"💰 Сумма: {fmt_amount(o.get('total', 0))}",
        f"💳 Оплата: {pay}",
        "",
        f"📈 <b>Статус проекта:</b> {ps_label(key)}",
        ps_desc(key),
    ]
    if ps_eta(key) and ps_eta(key) != "—":
        lines.append(f"⏱ Примерный срок: {ps_eta(key)}")
    lines.append(f"👨‍💻 Разработчик: @{DEVELOPER_USERNAME}")
    if o.get("urgent_request"):
        lines.append("\n⚡ Запрос на ускорение принят — команда проверяет возможность.")
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
    uname = f"@{user.get('username')}" if user.get("username") else "—"
    names = ", ".join(SERVICE.get(c, {}).get("name", c) for c in o.get("items", [])) or "—"
    cm = o.get("service_comments") or {}
    cm_txt = " | ".join(f"{SERVICE.get(c, {}).get('name', c)}: {t}" for c, t in cm.items()) or "—"
    p = user_get(uid) or {}
    sheet_post("ClientRequests", {
        "type": "urgent", "date": now_str(), "telegram_id": uid, "username": uname,
        "order_id": oid, "project_status": key, "services": names, "comment": cm_txt,
    })
    log_event(uid, "urgent", str(oid))
    create_task("order", oid, f"Проверить возможность ускорения (заказ №{oid})",
                telegram_id=uid, order_id=oid, priority="urgent", notify=False)
    notify_admins("⚡ <b>Клиент просит ускорить проект:</b>\n"
                  f"Клиент: {p.get('name', '—')}\n"
                  f"Telegram: {uname}\n"
                  f"Order ID: {oid}\n"
                  f"Статус проекта: {ps_label(key)}\n"
                  f"Услуги: {names}\n"
                  f"Комментарий: {cm_txt}")
    send(chat_id, "Мы получили ваш запрос. Команда ONYX проверит возможность ускорения "
                  "проекта и свяжется с вами.", MAIN_MENU)


def on_order_completed(o):
    """Спец-логика при статусе completed: уведомление, отзыв, активация услуг."""
    cuid = o.get("uid")
    if not cuid:
        return
    mark_purchased(cuid, o.get("items", []))  # активируем купленные услуги в профиле
    send(cuid, "🎉 <b>Ваш сайт готов!</b>\nПроверьте его и убедитесь, что всё нравится 👇",
         {"inline_keyboard": [[{"text": "🌐 Открыть сайт", "url": SITE_URL}]]})
    p = user_get(cuid) or {}
    review_start(cuid, cuid, p.get("username", ""), order_id=o.get("id"), intro=True)


def apply_project_status(oid, key, notify=True):
    """Сменить статус проекта, сохранить в Orders, уведомить клиента."""
    o = order_get(oid)
    if not o:
        return None
    o["status"] = key
    o["updated"] = now_str()
    if key in ("paid_waiting_start", "completed") and not o.get("paid") and o.get("payment_status") == "paid":
        o["paid"] = True
    order_save(o)
    sheet_order(o)
    cuid = o.get("uid")
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
    _set(f"onyx:quest:{uid}", d, ttl=YEAR)  # локально — для промпта дизайна и модуля домена
    sheet_post("Questionnaire", {
        "questionnaire_id": f"Q{uid}-{int(time.time())}", "telegram_id": uid,
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
    return "\n".join(lines)


# ------------------------- Тексты -------------------------
WELCOME = (
    "👋 <b>ONYX WEB — сайты для бизнеса</b>\n\n"
    "<b>Разработка — 0 ₽.</b> Вы платите только за домен, хостинг и доп.опции."
)
WELCOME_KB = {"inline_keyboard": [[{"text": "✅ Заполнить заявку на сайт", "callback_data": "brief:start"}]]}
# CHECKLIST — удалён: раздел заменён (см. «Поддержка» / «Информативный ONYX»).
TARIFFS_INFO = (
    "💰 <b>Оффер ONYX WEB</b>\n\n"
    "• Разработка сайта — <b>0 ₽</b>\n"
    "• Запуск (разово) — 3 990 ₽\n"
    "• Доп.опции — по желанию (см. «🛒 Тарифы и услуги»)\n\n"
    "Оплата — по счёту (для физлиц и юрлиц).\n"
    f"Подробнее: {SITE_URL}"
)
# PARTNER_INFO удалён — заменён на PARTNER_TEXT / PARTNER_HOW (Этап 9).


# ------------------------- Главное меню -------------------------
MAIN_MENU = {"keyboard": [
    [{"text": "🔍 Бесплатный аудит"}],
    [{"text": "🛒 Тарифы и услуги"}, {"text": "📦 Мой заказ"}],
    [{"text": "👤 Личный кабинет"}, {"text": "🤝 Стать партнёром"}],
    [{"text": "🆘 Поддержка"}],
], "resize_keyboard": True}


def main_menu(chat_id, text=WELCOME):
    send(chat_id, text, MAIN_MENU)


# ------------------------- Анкета -------------------------
BRIEF_STEPS = [
    {"key": "company_name", "icon": "🏢", "q": "Как называется ваша компания?", "hint": "Например: «Barbershop Ryzhiy»", "text": True},
    {"key": "niche", "icon": "🧭", "q": "В какой нише вы работаете?", "hint": "Например: юридические услуги, кофейня, барбершоп", "text": True},
    {"key": "city", "icon": "📍", "q": "В каком городе вы работаете?", "text": True},
    {"key": "business_description", "icon": "📝", "q": "Чем занимается компания? Опишите в двух словах.", "text": True},
    {"key": "main_services", "icon": "🛠", "q": "Основные услуги / товары?", "hint": "Перечислите через запятую", "text": True},
    {"key": "priority_services", "icon": "⭐", "q": "Что важнее всего продавать в первую очередь?", "text": True},
    {"key": "show_prices", "icon": "💰", "q": "Нужно ли указывать цены на сайте?", "opts": ["Да, показывать цены", "Нет", "Только «от …» / по запросу"]},
    {"key": "advantages", "icon": "🏆", "q": "Почему клиенты выбирают именно вас?", "hint": "Ваши сильные стороны, преимущества", "text": True},
    {"key": "target_audience", "icon": "🎯", "q": "Кто ваши клиенты?", "hint": "Опишите целевую аудиторию", "text": True},
    {"key": "main_action", "icon": "👉", "q": "Какое главное действие должен совершить посетитель сайта?", "opts": ["📞 Позвонить", "📝 Оставить заявку", "💬 Написать в Telegram", "🗓 Записаться", "🛒 Купить товар"]},
    {"key": "special_offer", "icon": "🎁", "q": "Есть акция или спецпредложение?", "hint": "Необязательно — можно пропустить", "text": True, "opt": True},
    {"key": "style_preferences", "icon": "🎨", "q": "Какой стиль сайта вам нравится?", "hint": "Строгий, яркий, минимализм... Необязательно", "text": True, "opt": True},
    {"key": "color_preferences", "icon": "🌈", "q": "Есть пожелания по цветам?", "hint": "Необязательно", "text": True, "opt": True},
    {"key": "reference_sites", "icon": "🔗", "q": "Скиньте ссылки на сайты, которые вам нравятся", "hint": "Необязательно", "text": True, "opt": True},
    {"key": "phone", "icon": "📞", "q": "Ваш телефон для связи?", "text": True, "contact": True},
    {"key": "messengers", "icon": "✈️", "q": "Telegram или WhatsApp для связи?", "text": True},
    {"key": "email", "icon": "📧", "q": "Email для связи?", "hint": "Необязательно", "text": True, "opt": True},
    {"key": "address", "icon": "🏠", "q": "Адрес (если есть офис/точка)?", "hint": "Необязательно", "text": True, "opt": True},
    {"key": "working_hours", "icon": "🕒", "q": "График работы?", "hint": "Необязательно", "text": True, "opt": True},
    {"key": "social_links", "icon": "📱", "q": "Ссылки на ваши соцсети?", "hint": "Необязательно", "text": True, "opt": True},
    {"key": "has_logo", "icon": "🖼", "q": "У вас уже есть логотип?", "opts": ["Да, есть", "Нет"]},
    {"key": "has_photos", "icon": "📸", "q": "Есть фото для сайта?", "opts": ["Да, есть", "Нет"]},
    {"key": "additional_functions", "icon": "⚙️", "q": "Какие доп.функции нужны на сайте?", "hint": "CRM, онлайн-оплата, онлайн-запись, корзина, каталог, аналитика, Telegram-уведомления. Необязательно", "text": True, "opt": True},
    {"key": "has_domain", "icon": "🌐", "q": "У вас уже есть домен?", "opts": ["Да, есть", "Нет"]},
    {"key": "domain_name", "icon": "🔤", "q": "Укажите ваш домен", "hint": "Необязательно", "text": True, "opt": True},
    {"key": "must_have", "icon": "✅", "q": "Что обязательно должно быть на сайте?", "hint": "Необязательно", "text": True, "opt": True},
    {"key": "must_not_have", "icon": "🚫", "q": "Чего точно не должно быть на сайте?", "hint": "Необязательно", "text": True, "opt": True},
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
}


def brief_progress_bar(i, n):
    filled = round((i / n) * 10)
    return "🟩" * filled + "⬜️" * (10 - filled)


def brief_render(st):
    i = st["i"]
    step = BRIEF_STEPS[i]
    n = len(BRIEF_STEPS)
    bar = brief_progress_bar(i, n)
    pct = int((i / n) * 100)
    head = (f"{bar}  <b>{pct}%</b>\n\n"
            f"{step.get('icon','📝')} <b>Вопрос {i+1} из {n}</b>\n\n"
            f"<b>{step['q']}</b>")
    if step.get("hint"):
        head += f"\n<i>{step['hint']}</i>"
    nav = []
    if step.get("opt"):
        nav.append({"text": "⏭ Пропустить", "callback_data": "b:skip"})
    if i > 0:
        nav.append({"text": "⬅️ Назад", "callback_data": "b:back"})
    nav.append({"text": "❌ Отменить", "callback_data": "b:cancel"})
    if step.get("opts"):
        kb_rows = [[{"text": o, "callback_data": f"b:o:{idx}"}] for idx, o in enumerate(step["opts"])]
        kb_rows.append(nav)
    else:
        kb_rows = [nav]
    return head, {"inline_keyboard": kb_rows}


def brief_push(chat_id, uid, st, force_send=False):
    """Показать текущий шаг анкеты — редактируя предыдущее сообщение, а не спамя новыми."""
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
    step = BRIEF_STEPS[st["i"]]
    n = len(BRIEF_STEPS)
    bar = brief_progress_bar(st["i"], n)
    pct = int((st["i"] / n) * 100)
    head = (f"{bar}  <b>{pct}%</b>\n\n"
            f"{step.get('icon','📝')} <b>Вопрос {st['i']+1} из {n}</b>\n\n"
            f"<b>{step['q']}</b>")
    kb_rows = []
    for j, o in enumerate(step["opts"]):
        kb_rows.append([{"text": f"✅ {o}" if j == idx else o,
                         "callback_data": "b:noop" if j == idx else f"b:o:{j}"}])
    mid = st.get("mid")
    if mid:
        tg("editMessageText", chat_id=chat_id, message_id=mid, text=head,
           parse_mode="HTML", reply_markup={"inline_keyboard": kb_rows})
    time.sleep(0.35)


def brief_summary_text(data):
    n = len(BRIEF_STEPS)
    bar = "🟩" * 10
    lines = ["🎉 <b>Анкета почти готова!</b>", f"{bar}  <b>100%</b>", "", "Проверьте ответы:"]
    for step in BRIEF_STEPS:
        k = step["key"]
        val = (data.get(k) or "").strip()
        mark = "✅" if val else "➖"
        lines.append(f"{mark} <b>{BRIEF_LABELS.get(k, k)}:</b> {val or '—'}")
    lines.append("")
    lines.append("Всё верно?")
    return "\n".join(lines)


def show_brief_summary(chat_id, uid, st):
    st["stage"] = "summary"
    text = brief_summary_text(st["data"])
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
    if st["i"] >= len(BRIEF_STEPS):
        show_brief_summary(chat_id, uid, st)
    else:
        brief_push(chat_id, uid, st)


def finish_brief(chat_id, user, data, mid=None):
    username = f"@{user.get('username')}" if user.get("username") else "—"
    uid = user.get("id")
    contact = data.get("phone") or data.get("messengers") or ""
    upsert_user(uid, name=data.get('company_name'), contact=contact, username=user.get("username"))
    mark_questionnaire_filled(uid, data)
    sheet_questionnaire(uid, username, data)
    log_event(uid, "questionnaire_done")
    lead_touch(uid, username=user.get("username"), name=data.get("company_name"),
               status="questionnaire_completed", action="questionnaire_done")
    cancel_followups(uid, ("questionnaire_abandoned", "idle_after_start"))
    try:
        domain_from_questionnaire(uid, data)  # доменный модуль
    except Exception as e:
        print("domain_from_questionnaire err", e)
    recompute_tags(uid)
    notify_manager(
        "🔔 <b>Новая анкета ONYX</b>\n\n"
        f"🏢 Компания: {data.get('company_name','—')}\n"
        f"🧭 Ниша: {data.get('niche','—')} • Город: {data.get('city','—')}\n"
        f"💬 Telegram: {username} (id {uid})\n"
        f"📞 Телефон: {data.get('phone','—')} • Мессенджеры: {data.get('messengers','—')}\n"
        f"📝 Деятельность: {data.get('business_description','—')}\n"
        f"🛠 Услуги: {data.get('main_services','—')}\n"
        f"🎯 Аудитория: {data.get('target_audience','—')} • Действие: {data.get('main_action','—')}\n"
        f"🌐 Домен: {data.get('has_domain','—')} {data.get('domain_name','')}\n"
        f"⚙️ Доп.функции: {data.get('additional_functions','—')}"
    )
    closing = "🎉 <b>Анкета заполнена!</b>\nТеперь вы можете выбрать услуги и перейти к оформлению заказа."
    nav = [[{"text": "👤 Личный кабинет", "callback_data": "b:cab"}],
           [{"text": "🛒 Тарифы и услуги", "callback_data": "svc:list"}]]
    if cart_get(uid):
        nav.insert(0, [{"text": "💳 К оформлению заказа", "callback_data": "cart:open"}])
    if mid:
        tg("editMessageText", chat_id=chat_id, message_id=mid, text=closing,
           parse_mode="HTML", reply_markup={"inline_keyboard": nav})
    else:
        send(chat_id, closing, {"inline_keyboard": nav})
    main_menu(chat_id, "Выберите, что дальше 👇")


def brief_text_input(chat_id, user, st, text, contact, msg_id=None):
    uid = user["id"]
    step = BRIEF_STEPS[st["i"]]
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
CAP = {
    "legal": {"steps": [("company", "Название компании / ИП:"),
                        ("inn", "ИНН компании:"),
                        ("email", "E-mail для выставления счёта:")],
              "type": "Счёт юрлицу",
              "done": "🧾 Спасибо! Мы подготовим счёт на реквизиты и пришлём его на указанную почту. "
                      "После оплаты счёта мы сразу запустим работу над сайтом."},
    "individual": {"steps": [("name", "На кого выставить счёт? Укажите ФИО:"),
                             ("phone", "Телефон для связи:"),
                             ("email", "E-mail для отправки счёта:")],
                   "type": "Счёт физлицу",
                   "done": "🧾 Спасибо! Мы подготовим счёт и пришлём его на указанную почту. "
                           "После оплаты счёта мы сразу запустим работу над сайтом."},
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
    uid = user.get("id")
    username = f"@{user.get('username')}" if user.get("username") else "—"
    cfg = CAP[kind]
    payer = "Юрлицо" if kind == "legal" else "Физлицо"
    comment = " | ".join(f"{k}: {v}" for k, v in data.items() if not k.startswith("_"))
    extra = ""
    amount = ""
    target_oid = None
    reqs = {"company": data.get("company", ""), "inn": data.get("inn", ""),
            "email": data.get("email", ""), "payer_name": data.get("name", ""),
            "phone": data.get("phone", "")}
    if st.get("order_id"):
        # реквизиты к уже оформленному заказу
        o = order_get(st["order_id"])
        if o:
            o.update({**reqs, "payment_type": payer, "payment_method": "Счёт",
                      "payment_status": "invoice_requested", "status": "waiting_invoice",
                      "updated": now_str()})
            order_save(o)
            sheet_order(o)
            target_oid = o["id"]
            names = ", ".join(SERVICE.get(c, {}).get("name", c) for c in o.get("items", []))
            amount = o.get("total", "")
            extra = f"\nЗаказ №{o['id']}: {names} — {fmt_amount(o.get('total', 0))}"
            comment += extra
    elif st.get("cart"):
        items = [ITEM[c][0] for c in st["cart"] if c in ITEM]
        total = cart_total(st["cart"])
        oid = order_new(uid, items, total, payer, status="waiting_invoice",
                        payment_method="Счёт",
                        extra={**reqs, "payment_status": "invoice_requested"})
        cart_set(uid, [])
        target_oid = oid
        amount = total
        extra = f"\nЗаказ №{oid}: " + ", ".join(items) + f" — {total} ₽"
        comment += extra
    upsert_user(uid, name=data.get("name") or None,
                contact=data.get("phone") or data.get("email"), username=user.get("username"))
    # аналитика/лид/задача на выставление счёта
    log_event(uid, "invoice_requested", str(target_oid or ""))
    lead_touch(uid, username=user.get("username"), status="invoice_requested", action="invoice_requested")
    recompute_tags(uid)
    if target_oid:
        create_task("order", target_oid,
                    f"Выставить счёт ({payer}, заказ №{target_oid})",
                    description=comment, telegram_id=uid, order_id=target_oid,
                    priority="high", notify=False)
    notify_admins(f"📥 <b>{cfg['type']} — нужно выставить счёт</b>\n💬 {username} (id {uid})\n{comment}")
    post_to_sheet(sheet_row(cfg["type"], tg_=username, contact=data.get("phone") or data.get("email", ""),
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


def start_cap(chat_id, uid, kind, cart=None, order_id=None):
    st = {"flow": "cap", "kind": kind, "i": 0, "data": {}}
    if cart:
        st["cart"] = cart
    if order_id:
        st["order_id"] = order_id
    state_set(uid, st)
    send_cap_step(chat_id, st)


# ------------------------- Этап 3: оформление заказа -------------------------
def checkout(chat_id, user, uid):
    ensure_mandatory(uid)
    cart = cart_get(uid)
    if not cart:
        send(chat_id, "🛒 Корзина пуста — сначала выберите услуги.", services_list_kb(uid))
        return
    if not anketa_done(uid):
        send(chat_id, "Для оформления заказа сначала нужно зарегистрировать личный кабинет и "
                      "заполнить анкету на разработку сайта. Это поможет нам корректно подготовить "
                      "сайт под ваш бизнес.",
             {"inline_keyboard": [[{"text": "📝 Заполнить анкету", "callback_data": "brief:start"}]]})
        return
    comments = cart_comments_get(uid)
    items = [c for c in cart if c in ITEM]
    total = cart_total(cart)
    named_comments = {cid: comments[cid] for cid in items if comments.get(cid)}
    oid = order_new(uid, items, total, "", status="created", comments=named_comments)
    cart_set(uid, [])
    cart_comments_set(uid, {})
    uname = f"@{user.get('username')}" if user.get("username") else "—"
    cm_txt = ""
    if named_comments:
        cm_txt = "\n" + "\n".join(f"   💬 {SERVICE.get(c, {}).get('name', c)}: {t}"
                                  for c, t in named_comments.items())
    notify_manager(f"🧾 <b>Новый заказ №{oid}</b>\n💬 {uname} (id {uid})\n" +
                   "\n".join(f"• {SERVICE.get(c, {}).get('name', c)}" for c in items) +
                   cm_txt + f"\n\n<b>Итого: {fmt_amount(total)}</b>\n"
                   "Статус: ожидает выбора способа оплаты")
    send(chat_id, f"✅ <b>Заказ №{oid} оформлен.</b>\nСумма: <b>{fmt_amount(total)}</b>\n\n"
                  "Оплата — по счёту. На кого выставить счёт?",
         {"inline_keyboard": [
             [{"text": "👤 На физлицо", "callback_data": f"ord:pay:individual:{oid}"}],
             [{"text": "🏢 На юрлицо (по ИНН)", "callback_data": f"ord:pay:legal:{oid}"}],
         ]})


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
    uname = f"@{user.get('username')}" if user.get("username") else "—"
    names = ", ".join(SERVICE.get(c, {}).get("name", c) for c in o.get("items", []))
    cm = o.get("service_comments") or {}
    cm_txt = " | ".join(f"{SERVICE.get(c, {}).get('name', c)}: {t}" for c, t in cm.items()) or "—"
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
    rows = []
    if CHECKLIST_URL:
        rows.append([{"text": "📖 Открыть чек-лист", "url": CHECKLIST_URL}])
    if with_brief_button:
        rows.append([{"text": "✅ Заполнить заявку на сайт", "callback_data": "brief:start"}])
    kb = {"inline_keyboard": rows} if rows else None
    if CHECKLIST_URL:
        send(chat_id, "📋 <b>Чек-лист: что подготовить для сайта</b>\nОткройте — там 12 пунктов и мини-проверка вашего сайта.", kb)
    elif CHECKLIST_PDF_URL:
        tg("sendDocument", chat_id=chat_id, document=CHECKLIST_PDF_URL,
           caption="📋 Что подготовить для создания сайта — смотрите в чек-листе 👇",
           reply_markup=kb)
    else:
        send(chat_id, "📋 Чек-лист скоро будет доступен. Нажмите «Заполнить заявку» — менеджер поможет.", kb)


def start_flow(chat_id):
    # 1) чек-лист (веб-страница) + постоянное меню
    if CHECKLIST_URL:
        tg("sendMessage", chat_id=chat_id, parse_mode="HTML",
           text=("📋 <b>Чек-лист для бизнеса от ONYX</b>\n"
                 "Как не слить деньги на сайт и получить инструмент, который продаёт.\n\n"
                 f"<a href=\"{CHECKLIST_URL}\">📖 Открыть чек-лист →</a>"),
           reply_markup=MAIN_MENU)
        send(chat_id, WELCOME, WELCOME_KB)
    elif CHECKLIST_PDF_URL:
        tg("sendDocument", chat_id=chat_id, document=CHECKLIST_PDF_URL,
           caption="📋 Чек-лист для бизнеса от ONYX", reply_markup=MAIN_MENU)
        send(chat_id, WELCOME, WELCOME_KB)
    else:
        send(chat_id, WELCOME, MAIN_MENU)


def rating_kb():
    rows, row = [], []
    for n in range(1, 11):
        row.append({"text": str(n), "callback_data": f"rev:rate:{n}"})
        if len(row) == 5:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return {"inline_keyboard": rows}


# ------------------------- Этап 8: Отзывы -------------------------
REVIEW_ASK = ("🎉 <b>Ваш сайт готов</b>\n\n"
              "Спасибо, что выбрали ONYX. Будем благодарны, если вы оцените нашу работу — "
              "это поможет нам становиться лучше и показывать реальные результаты будущим клиентам.")

REVIEW_VIDEO_ASK = ("📹 Если удобно, запишите короткий видеоотзыв в формате кружка: "
                    "что понравилось, насколько понятным был процесс и готовы ли вы "
                    "рекомендовать ONYX другим предпринимателям.")

REVIEW_THANKS = "Спасибо за ваш отзыв, это помогает ONYX расти."


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


def sheet_review(r):
    perm = r.get("permission_to_publish")
    sheet_post("Reviews", {
        "review_id": r.get("review_id", ""), "telegram_id": r.get("telegram_id", ""),
        "username": r.get("username", ""), "order_id": r.get("order_id", ""),
        "rating": r.get("rating", ""), "text_review": r.get("text_review", ""),
        "video_file_id": r.get("video_file_id", ""), "status": r.get("status", ""),
        "permission_to_publish": ("true" if perm is True else "false" if perm is False else ""),
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
    """Запуск сценария отзыва (после completed или из меню «Оценить сервис»)."""
    r = review_new(uid, username, order_id)
    state_set(uid, {"flow": "review", "step": "rate", "rid": r["review_id"]})
    if intro:
        send(chat_id, REVIEW_ASK)
    send(chat_id, "Оцените, пожалуйста, нашу работу:", rating_kb())
    return r


def review_ask_text(chat_id, uid, rid):
    state_set(uid, {"flow": "review", "step": "ask_text", "rid": rid})
    send(chat_id, "Хотите оставить короткий комментарий о работе с ONYX?",
         {"inline_keyboard": [
             [{"text": "✍️ Написать отзыв", "callback_data": "rev:text"}],
             [{"text": "Пропустить", "callback_data": "rev:skip_text"}],
         ]})


def review_ask_video(chat_id, uid, rid):
    state_set(uid, {"flow": "review", "step": "ask_video", "rid": rid})
    send(chat_id, REVIEW_VIDEO_ASK,
         {"inline_keyboard": [
             [{"text": "📹 Отправить видеоотзыв", "callback_data": "rev:video"}],
             [{"text": "Пропустить", "callback_data": "rev:skip_video"}],
         ]})


def review_ask_permission(chat_id, uid, rid):
    state_set(uid, {"flow": "review", "step": "ask_perm", "rid": rid})
    send(chat_id, "Можно ли использовать ваш отзыв на сайте ONYX и в наших материалах?",
         {"inline_keyboard": [
             [{"text": "✅ Да, можно", "callback_data": "rev:perm:1"}],
             [{"text": "🔒 Нет, только для внутреннего использования", "callback_data": "rev:perm:0"}],
         ]})


def review_finish(chat_id, uid, rid):
    r = review_get(rid)
    if not r:
        state_del(uid)
        return
    r["status"] = "completed"
    review_save(r)
    log_event(uid, "review_left", str(r.get("rating", "")))
    state_del(uid)
    send(chat_id, REVIEW_THANKS, MAIN_MENU)
    notify_review_admins(r)
    # низкая оценка — отдельно спросим, что улучшить
    try:
        if int(r.get("rating") or 0) <= 6:
            create_task("support", uid, f"Разобрать обратную связь (оценка {r.get('rating')}/10)",
                        description=r.get("text_review", ""), telegram_id=uid, priority="high", notify=False)
            state_set(uid, {"flow": "review_improve", "rid": rid})
            send(chat_id, "Нам важно стать лучше. Что нам стоит улучшить?",
                 {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True})
    except Exception:
        pass


def notify_review_admins(r):
    uname = f"@{r['username']}" if r.get("username") else "—"
    perm = r.get("permission_to_publish")
    notify_admins("⭐ <b>Новый отзыв ONYX:</b>\n"
                  f"Клиент: id {r.get('telegram_id')} {uname}\n"
                  f"Order ID: {r.get('order_id') or '—'}\n"
                  f"Оценка: {r.get('rating') or '—'}/10\n"
                  f"Текст: {r.get('text_review') or '—'}\n"
                  f"Видео: {'есть' if r.get('video_file_id') else 'нет'}\n"
                  f"Разрешение на публикацию: {'да' if perm is True else 'нет'}")
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
    if r and r.get("status") == "completed":
        send(chat_id, "Спасибо, вы уже оставили отзыв по последнему завершённому проекту.", MAIN_MENU)
        return
    review_start(chat_id, uid, username, order_id=o["id"], intro=False)


# ------------------------- Этап 9: Партнёрская программа -------------------------
PARTNER_TEXT = (
    "🤝 <b>Станьте партнёром ONYX</b>\n\n"
    "Рекомендуйте наши услуги своим клиентам и получайте вознаграждение за каждый "
    "реализованный проект.\n\n"
    "Если ваши клиенты нуждаются в сайте, интернет-магазине, CRM, онлайн-записи или "
    "цифровых решениях для бизнеса — мы берём разработку на себя, а вы получаете "
    "партнёрское вознаграждение.\n\n"
    "<b>Преимущества партнёрства:</b>\n"
    "— дополнительный источник дохода;\n"
    "— вознаграждение за каждого клиента;\n"
    "— прозрачные условия;\n"
    "— сопровождение проекта нашей командой;\n"
    "— возможность долгосрочного сотрудничества.\n\n"
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
    ("comment", "Комментарий (если нечего добавить — напишите «нет»):"),
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
             f"Партнёрский код: <code>{p.get('partner_code', '—')}</code>",
             f"Подана: {p.get('created_at', '—')}"]
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
    uname = f"@{user.get('username')}" if user.get("username") else "—"
    notify_admins("🤝 <b>Новая заявка партнёра ONYX:</b>\n"
                  f"Имя: {data.get('name', '—')}\n"
                  f"Telegram: {uname} (id {uid})\n"
                  f"Чем занимается: {data.get('activity', '—')}\n"
                  f"Есть ли клиенты: {data.get('has_clients', '—')}\n"
                  f"Ссылка: {data.get('portfolio_link', '—')}\n"
                  f"Комментарий: {data.get('comment', '—')}\n"
                  f"Контакт: {data.get('contact', '—')}\n"
                  f"Код: {p['partner_code']}")


# ------------------------- Этап 10: Информативный ONYX + рассылки -------------------------
TOPICS = {
    "ai": "🤖 AI для бизнеса",
    "sites": "📈 Сайты и заявки",
    "crm": "⚙️ CRM и автоматизация",
    "cases": "💼 Кейсы ONYX",
    "digital": "🌐 Цифровые технологии",
    "mistakes": "⚠️ Ошибки старых сайтов",
    "shop": "🛍 Интернет-магазины и оплата",
    "partner": "🤝 Партнёрская программа",
}
# Полные названия тем (для текстов и AI-черновиков)
TOPIC_FULL = {
    "ai": "AI-технологии для бизнеса",
    "sites": "Как сайт влияет на продажи и заявки; польза сайта для бизнеса",
    "crm": "CRM, аналитика и автоматизация",
    "cases": "Кейсы ONYX",
    "digital": "Цифровые технологии для предпринимателей",
    "mistakes": "Ошибки старых сайтов",
    "shop": "Интернет-магазины и онлайн-оплата",
    "partner": "Партнёрская программа ONYX",
}

CONTENT_INTRO = ("ℹ️ <b>Информативный ONYX</b>\n\n"
                 "Выберите, какие материалы вы хотите получать от ONYX. Мы будем отправлять "
                 "только полезные материалы для развития бизнеса, сайтов, AI и цифровых "
                 "инструментов.\n\n"
                 "Нажмите на темы, которые вам интересны 👇")


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
    s = csub_ensure(uid)
    chosen = set(s.get("topics") or [])
    rows = []
    for key, label in TOPICS.items():
        mark = "✅ " if key in chosen else "◻️ "
        rows.append([{"text": f"{mark}{label}", "callback_data": f"ct:t:{key}"}])
    rows.append([{"text": "📚 Все материалы", "callback_data": "ct:all"}])
    rows.append([{"text": "🔕 Отписаться от рассылки", "callback_data": "ct:off"}])
    rows.append([{"text": "⬅️ Назад", "callback_data": "b:cab"}])
    return {"inline_keyboard": rows}


def content_text(uid):
    s = csub_ensure(uid)
    topics = s.get("topics") or []
    if not topics:
        return CONTENT_INTRO + "\n\n<i>Сейчас вы не подписаны ни на одну тему.</i>"
    names = ", ".join(TOPICS[t].split(" ", 1)[1] for t in topics if t in TOPICS)
    return CONTENT_INTRO + f"\n\n✅ <b>Вы подписаны:</b> {names}"


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
        try:
            send(uid, b["text"], unsub_kb)
            sent += 1
            s["last_sent_at"] = now_str()
            csub_save(s, to_sheet=False)
        except Exception as e:
            failed += 1
            print("broadcast send err", uid, e)
        time.sleep(0.05)  # ~20 сообщений/сек — в пределах лимитов Telegram
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
    """Черновик поста по теме. Без AI_API_KEY — готовый шаблон (не выдумывает фактов)."""
    full = TOPIC_FULL.get(topic, topic)
    if not AI_API_KEY:
        return (f"<b>Кейс ONYX: как современный сайт помогает бизнесу получать больше заявок</b>\n\n"
                "Многие клиенты принимают решение о покупке ещё до звонка — по сайту компании. "
                "Если сайт выглядит устаревшим или непонятным, бизнес теряет обращения.\n\n"
                "ONYX помогает компаниям запускать современные сайты без лишней сложности "
                f"и с понятной структурой под заявки.\n\n<i>(Тема: {full}. "
                "Черновик-шаблон — задайте AI_API_KEY для генерации.)</i>")
    prompt = (
        "Ты — контент-маркетолог веб-студии ONYX (делаем сайты бизнесу: разработка 0 ₽, "
        "клиент платит за запуск и доп. опции, оплата по счёту).\n"
        f"Напиши короткий полезный пост для Telegram-рассылки на тему: {full}.\n"
        "Требования: 3-5 абзацев, простым языком для владельца малого бизнеса, "
        "без обещаний и гарантий роста, без выдуманных цифр и статистики. "
        "В конце — мягкое упоминание ONYX. Только текст поста, без заголовков-служебок."
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
    return f"Черновик по теме «{full}» — не удалось сгенерировать, напишите текст вручную."


def bc_topic_kb(action):
    rows = [[{"text": TOPICS[k], "callback_data": f"bc:{action}:{k}"}] for k in TOPICS]
    return {"inline_keyboard": rows}


def bc_preview(chat_id, uid, topic, text):
    cnt = len(topic_subscribers(topic))
    state_set(uid, {"flow": "bc_confirm", "topic": topic, "text": text})
    send(chat_id, f"👀 <b>Предпросмотр рассылки</b>\n"
                  f"Тема: {TOPICS.get(topic, topic)}\n"
                  f"Получателей: <b>{cnt}</b>\n\n"
                  f"— — —\n{text}\n— — —",
         {"inline_keyboard": [
             [{"text": f"✅ Отправить ({cnt})", "callback_data": "bc:send"}],
             [{"text": "✍️ Переписать", "callback_data": f"bc:new:{topic}"},
              {"text": "❌ Отмена", "callback_data": "bc:cancel"}],
         ]})


# ============================================================================
#  ЭТАП 11: ОПЕРАЦИОННЫЙ ЦЕНТР ONYX — Events, Leads, Tags, Админ-панель
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
        _redis("INCRBY", f"onyx:cnt:{metric}", str(n))
        k = f"onyx:cnt:{metric}:{_today_key()}"
        _redis("INCRBY", k, str(n))
        _redis("EXPIRE", k, str(DAY * 40))
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
}


def log_event(uid, event_type, data=""):
    """Логирует действие пользователя: счётчик воронки (+ Sheets по флагу)."""
    try:
        metric = FUNNEL_METRICS.get(event_type)
        if metric:
            bump(metric)
        eid = _redis("INCR", "onyx:event_seq") if KV_URL else _MEM.get("_event_seq", 0) + 1
        if not KV_URL:
            _MEM["_event_seq"] = eid
        if EVENT_SHEET_ON:
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
    return f"{round(a / b * 100)}%" if b else "—"


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
    return f"{temp} {uname} · {l.get('name', '—')} · {l.get('lead_status', '')}"


def admin_followup_text():
    leads = leads_to_followup()[:20]
    if not leads:
        return "📞 <b>Клиенты для дожима</b>\n\nПока никого — все горячие лиды обработаны 👍"
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
        lines.append(f"• {uname} · {p.get('name', '—')} · {p.get('niche', '—')}")
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
        return f"✅ <b>Задачи — {title}</b>\n\nПусто 👍", None
    lines = [f"✅ <b>Задачи — {title}</b>\n"]
    rows = []
    for t in tasks:
        lines.append(f"#{t['task_id']} {PRIORITY_RU.get(t.get('priority'), '')} — {t['title']} — {TASK_STATUS_RU.get(t.get('task_status'), '')}")
        rows.append([{"text": f"#{t['task_id']} {t['title'][:24]}", "callback_data": f"task:v:{t['task_id']}"}])
    rows.append([{"text": "⬅️ Назад", "callback_data": "adm:home"}])
    return "\n".join(lines), {"inline_keyboard": rows}


def admin_task_detail(t):
    lines = [f"✅ <b>Задача #{t['task_id']}</b>", "",
             f"<b>{t['title']}</b>",
             t.get("description", "") or "",
             f"Тип: {t.get('related_type', '—')} · Приоритет: {PRIORITY_RU.get(t.get('priority'), '')}",
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
                "или стоимости — команда ONYX поможет разобраться.",
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
    "audit_no_order": {
        "delay": 24 * 3600,
        "text": "Мы уже подготовили аудит вашего сайта. Если хотите, ONYX может исправить слабые "
                "места и подготовить сайт, который лучше работает на доверие и заявки.",
        "kb": {"inline_keyboard": [
            [{"text": "📄 Получить предложение", "callback_data": "audit:offer"}],
            [{"text": "🌐 Заказать сайт", "callback_data": "brief:start"}],
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
        # оплатил/сконвертился/отключены дожимы — снимаем
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
                description=f"Ниша: {p.get('niche', '—')}. Услуги: {', '.join(items)}",
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
        rows.append([{"text": f"№{pr['order_id']} · {pr.get('client_name', '—')[:18]} · {pr.get('stage')}",
                      "callback_data": f"prod:v:{pr['order_id']}"}])
    rows.append([{"text": "⬅️ Назад", "callback_data": "adm:home"}])
    return "\n".join(lines), {"inline_keyboard": rows}


def admin_prod_detail(pr):
    lines = [f"🏭 <b>Проект №{pr['order_id']}</b> ({pr.get('production_id')})", "",
             f"Клиент: {pr.get('client_name', '—')} (id {pr.get('telegram_id')})",
             f"Ниша: {pr.get('niche', '—')} · Тип: {pr.get('website_type')}",
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
         {"text": "📤 Запросить материалы", "callback_data": f"prod:materials:{oid}"}],
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
    "уже подготовлены. Твоя задача — улучшить только визуальный дизайн сайта.\n\n"
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
        niche=p.get("niche") or q.get("niche", "—"), city=q.get("city", "—"),
        style=q.get("style_preferences", "—"), colors=q.get("color_preferences", "—"),
        refs=q.get("reference_sites", "—"),
        github=pr.get("github_repo_url", "—"), preview=pr.get("vercel_preview_url", "—"))


def create_design_task(order, pr):
    oid = order["id"]
    create_task("production", f"design-{oid}", f"Доработать дизайн сайта (заказ №{oid})",
                description="Индивидуальный дизайн. Промпт для Claude Code сформируется по данным анкеты.",
                telegram_id=order.get("uid"), order_id=oid, priority="high")
    pr["claude_task_status"] = "created"
    prod_save(pr)


# ============================================================================
#  ЭТАП 15: ДОМЕННЫЙ МОДУЛЬ (Domains)
# ============================================================================
DOMAIN_INSTRUCTION = (
    "🌐 <b>Как подключить домен</b>\n\n"
    "1. Если домен уже есть — отправьте нам его название.\n"
    "2. Если домена нет — напишите 2–3 желаемых варианта.\n"
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
    uname = f"@{username}" if username else "—"
    notify_admins("🆘 <b>Новое обращение в поддержку ONYX</b>\n"
                  f"Обращение #{tid}\nКатегория: {TICKET_CAT_RU.get(category, category)}\n"
                  f"Сообщение: {message}\nTelegram: {uname} (id {uid})\n"
                  f"Order ID: {t['order_id'] or '—'}")
    create_task("support", uid, f"Ответить на обращение #{tid} ({TICKET_CAT_RU.get(category, category)})",
                description=message, telegram_id=uid, order_id=t["order_id"], priority="high", notify=False)
    return t


def user_tickets(uid):
    return [ticket_get(t) for t in all_ticket_ids() if (ticket_get(t) or {}).get("telegram_id") == uid]


FAQ = [
    ("Почему сайт бесплатно?", "Разработка базового сайта — 0 ₽. ONYX зарабатывает на запуске "
     "и доп.опциях, а базовый сайт делаем бесплатно, чтобы вам было легко начать."),
    ("За что платит клиент?", "За запуск (домен, хостинг, публикация) и дополнительные опции по "
     "желанию (CRM, каталог, дизайн и т.д.). Оплата — по счёту."),
    ("Что входит в запуск?", "Домен, хостинг, SSL, публикация сайта в интернете и первичная "
     "техническая поддержка."),
    ("Как происходит оплата?", "Мы выставляем счёт — на физлицо или на юрлицо (по ИНН). "
     "После оплаты счёта сразу запускаем работу над сайтом."),
    ("Кто владелец домена?", "Домен оформляется на вас или вашу компанию. ONYX помогает с подбором, "
     "регистрацией и настройкой, но владельцем остаётесь вы."),
    ("Как проходит разработка сайта?", "Вы заполняете анкету → мы готовим структуру и собираем сайт "
     "→ проверяем → подключаем домен → сдаём вам. Статус виден в разделе «Мой заказ»."),
    ("Сколько времени занимает запуск?", "Обычно базовый сайт готов за 1–3 рабочих дня после "
     "получения материалов и оплаты."),
    ("Можно ли принимать оплату на сайте?", "Да, есть опция «Онлайн-оплата» — приём карт и СБП "
     "прямо на вашем сайте (это функция сайта для ваших клиентов)."),
    ("Можно ли подключить CRM?", "Да, есть опция «Подключение CRM» — заявки попадают в вашу систему "
     "автоматически."),
    ("Что делать, если уже есть сайт?", "Сделаем бесплатный аудит и предложим, что улучшить, "
     "или соберём новый сайт."),
    ("Что делать, если уже есть домен?", "Отправьте нам его название — мы подключим сайт к вашему "
     "домену без переоформления."),
    ("Как связаться с поддержкой?", "Раздел «🆘 Поддержка» → «Создать обращение» или напишите "
     "менеджеру напрямую."),
    ("Как стать партнёром?", "Раздел «🤝 Стать партнёром» — заполните короткую заявку и получите "
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


# ------------------------- Обработка сообщений -------------------------
def process_message(msg):
    chat_id = msg["chat"]["id"]
    user = msg.get("from", {})
    uid = user.get("id")
    text = (msg.get("text") or "").strip()
    contact = msg.get("contact")
    subscribe(uid)

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
                review_ask_permission(chat_id, uid, r["review_id"])
                return
        send(chat_id, "Спасибо за видео! Если это отзыв — откройте «⭐ Оценить сервис» в меню.", MAIN_MENU)
        return

    if text == "🏠 Главное меню":
        state_del(uid); main_menu(chat_id); return
    if text.startswith("/start"):
        state_del(uid)
        parts = text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        existed = bool(user_get(uid))
        register_client(uid, user)
        if not existed:
            log_event(uid, "start", payload or "direct")
            schedule_followup(uid, "idle_after_start", user.get("username", ""))
        src = "referral" if payload.startswith("ref") else ("audit_link" if "audit" in payload else "telegram")
        lead_touch(uid, username=user.get("username"), name=user.get("first_name"),
                   source=src, action="start")
        recompute_tags(uid)
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
        if low.startswith("/partners"):
            ids = partners_all()
            if not ids:
                send(chat_id, "Заявок партнёров пока нет."); return
            lines = ["🤝 <b>Партнёры</b>"]
            for puid in ids[-20:]:
                p = partner_get(puid)
                if not p:
                    continue
                lines.append(f"{p['partner_code']} · id {puid} · {p.get('name', '—')} · "
                             f"{p.get('activity', '—')} · {PARTNER_STATUS_RU.get(p.get('partner_status'), '')}")
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
            send(chat_id, "📣 <b>Новая рассылка</b>\nВыберите тему — отправим только "
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
                 "ProductionSites, Domains, SupportTickets, AuditOffers) — это и есть выгрузка/бэкап.\n\n"
                 "<b>Сейчас в базе:</b>\n"
                 f"Клиентов/лидов: {leads}\nЗаказов: {orders}\nЗадач: {tasks}\n"
                 f"Обращений: {tickets}\nПроектов в производстве: {prods}\n\n"
                 "Открыть таблицу можно по ссылке, настроенной в SHEETS_WEBHOOK_URL / вашей Google-таблице.")
            return
        if low == "/admin_help":
            send(chat_id,
                 "🛠 <b>Админ-команды</b>\n"
                 "/orders — последние заказы\n"
                 "/invoices — заказы, ждущие счёта (юрлица)\n"
                 "/order &lt;№&gt; — детали заказа\n"
                 "/status &lt;№&gt; &lt;ключ&gt; — сменить статус\n"
                 "/set_order_status &lt;№&gt; [статус] — статус проекта (кнопки, если без статуса)\n"
                 "/paid &lt;№&gt; — отметить заказ оплаченным (после поступления по счёту)\n"
                 "ключи проекта: created, waiting_payment, paid_waiting_start, questionnaire_review, in_production, design_review, domain_setup, final_check, completed, paused, cancelled\n"
                 "/reply &lt;id&gt; &lt;текст&gt; — ответить на обращение\n"
                 "/export — сводка по данным\n"
                 "/post — рассылка по теме (с предпросмотром)\n"
                 "/broadcasts — история рассылок\n"
                 "/partners — заявки партнёров\n"
                 "/partner_status &lt;id&gt; &lt;статус&gt; — статус партнёра\n"
                 "/broadcast &lt;текст&gt; — рассылка всем")
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
                lines.append(f"№{oid} · id {o['uid']} · ИНН {o.get('inn', '—')} · "
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
                              "Без статуса — покажу кнопки."); return
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
                             f"{STATUS.get(o.get('status'), o.get('status'))} · 💳 {o.get('payment_status', '—')}")
            send(chat_id, "\n".join(lines)); return
        if low.startswith("/order "):
            try:
                oid = int(low.split()[1])
            except Exception:
                send(chat_id, "Формат: /order &lt;№&gt;"); return
            o = order_get(oid)
            if not o:
                send(chat_id, "Заказ не найден."); return
            send(chat_id, f"📦 <b>Заказ №{oid}</b>\nКлиент id: {o['uid']}\nУслуги: {', '.join(o.get('items', []))}\nСумма: {o.get('total', 0)} ₽\nСпособ: {o.get('payment_method', '') or o.get('payment_type', '')}\nОплата: {o.get('payment_status', '—')}\nИНН: {o.get('inn', '—')}\nСтатус: {STATUS.get(o.get('status'), o.get('status'))}\nСоздан: {o.get('created', '')}")
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

    MENU_TRIGGERS = {"🔍 Бесплатный аудит", "🛒 Тарифы и услуги", "📦 Мой заказ",
                     "👤 Личный кабинет", "🤝 Стать партнёром", "🆘 Поддержка"}
    st = state_get(uid)
    if st and st.get("flow") in ("brief", "cap", "svc_comment", "invoice_inn", "audit_url",
                                 "review", "review_improve", "partner", "bc_text", "bc_confirm") and text in MENU_TRIGGERS:
        state_del(uid); st = None
    # --- Этап 8: текст отзыва / что улучшить ---
    if st and st.get("flow") == "review_improve":
        r = review_get(st.get("rid"))
        state_del(uid)
        uname = f"@{user.get('username')}" if user.get("username") else "—"
        notify_admins("⚠️ <b>Важная обратная связь (низкая оценка)</b>\n"
                      f"Клиент: id {uid} {uname}\n"
                      f"Оценка: {(r or {}).get('rating', '—')}/10\n"
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
        send(chat_id, "Жду видеокружок 📹 — или нажмите «Пропустить» выше.")
        return
    if st and st.get("flow") == "audit_url":
        url = valid_url(text)
        if not url:
            send(chat_id, "Это не похоже на ссылку. Пришлите адрес сайта, например: onyx-web.ru")
            return
        state_del(uid)
        audit_start(chat_id, uid, url, username=user.get("username", ""))
        return
    if st and st.get("flow") == "invoice_inn":
        invoice_inn_input(chat_id, user, uid, st, text); return
    if st and st.get("flow") == "svc_comment":
        svc_comment_input(chat_id, uid, st, text); return
    if st and st.get("flow") == "brief":
        if st.get("stage") == "summary" or st.get("i", 0) >= len(BRIEF_STEPS):
            send(chat_id, "Подтвердите анкету кнопками выше 👆"); return
        step = BRIEF_STEPS[st["i"]]
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
            prod_save(pr)
            send(chat_id, f"Сохранено: {field} = {text} ✅")
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
    if text == "🛒 Тарифы и услуги":
        ensure_mandatory(uid)
        log_event(uid, "tariffs_open")
        lead_touch(uid, username=user.get("username"), action="tariffs_open", inc_tariffs=True)
        recompute_tags(uid)
        send(chat_id, services_list_text(uid), services_list_kb(uid)); return
    if text == "📦 Мой заказ":
        log_event(uid, "myorder_open")
        txt, kb = render_my_order(uid)
        send(chat_id, txt, kb); return
    if text == "👤 Личный кабинет":
        log_event(uid, "cabinet_open")
        send(chat_id, "👤 <b>Личный кабинет</b>\nВыберите раздел:", CABINET_KB); return
    if text == "🤝 Стать партнёром":
        open_partner_section(chat_id, uid); return
    if text == "🆘 Поддержка":
        send(chat_id, SUPPORT_TEXT, support_kb()); return

    # старые кнопки (обратная совместимость со старыми чатами, в меню не показываются)
    if text in ("🌐 Получить сайт", "/brief"):
        st = {"flow": "brief", "i": 0, "data": {}}
        brief_push(chat_id, uid, st, force_send=True); return
    if text in ("⭐ Оценить сервис", "/review"):
        open_review_section(chat_id, uid, user.get("username", "")); return
    # «📋 Что подготовить», «💬 Вопрос менеджеру», «Разработчику» — удалены,
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

    if data == "b:home":
        state_del(uid); main_menu(chat_id); return

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
                    rows.append(f"• id {luid} · {d.get('domain_name') or '—'} · {d.get('domain_status')}")
            edit_or_send(chat_id, mid, "🌐 <b>Домены</b>\n\n" + ("\n".join(rows[:25]) if rows else "Пока нет доменных записей."),
                         {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "adm:home"}]]}); return
        if sub == "partners":
            lines = ["🤝 <b>Партнёры</b>"]
            for puid in (partners_all() or [])[-20:]:
                pp = partner_get(puid)
                if pp:
                    lines.append(f"{pp['partner_code']} · id {puid} · {pp.get('name', '—')} · "
                                 f"{PARTNER_STATUS_RU.get(pp.get('partner_status'), '')}")
            edit_or_send(chat_id, mid, "\n".join(lines) if len(lines) > 1 else "Заявок партнёров пока нет.",
                         {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "adm:home"}]]}); return
        if sub == "errors":
            errs = _get("onyx:errors_recent") or []
            edit_or_send(chat_id, mid, "🐞 <b>Последние ошибки</b>\n\n" + ("\n".join(f"• {e}" for e in errs[-15:]) if errs else "Ошибок не зафиксировано 👍"),
                         {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "adm:home"}]]}); return
        # разделы, использующие существующие команды
        hint = {"payments": "💳 <b>Оплаты / счета</b>\n\nОплата у клиентов — по счёту. Команды: /invoices (ждут счёта), /paid № (отметить оплаченным), /orders.",
                "audits": "🔍 <b>Аудиты</b>\n\nАудиты приходят админам уведомлениями. Коммерческие предложения — в листе AuditOffers.",
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
                safe_send(pr["telegram_id"], "📤 Для продолжения работы над сайтом пришлите, пожалуйста, "
                          "материалы: логотип, фото, тексты и прочее, что у вас есть.", MAIN_MENU)
                answer_cb(cq["id"], "Запрос материалов отправлен клиенту")
            return
        if act == "approve":
            pr = prod_get(int(parts[2]))
            if pr:
                pr["client_approval_status"] = "sent"; prod_save(pr)
                url = pr.get("vercel_preview_url") or pr.get("production_url") or ""
                safe_send(pr["telegram_id"], "👀 <b>Ваш сайт готов к проверке!</b>\n"
                          + (f"Посмотрите: {url}\n\n" if url else "") +
                          "Напишите в поддержку, если нужны правки, или подтвердите — и мы опубликуем финальную версию.", MAIN_MENU)
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
        edit_or_send(chat_id, mid, "Хорошо, больше не будем напоминать. Если понадобимся — мы на связи 🤝"); return
    if data in ("fu:improve", "fu:audit"):
        state_set(uid, {"flow": "audit_url"})
        send(chat_id, AUDIT_INTRO, {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True}); return
    if data == "fu:browse":
        answer_cb(cq["id"], "Хорошо!")
        edit_or_send(chat_id, mid, "Хорошо! Изучайте — а когда будете готовы, мы поможем с сайтом. 🤝"); return

    if data == "b:cab":
        edit_or_send(chat_id, mid, "👤 <b>Личный кабинет</b>\nВыберите раздел:", CABINET_KB); return
    if data == "brief:start":
        st = {"flow": "brief", "i": 0, "data": {}, "mid": mid}
        log_event(uid, "questionnaire_start")
        lead_touch(uid, username=user.get("username"), status="questionnaire_started", action="questionnaire_start")
        cancel_followups(uid, ("idle_after_start",))
        schedule_followup(uid, "questionnaire_abandoned", user.get("username", ""))
        brief_push(chat_id, uid, st); return
    if data == "cart:open":
        edit_or_send(chat_id, mid, cart_show_text(uid), cart_show_kb(uid)); return

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
        checkout(chat_id, user, uid)
        return
    if data.startswith("ord:pay:"):
        parts = data.split(":", 3)
        if len(parts) == 4:
            order_pay_method(chat_id, uid, parts[3], parts[2])
        return

    # --- Этап 5: раздел «Мой заказ» ---
    if data == "audit:offer":
        p = user_get(uid) or {}
        uname = f"@{user.get('username')}" if user.get("username") else "—"
        log_event(uid, "offer_requested")
        lead_touch(uid, username=user.get("username"), action="offer_requested", offer=True)
        recompute_tags(uid)
        notify_admins("📩 <b>Запрос предложения после аудита</b> 🔥\n"
                      f"Клиент: {p.get('name', '—')}\nTelegram: {uname} (id {uid})\n"
                      f"Контакт: {p.get('contact', '—')}")
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
                   else "Выберите нужные услуги в каталоге — и оформим исправления.")
        if not anketa_done(uid):
            msg_txt += "\n\nЧтобы оформить заказ, сначала заполните короткую анкету."
        send(chat_id, msg_txt, services_list_kb(uid))
        return

    if data == "myorder:open":
        txt, kb = render_my_order(uid)
        edit_or_send(chat_id, mid, txt, kb); return
    if data == "myorder:ok":
        answer_cb(cq["id"], "Отлично! Мы на связи 🤝"); return
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
    if data == "ct:off":
        csub_unsubscribe(uid)
        answer_cb(cq["id"], "Вы отписались")
        send(chat_id, "🔕 Вы отписались от рассылки ONYX.\n"
                      "Вернуться можно в любой момент: Личный кабинет → Информативный ONYX.", MAIN_MENU)
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
            send(chat_id, f"✅ Отправлено {sent}. Осталось {rest} — дошлём автоматически.", MAIN_MENU)
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
        answer_cb(cq["id"], f"Оценка {n}/10 — спасибо!")
        tg("editMessageText", chat_id=chat_id, message_id=mid,
           text=f"Ваша оценка: <b>{n}/10</b> ⭐", parse_mode="HTML")
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
        review_ask_permission(chat_id, uid, st.get("rid"))
        return
    if data.startswith("rev:perm:"):
        st = state_get(uid) or {}
        rid = st.get("rid")
        r = review_get(rid) if rid else None
        if not r:
            state_del(uid)
            return
        r["permission_to_publish"] = data.endswith("1")
        review_save(r, to_sheet=False)
        answer_cb(cq["id"], "Спасибо!")
        review_finish(chat_id, uid, rid)
        return

    if data == "b:noop":
        return

    # анкета — выбор варианта / навигация / резюме (всё редактируется в одном сообщении)
    if data in ("b:back", "b:skip", "b:cancel", "b:ok", "b:redo") or data.startswith("b:o:"):
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
            st = {"flow": "brief", "i": 0, "data": {}, "mid": st.get("mid")}
            brief_push(chat_id, uid, st); return
        if data == "b:back":
            st.pop("stage", None)
            st["i"] = max(0, min(st["i"], len(BRIEF_STEPS)) - 1)
            brief_push(chat_id, uid, st); return
        # b:skip / b:o:idx — только на активном вопросе
        if st.get("stage") == "summary" or st["i"] >= len(BRIEF_STEPS):
            return
        step = BRIEF_STEPS[st["i"]]
        if data == "b:skip":
            if not step.get("opt"):
                return
            st["data"][step["key"]] = ""
        else:
            idx = int(data.split(":")[2])
            brief_flash_choice(chat_id, st, idx)
            st["data"][step["key"]] = step["opts"][idx]
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
            log_event(uid, "payment_start")
            lead_touch(uid, username=user.get("username"), status="payment_pending", action="payment_start")
            send(chat_id, "Оплата — по счёту. На кого выставить счёт?", {"inline_keyboard": [
                [{"text": "👤 На физлицо", "callback_data": "pm:fiz"}],
                [{"text": "🏢 На юрлицо (по ИНН)", "callback_data": "pm:ur"}],
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

    # способ оплаты — только счёт (физлицо / юрлицо)
    if data in ("pm:fiz", "pm:ur"):
        cart = cart_get(uid)
        if not cart:
            answer_cb(cq["id"], "Корзина пуста"); return
        # защита от двойного тапа
        lock = f"onyx:order_lock:{uid}"
        if _get(lock):
            answer_cb(cq["id"], "Уже оформляем, секунду…"); return
        _set(lock, 1, ttl=8)
        if not anketa_done(uid):
            answer_cb(cq["id"])
            send(chat_id, "Для оформления заказа сначала заполните анкету.",
                 {"inline_keyboard": [[{"text": "📝 Заполнить анкету", "callback_data": "brief:start"}]]}); return
        start_cap(chat_id, uid, "legal" if data == "pm:ur" else "individual", cart=cart)
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
            self._ok(f"cron ok: {n}".encode("utf-8")); return
        self._ok("ONYX bot webhook is running".encode("utf-8"))

    def do_POST(self):
        path = self.path or ""
        length = int(self.headers.get("content-length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        # --- Telegram webhook ---
        if WEBHOOK_SECRET and self.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != WEBHOOK_SECRET:
            self._ok(b"forbidden", 403); return
        try:
            process_update(json.loads(raw or b"{}"))
        except Exception as e:
            log_error("handler", e, notify=True)
        self._ok()
