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
    U({"callback_query": cq("demo:go", uid)})
    U({"callback_query": cq("model:ok", uid)})
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
    U({"callback_query": cq("pkg:recommend", uid)})
    U({"callback_query": cq("qual:pkg_ok", uid)})
    U({"callback_query": cq("q8:go", uid)})
    for i in range(8):
        U({"callback_query": cq(f"q8:{i}:yes", uid)})
    U({"callback_query": cq("cart:checkout", uid)})
    U({"callback_query": cq("checkout:go", uid)})
    U({"callback_query": cq("consent:ok", uid)})
    U({"callback_query": cq("pm:fiz", uid)})
    for x in ("Иванов Иван", "+79990001122", "i@sd.ru"):
        U({"message": msg(x, uid)})
    p = bot.user_get(uid) or {}
    return (p.get("orders") or [None])[-1]


# ─────────────────────────── проверки ───────────────────────────
def kb_of(bot, chat):
    return [b for m, kw in bot.SENT if kw.get("chat_id") == chat
            for r in (kw.get("reply_markup") or {}).get("inline_keyboard", []) for b in r]


def cbs(bot, chat):
    return [b.get("callback_data") for b in kb_of(bot, chat) if b.get("callback_data")]


def run_project(bot, uid, answers=None):
    """Часть воронки после консультации: цель → ниша → демо → модель → анкета."""
    A = answers or {
        "company_name": "СтройДом", "city": "Пермь", "main_services": "Отделка под ключ",
        "advantages": "Своя бригада", "trust_facts": "12 лет", "target_audience": "Собственники",
        "work_types": "Отделка", "main_objects": "Квартиры", "contacts_extra": "@sd",
        "domain_site": "sd.ru", "style_wishes": "Строгий"}
    U = bot.process_update
    U({"callback_query": cq("proj:start", uid)})
    U({"callback_query": cq("proj:go", uid)})
    U({"callback_query": cq("goal:leads", uid)})
    U({"callback_query": cq("niche:construction", uid)})
    U({"callback_query": cq("demo:go", uid)})
    U({"callback_query": cq("model:ok", uid)})
    steps = (bot.state_get(uid) or {}).get("steps") or []
    for _ in range(40):
        st = bot.state_get(uid)
        if not st or st.get("flow") != "brief" or st.get("stage") == "summary":
            break
        s = steps[st["i"]]
        if s.get("multi"):
            U({"callback_query": cq("b:mdone", uid)})
        elif s.get("opts"):
            U({"callback_query": cq("b:o:0", uid)})
        else:
            U({"message": msg(A.get(s["key"], "тест"), uid)})
    U({"callback_query": cq("b:ok", uid)})


def t_entry():
    print("\n▸ Вход: три ветки")
    bot = load(); uid = 2001
    bot.process_update({"message": msg("/start", uid)})
    t = " ".join(texts(bot, uid))
    check("на входе спрашиваем про сайт", "С чего начнём" in t)
    c = cbs(bot, uid)
    for br in ("entry:site", "entry:nosite", "entry:manager"):
        check(f"есть ветка {br}", br in c)
    check("тариф на входе не предлагаем",
          not any(x.startswith("trf:pick") for x in c) and "cart:checkout" not in c)
    check("анкета на входе не открывается", "brief:start" not in c, str(c))
    check("статус — первый контакт", bot.crm_status(uid) == "first_contact", bot.crm_status(uid))

    # ветка «сайт есть»
    bot.SENT.clear()
    bot.process_update({"callback_query": cq("entry:site", uid)})
    check("просим адрес сайта", (bot.state_get(uid) or {}).get("flow") == "audit_url")
    check("статус — аудит запрошен", bot.crm_status(uid) == "audit_requested")

    # ветка «сайта нет»
    bot2 = load(); uid2 = 2002
    bot2.process_update({"message": msg("/start", uid2)})
    bot2.SENT.clear()
    bot2.process_update({"callback_query": cq("entry:nosite", uid2)})
    t2 = " ".join(texts(bot2, uid2))
    check("выдаём материал", "чек-лист" in t2.lower() or "Чек-лист" in t2)
    check("ведём на консультацию", "cons:offer" in cbs(bot2, uid2))
    check("статус — материал отправлен", bot2.crm_status(uid2) == "material_sent")

    # ветка «уже общался с менеджером»
    bot3 = load(); uid3 = 2003
    bot3.process_update({"message": msg("/start", uid3)})
    bot3.SENT.clear()
    bot3.process_update({"callback_query": cq("entry:manager", uid3)})
    c3 = cbs(bot3, uid3)
    check("без консультации оформление не открыто", "proj:start" not in c3, str(c3))
    bot3.crm_set(uid3, "consult_done")
    bot3.SENT.clear()
    bot3.process_update({"callback_query": cq("entry:manager", uid3)})
    check("после консультации оформление доступно", "proj:start" in cbs(bot3, uid3))


def t_converter():
    print("\n▸ Конвертер: стратегическая консультация")
    bot = load(); uid = 2004
    U = bot.process_update
    U({"message": msg("/start", uid)})
    U({"callback_query": cq("entry:nosite", uid)})
    bot.SENT.clear()
    U({"callback_query": cq("cons:offer", uid)})
    t = " ".join(texts(bot, uid))
    check("оффер — персональный план", "Персональный план сайта" in t)
    for frag in ("Разбор текущей ситуации", "структуру сайта", "пакет запуска", "План действий"):
        check(f"обещан результат: {frag[:24]}", frag in t)
    check("статус — консультация предложена", bot.crm_status(uid) == "consult_offered")
    check("цену не называем", "₽" not in t, t[max(0, t.find("₽") - 60):t.find("₽") + 20])

    U({"callback_query": cq("cons:way:call", uid)})
    U({"message": msg("+7 999 123-45-67", uid)})
    U({"callback_query": cq("cons:when:tomorrow", uid)})
    check("статус — консультация назначена", bot.crm_status(uid) == "consult_scheduled")
    rec = bot._get(f"onyx:consult:{uid}") or {}
    check("запись сохранена", rec.get("way") == "call" and rec.get("when") == "tomorrow", str(rec))
    check("напоминание поставлено",
          any((bot.followup_get(f) or {}).get("type") == "consult_reminder"
              for f in bot.all_followup_ids()))
    check("менеджер уведомлён",
          any("онсультац" in txt(kw) for m, kw in bot.SENT if kw.get("chat_id") == 999))

    # менеджер отмечает, что провёл
    bot.SENT.clear()
    bot.process_update({"message": msg(f"/consdone {uid}", 999)})
    check("статус — консультация проведена", bot.crm_status(uid) == "consult_done")
    check("клиенту ушло оформление",
          "Оформляем проект" in " ".join(texts(bot, uid)))
    check("напоминание о консультации снято",
          not any((bot.followup_get(f) or {}).get("status") == "scheduled"
                  and (bot.followup_get(f) or {}).get("type") == "consult_reminder"
                  for f in bot.all_followup_ids()))


def t_after_consult():
    print("\n▸ После консультации: оформление до квалификации")
    bot = load(); uid = 2005
    bot.crm_set(uid, "consult_done")
    bot.SENT.clear()
    run_project(bot, uid)
    t = " ".join(texts(bot, uid))
    check("модель объяснили ДО анкеты", t.find("Как мы работаем") < t.find("Анкета принята"),
          f"{t.find('Как мы работаем')} vs {t.find('Анкета принята')}")
    check("в объяснении есть все шесть условий",
          all(x in t for x in ("0 ₽", "один пакет правок", "финальная версия",
                               "оплата пакета запуска", "домен", "известна заранее")))
    check("статус — анкета завершена", bot.crm_status(uid) == "anketa_done", bot.crm_status(uid))
    check("после анкеты предлагаем пакет", "pkg:recommend" in cbs(bot, uid))

    bot.SENT.clear()
    bot.process_update({"callback_query": cq("pkg:recommend", uid)})
    check("статус — пакет рекомендован", bot.crm_status(uid) == "package_offered")
    check("названа цена запуска", "₽" in " ".join(texts(bot, uid)))
    bot.process_update({"callback_query": cq("qual:pkg_ok", uid)})
    check("со сводки пакета ведём в квалификацию", "q8:go" in cbs(bot, uid))


def t_qualification():
    print("\n▸ Квалификация: восемь подтверждений")
    bot = load(); uid = 2006
    bot.crm_set(uid, "consult_done")
    run_project(bot, uid)
    bot.process_update({"callback_query": cq("pkg:recommend", uid)})
    bot.process_update({"callback_query": cq("qual:pkg_ok", uid)})
    bot.SENT.clear()
    bot.process_update({"callback_query": cq("q8:go", uid)})
    check("восемь пунктов", len(bot.QUAL_POINTS) == 8, str(len(bot.QUAL_POINTS)))
    need = ("price_ok", "pay_after_demo", "one_revision", "materials",
            "scope", "deadline", "decision_maker", "standard")
    check("покрыты все требования ТЗ",
          {k for k, _, _ in bot.QUAL_POINTS} == set(need))
    for i in range(8):
        bot.process_update({"callback_query": cq(f"q8:{i}:yes", uid)})
    check("статус — квалифицирован",
          bot.crm_at_least(uid, "qualified"), bot.crm_status(uid))
    check("профиль помечен", (bot.user_get(uid) or {}).get("qualified") is True)
    check("дальше ждём материалы", bot.crm_status(uid) == "materials_waiting")
    check("создана задача на папку Drive",
          any("Drive" in (t or {}).get("title", "") for t in
              [bot.task_get(i) for i in bot.all_task_ids()]))
    check("поставлено напоминание про материалы",
          any((bot.followup_get(f) or {}).get("type") == "materials_missing"
              for f in bot.all_followup_ids()))


def t_edge_cases():
    print("\n▸ Пограничные сценарии")
    # сомнение в условиях — не отказ, а сигнал менеджеру
    bot = load(); uid = 2007
    bot.crm_set(uid, "consult_done"); run_project(bot, uid)
    bot.process_update({"callback_query": cq("pkg:recommend", uid)})
    bot.process_update({"callback_query": cq("qual:pkg_ok", uid)})
    bot.process_update({"callback_query": cq("q8:go", uid)})
    bot.SENT.clear()
    bot.process_update({"callback_query": cq("q8:0:ask", uid)})
    check("сомнение уходит менеджеру",
          any("омнени" in txt(kw) for m, kw in bot.SENT if kw.get("chat_id") == 999))
    check("клиент может продолжить", "q8:back:0" in cbs(bot, uid))
    check("квалификация не сорвана", not (bot.user_get(uid) or {}).get("qualified"))

    # вопрос менеджеру с любого экрана
    bot2 = load(); uid2 = 2008
    bot2.process_update({"message": msg("/start", uid2)})
    bot2.process_update({"callback_query": cq("ask:mgr", uid2)})
    bot2.SENT.clear()
    bot2.process_update({"message": msg("А домен точно будет мой?", uid2)})
    check("вопрос доставлен менеджеру",
          any("домен точно" in txt(kw) for m, kw in bot2.SENT if kw.get("chat_id") == 999))
    check("клиенту подтвердили", "Передал менеджеру" in " ".join(texts(bot2, uid2)))
    check("состояние очищено", not bot2.state_get(uid2))

    # клиент без материалов
    bot3 = load(); uid3 = 2009
    bot3.crm_set(uid3, "materials_waiting")
    bot3.SENT.clear()
    bot3.process_update({"callback_query": cq("mat:help", uid3)})
    check("предложена платная упаковка",
          "Соберём за вас" in " ".join(texts(bot3, uid3)))
    check("менеджер знает", any("паковка" in txt(kw) for m, kw in bot3.SENT if kw.get("chat_id") == 999))

    # материалы отправлены
    bot3.SENT.clear()
    bot3.process_update({"callback_query": cq("mat:done", uid3)})
    check("статус — материалы получены", bot3.crm_status(uid3) == "materials_received")

    # отказ и откладывание фиксируются
    bot4 = load(); uid4 = 2010
    bot4.process_update({"message": msg(f"/crm {uid4} refused", 999)})
    check("отказ фиксируется", bot4.crm_status(uid4) == "refused")
    bot4.process_update({"message": msg(f"/crm {uid4} postponed", 999)})
    check("откладывание фиксируется", bot4.crm_status(uid4) == "postponed")


def t_crm():
    print("\n▸ CRM-статусы")
    bot = load()
    check("28 статусов", len(bot.CRM_STATUSES) == 28, str(len(bot.CRM_STATUSES)))
    need = ["new_lead", "first_contact", "material_sent", "audit_requested", "audit_delivered",
            "consult_offered", "consult_scheduled", "consult_done", "project_setup",
            "anketa_started", "anketa_done", "package_offered", "qualifying", "qualified",
            "materials_waiting", "materials_received", "production", "presentation_set",
            "presentation_first", "changes_waiting", "changes_received", "final_version",
            "invoice_sent", "paid", "launch", "done", "refused", "postponed"]
    check("состав совпадает с ТЗ", [k for k, _ in bot.CRM_STATUSES] == need)
    uid = 2011
    bot.crm_set(uid, "consult_scheduled")
    bot.crm_set(uid, "consult_done")
    r = bot.crm_get(uid)
    check("история пишется", len(r.get("history", [])) == 2, str(r.get("history")))
    check("порядок этапов знает", bot.crm_at_least(uid, "consult_offered")
          and not bot.crm_at_least(uid, "qualified"))
    bot.SENT.clear()
    bot.crm_set(uid, "qualified")
    check("о ключевом этапе сообщают админу",
          any("Квалифицирован" in txt(kw) for m, kw in bot.SENT if kw.get("chat_id") == 999))


def t_automations():
    print("\n▸ Автоматизации")
    bot = load()
    need = ["consult_invite", "consult_reminder", "anketa_unfinished", "materials_missing",
            "changes_reminder", "invoice_reminder"]
    for n in need:
        check(f"есть напоминание {n}", n in bot.FOLLOWUP_DEFS)
    bad = [n for n, d in bot.FOLLOWUP_DEFS.items()
           if not d.get("text") or not d.get("kb")]
    check("у всех напоминаний есть текст и кнопки", not bad, str(bad))
    # кнопки напоминаний должны существовать в боте
    src = open(BOT, encoding="utf-8").read()
    miss = []
    for n, d in bot.FOLLOWUP_DEFS.items():
        for row in d["kb"]["inline_keyboard"]:
            for b in row:
                cd = b.get("callback_data")
                if cd and f'"{cd}"' not in src:
                    miss.append(f"{n}:{cd}")
    check("кнопки напоминаний ведут в живые обработчики", not miss, str(miss))


def t_funnel_report():
    print("\n▸ Воронка в цифрах (/funnel)")
    bot = load()
    check("этапы идут по порядку воронки",
          [m for _, m in bot.FUNNEL_VIEW][:5] ==
          ["ev_start", "ev_entry_branch", "ev_cons_offer", "ev_cons_set", "ev_cons_done"])
    metrics = set(bot.FUNNEL_METRICS.values()) | {"ev_entry_branch"}
    miss = [m for _, m in bot.FUNNEL_VIEW if m not in metrics]
    check("каждому этапу есть счётчик", not miss, str(miss))

    bot.SENT.clear()
    bot.process_update({"message": msg("/funnel", 999)})
    check("на пустых данных не падает", "Пока пусто" in " ".join(texts(bot, 999)))

    for u in (3001, 3002, 3003):
        bot.process_update({"message": msg("/start", u)})
    bot.process_update({"callback_query": cq("entry:nosite", 3001)})
    bot.process_update({"callback_query": cq("entry:site", 3002)})
    bot.process_update({"callback_query": cq("cons:offer", 3001)})
    bot.SENT.clear()
    bot.process_update({"message": msg("/funnel", 999)})
    t = " ".join(texts(bot, 999))
    check("отчёт построен", "Воронка за 7 дн." in t)
    check("вход посчитан", "| Зашли в бота | 3 |" in t, t[:200])
    check("ветки посчитаны", "| Выбрали ветку | 2 |" in t)
    check("конверсия от входа", "67%" in t, t[t.find("Выбрали ветку"):][:80])
    check("видно, где теряем", "Где теряем больше всего" in t)

    bot.SENT.clear()
    bot.process_update({"message": msg("/funnel 30", 999)})
    check("период настраивается", "Воронка за 30 дн." in " ".join(texts(bot, 999)))
    bot.SENT.clear()
    bot.process_update({"message": msg("/funnel", 12345)})
    check("не админу отчёт не показывают",
          "Воронка за" not in " ".join(texts(bot, 12345)))


def t_static_assets():
    print("\n▸ Картинки и чек-лист без ручных настроек")
    for env in ({}, {"VERCEL_URL": "onyx-bot-4xn3-abc.vercel.app"},
                {"TARIFF_IMG_BASE": "https://bot.test"}):
        for k in ("TARIFF_IMG_BASE", "PUBLIC_BASE_URL",
                  "VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL"):
            os.environ.pop(k, None)
        os.environ.update(env)
        b = load()
        label = list(env) or ["ничего не задано"]
        check(f"адрес есть: {label[0]}", b.public_base().startswith("https://"),
              b.public_base())
        check(f"карточка тарифа собирается: {label[0]}",
              b.tariff_image_url("start").endswith("/tariffs/start.png"))
        check(f"чек-лист собирается: {label[0]}",
              b.CHECKLIST_URL.endswith("/checklist.html"), b.CHECKLIST_URL)
    os.environ["TARIFF_IMG_BASE"] = "https://bot.test"

    bot = load(); uid = 4001
    bot.SENT.clear()
    bot.process_update({"message": msg("🛒 Тарифы и услуги", uid)})
    media = [kw for m, kw in bot.SENT if m in ("sendMediaGroup", "sendPhoto", "sendAnimation")]
    check("карточки тарифов отправляются", bool(media), str([m for m, _ in bot.SENT]))


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
    U({"callback_query": cq("demo:go", uid)})
    U({"callback_query": cq("model:ok", uid)})
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
    U({"callback_query": cq("pkg:recommend", uid)})
    check("пакет предлагается только после анкеты",
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
    U({"callback_query": cq("niche:construction", uid)}); grab("примеры работ")
    U({"callback_query": cq("demo:go", uid)}); grab("первый вопрос анкеты")
    for lbl, ok, btns in screens:
        check(f"возврат есть: {lbl}", ok, str(btns))


def t_demos():
    print("\n▸ Примеры работ под нишу")
    bot = load(); uid = 1012
    U = bot.process_update
    U({"callback_query": cq("brief:start", uid)})
    U({"callback_query": cq("goal:leads", uid)})
    bot.SENT.clear()
    U({"callback_query": cq("niche:legal", uid)})
    t = " ".join(texts(bot, uid))
    check("показаны примеры до вопросов", "Сначала посмотрите" in t)
    check("подобрано по нише", "Egorov" in t, t[:120])
    check("чужой ниши на экране нет", "Prime Logistics" not in t)
    check("анкета ещё не началась", not bot.state_get(uid))
    media = [m for m, kw in bot.SENT if m in ("sendMediaGroup", "sendPhoto")]
    check("скриншотами чат не засоряем", not media, str(media))
    check("экран один, без лишних сообщений",
          sum(1 for m, kw in bot.SENT if kw.get("chat_id") == uid) == 1)
    kb = [b for m, kw in bot.SENT if kw.get("chat_id") == uid
          for r in (kw.get("reply_markup") or {}).get("inline_keyboard", []) for b in r]
    links = [b["url"] for b in kb if b.get("url")]
    check("ровно один пример на нишу", len(links) == 1, str(links))
    check("ссылка ведёт на конкретное демо, а не на главную",
          all("#case/" in u for u in links), str(links))
    check("есть кнопка продолжения", any(b.get("callback_data") == "demo:go" for b in kb))
    check("можно сменить нишу", any(b.get("callback_data") == "brief:niche" for b in kb))
    U({"callback_query": cq("demo:go", uid)})
    check("после примеров объясняем модель",
          "Как мы работаем" in " ".join(texts(bot, uid)))
    U({"callback_query": cq("model:ok", uid)})
    check("после объяснения начинается анкета", bool(bot.state_get(uid)))
    bad = [nid for nid, _ in bot.NICHES if len(bot.demos_for(nid)) != 1]
    check("у каждой ниши ровно один пример", not bad, str(bad))
    real = [n for n, _ in bot.NICHES if n != "other"]
    own = [(n, bot.demos_for(n)[0][0]) for n in real]
    check("у каждой ниши своё демо, без повторов",
          len({d for _, d in own}) == len(real), str(own))
    check("демо ведут на живые маршруты сайта",
          all(d["url"].startswith("https://onyx-web.ru/#case/") for d in bot.DEMOS.values()))

    # подбор под нишу на свежем экземпляре: салону — салон, стройке — стройка
    for nid, want in (("beauty", "Fleur"), ("construction", "Osnova"),
                      ("food", "Brasero"), ("hotel", "Taiga")):
        b2 = load(); u2 = 1100
        b2.process_update({"callback_query": cq("brief:start", u2)})
        b2.process_update({"callback_query": cq("goal:leads", u2)})
        b2.SENT.clear()
        b2.process_update({"callback_query": cq(f"niche:{nid}", u2)})
        check(f"{nid} → {want}", want in " ".join(texts(b2, u2)),
              " ".join(texts(b2, u2))[:100])

    # первые шесть ниш — те, под которые есть собственное демо
    first = [n for n, _ in bot.NICHES[:6]]
    own = {n for n, _ in bot.NICHES[:6] if bot.demos_for(n)[0][0] in bot.DEMOS}
    check("первыми идут ниши со своим демо", len(own) == 6, str(first))

    # раздел меню
    bot.SENT.clear()
    bot.process_update({"message": msg("🖼 Примеры сайтов", uid)})
    g = " ".join(texts(bot, uid))
    check("раздел «Примеры сайтов» открывается", "Примеры сайтов" in g)
    check("перечислены все демо", all(d["name"] in g for d in bot.DEMOS.values()))
    check("есть ссылка на галерею сайта", bot.CASES_URL in g)
    gkb = [b for m, kw in bot.SENT if kw.get("chat_id") == uid
           for r in (kw.get("reply_markup") or {}).get("inline_keyboard", []) for b in r]
    check("кнопка на каждое демо + галерея",
          sum(1 for b in gkb if b.get("url")) == len(bot.DEMOS) + 1,
          str(len([b for b in gkb if b.get("url")])))
    check("галерея ведёт на секцию шаблонов",
          any(b.get("url", "").endswith("#templates") for b in gkb))
    check("кнопка в меню есть",
          any("Примеры сайтов" in b["text"] for r in bot.MAIN_MENU["keyboard"] for b in r))


def t_audit():
    print("\n▸ Аудит сайта")
    bot = load(); uid = 1004
    bot.audit_start(uid, uid, "https://salon.ru", username="cli")
    t = " ".join(texts(bot, uid))
    check("отчёт отправлен", "Аудит сайта" in t)
    check("показан план решения", "Как мы это решим" in t)
    check("описан порядок работы", "Как проходит работа" in t)
    check("внутренняя цель не показана клиенту", "booking" not in t and "Цель сайта" not in t)
    check("нет названий тарифов",
          not any(v["name"] in t for v in bot.TARIFF.values()),
          str([v["name"] for v in bot.TARIFF.values() if v["name"] in t]))
    prices = [m for m in re.findall(r"[\d\s]{3,}₽", t) if m.strip() not in ("0 ₽",)]
    check("нет цен, кроме «разработка 0 ₽»", not prices, str(prices[:3]))
    a = bot.audit_get(1)
    check("цель определена (запись)", (a or {}).get("site_goal") == "booking",
          str((a or {}).get("site_goal")))
    p = bot.user_get(uid) or {}
    check("тариф остался внутренней подсказкой",
          p.get("audit_tariff_hint") == (a or {}).get("recommended_tariff")
          and not p.get("chosen_tariff"), str(p.get("chosen_tariff")))
    kb = [b for m, kw in bot.SENT if kw.get("chat_id") == uid
          for r in (kw.get("reply_markup") or {}).get("inline_keyboard", []) for b in r]
    check("главная кнопка — персональный план",
          any(b.get("callback_data") == "cons:offer" for b in kb))
    check("нет кнопки оформления тарифа",
          not any(b.get("callback_data", "").startswith("trf:pick:") for b in kb))


def t_start_checklist():
    print("\n▸ Чек-лист на старте")
    bot = load(); uid = 1016
    check("адрес чек-листа собрался сам",
          bot.CHECKLIST_URL.endswith("/checklist.html"), bot.CHECKLIST_URL)
    bot.process_update({"message": msg("/start", uid)})
    bot.process_update({"callback_query": cq("entry:nosite", uid)})
    t = " ".join(texts(bot, uid))
    check("чек-лист выдаётся в ветке «сайта нет»", "чек-лист" in t.lower())
    check("описан состав", "14 пунктов" in t and "6 условий" in t)
    kb = [b for m, kw in bot.SENT if kw.get("chat_id") == uid
          for r in (kw.get("reply_markup") or {}).get("inline_keyboard", []) for b in r]
    check("есть кнопка-ссылка",
          any(b.get("url") == bot.CHECKLIST_URL for b in kb), str([b.get("url") for b in kb]))
    check("рядом ведём на консультацию",
          any(b.get("callback_data") == "cons:offer" for b in kb))
    check("постоянное меню показано",
          any((kw.get("reply_markup") or {}).get("keyboard") for m, kw in bot.SENT
              if kw.get("chat_id") == uid))
    check("в тексте нет ссылки на pr-cy", "pr-cy" not in t.lower())


def t_deeplink_audit():
    print("\n▸ Вход на аудит прямо с сайта")
    bot = load()
    check("домен кодируется и читается обратно",
          bot.decode_audit_payload(bot.encode_audit_payload("salon-krasoty.ru")) == "salon-krasoty.ru")
    tok = bot.encode_audit_payload("https://salon.ru/uslugi")
    check("в ссылке только разрешённые символы",
          re.fullmatch(r"[A-Za-z0-9_-]+", tok) is not None, tok)
    check("ссылка влезает в лимит Telegram", len("a_" + tok) <= 64, str(len(tok)))
    src, tt, val = bot.parse_start_payload("a_" + tok)
    check("payload разобран", (src, tt) == ("cta_audit", "url") and val == "https://salon.ru/uslugi")

    # без адреса — как раньше, спрашиваем ссылку
    uid = 1013
    bot.process_update({"message": msg("/start audit", uid)})
    check("без адреса бот просит ссылку", (bot.state_get(uid) or {}).get("flow") == "audit_url")

    # с адресом — аудит стартует сразу
    bot2 = load(); uid2 = 1014
    bot2.process_update({"message": msg("/start a_" + bot2.encode_audit_payload("salon.ru"), uid2)})
    t = " ".join(texts(bot2, uid2))
    check("с адресом аудит начинается сразу", "Аудит сайта" in t)
    check("лишнего вопроса про ссылку нет", "Пришлите адрес" not in t)
    check("источник записан", (bot2.lead_get(uid2) or {}).get("source") == "cta_audit",
          str((bot2.lead_get(uid2) or {}).get("source")))

    # мусор в ссылке не должен ронять бота
    bot3 = load(); uid3 = 1015
    bot3.process_update({"message": msg("/start a_!!!!", uid3)})
    check("битая ссылка не ломает вход",
          (bot3.state_get(uid3) or {}).get("flow") == "audit_url")


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


def t_tariff_images():
    """Карточки тарифов должны уходить картинкой, а не только текстом.
    Раньше воронка молча скатывалась в текстовый список: файлы лежали в public,
    но ни один экран нового пути их не отправлял."""
    print("\n\u25b8 Картинки тарифов")
    bot = load()

    # 1. Одиночная карточка уходит фото и кэширует file_id
    photos = []
    orig = bot.tg

    def tg_photo(m, **kw):
        if m == "sendPhoto":
            photos.append(kw.get("photo"))
            return {"ok": True, "result": {"message_id": 7, "photo": [{"file_id": "FID1"}]}}
        return orig(m, **kw)
    bot.tg = tg_photo

    ok = bot.send_tariff_card(1, "start")
    check("карточка уходит через sendPhoto", ok and len(photos) == 1)
    check("в первый раз шлём по ссылке", str(photos[0]).endswith("/tariffs/start.png"))
    check("file_id сохранён", bot._get("onyx:tariff_fid:start") == "FID1")
    bot.send_tariff_card(1, "start")
    check("повторно шлём file_id, а не 900 КБ", photos[1] == "FID1")

    # 2. Экран рекомендации показывает картинку
    bot = load()
    photos = []
    orig = bot.tg

    def tg_photo2(m, **kw):
        if m == "sendPhoto":
            photos.append(kw.get("caption", ""))
            return {"ok": True, "result": {"message_id": 7, "photo": [{"file_id": "F"}]}}
        return orig(m, **kw)
    bot.tg = tg_photo2
    bot.show_tariff_recommendation(1, 1, "leads", {}, mid=50)
    check("на экране рекомендации есть фото", len(photos) == 1)
    check("в подписи назван пакет", "Рекомендуем" in (photos[0] if photos else ""))
    check("старое сообщение убрано", any(m == "deleteMessage" for m, _ in bot.SENT))

    # 3. Просмотр отдельного тарифа
    bot = load()
    photos = []
    orig = bot.tg

    def tg_photo3(m, **kw):
        if m == "sendPhoto":
            photos.append(kw.get("photo"))
            return {"ok": True, "result": {"message_id": 7, "photo": [{"file_id": "F"}]}}
        return orig(m, **kw)
    bot.tg = tg_photo3
    bot.process_callback(cq("trf:v:system", 1))
    check("карточка тарифа открывается с фото", len(photos) == 1)

    # 4. Альбом упал — шлём поштучно, воронка не скатывается в текст
    bot = load()
    single = []
    orig = bot.tg

    def tg_album_fail(m, **kw):
        if m == "sendMediaGroup":
            return {"ok": False, "description": "wrong file identifier"}
        if m == "sendPhoto":
            single.append(kw.get("photo"))
            return {"ok": True, "result": {"message_id": 7, "photo": [{"file_id": "F"}]}}
        return orig(m, **kw)
    bot.tg = tg_album_fail
    bot.send_tariff_album(1, 1)
    check("при отказе альбома карточки уходят по одной", len(single) == len(bot.TARIFFS))

    # 5. Картинки недоступны совсем — текст + предупреждение администратору
    bot = load()
    orig = bot.tg
    admin = []

    def tg_all_fail(m, **kw):
        if m in ("sendMediaGroup", "sendPhoto"):
            return {"ok": False}
        if m == "sendMessage" and kw.get("chat_id") == 999:
            admin.append(kw.get("text", ""))
        return orig(m, **kw)
    bot.tg = tg_all_fail
    bot.send_tariff_album(1, 1)
    texts = [kw.get("text", "") for m, kw in bot.SENT if m == "sendMessage"]
    check("воронка не ломается — уходит текстовый список",
          any("ТАРИФЫ" in t.upper() or "Разработка" in t for t in texts))
    check("администратору приходит предупреждение",
          any("не отправляются" in a for a in admin))


def t_security():
    """Регрессия по найденным уязвимостям. Каждая проверка — про конкретную
    дыру из SECURITY_AUDIT.md; если она снова откроется, тест покраснеет."""
    print("\n\u25b8 Безопасность")
    bot = load()

    # --- C2: подделка апдейта не должна давать админские права ---
    bot._REQUEST_TRUSTED[0] = True
    check("C2 админ опознаётся при подтверждённом источнике", bot.is_admin(999))
    bot._REQUEST_TRUSTED[0] = False
    check("C2 при неподтверждённом источнике админа нет", not bot.is_admin(999))
    bot._REQUEST_TRUSTED[0] = True

    # --- C3: SSRF ---
    blocked = [
        ("http://169.254.169.254/latest/meta-data/", "метаданные облака"),
        ("http://127.0.0.1:8080/", "локальный адрес"),
        ("http://localhost/", "localhost"),
        ("http://10.1.2.3/", "сеть 10/8"),
        ("http://192.168.0.1/", "сеть 192.168"),
        ("http://172.16.5.5/", "сеть 172.16"),
        ("http://[::1]/", "IPv6 loopback"),
        ("file:///etc/passwd", "схема file"),
        ("gopher://evil/", "схема gopher"),
        ("http://db.internal/", "служебный домен"),
        ("http://example.com:22/", "порт SSH"),
    ]
    bad = [why for url, why in blocked if bot.ssrf_check(url)[0]]
    check("C3 закрытые адреса не пропускаются", not bad, ", ".join(bad))

    real = bot.socket.getaddrinfo
    bot.socket.getaddrinfo = lambda h, p, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
    check("C3 обычный сайт проверяется", bot.ssrf_check("https://example.com/")[0])
    bot.socket.getaddrinfo = lambda h, p, **k: [(2, 1, 6, "", ("127.0.0.1", 443))]
    check("C3 домен, указывающий на localhost, отклоняется",
          not bot.ssrf_check("https://rebind.example/")[0])
    bot.socket.getaddrinfo = real

    calls = []
    bot._fetch_once = lambda u, t=6, m=400000: (calls.append(u), (200, {}, "<html></html>", 0.1, u))[1]
    bot._fetch("http://169.254.169.254/")
    check("C3 запрос к метаданным не выполняется", not calls)

    # --- H1: formula injection ---
    cases = ["=IMPORTXML(\"http://evil\",\"//a\")", "+1+1", "-2+3", "@SUM(A1)", "\t=cmd"]
    safe = [bot.sheet_safe(c) for c in cases]
    check("H1 формулы обезврежены", all(x.startswith("'") for x in safe), str(safe[:2]))
    check("H1 обычный текст не портится", bot.sheet_safe("ООО «Ромашка»") == "ООО «Ромашка»")
    check("H1 вложенные значения тоже чистятся",
          bot.sheet_safe({"a": ["=x"]})["a"][0].startswith("'"))
    bot._SHEET_Q.clear()
    bot.post_to_sheet({"table": "T", "company": "=HYPERLINK(\"http://evil\")"})
    check("H1 в очередь попадает уже безопасное значение",
          bot._SHEET_Q and str(bot._SHEET_Q[0]["company"]).startswith("'"))

    # --- H2: HTML-инъекция ---
    t = bot.clean_user_text('<a href="https://phish">Оплатить</a>')
    check("H2 теги не доходят до чата", "<a" not in t and "&lt;a" in t)
    check("H2 незакрытый тег не ломает отправку", "<" not in bot.clean_user_text("<b"))
    check("H2 длина ограничена", len(bot.clean_user_text("я" * 5000)) <= bot.MAX_USER_TEXT + 1)
    check("H2 управляющие символы убраны", "\x07" not in bot.clean_user_text("а\x07б"))

    # --- H3: ограничение частоты ---
    bot2 = load()
    bot2._REQUEST_TRUSTED[0] = True
    limit = bot2.RATE_LIMITS["audit"][0]
    passed = sum(1 for _ in range(limit + 4) if bot2.rate_ok(555, "audit"))
    check("H3 аудит ограничен по частоте", passed == limit, f"прошло {passed}")
    check("H3 админа лимит не трогает", all(bot2.rate_ok(999, "audit") for _ in range(20)))

    # --- M1/M5: чистка логов ---
    r = bot.redact("сбой на https://api.telegram.org/bot123456789:AAF-abcdefghijklmnopqrstuvwxyz012345/send")
    check("M5 токен не попадает в лог", "AAF-" not in r and "‹токен›" in r)
    check("M5 телефон вырезается", "‹телефон›" in bot.redact("клиент +7 999 123-45-67"))
    check("M5 почта вырезается", "‹почта›" in bot.redact("пишет ivan@mail.ru"))

    # --- M3: проверка файлов ---
    ok_ext, _ = bot.upload_allowed("логотип.png", 1000)
    check("M3 картинка принимается", ok_ext)
    bad_ext, why = bot.upload_allowed("вирус.exe", 1000)
    check("M3 исполняемый файл отклоняется", not bad_ext)
    big, _ = bot.upload_allowed("video.mp4", 99 * 1024 * 1024)
    check("M3 слишком большой файл отклоняется", not big)
    check("M3 путь в имени обезврежен",
          "/" not in bot.safe_file_name("../../etc/passwd")
          and bot.safe_file_name("../../etc/passwd") == "passwd")

    # --- C1: крон закрыт ---
    src = open(BOT, encoding="utf-8").read()
    check("C1 крон требует секрет", "_cron_allowed" in src and "CRON_SECRET" in src)
    check("C1 путь сверяется целиком, а не подстрокой", '"cron" in self.path' not in src)
    check("C2 сравнение секрета за постоянное время", "compare_digest" in src)
    check("M2 размер тела ограничен", "MAX_BODY_BYTES" in src)


def t_menu_button():
    """Синяя кнопка «Меню» слева от поля ввода: клиенту одна команда,
    администратору — полный список только в его чате."""
    print("\n\u25b8 Кнопка «Меню»")
    bot = load()
    sent = []
    orig = bot.tg
    bot.tg = lambda m, **kw: (sent.append((m, kw)), orig(m, **kw))[1]

    bot.setup_bot_commands(force=True)
    cmds = [kw for m, kw in sent if m == "setMyCommands"]
    default = [c for c in cmds if c.get("scope", {}).get("type") == "default"]
    perchat = [c for c in cmds if c.get("scope", {}).get("type") == "chat"]

    check("клиенту уходит короткий список",
          default and len(default[0]["commands"]) == len(bot.CLIENT_COMMANDS))
    check("клиент видит только «Старт»",
          default and [c["command"] for c in default[0]["commands"]] == ["start"])
    check("админу уходит полный список",
          perchat and len(perchat[0]["commands"]) == len(bot.ADMIN_COMMANDS))
    check("список админа привязан к его чату",
          perchat and perchat[0]["scope"]["chat_id"] in bot.ADMIN_IDS)
    check("кнопка переведена в режим команд",
          any(m == "setChatMenuButton" and kw["menu_button"]["type"] == "commands"
              for m, kw in sent))

    # Требования Telegram к формату
    bad = []
    for name, lst in (("client", bot.CLIENT_COMMANDS), ("admin", bot.ADMIN_COMMANDS)):
        for c, d in lst:
            if not (1 <= len(c) <= 32) or not all(x.islower() or x.isdigit() or x == "_" for x in c):
                bad.append(c)
            if not (1 <= len(d) <= 256):
                bad.append(c + " (описание)")
    check("формат команд соответствует Telegram", not bad, ", ".join(bad))
    check("не больше сотни команд", len(bot.ADMIN_COMMANDS) <= 100)

    # Каждая команда из меню должна что-то делать
    src = open(BOT, encoding="utf-8").read()
    missing = [c for c, _ in bot.ADMIN_COMMANDS if f"/{c}" not in src]
    check("у каждой команды меню есть обработчик", not missing, ", ".join(missing))

    # Повторный вызов не дёргает Telegram зря
    sent.clear()
    bot.setup_bot_commands()
    check("список не переписывается на каждое сообщение", not sent)

    # /menurefresh доступен только администратору
    bot2 = load()
    calls = []
    bot2.setup_bot_commands = lambda force=False: calls.append(force) or True
    bot2.process_message({"chat": {"id": 5}, "from": {"id": 5}, "text": "/menurefresh"})
    check("клиенту команда обновления недоступна", True not in calls)
    calls.clear()
    bot2.process_message({"chat": {"id": 999}, "from": {"id": 999}, "text": "/menurefresh"})
    check("админ может обновить меню вручную", True in calls)


if __name__ == "__main__":
    print("═" * 60)
    print("  ONYX — самопроверка бота")
    print("═" * 60)
    for fn in (t_static, t_static_assets, t_funnel_report, t_entry, t_converter, t_after_consult, t_qualification,
               t_edge_cases, t_crm, t_automations,
               t_funnel, t_order_before_tariff, t_navigation, t_demos,
               t_audit, t_start_checklist, t_deeplink_audit, t_drive, t_consent, t_reviews,
               t_no_blocking, t_sheets_batch, t_kv_mode, t_rich_fallback,
               t_tariff_images, t_security, t_menu_button):
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
