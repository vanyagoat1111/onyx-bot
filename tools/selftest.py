#!/usr/bin/env python3
"""ONYX — самопроверка бота без обращения к сети.

Запуск:  python3 tools/selftest.py
Проверяет: воронку целиком, аудит, Google Drive, согласия, отзывы,
навигацию «Назад», работу с включённой базой, скорость (нет ли блокировок).
"""
import os, sys, re, importlib.util, collections

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.join(os.path.dirname(HERE), "api", "index.py")
os.environ.setdefault("BOT_TOKEN", "TEST")
os.environ["ADMIN_TELEGRAM_IDS"] = "999"
os.environ["SHEETS_WEBHOOK_URL"] = "https://fake/exec"
os.environ["TARIFF_IMG_BASE"] = "https://bot.test"

FAILS = []


def load(kv=False):
    """Свежий экземпляр бота. kv=True — с включённой базой."""
    os.environ["KV_REST_API_URL"] = "https://fake.upstash.io" if kv else ""
    os.environ["KV_REST_API_TOKEN"] = "t" if kv else ""
    spec = importlib.util.spec_from_file_location(f"onyx_{kv}", BOT)
    bot = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bot
    spec.loader.exec_module(bot)
    bot.SENT = []
    db = {}

    def fake_redis(*cmd):
        c = cmd[0].upper()
        if c == "GET":
            return db.get(cmd[1])
        if c == "SET":
            db[cmd[1]] = cmd[2]; return "OK"
        if c == "DEL":
            db.pop(cmd[1], None); return 1
        if c in ("INCR", "INCRBY"):
            step = int(cmd[2]) if c == "INCRBY" else 1
            db[cmd[1]] = str(int(db.get(cmd[1], 0)) + step); return int(db[cmd[1]])
        if c in ("LRANGE", "SMEMBERS"):
            return db.get(cmd[1], [])
        if c in ("RPUSH", "SADD"):
            db.setdefault(cmd[1], []).append(cmd[2]); return 1
        return 1

    def fake_tg(m, **kw):
        bot.SENT.append((m, kw))
        if m == "getFile":
            return {"ok": True, "result": {"file_path": "p/f.jpg"}}
        if m == "sendMessage":
            return {"ok": True, "result": {"message_id": 50, "chat": {"id": kw.get("chat_id")}}}
        return {"ok": True, "result": {}}

    bot._redis = fake_redis
    bot._redis_many = lambda cmds: [fake_redis(*c) for c in cmds]
    bot.tg = fake_tg
    bot._post_to_sheet_now = lambda row: True
    bot.drive_call = lambda p, timeout=20: {
        "ok": True, "folder_url": "https://drive/F", "folder_id": "F", "name": "n"}
    bot.site_probe = lambda url: dict(
        domain="salon.ru", reachable=True, https=True, load_sec=2.1, viewport=True,
        title="Салон красоты в Казани", description="", h1=["Салон"], analytics=[],
        constructor="Tilda", forms=1, tel=1, mail=0, wa=True, tg=True, map=True,
        images=12, lazy=0, footer_year=2026, robots=True, sitemap=False, size_kb=300,
        text_len=5000, sig_cart=0, sig_catalog=2, sig_booking=14, sig_lead=4,
        sig_about=5, sig_portfolio=3, sig_price=8, sig_reviews=6, h2_count=6, og=True)
    bot._DB = db
    return bot


def txt(kw):
    return (kw.get("text") or (kw.get("rich_message") or {}).get("markdown")
            or (kw.get("rich_message") or {}).get("html") or kw.get("caption") or "")


def texts(bot, chat):
    return [txt(kw) for m, kw in bot.SENT if kw.get("chat_id") == chat]


def check(name, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  → {detail}"))
    if not cond:
        FAILS.append(name)


def cq(data, uid, mid=50):
    return {"id": "c", "data": data, "from": {"id": uid, "username": "cli"},
            "message": {"chat": {"id": uid}, "message_id": mid}}


def msg(text, uid):
    return {"chat": {"id": uid}, "from": {"id": uid, "username": "cli"},
            "text": text, "message_id": 9}


def run_funnel(bot, uid, answers=None):
    """Пройти воронку от приветствия до заявки. Возвращает id заказа."""
    A = answers or {
        "company_name": "СтройДом", "city": "Пермь и Пермский район",
        "main_services": "Отделка под ключ, кровля", "advantages": "Своя бригада",
        "trust_facts": "12 лет, гарантия 2 года", "target_audience": "Собственники квартир",
        "work_types": "Отделка, кровля", "main_objects": "Квартиры и дома",
        "contacts_extra": "@sd, sd@mail.ru", "domain_site": "stroydom59.ru",
        "style_wishes": "Строгий, тёмно-синий"}
    U = bot.process_update
    U({"callback_query": cq("brief:start", uid)})
    U({"callback_query": cq("goal:leads", uid)})
    U({"callback_query": cq("niche:construction", uid)})
    steps = bot.state_get(uid)["steps"]
    for _ in range(40):
        st = bot.state_get(uid)
        if not st or st.get("stage") == "summary":
            break
        s = steps[st["i"]]
        if s.get("multi"):
            for i in range(len(s["multi"])):
                U({"callback_query": cq(f"b:m:{i}", uid)})
            U({"callback_query": cq("b:mdone", uid)})
        elif s.get("opts"):
            U({"callback_query": cq("b:o:0", uid)})
        else:
            U({"message": msg(A.get(s["key"], "тест"), uid)})
    U({"callback_query": cq("b:ok", uid)})
    for c in ("qual:prod", "qual:rules", "qual:payment", "qual:start"):
        U({"callback_query": cq(c, uid)})
    for i in range(4):
        U({"callback_query": cq(f"mq:{i}:yes", uid)})
    U({"callback_query": cq("qual:pkg_ok", uid)})
    U({"callback_query": cq("cart:checkout", uid)})
    U({"callback_query": cq("checkout:go", uid)})
    U({"callback_query": cq("consent:ok", uid)})
    U({"callback_query": cq("pm:fiz", uid)})
    for x in ("Иванов Иван", "+79990001122", "i@sd.ru"):
        U({"message": msg(x, uid)})
    p = bot.user_get(uid) or {}
    return (p.get("orders") or [None])[-1]


# ─────────────────────────── проверки ───────────────────────────
def t_static():
    print("\n▸ Статический анализ")
    src = open(BOT, encoding="utf-8").read()
    tree = __import__("ast").parse(src)
    ast = __import__("ast")
    dups = {k: v for k, v in collections.Counter(
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))).items() if v > 1}
    check("нет дублей функций", not dups, str(dups))
    no_to = [m for m in re.finditer(r"urlopen\(([^)]*)\)", src) if "timeout" not in m.group(1)]
    check("у всех сетевых вызовов есть таймаут", not no_to, f"{len(no_to)} шт.")
    bare = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.ExceptHandler) and n.type is None]
    check("нет голых except", not bare, str(bare[:5]))


def t_funnel():
    print("\n▸ Воронка целиком")
    bot = load(); uid = 1001
    oid = run_funnel(bot, uid)
    p = bot.user_get(uid)
    check("квалификация пройдена", p.get("qualified") is True)
    check("тариф подобран", bool(p.get("chosen_tariff")), str(p.get("chosen_tariff")))
    check("заказ создан", bool(oid), str(oid))
    row = bot._get(f"onyx:prompts:{uid}")
    check("промпты собраны", bool(row) and row.get("generation_status") == "READY")
    gap = re.compile(r"^[А-ЯЁ][А-ЯЁ ,/\-.]*:\s*\[НУЖНЫ ДАННЫЕ\]\s*$")
    gaps = [l for l in (row or {}).get("prompt_1", "").split("\n") if gap.match(l)]
    check("в промпте №1 нет пробелов", not gaps, str(gaps[:3]))
    check("папка Drive создана", bool((bot._get(f"onyx:drive:{uid}") or {}).get("folder_url")))


def t_order_before_tariff():
    print("\n▸ Квалификация идёт ДО тарифа")
    bot = load(); uid = 1002
    U = bot.process_update
    U({"callback_query": cq("brief:start", uid)})
    U({"callback_query": cq("goal:leads", uid)})
    U({"callback_query": cq("niche:other", uid)})
    steps = bot.state_get(uid)["steps"]
    for _ in range(40):
        st = bot.state_get(uid)
        if not st or st.get("stage") == "summary":
            break
        s = steps[st["i"]]
        if s.get("multi"):
            U({"callback_query": cq("b:mdone", uid)})
        elif s.get("opts"):
            U({"callback_query": cq("b:o:0", uid)})
        else:
            U({"message": msg("тест Пермь", uid)})
    U({"callback_query": cq("b:ok", uid)})
    check("после анкеты тарифа ещё нет", not (bot.user_get(uid) or {}).get("chosen_tariff"))
    for c in ("qual:prod", "qual:rules", "qual:payment", "qual:start"):
        U({"callback_query": cq(c, uid)})
    for i in range(4):
        U({"callback_query": cq(f"mq:{i}:yes", uid)})
    check("тариф появился после квалификации",
          bool((bot.user_get(uid) or {}).get("chosen_tariff")))


def t_navigation():
    print("\n▸ Кнопка «Назад» на каждом экране")
    bot = load(); uid = 1003
    back = re.compile(r"⬅️|🏠|Назад|меню", re.I)
    U = bot.process_update
    screens = []

    def grab(label):
        kbs = [kw.get("reply_markup") or {} for m, kw in bot.SENT if kw.get("chat_id") == uid]
        btns = [b.get("text", "") for r in (kbs[-1] if kbs else {}).get("inline_keyboard", [])
                for b in r] if kbs else []
        screens.append((label, any(back.search(b) for b in btns), btns))
        bot.SENT.clear()

    U({"callback_query": cq("brief:start", uid)}); grab("выбор цели")
    U({"callback_query": cq("goal:leads", uid)}); grab("выбор ниши")
    U({"callback_query": cq("niche:construction", uid)}); grab("первый вопрос анкеты")
    for lbl, ok, btns in screens:
        check(f"возврат есть: {lbl}", ok, str(btns))


def t_audit():
    print("\n▸ Аудит сайта")
    bot = load(); uid = 1004
    bot.audit_start(uid, uid, "https://salon.ru", username="cli")
    t = " ".join(texts(bot, uid))
    check("отчёт отправлен", "Аудит сайта" in t)
    check("есть предложение тарифа", "ЧТО МЫ ПРЕДЛАГАЕМ" in t.upper())
    check("внутренняя цель не показана клиенту", "booking" not in t and "Цель сайта" not in t)
    a = bot.audit_get(1)
    check("цель определена (запись)", (a or {}).get("site_goal") == "booking",
          str((a or {}).get("site_goal")))
    check("тариф записан в профиль",
          (bot.user_get(uid) or {}).get("chosen_tariff") == (a or {}).get("recommended_tariff"))
    kb = [b for m, kw in bot.SENT if kw.get("chat_id") == uid
          for r in (kw.get("reply_markup") or {}).get("inline_keyboard", []) for b in r]
    check("кнопка оформления ведёт на тариф",
          any(b.get("callback_data", "").startswith("trf:pick:") for b in kb))


def t_drive():
    print("\n▸ Google Drive")
    bot = load(); uid = 1005
    calls = []
    bot.drive_call = lambda p, timeout=20: (calls.append(p) or {
        "ok": True, "folder_url": "https://drive/F", "folder_id": "F", "name": "n"})
    bot.user_save(uid, {"telegram_id": uid, "name": "Тест", "orders": [1]})
    bot._set(f"onyx:quest:{uid}", {"company_name": "Тест", "city": "Пермь"})
    bot.process_update({"callback_query": cq("up:cat:logo", uid)})
    bot.process_update({"message": {"chat": {"id": uid}, "from": {"id": uid, "username": "c"},
                                    "photo": [{"file_id": "A"}, {"file_id": "B"}], "message_id": 3}})
    up = [c for c in calls if c.get("action") == "drive_upload"]
    check("файл ушёл в Drive", bool(up))
    check("передаётся ссылка, а не file_id",
          bool(up) and up[0].get("file_url", "").startswith("https://api.telegram.org/file/bot"))
    check("категория сохранена", bool(up) and up[0].get("category") == "logo")
    cnt = (bot._get(f"onyx:drive:{uid}") or {}).get("counts") or {}
    check("счётчик обновлён", cnt.get("logo") == 1, str(cnt))


def t_consent():
    print("\n▸ Согласия (152-ФЗ)")
    bot = load(); uid = 1006
    run_funnel(bot, uid)
    check("согласие зафиксировано", bot.has_consent(uid, "pdn"))
    rec = bot.consent_get(uid, "pdn") or {}
    check("сохранены версия и время",
          bool(rec.get("consent_version")) and bool(rec.get("accepted_at")))
    check("реклама отдельно и не включена сама", not bot.has_consent(uid, "marketing"))
    old = bot.CONSENT_VERSION
    bot.CONSENT_VERSION = "999"
    check("смена версии требует нового согласия", not bot.has_consent(uid, "pdn"))
    bot.CONSENT_VERSION = old


def t_reviews():
    print("\n▸ Отзывы")
    bot = load(); uid = 1007
    oid = run_funnel(bot, uid)
    o = bot.order_get(oid); o["uid"] = uid; bot.order_save(o)
    pr = bot.production_create(o); pr["production_url"] = "https://stroydom59.ru"; bot.prod_save(pr)
    bot.apply_project_status(oid, "completed")
    check("сайт привязан к профилю",
          (bot.user_get(uid) or {}).get("website") == "https://stroydom59.ru")
    bot.process_update({"callback_query": cq(f"rev:begin:{oid}", uid)})
    rid = (bot.state_get(uid) or {}).get("rid")
    for c in ("rev:rate:5", "rev:skip_text", "rev:skip_video", "rev:publish"):
        bot.process_update({"callback_query": cq(c, uid)})
    check("отзыв ушёл на модерацию", (bot.review_get(rid) or {}).get("status") == "pending_review")
    bot.process_update({"callback_query": cq(f"rev:adm:approve:{rid}", 999)})
    check("после одобрения виден в разделе", len(bot.public_reviews_list()) == 1)


def t_no_blocking():
    print("\n▸ Клиент получает ответ раньше записи в таблицу")
    bot = load(); uid = 1008
    log = []
    bot._post_to_sheet_now = lambda row: (log.append(("SHEET", None)), True)[1]
    orig = bot.tg

    def tg2(m, **kw):
        log.append(("TG", kw.get("chat_id")))
        return orig(m, **kw)
    bot.tg = tg2
    for label, upd in (("/start", {"message": msg("/start", uid)}),
                       ("кабинет", {"message": msg("👤 Личный кабинет", uid)}),
                       ("тарифы", {"message": msg("🛒 Тарифы и услуги", uid)})):
        log.clear(); bot.process_update(upd)
        first = next((i for i, (k, c) in enumerate(log) if k == "TG" and c == uid), len(log))
        check(f"{label}: нет ожидания таблицы",
              not any(k == "SHEET" for k, _ in log[:first]))


def t_sheets_batch():
    print("\n▸ Запись в таблицу одним пакетом")
    bot = load(); posts = []
    bot._post_to_sheet_now = lambda row: (posts.append(row), True)[1]
    bot.req_cache_begin()
    for i in range(4):
        bot.post_to_sheet({"table": "T", "n": i})
    bot.flush_sheets(); bot.req_cache_end()
    check("4 строки = 1 запрос", len(posts) == 1 and len(posts[0].get("batch", [])) == 4,
          f"{len(posts)} запрос(ов)")


def t_kv_mode():
    print("\n▸ Работа с включённой базой (кэш и отложенная запись)")
    bot = load(kv=True); uid = 1009
    oid = run_funnel(bot, uid)
    check("заказ создаётся", bool(oid))
    check("кэш очищен после запроса", not bot._REQ_CACHE)
    check("очередь записи пуста", not bot._WRITE_Q)
    check("данные долетели в базу", any(k.startswith("onyx:user:") for k in bot._DB))


def t_rich_fallback():
    print("\n▸ Rich Messages и откат на HTML")
    bot = load()
    calls = []

    def tg3(m, **kw):
        calls.append(m)
        if m == "sendRichMessage":
            return {"ok": False}
        if m == "sendMessage":
            return {"ok": True, "result": {"message_id": 1, "chat": {"id": kw.get("chat_id")}}}
        return {"ok": True, "result": {}}
    bot.tg = tg3
    bot._del("onyx:rich_shape")
    bot.send_rich(1, "## Заголовок\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    check("при отказе уходит обычным сообщением", calls[-1] == "sendMessage")
    calls.clear(); bot.send_rich(1, "## Ещё")
    check("повторно rich не пробуется", "sendRichMessage" not in calls)
    h = bot.md_to_html("## Тест\n- [x] готово\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> цитата")
    check("markdown превращается в HTML",
          "<b>ТЕСТ</b>" in h and "✅ готово" in h and "<pre>" in h and "<blockquote>" in h)
    check("опасные символы экранируются", "&lt;" in bot.md_to_html("5 < 10"))


if __name__ == "__main__":
    print("═" * 60)
    print("  ONYX — самопроверка бота")
    print("═" * 60)
    for fn in (t_static, t_funnel, t_order_before_tariff, t_navigation, t_audit,
               t_drive, t_consent, t_reviews, t_no_blocking, t_sheets_batch,
               t_kv_mode, t_rich_fallback):
        try:
            fn()
        except Exception as e:
            import traceback
            print(f"  ❌ {fn.__name__} упал: {e}")
            traceback.print_exc(limit=3)
            FAILS.append(fn.__name__)
    print("\n" + "═" * 60)
    if FAILS:
        print(f"  ПРОВАЛЕНО: {len(FAILS)}")
        for f in FAILS:
            print("   ·", f)
        sys.exit(1)
    print("  ВСЁ ПРОШЛО ✅")
    print("═" * 60)
