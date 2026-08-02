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
    check("на входе спрашиваем про сайт", "с чего начать" in t.lower())
    check("на входе сразу снимаем страх оплаты",
          "не должны ни рубля" in t or "0 ₽" in t)
    # Первый экран не должен быть перегружен: одна короткая строка сверху
    # и развилка. Раньше здесь шло два больших сообщения подряд.
    heads = [x for x in texts(bot, uid) if "ONYX WEB" in x]
    check("шапка на входе короткая", all(len(h) < 120 for h in heads), str(heads)[:120])
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
    check("раздел «Примеры сайтов» открывается", "Примеры наших работ" in g)
    gkb = [b for m, kw in bot.SENT if kw.get("chat_id") == uid
           for r in (kw.get("reply_markup") or {}).get("inline_keyboard", []) for b in r]
    cards = [b for b in gkb if str(b.get("callback_data", "")).startswith("gal:d:")]
    check("на каждое демо своя карточка", len(cards) == len(bot.DEMOS), str(len(cards)))
    check("в витрине не стена ссылок, а кнопки-карточки",
          sum(1 for b in gkb if b.get("url")) == 1, str(sum(1 for b in gkb if b.get("url"))))
    check("есть ссылка на галерею сайта", any(b.get("url") == bot.CASES_URL for b in gkb))
    check("витрина не грузит текстом", len(g) < 700, f"{len(g)} символов")

    # Карточка одного примера
    bot.SENT.clear()
    bot.process_callback(cq("gal:d:osnova", uid))
    card = " ".join(texts(bot, uid))
    check("карточка открывается", "Osnova" in card)
    check("в карточке сказано, что внутри", "Что внутри" in card)
    check("указано число страниц", "страниц" in card.lower())
    check("сказано, кому подходит", "подходит" in card.lower())
    ckb = [b for m, kw in bot.SENT if kw.get("chat_id") == uid
           for r in (kw.get("reply_markup") or {}).get("inline_keyboard", []) for b in r]
    check("из карточки можно открыть сайт",
          any(b.get("url", "").startswith("https://onyx-web.ru/#case/") for b in ckb))
    check("из карточки можно вернуться к списку",
          any(b.get("callback_data") == "gallery:open" for b in ckb))
    check("из карточки можно сразу начать",
          any(b.get("callback_data") == "brief:start" for b in ckb))
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
    check("адрес разбора по нишам собрался сам",
          bot.GUIDE_URL.endswith("/site-guide.html"), bot.GUIDE_URL)
    check("чек-лист выдаётся в ветке «сайта нет»", "чек-лист" in t.lower())
    # Раньше проверялось буквально «14 пунктов» и «6 условий». Это оказалось
    # ловушкой: чек-лист переписали, в нём стало 16 разделов в пяти частях,
    # и тест держал в тексте цифру, которая перестала быть правдой. Теперь
    # проверяем смысл - что состав описан, а не просто упомянут название.
    check("описан состав чек-листа",
          "собрать до начала" in t and "договориться" in t)
    check("разбор по нишам предложен там, где сайта ещё нет",
          "в моей нише" in t or "вашей отрасли" in t.lower())
    kb = [b for m, kw in bot.SENT if kw.get("chat_id") == uid
          for r in (kw.get("reply_markup") or {}).get("inline_keyboard", []) for b in r]
    check("есть кнопка-ссылка",
          any(b.get("url") == bot.CHECKLIST_URL for b in kb), str([b.get("url") for b in kb]))
    check("ссылка на разбор по нишам тоже есть",
          any(b.get("url") == bot.GUIDE_URL for b in kb))
    # Порядок здесь - продающее решение, а не оформление: человеку без сайта
    # сначала нужно понять, что ему вообще нужно, и только потом - как
    # выбирать подрядчика. Если кнопки поменяют местами, тест это заметит.
    urls = [b.get("url") for b in kb if b.get("url")]
    check("разбор по нишам стоит выше чек-листа",
          bot.GUIDE_URL in urls and bot.CHECKLIST_URL in urls
          and urls.index(bot.GUIDE_URL) < urls.index(bot.CHECKLIST_URL))
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
    # Клиентский текст может уйти и как rich, и как обычное сообщение -
    # смотрим всё, что улетело клиенту (chat_id 1), а не админу.
    texts = [str(kw) for m, kw in bot.SENT if kw.get("chat_id") == 1]
    check("воронка не ломается: вместо картинок уходит текст с развилкой",
          any("консультаци" in t.lower() for t in texts), str(texts)[:150])
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
    # Сайты за Cloudflare отдают вперемешку обычные адреса и служебные вроде
    # 64:ff9b::/96. Из-за одного такого аудит переставал работать вообще.
    bot.socket.getaddrinfo = lambda h, p, **k: [
        (2, 1, 6, "", ("64:ff9b::5db8:d822", 443)),
        (2, 1, 6, "", ("93.184.216.34", 443))]
    check("C3 служебный адрес рядом с публичным не блокирует сайт",
          bot.ssrf_check("https://example.com/")[0])
    bot.socket.getaddrinfo = lambda h, p, **k: [
        (2, 1, 6, "", ("2606:4700:3033::6815:1", 443))]
    check("C3 сайт только на IPv6 работает", bot.ssrf_check("https://ipv6.example/")[0])
    bot.socket.getaddrinfo = lambda h, p, **k: [
        (2, 1, 6, "", ("fe80::1%eth0", 443)), (2, 1, 6, "", ("10.0.0.1", 443))]
    check("C3 все адреса служебные — блокируем",
          not bot.ssrf_check("https://evil.example/")[0])
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


def t_base_url():
    """Адрес статики собирается из переменных окружения. В поле значения на
    Vercel регулярно попадает лишнее — имя переменной, кавычки, пробелы.
    Одна такая опечатка не должна ломать все картинки."""
    print("\n\u25b8 Адрес статики")
    bot = load()

    cases = [
        ("TARIFF_IMG_BASE = https://onyx.app", "https://onyx.app", "имя переменной в значении"),
        ("TARIFF_IMG_BASE=https://onyx.app",   "https://onyx.app", "то же без пробелов"),
        ("export PUBLIC_BASE_URL=https://onyx.app", "https://onyx.app", "префикс export"),
        ('"https://onyx.app"',                 "https://onyx.app", "кавычки"),
        ("  https://onyx.app/  ",              "https://onyx.app", "пробелы и слеш"),
        ("onyx.app",                           "https://onyx.app", "голый домен"),
        ("https://onyx.app",                   "https://onyx.app", "правильное значение"),
        ("просто текст",                       "",                 "мусор отбрасывается"),
        ("",                                   "",                 "пустое значение"),
    ]
    bad = [why for raw, want, why in cases if bot.clean_base_url(raw) != want]
    check("значение переменной чистится", not bad, ", ".join(bad))

    # Битая первая переменная не должна мешать: берём следующую по списку
    import os as _os
    saved = {k: _os.environ.get(k) for k in
             ("TARIFF_IMG_BASE", "PUBLIC_BASE_URL", "VERCEL_URL")}
    try:
        _os.environ["TARIFF_IMG_BASE"] = "TARIFF_IMG_BASE = https://onyx.app"
        _os.environ.pop("PUBLIC_BASE_URL", None)
        _os.environ["VERCEL_URL"] = "fallback.vercel.app"
        check("опечатка в переменной чинится сама",
              bot.public_base() == "https://onyx.app", bot.public_base())

        _os.environ["TARIFF_IMG_BASE"] = "мусор без адреса"
        check("совсем битое значение уступает следующей переменной",
              bot.public_base() == "https://fallback.vercel.app", bot.public_base())

        _os.environ["TARIFF_IMG_BASE"] = "https://onyx.app"
        check("адрес картинки собирается верно",
              bot.tariff_image_url("start") == "https://onyx.app/tariffs/start.png",
              bot.tariff_image_url("start"))
        check("в адресе нет пробелов и знака равенства",
              " " not in bot.tariff_image_url("start")
              and "=" not in bot.tariff_image_url("start"))
    finally:
        for k, v in saved.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v


def t_funnel_to_consult():
    """Ключевая механика: всё ведёт к консультации. Если хоть один экран
    аудита не предлагает её, воронка теряет самых горячих людей."""
    print("\n\u25b8 Всё ведёт к консультации")
    bot = load()

    def buttons(kb):
        return [b.get("callback_data", "") for row in (kb or {}).get("inline_keyboard", []) for b in row]

    for name, kb in (("отчёт с проблемами", bot.audit_report_kb()),
                     ("план после аудита", bot.audit_plan_kb()),
                     ("повторный показ аудита", bot.audit_result_kb())):
        b = buttons(kb)
        check(f"{name}: ведёт на консультацию", "cons:offer" in b, str(b))
        check(f"{name}: консультация первой кнопкой", b and b[0] == "cons:offer", str(b[:1]))

    # Сайт не открылся — человек всё равно должен попасть на консультацию
    sent = []
    orig = bot.tg
    bot.tg = lambda m, **kw: (sent.append(kw), orig(m, **kw))[1]
    bot.audit_fail({"telegram_id": 7, "audit_id": "A1", "website_url": "https://x.test"}, "таймаут")
    kbs = [kw.get("reply_markup") for kw in sent if kw.get("reply_markup")]
    allb = [c for kb in kbs for c in buttons(kb)]
    check("недоступный сайт: тоже зовём на консультацию", "cons:offer" in allb, str(allb))
    check("недоступный сайт: можно проверить другой адрес", "audit:again" in allb)

    # Последствия в отчёте
    findings = [{"title": "Сайт не адаптирован под телефоны", "risk": "r", "gain": "g", "sev": 3, "svc": []},
                {"title": "Сайт загружается медленно — 6 сек", "risk": "r", "gain": "g", "sev": 3, "svc": []}]
    md = bot.render_audit_md({"website_url": "https://x.test"},
                             {"domain": "x.test", "analytics": []}, findings, "start", [], "")
    check("в отчёте есть раздел про последствия", "Что это значит для бизнеса" in md)
    check("последствия объяснены словами, а не цифрой с потолка",
          "смартфон" in md.lower() and "%" not in md.split("Что это значит")[1][:600])
    check("отчёт заканчивается приглашением на консультацию",
          "консультации" in md.lower())
    check("нет выдуманных сумм потерь",
          "₽" not in md.split("Что это значит")[1][:800])

    # Кнопка загрузки материалов
    bot2 = load()
    shown = []
    bot2.edit_rich = lambda c, m, md_, h=None, kb=None: shown.append((md_, kb))
    bot2.process_callback(cq("up:menu", 5))
    check("кнопка «Загрузить материалы» открывает экран", len(shown) == 1)
    if shown:
        txt, kb = shown[0]
        check("сказано, что файлы шлём прямо в чат", "в чат" in txt)
        check("перечислено, что именно присылать",
              "Логотип" in txt and "Видео" in txt and "Фотографии" in txt)
        check("названы форматы и размер", "JPG" in txt and "45 МБ" in txt)
        back = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
        check("есть кнопка назад", any(x in ("b:cab", "b:home") for x in back), str(back))

    # Менеджер: имя, кликабельный ник, 30 минут
    src = open(BOT, encoding="utf-8").read()
    check("менеджер назван по имени", "MANAGER_NAME" in src)
    check("ник кликабельный", "def manager_md" in src and "def manager_link" in src)
    check("обещаем 30 минут, а не рабочий день",
          "рабочего дня" not in src and "до 30 минут" in src)
    check("предлагаем написать самому", "напишите ему" in src.lower())


def t_step_back():
    """Кнопка «Назад» должна возвращать на шаг, а не выбрасывать в начало.
    Раньше почти отовсюду был только выход в главное меню, и человек терял
    весь пройденный путь из-за одного случайного нажатия."""
    print("\n\u25b8 Шаг назад")
    bot = load()
    uid = 4242
    bot._REQUEST_TRUSTED[0] = True

    # История копится по мере переходов
    for d in ("gallery:open", "gal:d:osnova"):
        bot.nav_push(uid, d)
    check("история запоминает экраны", bot.nav_back_target(uid) == "gallery:open",
          str(bot._get(f"onyx:nav:{uid}")))

    # Повторное нажатие того же экрана не плодит записи
    bot.nav_push(uid, "gal:d:osnova")
    check("дубли в историю не пишутся", len(bot._get(f"onyx:nav:{uid}")) == 2)

    # Назад открывает предыдущий экран
    shown = []
    bot.edit_rich = lambda c, m, md, h=None, kb=None: shown.append(md)
    bot.process_callback(cq("b:back", uid))
    check("назад открывает предыдущий экран",
          shown and "Примеры наших работ" in shown[-1], str(shown)[:80])
    check("пройденный экран убран из истории",
          bot._get(f"onyx:nav:{uid}") == ["gallery:open"])

    # Истории нет - уводим в меню, а не в никуда
    bot2 = load()
    bot2._REQUEST_TRUSTED[0] = True
    home = []
    bot2.main_menu = lambda c, t=None: home.append(c)
    bot2.process_callback(cq("b:back", 77))
    check("без истории ведём в меню", home == [77])

    # Служебные экраны в историю не попадают
    bot3 = load()
    for d in ("adm:panel", "b:home", "qual:pkg_ok", "b:back"):
        bot3.nav_push(9, d)
    check("служебные экраны не запоминаются", not (bot3._get("onyx:nav:9") or []))

    # Ряд навигации
    bot4 = load()
    bot4.nav_push(5, "gallery:open"); bot4.nav_push(5, "gal:d:apex")
    row = bot4.nav_row(5)
    cbs = [b["callback_data"] for b in row]
    check("в ряду есть и назад, и меню", cbs == ["b:back", "b:home"], str(cbs))
    check("без истории показываем только меню",
          [b["callback_data"] for b in bot4.nav_row(31337)] == ["b:home"])

    # Экраны, которые раньше были тупиком
    src = open(BOT, encoding="utf-8").read()
    for frag, why in (
        ('"⬅️ Изменить время"', "после записи на консультацию"),
        ('"⬅️ Дослать материалы"', "после отправки материалов"),
        ('"⬅️ К материалам"', "после запроса упаковки контента"),
    ):
        check(f"есть шаг назад: {why}", frag in src)


def t_consult_first_and_digest():
    """Экран тарифов ведёт на консультацию, а после анкеты предлагаем кабинет
    с подпиской на письма."""
    print("\n\u25b8 Тарифы, кабинет и письма")
    bot = load()
    uid = 8080
    bot._REQUEST_TRUSTED[0] = True

    def cbs(kb):
        return [b.get("callback_data", "") for r in (kb or {}).get("inline_keyboard", []) for b in r]

    # Экран тарифов
    b1 = cbs(bot.tariffs_list_kb(uid))
    check("на экране тарифов консультация первой кнопкой", b1[0] == "cons:offer", str(b1[:2]))
    check("тарифы при этом остались", any(x.startswith("trf:v:") for x in b1))
    txt = bot.tariffs_after_md()
    check("текст продаёт консультацию, а не спрашивает «какой берём»",
          "бесплатную консультацию" in txt.lower() and "какой тариф берём" not in txt.lower())
    check("готовому клиенту не мешаем", "уже определились" in txt.lower())

    # Карточка тарифа
    b2 = cbs(bot.tariff_card_kb("start", uid))
    check("в карточке тарифа есть и «беру», и «сомневаюсь»",
          any(x.startswith("trf:pick:") for x in b2) and "cons:offer" in b2, str(b2))

    # После анкеты - кабинет
    shown = []
    bot.edit_rich = lambda c, m, md, h=None, kb=None: shown.append((md, kb))
    bot.finish_brief(uid, {"id": uid, "username": "u"}, {"company_name": "Тест"}, mid=1)
    md, kb = shown[-1]
    check("после анкеты предлагаем личный кабинет", "b:cab" in cbs(kb), str(cbs(kb)))
    check("объяснили, зачем кабинет нужен",
          "Статус заказа" in md and "Материалы" in md and "письма" in md.lower())

    # Темы писем
    check("тем стало больше восьми", len(bot.TOPICS) >= 12, str(len(bot.TOPICS)))
    check("темы не только про сайты",
          all(k in bot.TOPICS for k in ("leads", "sales", "money", "team")))
    check("у каждой темы есть развёрнутое описание",
          all(k in bot.TOPIC_FULL for k in bot.TOPICS))

    # Выбор тем: пока ничего не выбрано - «Пропустить», выбрал - «Готово»
    bot2 = load()
    u2 = 9090
    empty = cbs(bot2.content_kb(u2))
    check("без выбора предлагаем пропустить", "ct:skip" in empty and "ct:done" not in empty)
    bot2.csub_toggle(u2, "leads")
    picked = cbs(bot2.content_kb(u2))
    check("после выбора появляется подтверждение", "ct:done" in picked)
    check("темы идут в два столбца",
          any(len(r) == 2 for r in bot2.content_kb(u2)["inline_keyboard"]))
    check("есть возврат в кабинет", "b:cab" in picked)

    # Подтверждение подписки
    shown2 = []
    bot2.edit_rich = lambda c, m, md, h=None, kb=None: shown2.append((md, kb))
    bot2.process_callback(cq("ct:done", u2))
    md2, kb2 = shown2[-1]
    check("подписка подтверждается отдельным экраном", "Подписка оформлена" in md2)
    check("перечислены выбранные темы", "клиентов" in md2.lower())
    check("сказано про частоту", "раза в неделю" in md2)
    check("после подписки ведём дальше, а не в тупик",
          "cab:orders" in cbs(kb2) or "up:open" in cbs(kb2), str(cbs(kb2)))

    # Пустой раздел отзывов не просит «стать первым»
    src = open(BOT, encoding="utf-8").read()
    check("не просим клиента стать первым отзывом", "станьте первыми" not in src)
    check("вместо «Другая категория» - «Далее»",
          '"➡️ Далее"' in src and "Другая категория" not in src)


def t_kev_everywhere():
    """Главная задача бота - привести на консультацию. Значит выход на неё
    должен быть с каждого клиентского экрана, а контакт мы обязаны получить
    даже если человек выбрал общение в Telegram."""
    print("\n\u25b8 Всё ведёт на КЭВ")
    bot = load()
    bot._REQUEST_TRUSTED[0] = True
    uid = 6060

    def cbs(kb):
        return [x.get("callback_data", "") for r in (kb or {}).get("inline_keyboard", []) for x in r]

    screens = {
        "витрина примеров": bot.gallery_kb(),
        "карточка примера": bot.demo_card_kb("osnova"),
        "список тарифов": bot.tariffs_list_kb(uid),
        "карточка тарифа": bot.tariff_card_kb("start", uid),
        "доп. опции": bot.services_list_kb(uid),
        "личный кабинет": bot.CABINET_KB,
        "темы писем": bot.content_kb(uid),
        "отчёт аудита": bot.audit_report_kb(),
        "план аудита": bot.audit_plan_kb(),
        "повтор аудита": bot.audit_result_kb(),
    }
    missing = [n for n, kb in screens.items() if "cons:offer" not in cbs(kb)]
    check("с каждого экрана можно попасть на консультацию", not missing, ", ".join(missing))

    # Без навязывания: на экранах выбора зовём «обсудить», а не «оформить»
    gal = " ".join(x.get("text", "") for r in bot.gallery_kb()["inline_keyboard"] for x in r)
    check("в витрине зовём обсудить, а не оформить", "Обсудить" in gal, gal[:80])

    # Контакт: телефон можно оставить и после записи через Telegram
    src = open(BOT, encoding="utf-8").read()
    check("после записи предлагаем оставить номер", '"cons:phone"' in src)
    check("номер отправляется одним нажатием", '"request_contact": True' in src)
    check("есть вежливый отказ", '"Не сейчас"' in src)

    shown = []
    bot.send = lambda c, t, kb=None: shown.append((t, kb))
    bot.process_callback(cq("cons:phone", uid))
    txt, kb = shown[-1]
    check("объясняем, зачем номер", "не достучимся" in txt)
    check("обещаем не звонить без дела", "только по делу" in txt)
    btns = [b.get("text") for r in (kb or {}).get("keyboard", []) for b in r]
    check("кнопка запроса контакта на месте", "📱 Отправить мой номер" in btns, str(btns))

    # Номер сохраняется и уходит в CRM
    bot2 = load()
    bot2._REQUEST_TRUSTED[0] = True
    bot2.state_set(7, {"flow": "cons_phone"})
    bot2.process_message({"chat": {"id": 7}, "from": {"id": 7, "username": "u"},
                          "contact": {"phone_number": "+79991234567"}, "text": ""})
    prof = bot2.user_get(7) or {}
    check("номер сохранён в профиле", prof.get("phone") == "+79991234567", str(prof.get("phone")))
    rows = [r for r in bot2._SHEET_Q if r.get("table") == "Consultations"]
    check("номер уходит в таблицу", bool(rows), str(bot2._SHEET_Q)[:100])

    # Отказ не ломает поток
    bot3 = load()
    bot3._REQUEST_TRUSTED[0] = True
    bot3.state_set(8, {"flow": "cons_phone"})
    bot3.process_message({"chat": {"id": 8}, "from": {"id": 8}, "text": "Не сейчас"})
    check("отказ принимается спокойно", not bot3.state_get(8))


def t_acceptance():
    """Приёмка: то, что заметно клиенту с первого экрана."""
    print("\n\u25b8 Приёмка глазами клиента")
    bot = load()

    # Длина сообщений: всё, что длиннее лимита Telegram, отклоняется целиком
    long_txt = "<b>Заголовок</b>\n\n" + ("Очень длинный абзац. " * 400)
    clipped = bot.clip_message(long_txt)
    check("слишком длинное сообщение обрезается", len(clipped) <= bot.TG_TEXT_LIMIT + 90,
          str(len(clipped)))
    check("обрезка не рвёт теги", clipped.count("<") == clipped.count(">"))
    check("после обрезки зовём на консультацию", "консультации" in clipped)
    check("короткое сообщение не трогаем", bot.clip_message("привет") == "привет")

    # Отчёт аудита влезает в лимит и не превращается в простыню
    p = dict(domain="x.ru", reachable=True, https=False, load_sec=6.0, viewport=False,
             title="", description="", h1=[], analytics=[], constructor="", forms=0,
             tel=0, mail=0, wa=False, tg=False, map=False, images=3, lazy=0,
             footer_year=2018, robots=False, sitemap=False, size_kb=1200, text_len=300,
             sig_cart=0, sig_catalog=0, sig_booking=0, sig_lead=0, sig_about=0,
             sig_portfolio=0, sig_price=0, sig_reviews=0, h2_count=0, og=False,
             inputs=0, favicon=False, hsts=False, gzip=False, server="")
    f = bot.audit_findings(p)
    md = bot.render_audit_md({"website_url": "x"}, p, f, "start", [], "")
    html = bot.md_to_html(md)
    check("отчёт влезает в лимит Telegram", len(html) < 4096, f"{len(html)} символов")
    check("в отчёте не больше четырёх разборов", md.count("**Чем это грозит.**") <= 4,
          str(md.count("**Чем это грозит.**")))
    check("остальные находки названы, а не выброшены",
          "Нашли ещё" in md or len(f) <= 4)
    check("остаток - повод прийти на консультацию",
          "на консультации" in md.lower())

    # Первый экран
    uid = 3003
    bot.process_update({"message": msg("/start", uid)})
    first = texts(bot, uid)
    check("на входе ровно два сообщения", len(first) == 2, str(len(first)))
    check("первое - короткая шапка", len(first[0]) < 120, first[0][:60])
    # Обещание вперёд, цифра следом: «сайт за 0 ₽» приводит неплатёжеспособных,
    # «увидите раньше, чем заплатите» - тех, кто боится предоплаты.
    check("второе - развилка со снятием риска",
          "ни рубля" in first[1] and "увидите раньше" in first[1])
    # Цифра «0 ₽» может отсутствовать вовсе - главное, что обещание идёт первым
    if "0 ₽" in first[1]:
        check("обещание идёт раньше цены",
              first[1].index("увидите раньше") < first[1].index("0 ₽"))
    else:
        check("обещание идёт раньше цены", True)


def t_strategy_alignment():
    """Сверка с документом «Стратегия 2026-2031»: где бот обязан ему следовать."""
    print("\n\u25b8 Соответствие стратегии")
    bot = load()
    src = open(BOT, encoding="utf-8").read()

    # «0 ₽ - это risk reversal, а не позиционирование бренда»
    check("подпись в меню не про цену",
          "разработка 0 ₽" not in bot.WELCOME.lower(), bot.WELCOME[:70])
    check("на входе сначала обещание, потом цифра",
          bot.ENTRY_MD.index("увидите раньше") < bot.ENTRY_MD.index("бесплатна"))
    check("оффер начинается с порядка работы, а не с прайса",
          "Как устроена оплата" in bot.TARIFFS_INFO)

    # Партнёры - больше половины запусков по плану, значит раздел для студий
    p = bot.PARTNER_TEXT
    check("партнёрам обещаем производство, а не комиссию",
          "под вашим брендом" in p or "производство" in p.lower())
    check("гарантируем, что не заберём клиента",
          "клиент остаётся вашим" in p.lower() or "ваш клиент - ваш" in p.lower())
    check("названа оптовая цена как выгода", "оптов" in p.lower())
    check("реферальный вход сохранён для одиночек",
          "вознаграждение" in p.lower())
    steps = [k for k, _ in bot.PARTNER_STEPS]
    check("спрашиваем тип партнёра", "ptype" in steps, str(steps))
    check("спрашиваем объём заказов", "volume" in steps, str(steps))

    # Права на материалы
    offer = open(os.path.join(os.path.dirname(BOT), "..", "docs", "legal",
                              "06-Публичная-оферта.md"), encoding="utf-8").read()
    check("в оферте описаны AI-материалы", "нейросет" in offer.lower())
    check("защищены шаблоны и промпты", "промпт" in offer.lower())
    check("описан статус материалов до оплаты", "До полной оплаты" in offer)
    check("описаны стоковые лицензии", "лиценз" in offer.lower())


def t_sheet_never_loses_rows():
    """Строка, которую не удалось записать, обязана дождаться следующей попытки.

    Появилось после боевого случая: аудит отработал, а запись в таблицу
    упала по таймауту, и заявка исчезла. Причина была в порядке действий -
    flush_sheets опустошал очередь ДО отправки, поэтому при сбое терять
    было уже нечего. То же делал предохранитель: тридцать секунд после
    ошибки он молча выбрасывал всё, что приходило следом.

    Таблица недоступна минуту - это переживаемо. Потерянная заявка
    не восстанавливается ничем, поэтому проверяем поведением, а не глазами.

    Три случая: сбой сети, сработавший предохранитель и удачная отправка
    после накопленного."""
    b = load()

    парк = {}
    b._get_orig, b._set_orig, b._del_orig = b._get, b._set, b._del
    b._get = lambda k: парк.get(k)
    b._set = lambda k, v, ttl=3600, immediate=False: парк.__setitem__(k, v)
    b._del = lambda k: парк.pop(k, None)
    отправлено = []

    try:
        # 1. Сеть недоступна - строка обязана остаться
        b._SHEET_Q[:] = []
        b._SHEET_BREAKER[0] = 0.0
        b._post_to_sheet_now = lambda row: (b._SHEET_FAIL_KIND.__setitem__(0, "net"), False)[1]
        b.post_to_sheet({"table": "Leads", "user_id": 1, "phone": "+70000000000"})
        b.flush_sheets()
        осталось = парк.get(b._SHEET_PARK_KEY) or []
        check("сбой сети: строка не потеряна", len(осталось) == 1, f"в парке {len(осталось)}")

        # 2. Предохранитель после свежей ошибки - тоже не выбрасываем
        b._SHEET_BREAKER[0] = b.time.time()
        b.post_to_sheet({"table": "Leads", "user_id": 2, "phone": "+70000000001"})
        b.flush_sheets()
        осталось = парк.get(b._SHEET_PARK_KEY) or []
        check("предохранитель: строка не потеряна", len(осталось) == 2, f"в парке {len(осталось)}")

        # 3. Связь вернулась - уходит и новое, и накопленное
        def удачно(row):
            отправлено.append(row)
            b._SHEET_FAIL_KIND[0] = ""
            return True
        b._post_to_sheet_now = удачно
        b._SHEET_BREAKER[0] = 0.0
        b.post_to_sheet({"table": "Leads", "user_id": 3, "phone": "+70000000002"})
        b.flush_sheets()
        пакет = отправлено[0].get("batch") if отправлено else []
        check("связь вернулась: накопленное дописано", len(пакет) == 3, f"ушло {len(пакет)}")
        check("парк очищен после удачи", not (парк.get(b._SHEET_PARK_KEY) or []),
              str(парк.get(b._SHEET_PARK_KEY))[:80])
    finally:
        b._get, b._set, b._del = b._get_orig, b._set_orig, b._del_orig
        b._SHEET_Q[:] = []
        b._SHEET_BREAKER[0] = 0.0


def t_claims_guard():
    """Правила достоверности из воронки 4.0.

    Продавать через выдуманную боль запрещено: нельзя утверждать, что клиент
    теряет клиентов, пока он сам этого не сказал; нельзя обещать заявки;
    нельзя приравнивать поисковые запросы к людям; нельзя считать упущенную
    прибыль без аналитики.

    Просить об этом модель в промпте недостаточно - просьбу нечем проверить.
    Поэтому запрет стоит на выходе, а тест держит и запрет, и обратную
    сторону: проверка не должна ругаться на наши законные тексты. Ложное
    срабатывание тут хуже отсутствия проверки - оно молча заменит нормальное
    сообщение заглушкой.
    """
    b = load()

    запрещено = [
        ("Вы теряете клиентов каждый день, пока сайта нет.", "потери без подтверждения"),
        ("Гарантируем рост заявок минимум вдвое.", "гарантия результата"),
        ("Конкуренты зарабатывают больше - у них есть сайт.", "оценка чужой выручки"),
        ("1000 запросов = 1000 клиентов в вашем городе.", "запросы как люди"),
        ("500 человек ищут вашу услугу прямо сейчас.", "частотность как люди"),
        ("Вы недополучаете 300 000 ₽ в месяц.", "денежная оценка потерь"),
        ("Сайт обязательно увеличит продажи на 40%.", "обещание роста"),
    ]
    for текст, что in запрещено:
        check(f"запрет ловит: {что}", bool(b.claims_violations(текст)), текст[:50])

    законно = [
        "как не терять клиента после первого разговора",
        "бизнесу, где важно не терять заявки",
        "сайт рабочий, но есть что усилить",
        "Покажем, что мешает получать заявки.",
        "Человек может не найти сайт в поиске - как это проявляется у вас?",
    ]
    for текст in законно:
        check(f"законное пропускается: {текст[:38]}", not b.claims_violations(текст), текст[:60])

    # Подмена происходит молча для клиента, но заметно для нас
    безопасно = "Предварительная оценка по открытым данным."
    вышло = b.claims_guard("Вы теряете клиентов.", безопасно, "тест")
    check("нарушение заменяется безопасным текстом", вышло == безопасно, вышло[:60])
    чисто = b.claims_guard(безопасно, "запасной", "тест")
    check("чистый текст проходит как есть", чисто == безопасно, чисто[:60])

    # Деньги называем только при всех четырёх исходных величинах
    полные = {"analytics_visits": 1200, "conversion_rate": 2.5,
              "close_rate": 30, "gross_profit_per_sale": 15000}
    check("деньги разрешены при полных данных", b.money_estimate_allowed(полные))
    for k in b.MONEY_INPUTS:
        d = dict(полные); d[k] = ""
        check(f"без {k} денежная оценка запрещена", not b.money_estimate_allowed(d))
    check("пустые данные - расчёта нет", not b.money_estimate_allowed({}))
    check("видно, чего не хватает", set(b.money_missing({})) == set(b.MONEY_INPUTS))

    # Факт без источника клиенту не показываем
    хороший = {"text": "На картах не указан режим работы",
               "source": "https://yandex.ru/maps/...", "checked_at": "31.07.2026"}
    плохой = {"text": "Похоже, у них нет отдела продаж"}
    ok, review = b.facts_for_client([хороший, плохой])
    check("факт с источником идёт клиенту", len(ok) == 1 and ok[0] is хороший)
    check("факт без источника остаётся на проверку", len(review) == 1 and review[0] is плохой)
    check("факт без даты проверки тоже не проходит",
          b.fact_needs_review({"text": "x", "source": "https://a.ru"}))


def t_no_claims_in_bot_texts():
    """Ни один клиентский текст самого бота не нарушает правила.

    Появилось после разбора: в отчёте аудита стояло «вы теряете бесплатный
    поток клиентов из поиска», а в заголовке - «сайт рабочий, но теряет
    клиентов». Оба утверждения мы ничем не подтверждали: аналитики клиента
    у нас нет. Проверка на выходе модели такое не поймала бы - это наш
    собственный текст, зашитый в код.
    """
    import ast
    b = load()
    src = open(BOT, encoding="utf-8").read()
    tree = ast.parse(src)

    # Документацию функций и пример запрещённых фраз внутри промпта пропускаем:
    # это не то, что видит клиент.
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.Module)):
            d = ast.get_docstring(n, clean=False)
            if d:
                docs.add(d)

    плохие = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) > 25:
            t = n.value
            if t in docs or "ЗАПРЕЩЕНО писать" in t or "Ты - эксперт веб-студии" in t:
                continue
            v = b.claims_violations(t)
            if v:
                плохие.append(f"«{t[:60]}» - {v[0][1]}")

    check("в текстах бота нет запрещённых утверждений", not плохие,
          "; ".join(плохие[:3]))


def t_site_mode():
    """Режим клиента: сайта нет / сайт есть / не подтверждён.

    Приёмочные тесты 1 и 2 из ТЗ воронки 4.0.

    Смысл UNCLEAR: по адресу нельзя узнать, чей это сайт. У малого бизнеса
    домен сплошь и рядом оформлен на того, кто когда-то делал сайт. Разбирать
    такой адрес как «ваш сайт» - значит выдать рекомендации, половину которых
    человек выполнить не может. Поэтому до подтверждения владения аудит
    не идёт.

    Отдельно про арендованные площадки. Страница в VK, карточка на Авито или
    в 2ГИС - это не сайт бизнеса, там владелец площадка. Такой клиент по
    воронке 4.0 как раз основной: собственного центра доверия у него нет.
    """
    b = load()

    # Тест 1 ТЗ: компания без сайта, есть карты и соцсети
    арендованные = [
        ("https://vk.com/mycompany", "страница ВКонтакте"),
        ("https://www.avito.ru/user/123", "профиль на Авито"),
        ("https://2gis.ru/perm/firm/70000", "карточка в 2ГИС"),
        ("https://taplink.cc/mybiz", "мультиссылка"),
        ("https://t.me/mychannel", "телеграм-канал"),
        ("https://zoon.ru/perm/beauty/salon", "карточка в справочнике"),
    ]
    for url, что in арендованные:
        режим, _ = b.classify_url(url)
        check(f"{что} - это не сайт бизнеса", режим == b.SITE_NO, f"{url} -> {режим}")

    # Тест 2 ТЗ: обычный домен - сайт есть, но чей, ещё неизвестно
    for url in ("https://example.ru", "http://мойбизнес.рф", "https://shop.company.com"):
        режим, почему = b.classify_url(url)
        check(f"обычный домен требует подтверждения: {url[:32]}",
              режим == b.SITE_UNCLEAR, f"{режим} / {почему}")

    check("пустой адрес не проходит", b.classify_url("")[0] == b.SITE_UNCLEAR)
    check("у каждого решения есть причина", all(b.classify_url(u)[1]
          for u in ("https://vk.com/x", "https://a.ru", "")))

    # UNCLEAR останавливает воронку, остальные два - нет
    check("не подтверждён - дальше не идём", not b.site_mode_ready.__doc__ is None)
    uid = 7301
    b.user_save(uid, {"telegram_id": uid})

    b.site_mode_set(uid, b.SITE_UNCLEAR, url="https://a.ru", why="тест")
    check("UNCLEAR: воронка остановлена", not b.site_mode_ready(uid), b.site_mode_of(uid))

    b.site_mode_set(uid, b.SITE_HAS, url="https://a.ru", owner_confirmed=True, why="тест")
    check("HAS_SITE: можно продолжать", b.site_mode_ready(uid))
    check("владение записано", (b.user_get(uid) or {}).get("website_owner_confirmed") is True)

    b.site_mode_set(uid, b.SITE_NO, why="тест")
    check("NO_SITE: можно продолжать", b.site_mode_ready(uid))
    check("режим виден в профиле", b.site_mode_of(uid) == b.SITE_NO)
    check("причина решения сохранена", (b.user_get(uid) or {}).get("site_mode_why"))

    check("выдуманный режим не принимается", b.site_mode_set(uid, "ЧТО-ТО") is None)
    check("режим по умолчанию не «сайта нет»", b.site_mode_of(999999) == "")

    # Тест 1 ТЗ, вторая половина: не говорить «вас нет в интернете»
    тексты = [b.NOSITE_MD, b.OWNER_ASK_MD]
    плохо = [t for t in тексты
             if "вас нет в интернете" in t.lower() or "вас не существует" in t.lower()]
    check("не говорим «вас нет в интернете»", not плохо, str(плохо)[:120])
    for t in тексты:
        check(f"текст режима без запрещённых утверждений: {t[:28]}",
              not b.claims_violations(t), str(b.claims_violations(t))[:100])


def t_undefined_names():
    """Имена, которые используются, но нигде не заданы.

    Появилось после боевого сбоя: в заголовках запроса стояло имя UA,
    которого не существовало. Python такое не ловит при компиляции -
    это ошибка времени выполнения. А внутри _fetch_once стоит общий
    except, который превращал NameError в «код 0», то есть в «сайт
    не ответил». Аудит падал на ЛЮБОМ адресе, клиент видел вежливую
    отговорку, и по логам это выглядело как капризы чужих сайтов.

    Тем же способом потерялись ANALYTICS, CONSTRUCTORS и niche_kb.

    Разбираем дерево кода, а не запускаем: заглушки в тестах подменяют
    сеть, и до настоящего вызова дело не доходит. Замыкания и распаковку
    в генераторах учитываем, иначе тест утонет в ложных срабатываниях."""
    import ast, builtins
    print("\n▸ Неопределённые имена")
    src = open(BOT, encoding="utf-8").read()
    tree = ast.parse(src)

    def stores(node):
        out = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                out.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(n.name)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                out.add(n.name)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    out.add((a.asname or a.name).split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    out.add(a.asname or a.name)
            elif isinstance(n, ast.Global):
                out.update(n.names)
        return out

    top = set(dir(builtins)) | stores(tree)

    def args_of(fn):
        a = fn.args
        got = {x.arg for x in list(a.args) + list(a.kwonlyargs) + list(a.posonlyargs)}
        if a.vararg:
            got.add(a.vararg.arg)
        if a.kwarg:
            got.add(a.kwarg.arg)
        return got

    # Для вложенных функций видимы имена всех объемлющих - это замыкания.
    def walk_scope(node, outer):
        bad = []
        for fn in [n for n in ast.iter_child_nodes(node)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            scope = outer | args_of(fn) | stores(fn)
            # Во вложенные функции не заходим: у них своя область видимости,
            # и они проверяются отдельным вызовом ниже. Иначе аргумент
            # вложенной функции выглядит как неопределённое имя снаружи -
            # тест ловил бы сам себя.
            nested = {id(x) for f2 in ast.walk(fn)
                      if isinstance(f2, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                      and f2 is not fn
                      for x in ast.walk(f2)}
            for n in ast.walk(fn):
                if id(n) in nested:
                    continue
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in scope:
                    bad.append((n.id, fn.name, n.lineno))
            bad += walk_scope(fn, scope)
        return bad

    bad = walk_scope(tree, top)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node not in tree.body:
            pass
    uniq = {}
    for name, fn, ln in bad:
        uniq.setdefault(name, (fn, ln))
    check("нет имён без определения",
          not uniq,
          "; ".join("%s (%s:%d)" % (k, v[0], v[1]) for k, v in sorted(uniq.items())))

    # Отдельно то, из-за чего сбой и случился: заголовок запроса.
    bot = load()
    check("бот представляется браузером, а не роботом",
          hasattr(bot, "UA") and "Mozilla" in bot.UA,
          getattr(bot, "UA", "нет UA"))
    check("в UA указано, кто мы", "onyx-web.ru" in getattr(bot, "UA", "").lower())
    check("таблица признаков аналитики не пуста", len(getattr(bot, "ANALYTICS", [])) >= 5)
    check("таблица конструкторов не пуста", len(getattr(bot, "CONSTRUCTORS", [])) >= 5)


def t_useful_materials():
    """Раздел «Полезное» и отраслевые гиды.

    Проверяем не только наличие кнопок, но и ПОРЯДОК: он здесь продающее
    решение, а не оформление. Материал про свою отрасль должен стоять выше
    общего, а чтение - ниже кнопки «Продолжить», чтобы горячего человека
    не уводить из разговора в статью. Если кнопки поменяют местами, тест
    об этом скажет."""
    print("\n▸ Полезные материалы")
    bot = load()
    uid = 4401

    g = bot.niche_guide("construction")
    check("адрес гида по стройке собрался сам",
          bool(g) and g["url"].endswith("/guide-stroy.html"), str(g))
    check("у ниши без гида гида нет", bot.niche_guide("dental") is None)
    check("пустая ниша не ломает", bot.niche_guide(None) is None
          and bot.niche_guide("") is None)

    bot.process_update({"message": msg("/start", uid)})
    bot.SENT.clear()
    bot.process_update({"message": msg("\U0001F4DA Полезное", uid)})
    t = " ".join(texts(bot, uid))
    check("раздел открывается по кнопке меню", "Полезное" in t)
    check("оба общих материала названы",
          "Что должен делать сайт" in t and "не пожалеть" in t)

    def urls_now():
        return [b.get("url") for m, kw in bot.SENT if kw.get("chat_id") == uid
                for r in (kw.get("reply_markup") or {}).get("inline_keyboard", [])
                for b in r if b.get("url")]

    u = urls_now()
    check("гид по стройке виден и без известной ниши", g["url"] in u, str(u))
    check("общий разбор на месте", bot.GUIDE_URL in u)
    check("чек-лист на месте", bot.CHECKLIST_URL in u)

    p = bot.user_get(uid) or {}
    p["niche"] = "construction"
    bot.user_save(uid, p)
    bot.SENT.clear()
    bot.process_update({"message": msg("\U0001F4DA Полезное", uid)})
    u = urls_now()
    check("при известной нише отраслевой гид первым", bool(u) and u[0] == g["url"], str(u))
    check("гид не продублировался", u.count(g["url"]) == 1, str(u))

    kb = bot.demos_kb("construction")["inline_keyboard"]
    flat = [b for r in kb for b in r]
    check("на экране примера стройки есть гид",
          any(b.get("url") == g["url"] for b in flat),
          str([b.get("text") for b in flat]))
    gi = [i for i, r in enumerate(kb) if any(b.get("url") == g["url"] for b in r)]
    ci = [i for i, r in enumerate(kb) if any(b.get("callback_data") == "demo:go" for b in r)]
    check("гид выше кнопки «Продолжить»", bool(gi) and bool(ci) and gi[0] < ci[0])
    check("в чужой нише гида по стройке нет",
          not any(b.get("url") == g["url"]
                  for r in bot.demos_kb("dental")["inline_keyboard"] for b in r))

    # Пустой url в Telegram валит весь запрос, поэтому кнопки-пустышки быть
    # не должно ни при каких обстоятельствах.
    saved = bot._CHECKLIST_BASE
    bot._CHECKLIST_BASE = ""
    check("без адреса статики гид отключается тихо",
          bot.niche_guide("construction") is None)
    bot._CHECKLIST_BASE = saved
    bot.GUIDE_URL = bot.CHECKLIST_URL = ""
    bot.SENT.clear()
    bot.process_update({"message": msg("\U0001F4DA Полезное", uid)})
    kb2 = [b for m, kw in bot.SENT if kw.get("chat_id") == uid
           for r in (kw.get("reply_markup") or {}).get("inline_keyboard", []) for b in r]
    check("кнопки с пустой ссылкой нет", all(b.get("url") for b in kb2 if "url" in b))
    check("раздел всё равно открылся", bool(kb2))


def t_site_button():
    """Кнопка «Сайт ONYX» в постоянном меню.

    Отдельная проверка нужна из-за особенности Telegram: в постоянном меню
    кнопок-ссылок не бывает, `url` разрешён только на встроенных. Поэтому
    кнопка обязана открывать сообщение, внутри которого уже есть ссылка -
    и если однажды кто-то попробует поставить `url` прямо в меню, Telegram
    молча откажет, а тест это заметит.

    Вторая проверка - про список MENU_TRIGGERS. Любая новая кнопка меню
    обязана в нём быть, иначе нажатие посреди анкеты будет принято за ответ
    на вопрос. Я сам на этом ошибся, когда добавлял кнопку."""
    print("\n▸ Кнопка «Сайт ONYX»")
    bot = load()
    uid = 4501

    keys = [b["text"] for r in bot.MAIN_MENU["keyboard"] for b in r]
    check("кнопка есть в постоянном меню", "🌐 Сайт ONYX" in keys, str(keys))
    check("в постоянном меню нет кнопок со ссылкой",
          all("url" not in b for r in bot.MAIN_MENU["keyboard"] for b in r))

    bot.process_update({"message": msg("/start", uid)})
    bot.SENT.clear()
    bot.process_update({"message": msg("\U0001F310 Сайт ONYX", uid)})
    t = " ".join(texts(bot, uid))
    kb = [b for m, kw in bot.SENT if kw.get("chat_id") == uid
          for r in (kw.get("reply_markup") or {}).get("inline_keyboard", []) for b in r]
    urls = [b.get("url") for b in kb if b.get("url")]
    check("сообщение открылось", "Сайт ONYX" in t)
    check("ссылка на сайт во встроенной кнопке", bot.SITE_URL in urls, str(urls))
    check("отдельный вход в примеры на сайте", bot.CASES_URL in urls, str(urls))
    check("есть возврат к тарифам в боте",
          any(b.get("callback_data") == "site:tariffs" for b in kb))

    # Любая кнопка меню, нажатая посреди анкеты, обязана анкету прервать.
    # Иначе нажатие уходит в неё ответом на вопрос: человек хотел открыть
    # раздел, а бот записал «🌐 Сайт ONYX» как адрес его сайта.
    #
    # Проверяем ВСЕ кнопки, а не только новую. Список MENU_TRIGGERS живёт
    # внутри обработчика, продублировать его в тесте значило бы получить
    # вторую копию правды. Поэтому сверяем по поведению - тогда забытая
    # кнопка ловится в тот же день, когда её добавили.
    # Различаем по метке, а не по «состояние пустое». Часть кнопок сама
    # начинает новый диалог - «Бесплатный аудит» сразу спрашивает адрес,
    # и состояние после него обязано быть. Важно другое: прежний диалог
    # должен быть выброшен. Метка живёт только в старом состоянии, поэтому
    # её исчезновение и есть доказательство, что нажатие не ушло ответом.
    stuck = []
    for label in keys:
        bot.process_update({"message": msg("/start", uid)})
        bot.state_set(uid, {"flow": "audit_url", "probe": "старый-диалог"})
        bot.SENT.clear()
        try:
            bot.process_update({"message": msg(label, uid)})
        except Exception as e:
            stuck.append("%s (упало: %s)" % (label, e))
            continue
        st = bot.state_get(uid) or {}
        if st.get("probe") == "старый-диалог":
            stuck.append(label)
    check("все кнопки меню прерывают прежний диалог, а не считаются ответом",
          not stuck, "залипли: " + ", ".join(stuck))


def t_tariffs_detailed():
    """Пакеты: новые цены и развёрнутые карточки."""
    print("\n\u25b8 Пакеты запуска")
    bot = load()

    want = {"start": 9990, "leads": 14990, "system": 24990}
    for tid, price in want.items():
        check(f"цена {tid} = {price}", bot.TARIFF[tid]["price"] == price,
              str(bot.TARIFF[tid]["price"]))

    for tid in want:
        t = bot.TARIFF[tid]
        for field in ("result", "pages", "days", "not_inc"):
            check(f"{tid}: есть поле {field}", bool(t.get(field)))

    card = bot.tariff_card_text("leads", 1)
    check("в карточке сказано, что человек получит", "Что вы получите" in card)
    check("в карточке есть объём и срок", "Объём" in card and "Первая версия" in card)
    check("честно названо, чего нет", "Чего в пакете нет" in card)
    check("объяснено, что недостающее докупается",
          "добавить отдельно" in card and "без сюрпризов" in card)
    check("цена привязана к утверждению",
          "после того, как утвердите" in card)
    check("карточка влезает в лимит Telegram", len(card) < 4000, str(len(card)))

    lst = bot.tariffs_list_text(1)
    for tid in want:
        check(f"в сводке видно объём: {tid}", bot.TARIFF[tid]["pages"] in lst)
    check("в сводке есть сроки", "первая версия за" in lst)

    src = open(BOT, encoding="utf-8").read()
    for old in ("5 990", "8 900", "19 900", "8 990", "13 990", "19 990"):
        check(f"старая цена {old} убрана из бота", old not in src)


if __name__ == "__main__":
    print("═" * 60)
    print("  ONYX — самопроверка бота")
    print("═" * 60)
    for fn in (t_static, t_static_assets, t_funnel_report, t_entry, t_converter, t_after_consult, t_qualification,
               t_edge_cases, t_crm, t_automations,
               t_funnel, t_order_before_tariff, t_navigation, t_demos,
               t_audit, t_start_checklist, t_deeplink_audit, t_drive, t_consent, t_reviews,
               t_no_blocking, t_sheets_batch, t_kv_mode, t_rich_fallback,
               t_tariff_images, t_security, t_menu_button, t_base_url, t_funnel_to_consult, t_step_back, t_consult_first_and_digest, t_kev_everywhere, t_acceptance, t_strategy_alignment,
               t_useful_materials, t_site_button, t_undefined_names, t_tariffs_detailed,
               t_sheet_never_loses_rows, t_claims_guard, t_no_claims_in_bot_texts, t_site_mode):
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
