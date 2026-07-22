# 05 — Фактическая модель данных (как есть, не план)

**Хранилище:** Upstash Redis (KV) — оперативное; Google Sheets — зеркало/панель через webhook (`SHEETS_WEBHOOK_URL`). Решение владельца: остаёмся на KV+Sheets.

**Ключевая проблема (честно): двойная модель.** Живой бот работает на «старых» KV-структурах, где `client_id == telegram_id`. Параллельно добавлен «Core»-слой с раздельными ID — но он **не подключён** к живым хендлерам. Ниже описаны обе.

## A. Старая (живая) модель — используется ботом сейчас
| Ключ KV | Сущность | Ключевые поля | Кто создаёт | Кто обновляет | ID |
|---|---|---|---|---|---|
| `onyx:user:{tg}` | Клиент/профиль | name, phone, city, niche, website, client_status, questionnaire_status, chosen_tariff, orders[], purchased_services, tags, source, referred_by | register_client (/start) | анкета, оплата, теги | **telegram_id (= client_id)** |
| `onyx:order:{id}` | Заказ (= заявка) | uid, items[], total, status, payment_type, payment_method, реквизиты(company/inn/kpp/…), created | finish_cap/order_new | apply_project_status, /paid | order_id; uid→tg |
| `onyx:quest:{tg}` | Анкета (последняя) | 27 полей | sheet_questionnaire | перезапись при перезаполнении | tg (перезапись!) |
| `onyx:state:{tg}` | FSM-состояние | flow, i, data, mid | хендлеры | — | tg |
| `onyx:cart:{tg}` | Корзина опций | [ids] | svc:add | svc:del | tg |
| `onyx:lead:{tg}` | Лид (температура) | lead_status, lead_temperature, source, followups_off | lead_touch | lead_touch | tg (≠ lead сайта!) |
| `onyx:audit:{id}` | Аудит | website_url, weak_points, ai_summary, status | audit_new | audit_finish | audit_id |
| `onyx:audit_domain:{domain}` | Реестр аудитов | audit_id, at | audit_registry_set | — | domain |
| `onyx:task:{id}` | Задача команды | related_type, title, status, priority | create_task | task callbacks | task_id |
| `onyx:followup:{id}` | Дожим | type, scheduled_ts, status | schedule_followup | run_followups | followup_id |
| `onyx:prod:{order_id}` | Производство | 25 полей конвейера | production_create | prod callbacks | =order_id |
| `onyx:domain:{tg}` | Домен | domain_status, name | domain_from_questionnaire | prod:set | tg |
| `onyx:ticket:{id}` | Тикет | category, message, status | ticket_create | /reply | ticket_id |
| `onyx:partner:{tg}` | Партнёр | partner_code, status | partner_new | admin | tg |
| `onyx:csub:{tg}` | Контент-подписка | topics[] | csub_* | csub_* | tg |
| `onyx:offer:{tg}` | Предложение после аудита | recommended_services, offer_status | offer_create | audit:fix | tg |
| счётчики `onyx:cnt:*` | Воронка/выручка | — | bump | — | — |

## B. Core-слой (Этап 1) — реализован, НО НЕ подключён
| Ключ | Сущность | ID | Индексы | Статус |
|---|---|---|---|---|
| `onyx:client:{cid}` | Клиент | **client_id (отдельный seq)** | tg2client, phone2client, email2client | 🧩 не вызывается |
| `onyx:app:{aid}` | Заявка (версии) | application_id | client_apps:{cid} | 🧩 |
| `onyx:quest2:{qid}` | Анкета-версия | questionnaire_id | client_quests:{cid} | 🧩 |
| `onyx:sitelead:{lid}` | Лид сайта | lead_id | watoken:{token} | 🧩 |
| `onyx:journal:{cid}` | Журнал событий | append-only | — | 🧩 |
| seq `onyx:seq:{kind}` | Последовательности | client/application/lead/quest | — | 🧩 |

Функции Core: `client_get_or_create` (дедуп tg/тел/email), `application_create/new_version` (версии, старые не удаляются), `questionnaire_save_version`, `sitelead_create/by_token/link_client`, `journal_add/get`, `client_set_stage`, `application_set_status`, `migrate_user_to_client`. Прошли изолированные тесты. **Не интегрированы в хендлеры.**

## Требуемые связи (arch) — состояние
| Связь | Есть? |
|---|---|
| Telegram ID → client_id | 🟡 сейчас это одно и то же (в Core — разделено, не подключено) |
| client_id → lead_id | 🧩 только в Core |
| client_id → application_id | 🟡 через onyx:user.orders (tg→order), не через client_id |
| application_id → анкета | 🧩 только в Core (quest2), живой — onyx:quest:{tg} |
| application_id → тариф/опции/сумма | 🟡 в order (items/total), tariff как строка «Тариф: …» |
| application_id → реквизиты | ✅ в order |
| application_id → статус | ✅ order.status |
| domain → audit_id | ✅ onyx:audit_domain |

## Дублирование и риски потери данных
- **Дублирование:** клиент представлен и в `onyx:user`, и (потенциально) в `onyx:client` — два источника правды, пока Core не подключён и не выполнена миграция.
- **Потеря данных:** `onyx:quest:{tg}` **перезаписывается** при перезаполнении анкеты (нарушает «не стирать старые» — BOT-031). Core это решает (quest2 версии), но не активен.
- **`onyx:lead` (температура) ≠ lead сайта** — два разных «lead». Именование путает; при подключении Core развести.
- **TTL:** большинство записей `ttl=YEAR`; счётчики дневные с EXPIRE. Long-term хранение зависит от плана Upstash.

## Google Sheets (панель)
Листы, в которые пишет бот: Clients, Questionnaire, Orders, Subscriptions(устар.), Audits, Reviews, Partners, ContentSubscriptions, BroadcastLogs, ClientRequests, Leads, Events, Tasks, FollowUps, ProductionSites, Domains, SupportTickets, AuditOffers, ClientsCore(Core), Applications(Core). Единого слоя синхронизации/дедупа между листами нет — это набор append-логов, а не связанная БД.
