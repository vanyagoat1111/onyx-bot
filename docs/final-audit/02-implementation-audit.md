# 02 — Сопоставление требований с реализацией

**Единица кода:** всё в `api/index.py` (монолит, ~5000+ строк) + `vercel.json` (cron) + `checklist.html`. Сайт onyx-web.ru — вне этого репозитория (исходников нет).

**Важно (Л2/Л3):** «Проверка» = как проверялось. `code` — трассировка по коду; `iso` — изолированный модульный тест проходил; `—` — не проверялось; **живой прогон в Telegram не выполнялся ни разу.** Поэтому статус «реализовано в коде» ≠ «работает в проде».

Статусы: ✅ реализовано в коде · 🟡 частично · ❌ не реализовано · 🧩 только каркас/не подключено · 🔌 заглушка/зависит от ключей · 🔒 заблокировано владельцем · 🚫 неактуально (бизнес-модель).

| ID | Статус | Что реально сделано | Что не сделано | Где (функция/ключ) | Проверка |
|---|---|---|---|---|---|
| BOT-001 | ✅ | MAIN_MENU, 6 кнопок, обработчики | — | `MAIN_MENU`, process_message | code |
| BOT-002 | ✅ | `/start` → `start_by_source` → 3 сценария | — | `start_by_source`, THREE_WAY_KB | iso |
| BOT-003 | ✅ | `t_<id>` распознаётся, тариф в заявку | сайт не шлёт эти ссылки (нет доступа к сайту) | `parse_start_payload`, `start_by_source` | iso |
| BOT-004 | ✅ | `o_<id>` → карточка опции | сайт не шлёт | `start_by_source` | iso |
| BOT-005 | ✅ | `build` → горячий сценарий | — | `start_by_source` | iso |
| BOT-006 | ✅ | `audit` → запрос URL | — | `start_by_source` | code |
| BOT-007 | ✅ | `checklist` → выдача | — | `start_by_source`, `start_flow` | code |
| BOT-008 | 🟡 | Бот распознаёт `wa_<token>`, связывает лид по токену | сайт не создаёт лид/токен (нет кода сайта) | `link_site_lead_by_token`, `sitelead_by_token` | code |
| BOT-009 | ✅ | `lm_<c>` → приветствие + воронка | — | `start_by_source` | iso |
| BOT-010 | ✅ | `set_source` пишет source | — | `set_source` | code |
| BOT-011 | ✅ | 3 пакета, список | — | `TARIFFS`, `tariffs_list_kb` | iso |
| BOT-012 | ✅ | Карточка: состав/цена/кому | — | `tariff_card_text` | code |
| BOT-013 | ✅ | `chosen_tariff` в профиле | — | `trf:pick` | iso |
| BOT-014 | ✅ | Смена тарифа | — | `trf:pick`, tariffs:list | code |
| BOT-015 | ❌ | — | Экрана сравнения нет | — | — |
| BOT-016 | 🟡 | Тарифы взяты с сайта (без сопровождения) | НЕ единый источник: цены зашиты в коде, сайт правится отдельно | `TARIFFS` | code |
| BOT-017 | ✅ | Список опций | — | `services_list_kb` | code |
| BOT-018 | 🟡 | Карточка опции (описание/зачем) | Нет поля «в какой тариф входит» | `service_card_text` | code |
| BOT-019 | ✅ | Добавление в корзину | — | `svc:add` | code |
| BOT-020 | ✅ | Удаление | — | `svc:del` | code |
| BOT-021 | ✅ | Сумма = тариф + опции | — | `order_items_with_tariff` | iso |
| BOT-022 | ✅ | 27 вопросов | — | `BRIEF_STEPS` | iso |
| BOT-023 | ✅ | Ответ пишется в state (KV) сразу | — | `brief_text_input`, `state_set` | code |
| BOT-024 | ✅ | «Назад» | — | `b:back` | iso |
| BOT-025 | ✅ | «Пропустить» для opt | — | `b:skip`/text | iso |
| BOT-026 | ✅ | «Отменить» | — | `b:cancel` | code |
| BOT-027 | ✅ | Резюме + подтверждение | — | `show_brief_summary` | iso |
| BOT-028 | ❌ | — | Уход в тарифы из середины анкеты сбрасывает flow (MENU_TRIGGERS/новый callback не сохраняет позицию) | — | code |
| BOT-029 | 🟡 | State анкеты в KV сохраняется | Нет экрана «Вы остановились на …» при повторном /start; /start делает state_del | `state_del` в /start | code |
| BOT-030 | 🟡 | Можно «Заполнить заново» | Точечного редактирования одного ответа нет | `b:redo` | code |
| BOT-031 | 🧩 | Функция `questionnaire_save_version` есть (Core) | НЕ подключена: живая анкета пишет в `onyx:quest:{uid}` (перезапись) | `questionnaire_save_version` (не вызывается) | iso |
| BOT-032 | 🧩 | Core связывает quest↔app | Живой флоу не создаёт application-сущность | `application_create` (не вызывается) | iso |
| BOT-033 | ✅ | Лист Questionnaire | — | `sheet_questionnaire` | code |
| BOT-034 | 🟡 | физ/юр | «ИП» отдельно не выделен (входит в «юр») | `CAP` | code |
| BOT-035 | ✅ | ФИО/тел/email | — | `CAP["individual"]` | iso |
| BOT-036 | ✅ | Название/ИНН/КПП/адрес/контакт/email/тел | — | `CAP["legal"]` | code |
| BOT-037 | ✅ | Текст про «Мой налог» перед/при оформлении | — | `finish_cap`, FAQ | code |
| BOT-038 | ✅ | Счёт-задача только при статусе `approved` | — | `apply_project_status` (key==approved) | iso |
| BOT-039 | 🟡 | Сводка суммы при checkout | Нет полной сводки (анкета+реквизиты вместе) перед подтверждением | `checkout` | code |
| BOT-040 | ✅ | Подтверждение → заявка (order) создаётся | — | `finish_cap` | code |
| BOT-041 | ✅ | Заявка → `consultation_pending` + задача | — | `finish_cap` | iso |
| BOT-042 | ✅ | Кабинет по TG ID | — | `render_cabinet`, CABINET_KB | code |
| BOT-043 | 🟡 | Профиль есть | Флага «создан на консультации» нет; создаётся на /start | `register_client` | code |
| BOT-044 | 🟡 | Показывает заказы (orders) | Не «заявки» как сущность; мультизаявочность — через orders | `render_orders` | code |
| BOT-045 | 🧩 | Журнал (`journal_get`) в Core | Не подключён к кабинету | `journal_get` | iso |
| BOT-046 | 🟡 | Можно оформить ещё заказ | Явной кнопки «новая заявка» в кабинете нет | — | code |
| BOT-047 | 🟡 | Статус по заказу (order) | Не по application_id; при нескольких — берёт активный | `render_my_order` | code |
| BOT-048 | ❌ | — | Выбора заявки при нескольких нет | — | — |
| BOT-049 | ✅ | Статус + описание + ETA | — | `PROJECT_STATUS`, `ps_*` | code |
| BOT-050 | ✅ | ~17 статусов проекта + лид-статусы | (35 из ТЗ покрыты по смыслу) | `PROJECT_STATUS`, LEAD_STATUSES | code |
| BOT-051 | 🟡 | Часть авто (лид-статус, теги, температура); статус проекта — вручную | Полной таблицы «событие→статус проекта» авто нет | `lead_touch`, `apply_project_status` | code |
| BOT-052 | ✅ | Кнопки/команды смены статуса | — | `setstatus:`, `/set_order_status` | code |
| BOT-053 | 🟡 | Уведомление клиенту при смене; время пишется | Журнал (Core) не подключён; событие в Events частично | `apply_project_status` | code |
| BOT-054 | ✅ | Чек-лист (HTML/ссылка) | зависит от `CHECKLIST_URL` | `start_flow` | 🔌 |
| BOT-055 | ✅ | Сопроводительный текст | — | `start_flow` | code |
| BOT-056 | 🟡 | Событие start логируется | Отдельного «checklist_received» события/статуса нет | `log_event` | code |
| BOT-057 | ❌ | — | Автосмены статуса на выдаче чек-листа нет | — | — |
| BOT-058 | ✅ | Ввод URL + `valid_url` | — | `valid_url` | code |
| BOT-059 | ✅ | Нормализация домена | — | `normalize_domain` | iso |
| BOT-060 | ✅ | Реестр по домену, дедуп | — | `audit_registry_get` | iso |
| BOT-061 | ✅ | Свежесть 30 дней; устаревший → обновить | — | `audit_registry_get`, `audit_start` | iso |
| BOT-062 | ✅ | Задача/запись нового аудита | — | `audit_new` | code |
| BOT-063 | 🔌 | PR-CY интеграция есть | endpoint не сверен на боевом ключе | `prcy_create_task/fetch` | 🔒 |
| BOT-064 | 🔌 | AI-резюме | без ключа — шаблонный fallback | `ai_summarize` | 🔒 |
| BOT-065 | ❌ | — | Адаптера внутреннего AI-агента + очереди нет (только PR-CY + cron дожим) | `run_pending_audits` (частично) | — |
| BOT-066 | ✅ | Отправка + кнопки после аудита | — | `render_audit`, `audit_result_kb` | code |
| BOT-067 | ✅ | AuditOffers + кнопки | — | `offer_create` | code |
| BOT-068 | ✅ | Шкала 1–10 | — | `rating_kb` | iso |
| BOT-069 | ✅ | Комментарий/видео/разрешение | — | review flow | code |
| BOT-070 | ✅ | Финальный текст | — | `REVIEW_THANKS` | code |
| BOT-071 | ✅ | Партнёрка + код | — | `partner_new` | code |
| BOT-072 | 🟡 | Реферал считается (referrals++) | Полной привязки «партнёр→оплата→выплата» нет | `/start` ref | code |
| BOT-073 | ✅ | 9 тем, подписка/отписка/рассылка | — | `csub_*`, broadcast | code |
| BOT-074 | 🧩 | `sitelead_create` в Core | Приём с сайта не подключён (нет endpoint/формы сайта) | `sitelead_create` | iso |
| BOT-075 | 🧩 | Статус `need_whatsapp` в Core | — | `sitelead_create` | iso |
| BOT-076 | 🟡 | Уведомление при связывании | При создании лида (нет входа с сайта) — не шлётся | `link_site_lead_by_token` | code |
| BOT-077 | ✅ | wa-токен генерится | — | `_wa_token` | iso |
| BOT-078 | 🟡 | Связывание по токену | Зависит от того, что сайт создаст лид+токен | `link_site_lead_by_token` | code |
| BOT-079 | 🧩 | Дедуп клиента (tg/тел/email) в Core | Живой флоу использует старый `register_client` (по tg) | `client_get_or_create` | iso |
| BOT-080 | ✅ | Блокировка двойного тапа | — | `onyx:order_lock` | code |
| BOT-081 | 🧩 | Раздельные ID реализованы в Core | НЕ используются в живом флоу (там client_id==telegram_id) | Core-слой | iso |
| BOT-082 | 🧩 | `journal_add/get` есть | Не вызывается из живых хендлеров | `journal_add` | iso |
| BOT-083 | 🟡 | Часть сущностей (Core) + старые (user/order/lead/audit/tasks/…) | Core и старое НЕ объединены; двойная модель | Core + старые | iso/code |
| BOT-084 | ✅ | FSM в KV (`onyx:state:{uid}`) переживает рестарт | (кроме анкеты при /start — см. BOT-029) | `state_*` | code |
| BOT-085 | ✅ | `/admin` 12 разделов | — | `admin_panel_kb` | code |
| BOT-086 | ✅ | Статистика | — | `admin_stats_text` | code |
| BOT-087 | ✅ | Воронка | — | `funnel_text` | iso |
| BOT-088 | ✅ | Температура | — | `compute_temperature` | iso |
| BOT-089 | ✅ | Авто-теги | — | `recompute_tags` | iso |
| BOT-090 | ✅ | Дожимы + cron | зависит от Cron Vercel | `run_followups` | iso |
| BOT-091 | ✅ | ProductionSites при оплате | — | `production_create` | iso |
| BOT-092 | ✅ | Дизайн-задача + промпт | — | `DESIGN_PROMPT_TEMPLATE` | code |
| BOT-093 | ✅ | Domains из анкеты + задача | — | `domain_from_questionnaire` | iso |
| BOT-094 | ✅ | Tasks + автозадачи + дедуп | — | `create_task` | iso |
| BOT-095 | ✅ | Тикеты: создать/ответить/закрыть | ответ через `/reply` | `ticket_create` | code |
| BOT-096 | ✅ | FAQ | — | `FAQ`, faq menu | code |
| BOT-097 | ✅ | Уведомления (10+ событий) | не хватает «начал анкету»/«лид сайта» (см. 04) | `notify_admins` | code |
| BOT-098 | ✅ | `/export` сводка | не файловый экспорт | `/export` | code |
| BOT-099 | ✅ | safe_send, log_error, раздел «Ошибки» | — | `safe_send`, `log_error` | code |
| BOT-100 | ✅ | Prodamus удалён полностью | — | (webhook/ссылки удалены) | code |
| BOT-101 | ✅ | Сопровождение удалено | — | (sub_* удалены) | iso |
| BOT-102 | 🟡 | Sheets пишутся из бота | Единого слоя синхронизации/дедупа листов нет | `sheet_post` | code |
| BOT-103 | ❌ | — | Сайт не интегрирован (нет доступа к его коду) | — | 🔒 |
| BOT-104 | 🔒 | На сайте есть 6 демо-карточек | Не подтверждено, что открываются; в боте демо нет | сайт | 🔒 |
| BOT-105 | ❌ | — | Квиза-подборщика нет | — | — |
| BOT-106 | ❌ | — | Ролей/авторизации как системы нет | — | — |
| BOT-107 | 🟡 | Часть — `07-owner-manual.md` | — | docs | — |

### Свод по статусам
- ✅ в коде: ~63 · 🟡 частично: ~20 · 🧩 каркас/не подключено: ~9 · ❌ нет: ~9 · 🔌/🔒 зависит от ключей/владельца/сайта: ~6 · 🚫: 0.
- **Ни одно требование не подтверждено живым сквозным прогоном** (Л2).
