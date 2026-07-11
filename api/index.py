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
MANAGER_USERNAME = os.environ.get("MANAGER_USERNAME", "onyxcoop")
DEVELOPER_USERNAME = os.environ.get("DEVELOPER_USERNAME", "softstaticg")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
# Prodamus (Этап 4)
PRODAMUS_SHOP_URL = (os.environ.get("PRODAMUS_SHOP_URL", "") or os.environ.get("PRODAMUS_URL", "")).rstrip("/")
PRODAMUS_SECRET_KEY = os.environ.get("PRODAMUS_SECRET_KEY", "")
PRODAMUS_WEBHOOK_SECRET = os.environ.get("PRODAMUS_WEBHOOK_SECRET", "") or PRODAMUS_SECRET_KEY
PRODAMUS_URL = PRODAMUS_SHOP_URL  # обратная совместимость
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
    {"id": "service", "name": "Обслуживание сайта", "price": 1990, "unit": "мес", "mandatory": True,
     "short": "Ежемесячное сопровождение: хостинг, бэкапы, защита, мелкие правки, контроль продлений.",
     "why": "Сайт остаётся быстрым, защищённым и актуальным — вы не теряете заявки из-за сбоёв."},
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


def prodamus_link(order):
    """Платёжная ссылка Prodamus для заказа. order_id кодирует внутренний №."""
    if not PRODAMUS_SHOP_URL:
        return None
    params = []
    items = [c for c in order.get("items", []) if c in ITEM]
    for i, cid in enumerate(items):
        name, price = ITEM[cid]
        params.append((f"products[{i}][name]", f"ONYX — {name}"))
        params.append((f"products[{i}][price]", str(price)))
        params.append((f"products[{i}][quantity]", "1"))
    params.append(("order_id", f"onyx-{order.get('id')}"))
    params.append(("do", "pay"))
    if PUBLIC_BASE_URL:
        params.append(("urlNotification", f"{PUBLIC_BASE_URL}/prodamus"))
        params.append(("urlReturn", SITE_URL))
        params.append(("urlSuccess", SITE_URL))
    return f"{PRODAMUS_SHOP_URL}/?{urllib.parse.urlencode(params)}"


# Обратная совместимость со старым вызовом build_payment_link(cart, uid)
def build_payment_link(cart, uid):
    return prodamus_link({"id": f"{uid}-{int(time.time())}", "items": list(cart)})


# ---- Подпись Prodamus (аналог их PHP-класса Hmac) ----
def _prodamus_stringify(v):
    if isinstance(v, dict):
        return {k: _prodamus_stringify(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_prodamus_stringify(x) for x in v]
    if isinstance(v, bool):
        return "1" if v else ""
    if v is None:
        return ""
    return str(v)


def prodamus_sign(data, secret):
    prepared = _prodamus_stringify(copy.deepcopy(data))
    js = json.dumps(prepared, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    js = js.replace("/", "\\/")  # PHP json_encode экранирует прямые слэши
    return hmac.new(secret.encode("utf-8"), js.encode("utf-8"), hashlib.sha256).hexdigest()


def prodamus_verify(data, secret, sign):
    if not secret or not sign:
        return False
    try:
        calc = prodamus_sign(data, secret)
    except Exception as e:
        print("prodamus_sign err", e)
        return False
    return hmac.compare_digest(calc, str(sign))


# ---- Разбор form-urlencoded с bracket-нотацией (products[0][name]=...) ----
def _form_listify(obj):
    if isinstance(obj, dict):
        obj = {k: _form_listify(v) for k, v in obj.items()}
        keys = list(obj.keys())
        if keys and all(k.isdigit() for k in keys):
            items = sorted(obj.items(), key=lambda kv: int(kv[0]))
            if [int(k) for k, _ in items] == list(range(len(items))):
                return [v for _, v in items]
        return obj
    return obj


def parse_form_nested(pairs):
    root = {}
    for key, value in pairs:
        if "[" in key:
            base = key[:key.index("[")]
            brackets = re.findall(r"\[([^\]]*)\]", key[key.index("["):])
            path = [base] + brackets
        else:
            path = [key]
        node = root
        for i, part in enumerate(path):
            if i == len(path) - 1:
                node[part] = value
            else:
                if not isinstance(node.get(part), dict):
                    node[part] = {}
                node = node[part]
    return _form_listify(root)


def mark_order_paid(o, source=""):
    """Пометить заказ оплаченным (из вебхука Prodamus или вручную админом)."""
    o["payment_status"] = "paid"
    o["status"] = "paid_waiting_start"
    o["paid"] = True
    o["updated"] = now_str()
    order_save(o)
    sheet_order(o)
    mark_purchased(o.get("uid"), o.get("items", []))
    uid = o.get("uid")
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


def handle_prodamus_webhook(raw_body, headers):
    """Проверка подписи и подтверждение оплаты. Меняет статус ТОЛЬКО при валидной подписи."""
    try:
        body_text = raw_body.decode("utf-8")
    except Exception:
        body_text = raw_body.decode("latin-1", "ignore")
    pairs = urllib.parse.parse_qsl(body_text, keep_blank_values=True)
    data = parse_form_nested(pairs)
    sign = data.pop("signature", None)
    if not sign:
        sign = headers.get("Sign") or headers.get("sign") or headers.get("SIGN")
    if not prodamus_verify(data, PRODAMUS_WEBHOOK_SECRET, sign):
        print("PRODAMUS signature FAILED order_id=", data.get("order_id"))
        return False
    raw_oid = str(data.get("order_id", ""))
    # --- Оплата подписки на обслуживание: order_id вида onyx-sub{uid} / sub{uid} ---
    msub = re.search(r"sub(\d+)", raw_oid)
    if msub:
        pstatus_s = str(data.get("payment_status", "")).lower()
        if pstatus_s and pstatus_s not in ("success", "paid"):
            print("PRODAMUS sub: payment not successful:", pstatus_s)
            return True
        suid = int(msub.group(1))
        sub = sub_renew(suid, payment_method="Карта (Prodamus)")
        send(suid, "✅ <b>Оплата обслуживания получена!</b>\n"
                   f"Подписка активна. Следующая оплата: {sub.get('next_payment_date')}", MAIN_MENU)
        notify_admins(f"💰 Оплачено обслуживание: id {suid} · {fmt_amount(SUB_PRICE)} · "
                      f"следующая оплата {sub.get('next_payment_date')}")
        return True
    m = re.search(r"(\d+)", raw_oid)
    if not m:
        print("PRODAMUS: no order id in", raw_oid)
        return False
    o = order_get(int(m.group(1)))
    if not o:
        print("PRODAMUS: order not found", raw_oid)
        return False
    pstatus = str(data.get("payment_status", "")).lower()
    if pstatus and pstatus not in ("success", "paid"):
        print("PRODAMUS: payment not successful:", pstatus)
        return True  # подпись валидна, но оплата не прошла — статус не трогаем
    if o.get("payment_status") == "paid":
        return True  # идемпотентность: повторный вебхук
    mark_order_paid(o, source="Prodamus (карта)")
    return True


# ------------------------- Этап 3: услуги, комментарии, обязательные платежи -------------------------
def cart_comments_get(uid): return _get(f"onyx:cart_comments:{uid}") or {}
def cart_comments_set(uid, v): _set(f"onyx:cart_comments:{uid}", v)


def has_active_site(uid):
    """Есть ли у клиента уже запущенный сайт ONYX / действующее обслуживание."""
    p = user_get(uid) or {}
    if p.get("subscription_status") in ("active", "payment_due", "overdue"):
        return True
    sub = p.get("subscription")
    if sub and (sub.get("status") in ("active", "payment_due", "overdue") or sub.get("active")):
        return True
    purchased = p.get("purchased_services") or []
    if "launch" in purchased or "service" in purchased:
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
        head += "\n\n🔒 «Запуск» и «Обслуживание» обязательны для нового сайта."
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
    return bool(p.get("name") and p.get("contact"))


def mark_purchased(uid, items):
    """Отметить услуги как купленные (для логики обязательных платежей и кабинета)."""
    p = user_get(uid)
    if not p:
        return
    pur = set(p.get("purchased_services") or [])
    pur.update(norm_service(i) for i in items)
    p["purchased_services"] = list(pur)
    # Подписка создаётся отдельно (sub_create) при completed — здесь статус не трогаем.
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


# ------------------------- Этап 6: подписки -------------------------
SUB_PLAN_NAME = "Обслуживание сайта"
SUB_PRICE = 1990
SUB_STATUSES = ("inactive", "active", "payment_due", "overdue", "cancelled")
DUE_BEFORE_DAYS = 3      # за сколько дней до оплаты считаем payment_due
OVERDUE_AFTER_DAYS = 3   # напоминание о просрочке через N дней после даты


def today_str():
    return time.strftime("%Y-%m-%d")


def days_between(a, b):
    """b - a в днях (ISO-даты)."""
    import datetime
    try:
        return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days
    except Exception:
        return 0


def sub_get(uid):
    p = user_get(uid) or {}
    return p.get("subscription")


def sub_save(uid, sub, sync_client=True):
    p = user_get(uid) or {"uid": uid, "created": now_str()}
    sub["updated_at"] = now_str()
    p["subscription"] = sub
    if sync_client:
        # subscription_status в Clients синхронизируем со статусом подписки
        p["subscription_status"] = sub.get("status", "inactive")
        act = set(p.get("active_services") or [])
        if sub.get("status") in ("active", "payment_due", "overdue"):
            act.add("service")
        else:
            act.discard("service")
        p["active_services"] = list(act)
    user_save(uid, p)
    sheet_subscription(uid, sub)
    return sub


def sub_create(uid, payment_method="", start=None, months=1):
    """Создать/активировать подписку на обслуживание."""
    start = start or today_str()
    p = user_get(uid) or {}
    old = p.get("subscription") or {}
    sub = {
        "subscription_id": old.get("subscription_id") or f"sub-{uid}",
        "telegram_id": uid,
        "client_name": p.get("name", ""),
        "website": p.get("website", ""),
        "plan_name": SUB_PLAN_NAME,
        "amount": SUB_PRICE,
        "status": "active",
        "start_date": old.get("start_date") or start,
        "last_payment_date": start,
        "next_payment_date": add_days(start, 30 * months),
        "payment_method": payment_method or old.get("payment_method", ""),
        "prodamus_subscription_id": old.get("prodamus_subscription_id", ""),
        "created_at": old.get("created_at") or now_str(),
        "active": True,  # обратная совместимость со старым кодом
        "plan": SUB_PLAN_NAME, "price": SUB_PRICE,
        "since": start, "next": add_days(start, 30 * months),
    }
    return sub_save(uid, sub)


def sub_renew(uid, payment_method=""):
    """Продление после успешной оплаты: next = +30 дней от сегодня (или от прошлой даты)."""
    sub = sub_get(uid)
    if not sub:
        return sub_create(uid, payment_method=payment_method)
    today = today_str()
    base = sub.get("next_payment_date") or today
    # если просрочили — считаем от сегодня, иначе от плановой даты
    nxt = add_days(base, 30) if days_between(today, base) > 0 else add_days(today, 30)
    sub.update({"status": "active", "last_payment_date": today, "next_payment_date": nxt,
                "active": True, "since": today, "next": nxt})
    if payment_method:
        sub["payment_method"] = payment_method
    return sub_save(uid, sub)


def sub_cancel(uid):
    sub = sub_get(uid)
    if not sub:
        return None
    sub.update({"status": "cancelled", "active": False})
    return sub_save(uid, sub)


def sub_refresh_status(uid, sub=None):
    """Пересчитать статус по датам: active -> payment_due -> overdue."""
    sub = sub or sub_get(uid)
    if not sub or sub.get("status") in ("cancelled", "inactive"):
        return sub
    nxt = sub.get("next_payment_date")
    if not nxt:
        return sub
    left = days_between(today_str(), nxt)  # >0 — ещё есть время, <0 — просрочено
    if left < 0:
        new = "overdue"
    elif left <= DUE_BEFORE_DAYS:
        new = "payment_due"
    else:
        new = "active"
    if new != sub.get("status"):
        sub["status"] = new
        sub["active"] = new in ("active", "payment_due")
        sub_save(uid, sub)
    return sub


def sub_pay_kb(uid):
    rows = []
    link = sub_payment_link(uid)
    if link:
        rows.append([{"text": f"💳 Оплатить обслуживание · {fmt_amount(SUB_PRICE)}", "url": link}])
    else:
        rows.append([{"text": "💳 Оплатить обслуживание", "callback_data": "sub:pay"}])
    rows.append([{"text": "🆘 Написать в поддержку", "callback_data": "myorder:support"}])
    return {"inline_keyboard": rows}


def sub_payment_link(uid):
    """Разовая ссылка на оплату обслуживания через Prodamus (если настроен)."""
    if not PRODAMUS_SHOP_URL:
        return None
    return prodamus_link({"id": f"sub{uid}", "items": ["service"]})


def run_subscription_reminders():
    """Планировщик (cron): напоминания за 3 дня, в день оплаты, через 3 дня после просрочки."""
    today = today_str()
    n = 0
    for uid in all_subscribers():
        p = user_get(uid)
        sub = (p or {}).get("subscription")
        if not sub or sub.get("status") in ("cancelled", "inactive"):
            continue
        nxt = sub.get("next_payment_date") or sub.get("next")
        if not nxt:
            continue
        sub = sub_refresh_status(uid, sub)
        left = days_between(today, nxt)  # 3 = через 3 дня, 0 = сегодня, -3 = просрочка 3 дня
        sent_map = sub.get("reminders_sent") or {}
        key = None
        text = None
        if left == DUE_BEFORE_DAYS:
            key, text = f"before:{nxt}", (
                "🔔 Напоминаем, что через 3 дня наступает срок оплаты обслуживания сайта ONYX. "
                "Вы можете продлить обслуживание заранее.")
        elif left == 0:
            key, text = f"due:{nxt}", (
                "🔔 Сегодня дата оплаты обслуживания сайта ONYX. "
                "Для продолжения сопровождения, пожалуйста, оплатите обслуживание.")
        elif left == -OVERDUE_AFTER_DAYS:
            key, text = f"overdue:{nxt}", (
                "⚠️ Оплата обслуживания сайта просрочена. "
                "Пожалуйста, свяжитесь с поддержкой ONYX или продлите обслуживание.")
        if not key or sent_map.get(key):
            continue
        send(uid, text, sub_pay_kb(uid))
        sent_map[key] = today
        sub["reminders_sent"] = sent_map
        sub_save(uid, sub)
        if left <= -OVERDUE_AFTER_DAYS:
            notify_admins(f"⚠️ Просрочка обслуживания: id {uid} ({(p or {}).get('name', '')}), "
                          f"дата оплаты {nxt}")
        n += 1
    return n


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
            send(cuid, "Ваш сайт готов! 🎉 Пожалуйста, оцените нашу работу:", rating_kb())
    return f"✅ Заказ №{oid} → {STATUS[key]}. Клиент уведомлён."


# ------------------------- Этап 1: клиент, профиль, статусы, витрины -------------------------
CLIENT_STATUS_RU = {"new": "новый", "client": "клиент", "vip": "VIP"}
Q_STATUS_RU = {"not_filled": "не заполнена", "filled": "заполнена"}
SUB_STATUS_RU = {"inactive": "неактивна", "active": "активна",
                 "payment_due": "требуется продление", "overdue": "просрочена",
                 "cancelled": "отменена"}

SUPPORT_TEXT = (
    "🆘 <b>Поддержка ONYX</b>\n\n"
    "Разработчик вашего проекта: @softstaticg\n"
    "Служба поддержки ONYX: @ONYXCOOP"
)
ONYX_INFO = (
    "ℹ️ <b>ONYX WEB</b>\n\n"
    "Мы создаём сайты для бизнеса под ключ. <b>Разработка — 0 ₽</b> — "
    "вы оплачиваете только домен, хостинг и дополнительные опции по желанию.\n\n"
    "• Запуск за 1–2 дня\n"
    "• Сначала сайт — потом оплата\n"
    "• Обслуживание и поддержка после запуска\n\n"
    "Сайт: https://onyx-web.ru\n"
    "Поддержка: @ONYXCOOP"
)
CABINET_KB = {"inline_keyboard": [
    [{"text": "👤 Мой профиль", "callback_data": "cab:profile"}],
    [{"text": "🛍 Мои покупки", "callback_data": "cab:orders"}],
    [{"text": "🆘 Поддержка", "callback_data": "cab:support"}],
    [{"text": "ℹ️ Информативный ONYX", "callback_data": "cab:info"}],
]}


def render_profile(uid):
    p = user_get(uid) or {}
    un = p.get("username")
    un = f"@{un}" if un else "не указано"
    cs = CLIENT_STATUS_RU.get(p.get("client_status", "new"), p.get("client_status", "new"))
    qs = Q_STATUS_RU.get(p.get("questionnaire_status", "not_filled"), p.get("questionnaire_status", "not_filled"))
    ss = SUB_STATUS_RU.get(p.get("subscription_status", "inactive"), p.get("subscription_status", "inactive"))
    return (
        "👤 <b>Мой профиль</b>\n\n"
        f"Имя: {p.get('name') or 'не указано'}\n"
        f"Telegram ID: {uid}\n"
        f"Username: {un}\n"
        f"Телефон: {p.get('phone') or 'не указано'}\n"
        f"Город: {p.get('city') or 'не указано'}\n"
        f"Ниша: {p.get('niche') or 'не указано'}\n\n"
        f"Статус клиента: {cs}\n"
        f"Статус анкеты: {qs}\n"
        f"Статус подписки: {ss}"
        + ("\n🔔 Обслуживание сайта: активно"
           if ("service" in (p.get("active_services") or []) or p.get("subscription_status") == "active")
           else "")
    )


def render_subscription_block(uid):
    """Блок подписки для «Мои покупки». Возвращает (text, keyboard|None)."""
    sub = sub_get(uid)
    if not sub or sub.get("status") in ("inactive", "cancelled"):
        return ("🔧 <b>Обслуживание сайта:</b> не подключено\n"
                "Обслуживание подключается вместе с запуском сайта.", None)
    sub = sub_refresh_status(uid, sub)
    st = sub.get("status")
    nxt = sub.get("next_payment_date") or "—"
    amount = fmt_amount(sub.get("amount", SUB_PRICE))
    if st == "active":
        return (f"🔧 <b>Обслуживание сайта: активно</b>\n"
                f"Следующая оплата: {nxt}\n"
                f"Сумма: {amount}", None)
    if st == "payment_due":
        return (f"🔧 <b>Обслуживание сайта: требуется продление</b>\n"
                f"Дата следующей оплаты: {nxt}\n"
                f"Сумма: {amount}", sub_pay_kb(uid))
    if st == "overdue":
        return ("🔧 <b>Обслуживание сайта: оплата просрочена</b>\n"
                "Пожалуйста, продлите обслуживание, чтобы сайт продолжал сопровождаться "
                "командой ONYX.\n"
                f"Дата оплаты была: {nxt} · Сумма: {amount}", sub_pay_kb(uid))
    return (f"🔧 <b>Обслуживание сайта:</b> {st}", None)


def render_orders(uid):
    """«Мои покупки»: активные услуги, купленные услуги, подписка. Возвращает (text, kb)."""
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

    sub_text, sub_kb = render_subscription_block(uid)
    lines.append(sub_text)

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
        [{"text": "🌐 Заказать сайт", "callback_data": "brief:start"}],
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
    notify_admins(f"⚠️ <b>Аудит №{a['audit_id']} — нужен ручной разбор</b>\n"
                  f"Сайт: {a['website_url']}\n"
                  f"Клиент: id {a['telegram_id']} {('@' + a['username']) if a.get('username') else ''}\n"
                  f"Причина: {reason or 'PR-CY недоступен'}")
    return a


def audit_start(chat_id, uid, url, username=""):
    a = audit_new(uid, username, url)
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
    """Спец-логика при статусе completed: уведомление, отзыв, активация услуг/подписки."""
    cuid = o.get("uid")
    if not cuid:
        return
    mark_purchased(cuid, o.get("items", []))  # активируем купленные услуги в профиле
    paid = o.get("paid") or o.get("payment_status") == "paid"
    if "service" in o.get("items", []) and paid:
        sub_create(cuid, payment_method=o.get("payment_method", ""))
    send(cuid, "🎉 <b>Ваш сайт готов!</b>\nПроверьте его и убедитесь, что всё нравится 👇",
         {"inline_keyboard": [[{"text": "🌐 Открыть сайт", "url": SITE_URL}]]})
    send(cuid, "Пожалуйста, оцените нашу работу — это помогает нам становиться лучше:", rating_kb())


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


def sheet_subscription(uid, sub):
    p = user_get(uid) or {}
    sheet_post("Subscriptions", {
        "subscription_id": sub.get("subscription_id") or f"sub-{uid}",
        "telegram_id": uid,
        "client_name": sub.get("client_name") or p.get("name", ""),
        "website": sub.get("website") or p.get("website", ""),
        "plan_name": sub.get("plan_name") or sub.get("plan", SUB_PLAN_NAME),
        "amount": sub.get("amount", sub.get("price", SUB_PRICE)),
        "status": sub.get("status", "inactive"),
        "start_date": sub.get("start_date") or sub.get("since", ""),
        "last_payment_date": sub.get("last_payment_date") or sub.get("since", ""),
        "next_payment_date": sub.get("next_payment_date") or sub.get("next", ""),
        "payment_method": sub.get("payment_method", ""),
        "prodamus_subscription_id": sub.get("prodamus_subscription_id", ""),
        "created_at": sub.get("created_at", ""),
        "updated_at": sub.get("updated_at") or now_str(),
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
    if data.get("biz"):
        p["niche"] = data["biz"]
    c = data.get("contact", "")
    if c and any(ch.isdigit() for ch in c) and "@" not in c:
        p["phone"] = c
    if data.get("name"):
        p["name"] = data["name"]
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
    [{"text": "🔍 Бесплатный аудит"}],
    [{"text": "🛒 Тарифы и услуги"}, {"text": "📦 Мой заказ"}],
    [{"text": "👤 Личный кабинет"}, {"text": "🤝 Стать партнёром"}],
    [{"text": "🆘 Поддержка"}],
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
    mark_questionnaire_filled(uid, data)
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
    amount = ""
    if kind == "legal" and st.get("order_id"):
        # реквизиты к уже оформленному заказу
        o = order_get(st["order_id"])
        if o:
            o.update({"company": data.get("company"), "inn": data.get("inn"),
                      "email": data.get("email"), "payment_type": "Юрлицо",
                      "payment_method": "Счёт (юрлицо)", "status": "invoice",
                      "updated": now_str()})
            order_save(o)
            sheet_order(o)
            names = ", ".join(SERVICE.get(c, {}).get("name", c) for c in o.get("items", []))
            amount = o.get("total", "")
            extra = f"\nЗаказ №{o['id']}: {names} — {fmt_amount(o.get('total', 0))}"
            comment += extra
    elif kind == "legal" and st.get("cart"):
        items = [ITEM[c][0] for c in st["cart"] if c in ITEM]
        total = cart_total(st["cart"])
        oid = order_new(user.get("id"), items, total, "Юрлицо", status="invoice",
                        extra={"company": data.get("company"), "inn": data.get("inn"), "email": data.get("email")})
        cart_set(user.get("id"), [])
        amount = total
        extra = f"\nЗаказ №{oid}: " + ", ".join(items) + f" — {total} ₽"
        comment += extra
    upsert_user(user.get("id"), contact=data.get("contact") or data.get("email"), username=user.get("username"))
    notify_manager(f"📥 <b>{cfg['type']}</b>\n💬 {username} (id {user.get('id')})\n{comment}")
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
        send(chat_id, "Перед оформлением заполните короткую анкету (2 минуты) — "
                      "менеджер сразу подготовит всё под ваш проект.",
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
                  "Как будете оплачивать?",
         {"inline_keyboard": [
             [{"text": "💳 Оплатить картой", "callback_data": f"ord:pay:card:{oid}"}],
             [{"text": "🏢 Счёт для юрлица", "callback_data": f"ord:pay:invoice:{oid}"}],
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
    total = fmt_amount(o.get("total", 0))
    if method == "card":
        link = prodamus_link(o)
        if link:
            o["payment_method"] = "Карта (Prodamus)"
            o["status"] = "wait_pay"
            o["updated"] = now_str()
            order_save(o)
            sheet_order(o)
            notify_admins(f"💳 Заказ №{o['id']}: клиент перешёл к оплате картой (id {uid}).")
            send(chat_id, f"Заказ <b>№{o['id']}</b> на <b>{total}</b>.\n"
                          "Нажмите кнопку ниже — оплата картой или через СБП, чек придёт автоматически.",
                 {"inline_keyboard": [[{"text": f"💳 Перейти к оплате · {total}", "url": link}]]})
        else:
            # Prodamus ещё не настроен — не теряем заказ, зовём менеджера
            o["payment_method"] = "Карта (ручная ссылка)"
            o["updated"] = now_str()
            order_save(o)
            notify_admins(f"💳 Заказ №{o['id']} на {total}: клиент хочет оплату картой, "
                          f"но Prodamus не настроен. Пришлите ссылку вручную. id {uid}")
            send(chat_id, f"Заказ <b>№{o['id']}</b> на <b>{total}</b> принят ✅\n"
                          "Менеджер пришлёт ссылку на оплату в ближайшее время 🤝", MAIN_MENU)
    else:  # invoice — счёт для юрлица (только ИНН)
        state_set(uid, {"flow": "invoice_inn", "order_id": o["id"]})
        send(chat_id, "🏢 <b>Счёт для юрлица</b>\n\n"
                      "Введите <b>ИНН</b> компании (10 или 12 цифр) — по нему мы подготовим счёт.\n"
                      "<i>Другие данные не нужны.</i>",
             {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True})


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
        register_client(uid, user)
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
                 "/invoices — заказы, ждущие счёта (юрлица)\n"
                 "/order &lt;№&gt; — детали заказа\n"
                 "/status &lt;№&gt; &lt;ключ&gt; — сменить статус\n"
                 "/set_order_status &lt;№&gt; [статус] — статус проекта (кнопки, если без статуса)\n"
                 "/paid &lt;№&gt; — отметить заказ оплаченным\n"
                 "ключи проекта: created, waiting_payment, paid_waiting_start, questionnaire_review, in_production, design_review, domain_setup, final_check, completed, paused, cancelled\n"
                 "/sub &lt;uid&gt; on|off — подписка обслуживания\n"
                 "/sub_date &lt;uid&gt; &lt;ГГГГ-ММ-ДД&gt; — дата следующей оплаты\n"
                 "/due — клиенты, у кого скоро оплата\n"
                 "/overdue — клиенты с просрочкой\n"
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
                sub = sub_create(tuid)
                send(chat_id, f"✅ Подписка включена для id {tuid}. "
                              f"Следующая оплата: {sub.get('next_payment_date')}")
                try:
                    send(tuid, f"✅ Подключено обслуживание сайта — {fmt_amount(SUB_PRICE)}/мес.\n"
                               f"Следующая оплата: {sub.get('next_payment_date')}")
                except Exception:
                    pass
            else:
                sub_cancel(tuid)
                send(chat_id, f"Подписка отключена для id {tuid}.")
            return
        if low.startswith("/sub_date "):
            parts = low.split()
            if len(parts) < 3:
                send(chat_id, "Формат: /sub_date &lt;uid&gt; &lt;ГГГГ-ММ-ДД&gt;"); return
            try:
                tuid = int(parts[1])
            except Exception:
                send(chat_id, "uid должен быть числом."); return
            date = parts[2]
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                send(chat_id, "Дата в формате ГГГГ-ММ-ДД."); return
            sub = sub_get(tuid)
            if not sub:
                send(chat_id, "У клиента нет подписки. Включите: /sub &lt;uid&gt; on"); return
            sub["next_payment_date"] = date
            sub["next"] = date
            sub["reminders_sent"] = {}  # сбрасываем, чтобы напоминания отправились по новой дате
            sub_save(tuid, sub)
            sub_refresh_status(tuid)
            send(chat_id, f"✅ Дата следующей оплаты для id {tuid}: {date}")
            return
        if low.startswith("/overdue") or low.startswith("/due"):
            want = "overdue" if low.startswith("/overdue") else "payment_due"
            title = "⚠️ <b>Просроченные подписки</b>" if want == "overdue" else "🔔 <b>Скоро оплата</b>"
            lines = [title]
            found = False
            for suid in all_subscribers():
                sub = sub_get(suid)
                if not sub or sub.get("status") in ("inactive", "cancelled"):
                    continue
                sub = sub_refresh_status(suid, sub)
                if sub.get("status") != want:
                    continue
                found = True
                pp = user_get(suid) or {}
                lines.append(f"id {suid} · {pp.get('name', '—')} · "
                             f"{fmt_amount(sub.get('amount', SUB_PRICE))} · "
                             f"оплата {sub.get('next_payment_date', '—')}")
            send(chat_id, "\n".join(lines) if found else "Никого нет.")
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
    if st and st.get("flow") in ("brief", "cap", "svc_comment", "invoice_inn", "audit_url") and text in MENU_TRIGGERS:
        state_del(uid); st = None
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
        step = BRIEF_STEPS[st["i"]]
        if step.get("text"):
            brief_text_input(chat_id, user, st, text, contact); return
        send(chat_id, "Пожалуйста, выберите вариант кнопкой выше 👆"); return
    if st and st.get("flow") == "cap":
        cap_text_input(chat_id, user, st, text, contact); return

    # Меню
    if text == "🔍 Бесплатный аудит":
        state_set(uid, {"flow": "audit_url"})
        send(chat_id, AUDIT_INTRO, {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True})
        return
    if text == "🛒 Тарифы и услуги":
        ensure_mandatory(uid)
        send(chat_id, services_list_text(uid), services_list_kb(uid)); return
    if text == "📦 Мой заказ":
        txt, kb = render_my_order(uid)
        send(chat_id, txt, kb); return
    if text == "👤 Личный кабинет":
        send(chat_id, "👤 <b>Личный кабинет</b>\nВыберите раздел:", CABINET_KB); return
    if text == "🤝 Стать партнёром":
        pu = user_get(uid)
        ref_link = f"https://t.me/{bot_username()}?start=ref{uid}"
        cnt = pu.get("referrals", 0)
        send(chat_id, PARTNER_INFO + f"\n\n🔗 Ваша ссылка: {ref_link}\n👥 Приведено: {cnt}",
             {"inline_keyboard": [[{"text": "✍️ Оставить контакт", "callback_data": "pt:start"}]]}); return
    if text == "🆘 Поддержка":
        send(chat_id, SUPPORT_TEXT, MAIN_MENU); return

    # старые кнопки (обратная совместимость, не показываются в меню)
    if text in ("🌐 Получить сайт", "/brief"):
        st = {"flow": "brief", "i": 0, "data": {}}
        state_set(uid, st); send_brief_step(chat_id, st); return
    if text == "📋 Что подготовить":
        send_checklist(chat_id); return
    if text == "💬 Вопрос менеджеру":
        start_cap(chat_id, uid, "ask"); return

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
        send(chat_id, cart_show_text(uid), cart_show_kb(uid)); return

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
        send(chat_id, added_text(cid), added_kb(cid))
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
        send(chat_id, cart_show_text(uid), cart_show_kb(uid))
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

    # --- Этап 6: оплата обслуживания ---
    if data == "sub:pay":
        link = sub_payment_link(uid)
        if link:
            send(chat_id, f"Оплата обслуживания сайта — {fmt_amount(SUB_PRICE)}/мес.",
                 {"inline_keyboard": [[{"text": f"💳 Перейти к оплате · {fmt_amount(SUB_PRICE)}", "url": link}]]})
        else:
            p = user_get(uid) or {}
            sheet_post("ClientRequests", {
                "type": "subscription_payment", "date": now_str(), "telegram_id": uid,
                "username": p.get("username", ""), "amount": SUB_PRICE,
                "comment": "Заявка на оплату обслуживания",
            })
            notify_admins(f"🔔 <b>Заявка на оплату обслуживания</b>\n"
                          f"Клиент: {p.get('name', '—')} (id {uid})\n"
                          f"Сумма: {fmt_amount(SUB_PRICE)}\n"
                          "Пришлите клиенту ссылку на оплату.")
            send(chat_id, "Заявка на оплату обслуживания принята ✅\n"
                          "Менеджер пришлёт ссылку на оплату в ближайшее время 🤝", MAIN_MENU)
        return

    # --- Этап 5: раздел «Мой заказ» ---
    if data == "audit:offer":
        p = user_get(uid) or {}
        uname = f"@{user.get('username')}" if user.get("username") else "—"
        notify_admins("📩 <b>Запрос предложения после аудита</b>\n"
                      f"Клиент: {p.get('name', '—')}\nTelegram: {uname} (id {uid})\n"
                      f"Контакт: {p.get('contact', '—')}")
        send(chat_id, "Спасибо! Мы подготовим предложение по улучшению вашего сайта "
                      "и свяжемся с вами в ближайшее время 🤝", MAIN_MENU)
        return

    if data == "myorder:open":
        txt, kb = render_my_order(uid)
        send(chat_id, txt, kb); return
    if data == "myorder:ok":
        answer_cb(cq["id"], "Отлично! Мы на связи 🤝"); return
    if data == "myorder:dev":
        send(chat_id, f"👨‍💻 Разработчик вашего проекта: @{DEVELOPER_USERNAME}"); return
    if data == "myorder:support":
        send(chat_id, f"🆘 Служба поддержки ONYX: @{MANAGER_USERNAME}"); return
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
    if data == "cab:support":
        send(chat_id, SUPPORT_TEXT, MAIN_MENU); return
    if data == "cab:info":
        send(chat_id, ONYX_INFO, MAIN_MENU); return
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
                try:
                    n += run_pending_audits()
                except Exception as e:
                    print("audits cron err", e)
            except Exception as e:
                print("cron err", e); n = -1
            self._ok(f"cron ok: {n}".encode("utf-8")); return
        self._ok("ONYX bot webhook is running".encode("utf-8"))

    def do_POST(self):
        path = self.path or ""
        length = int(self.headers.get("content-length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        # --- Prodamus webhook (Этап 4) ---
        if "prodamus" in path:
            try:
                handle_prodamus_webhook(raw, self.headers)
            except Exception as e:
                print("Prodamus webhook error:", e)
            self._ok(b"OK")  # всегда 200, чтобы Prodamus не ретраил бесконечно
            return
        # --- Telegram webhook ---
        if WEBHOOK_SECRET and self.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != WEBHOOK_SECRET:
            self._ok(b"forbidden", 403); return
        try:
            process_update(json.loads(raw or b"{}"))
        except Exception as e:
            print("Handler error:", e)
        self._ok()
