# Gear Flow: модель данных

## 1. Назначение

Этот документ переводит `SPEC.md` в исполняемую модель данных: таблицы, события, проекции и ограничения.

Модель ориентирована на реляционную базу данных. Названия таблиц и полей даны в `snake_case`. Конкретный стек пока не выбран, поэтому типы описаны логически: `uuid`, `text`, `timestamp`, `json`, `integer`, `boolean`.

Главная идея:

- журнал событий хранит физическую историю и является источником истины;
- проекции хранят текущее состояние для быстрых экранов и проверок;
- проекции можно пересобрать из журнала событий;
- старые события не редактируются и не удаляются.

## 2. Идентификаторы и время

Рекомендуемые правила:

- `event_id`, `user_id`, `holder_id`, `lot_id`, `evidence_id` - технические UUID/ULID;
- `item_id` - стабильный идентификатор индивидуального оборудования, который связывается с QR-кодом;
- `sku_id` - стабильный идентификатор количественно учитываемой позиции;
- `happened_at` - когда действие физически произошло;
- `recorded_at` - когда действие внесли в систему.

`happened_at` может быть раньше `recorded_at`, но не должен использоваться для переписывания истории задним числом. Если событие внесли поздно, оно все равно добавляется новым событием.

## 3. Справочники

### 3.1 users

Пользователи системы для доменных действий.

Админский вход в первую версию отдельный: один администратор входит в админку по полноценному паролю. 4-значный PIN используется для быстрых QR-действий участников и не заменяет админский пароль.

Поля:

- `user_id` PK;
- `display_name`;
- `email`, nullable;
- `role`: `admin`, `member`, `inventory_manager`;
- `pin_hash`;
- `pin_updated_at`;
- `pin_reset_required`;
- `is_active`;
- `created_at`;
- `updated_at`.

Правила:

- роль проверяется на уровне команд;
- обычный пользователь с ролью `member` может брать и возвращать оборудование по QR-коду;
- складские операции и коррекции выполняет `inventory_manager`.
- пользователь подтверждает QR-выдачу и QR-возврат 4-значным PIN-кодом;
- PIN нельзя хранить открытым текстом;
- сбросить PIN может только `admin`.

### 3.2 holders

Единая таблица держателей ответственности и остатков.

Поля:

- `holder_id` PK;
- `holder_type`: `person`, `location`, `external`, `retired`;
- `name`;
- `linked_user_id`, nullable FK -> `users.user_id`;
- `is_active`;
- `is_self_service_source`;
- `is_temporary`;
- `starts_at`, nullable;
- `ends_at`, nullable;
- `managed_by_user_id`, nullable FK -> `users.user_id`;
- `metadata` json;
- `created_at`;
- `updated_at`.

Правила:

- `person`-держатель может быть связан с пользователем через `linked_user_id`;
- у одного пользователя не должно быть больше одного активного `person`-держателя;
- при создании или сохранении `Person` через админку держатель `person` создается или синхронизируется автоматически;
- `location` может быть постоянным местом или временным местом мероприятия;
- `is_self_service_source = true` разрешает QR-выдачу из этого держателя;
- `external` используется для ремонта, партнеров и внешних организаций;
- `retired` используется как терминальный держатель для выведенного оборудования.
- базовые `location` первой версии: `Студия` и `Склад`.

Ограничения:

- `holder_type in ('person', 'location', 'external', 'retired')`;
- если `holder_type = 'person'`, желательно требовать `linked_user_id`;
- если `is_temporary = true`, желательно указывать хотя бы `starts_at` или `ends_at`;
- `linked_user_id` уникален среди активных `person`-держателей.

## 4. Оборудование

### 4.1 asset_items

Карточка индивидуально учитываемого объекта.

Поля:

- `item_id` PK;
- `asset_type`;
- `name`;
- `serial_number`, nullable;
- `model`, nullable;
- `manufacturer`, nullable;
- `inventory_number`, nullable;
- `metadata` json;
- `registered_event_id` FK -> `event_log.event_id`;
- `created_at`;
- `created_by` FK -> `users.user_id`.

Правила:

- `item_id` никогда не переиспользуется;
- обязательные поля первой версии: `item_id`, `asset_type`, `name`, начальный держатель;
- текущий держатель, состояние и жизненный статус не являются источником истины в этой таблице;
- текущие значения лежат в проекции `asset_current_state`.

### 4.2 asset_qr_codes

QR-коды оборудования.

Поля:

- `qr_code_id` PK;
- `code_value` unique;
- `payload_url`;
- `label_text`;
- `item_id` FK -> `asset_items.item_id`;
- `status`: `active`, `revoked`;
- `created_at`;
- `revoked_at`, nullable.

Правила:

- активный QR-код указывает ровно на один `item_id`;
- `payload_url` содержит абсолютную ссылку на страницу объекта, например `http://192.168.1.10:8000/i/GF-000123`;
- `label_text` содержит видимую надпись с `item_id`, которую нужно напечатать на наклейке рядом с QR-кодом или под ним;
- QR-наклейка печатается на обычном принтере и должна помещаться на минимальную поверхность уровня стандартной SD-карты;
- один объект может иметь новый QR-код после перевыпуска, но старый код переводится в `revoked`;
- QR-сканирование создает событие, а не меняет состояние напрямую.

### 4.3 qr_print_batches

Административные пакеты печати QR-наклеек. Это не доменное событие учета, а удобный артефакт печати.

Поля:

- `batch_id` PK;
- `created_at`;
- `created_by` FK -> `users.user_id`;
- `title`, nullable;
- `status`: `draft`, `printed`;
- `printed_at`, nullable.

Правила:

- пакет может содержать один или много QR-кодов;
- пакет нужен для печати листа наклеек на обычном принтере;
- макет первой версии: только QR-код и видимый `item_id`.

### 4.4 qr_print_batch_items

Связь пакета печати и QR-кодов.

Поля:

- `batch_id` FK -> `qr_print_batches.batch_id`;
- `qr_code_id` FK -> `asset_qr_codes.qr_code_id`;
- `position_index`;

Ключ:

- PK (`batch_id`, `qr_code_id`).

Правила:

- `position_index` задает порядок на печатном листе;
- один QR-код может входить в несколько пакетов, если наклейку нужно перепечатать.

### 4.5 asset_current_state

Проекция текущего состояния оборудования.

Поля:

- `item_id` PK, FK -> `asset_items.item_id`;
- `holder_id` FK -> `holders.holder_id`;
- `condition`: `working`, `needs_attention`, `broken`, `unknown`;
- `lifecycle_status`: `active`, `lost`, `repair`, `retired`, `disposed`;
- `due_on`, nullable;
- `checkout_reason`, nullable;
- `last_event_id` FK -> `event_log.event_id`;
- `updated_at`.

Правила:

- одна строка на один `item_id`;
- эта таблица обеспечивает быстрый ответ на вопрос "у кого объект сейчас";
- `due_on` заполняется, когда объект находится у человека;
- `checkout_reason` хранит последнюю причину взятия для быстрых экранов;
- изменение этой проекции допустимо только внутри транзакции, которая добавляет событие в `event_log`;
- при пересборке проекция восстанавливается из событий.

## 5. Мерч и расходники

### 5.1 stock_skus

Карточка количественно учитываемой позиции.

Поля:

- `sku_id` PK;
- `name`;
- `category`;
- `unit`;
- `attributes` json;
- `is_active`;
- `created_at`;
- `updated_at`;
- `created_by` FK -> `users.user_id`.

Правила:

- SKU не является физическим объектом;
- SKU не имеет `item_id`;
- изменение количества по SKU происходит только через события.

### 5.2 stock_lots

Партии SKU. Партии опциональны, но в данных всегда есть `lot_id`: если партия не важна, используется техническая партия по умолчанию.

Поля:

- `lot_id` PK;
- `sku_id` FK -> `stock_skus.sku_id`;
- `name`;
- `is_default`;
- `received_at`, nullable;
- `source`, nullable;
- `metadata` json;
- `created_at`.

Правила:

- у SKU может быть одна техническая партия по умолчанию;
- реальные партии используются только там, где это полезно: одежда, event-specific мерч, поставки с важным происхождением;
- баланс считается по `sku_id + lot_id + holder_id`.

### 5.3 stock_balances

Проекция текущих остатков.

Поля:

- `sku_id` FK -> `stock_skus.sku_id`;
- `lot_id` FK -> `stock_lots.lot_id`;
- `holder_id` FK -> `holders.holder_id`;
- `quantity`;
- `last_event_id` FK -> `event_log.event_id`;
- `updated_at`.

Ключ:

- PK (`sku_id`, `lot_id`, `holder_id`).

Правила:

- `quantity >= 0`;
- остаток нельзя редактировать напрямую;
- изменение остатка допустимо только внутри транзакции, которая добавляет событие и строки складского ledger;
- при пересборке баланс восстанавливается суммой `stock_ledger_entries.quantity_delta`.

## 6. Журнал событий

### 6.1 event_log

Главный append-only журнал.

Поля:

- `event_id` PK;
- `event_type`;
- `happened_at`;
- `recorded_at`;
- `recorded_by` FK -> `users.user_id`;
- `reason`;
- `comment`;
- `correction_of_event_id`, nullable FK -> `event_log.event_id`;
- `payload` json;
- `idempotency_key`, nullable unique.

Типы событий первой версии:

- `AssetRegistered`;
- `AssetTransferred`;
- `AssetAuditRecorded`;
- `AssetCorrectionRecorded`;
- `AssetRetired`;
- `AssetReactivated`;
- `StockReceived`;
- `StockMoved`;
- `StockIssued`;
- `StockAuditRecorded`;
- `StockAuditAdjusted`.

Правила:

- событие нельзя обновлять или удалять после создания;
- исправление старого события создается новым событием с `correction_of_event_id`;
- `payload` хранит дополнительные детали команды, но критичные связи выносятся в типизированные таблицы ниже.

### 6.2 event_evidence

Доказательства, приложенные к событию.

Поля:

- `evidence_id` PK;
- `event_id` FK -> `event_log.event_id`;
- `evidence_type`: `photo`, `file`, `link`, `qr_scan`;
- `uri_or_value`;
- `metadata` json;
- `created_at`;
- `created_by` FK -> `users.user_id`.

Правила первой версии:

- при расхождении аудита комментарий в `event_log.comment` обязателен;
- фото и файлы опциональны;
- фото проблем хранятся в файловой папке на сервере, а в базе хранится ссылка;
- факт QR-сканирования фиксируется автоматически как `qr_scan` или в `payload` события.

## 7. Детали событий оборудования

### 7.1 asset_event_items

Типизированные строки событий по оборудованию.

Поля:

- `event_id` FK -> `event_log.event_id`;
- `item_id` FK -> `asset_items.item_id`;
- `from_holder_id`, nullable FK -> `holders.holder_id`;
- `to_holder_id`, nullable FK -> `holders.holder_id`;
- `condition_before`, nullable;
- `condition_after`, nullable;
- `lifecycle_status_before`, nullable;
- `lifecycle_status_after`, nullable;
- `due_on`, nullable;
- `note`, nullable.

Ключ:

- PK (`event_id`, `item_id`).

Правила:

- `AssetRegistered` задает `to_holder_id`, `condition_after`, `lifecycle_status_after`;
- `AssetTransferred` задает `from_holder_id` и `to_holder_id`;
- QR-выдача задает `due_on` и причину взятия в `event_log.reason`;
- QR-возврат очищает `due_on` в проекции;
- состояние может измениться только в событии передачи или аудита;
- `AssetCorrectionRecorded`, меняющий состояние, должен ссылаться на аудит или передачу;
- команда передачи блокирует строки `asset_current_state` выбранных объектов и проверяет текущего держателя перед записью события.

### 7.2 asset_audit_observations

Наблюдения аудита оборудования.

Поля:

- `event_id` FK -> `event_log.event_id`;
- `item_id` FK -> `asset_items.item_id`;
- `expected_holder_id`, nullable FK -> `holders.holder_id`;
- `observed_holder_id`, nullable FK -> `holders.holder_id`;
- `expected_condition`, nullable;
- `observed_condition`, nullable;
- `result`: `matched`, `missing`, `unexpected_holder`, `condition_mismatch`;
- `note`, nullable.

Ключ:

- PK (`event_id`, `item_id`).

Правила:

- если найдено расхождение, комментарий аудита обязателен;
- коррекция расхождения создается отдельным событием;
- быстрое наблюдение состояния без большого аудита все равно оформляется как `AssetAuditRecorded`.

## 8. Детали событий мерча и расходников

### 8.1 stock_ledger_entries

Ledger движения количественных остатков.

Поля:

- `entry_id` PK;
- `event_id` FK -> `event_log.event_id`;
- `sku_id` FK -> `stock_skus.sku_id`;
- `lot_id` FK -> `stock_lots.lot_id`;
- `holder_id` FK -> `holders.holder_id`;
- `quantity_delta`;
- `purpose`, nullable;
- `recipient`, nullable;
- `note`, nullable.

Правила:

- `quantity_delta` не может быть 0;
- `StockReceived` создает положительную строку;
- `StockMoved` создает две строки в одном событии: отрицательную для источника и положительную для получателя;
- `StockIssued` создает отрицательную строку;
- `StockAuditAdjusted` создает положительную или отрицательную строку;
- итоговый `stock_balances.quantity` не может стать отрицательным.

### 8.2 stock_audit_observations

Наблюдения аудита мерча и расходников.

Поля:

- `event_id` FK -> `event_log.event_id`;
- `sku_id` FK -> `stock_skus.sku_id`;
- `lot_id` FK -> `stock_lots.lot_id`;
- `holder_id` FK -> `holders.holder_id`;
- `expected_quantity`;
- `observed_quantity`;
- `quantity_delta`;
- `adjustment_event_id`, nullable FK -> `event_log.event_id`;
- `note`, nullable.

Ключ:

- PK (`event_id`, `sku_id`, `lot_id`, `holder_id`).

Правила:

- `observed_quantity >= 0`;
- если `quantity_delta != 0`, комментарий аудита обязателен;
- корректировка остатка создается событием `StockAuditAdjusted`.

## 9. Транзакции команд

### 9.1 Register Asset

В одной транзакции:

- создать `event_log` с типом `AssetRegistered`;
- создать `asset_items`;
- создать активный QR-код, если QR уже известен;
- создать `asset_event_items`;
- создать строку `asset_current_state`.

### 9.2 Transfer Asset

В одной транзакции:

- заблокировать `asset_current_state` по всем `item_id`;
- проверить, что текущий `holder_id` совпадает с `from_holder_id`;
- проверить, что объекты не `retired`, `disposed`, `lost`;
- создать `event_log` с типом `AssetTransferred`;
- создать строки `asset_event_items`;
- обновить `asset_current_state`.

### 9.3 QR Checkout / QR Return

Это интерфейсные сценарии над `AssetTransferred`.

QR-выдача:

- определить `item_id` по активному QR-коду;
- выбрать пользователя из списка;
- проверить 4-значный PIN-код;
- проверить, что текущий держатель разрешает самообслуживание;
- проверить, что объект не числится за другим человеком или `external`;
- потребовать дату возврата;
- потребовать свободный текст причины взятия;
- если отмечена проблема с состоянием, сохранить новое состояние и опциональное фото;
- создать обычный `AssetTransferred` на `person`-держателя пользователя.

QR-возврат:

- определить `item_id` по активному QR-коду;
- выбрать пользователя из списка;
- проверить 4-значный PIN-код;
- проверить, что пользователь сейчас держит объект или имеет право вернуть спорный объект;
- выбрать место возврата: `Студия` или `Склад`;
- если отмечена проблема с состоянием, сохранить новое состояние и опциональное фото;
- создать обычный `AssetTransferred` на выбранное место;
- если обнаружено расхождение, сначала создать аудит/коррекцию.

### 9.4 Asset Audit

В одной транзакции:

- создать `event_log` с типом `AssetAuditRecorded`;
- сохранить `asset_audit_observations`;
- для расхождений создать отдельные корректирующие события или пометить их как требующие ручного подтверждения.

### 9.5 Receive Stock

В одной транзакции:

- создать `event_log` с типом `StockReceived`;
- создать положительную строку `stock_ledger_entries`;
- обновить или создать `stock_balances`.

### 9.6 Move Stock

В одной транзакции:

- заблокировать баланс источника;
- проверить достаточный остаток;
- создать `event_log` с типом `StockMoved`;
- создать две строки `stock_ledger_entries`;
- обновить балансы источника и получателя.

### 9.7 Issue Stock

В одной транзакции:

- заблокировать баланс источника;
- проверить достаточный остаток;
- создать `event_log` с типом `StockIssued`;
- создать отрицательную строку `stock_ledger_entries`;
- обновить баланс источника.

### 9.8 Stock Audit

В одной транзакции:

- создать `event_log` с типом `StockAuditRecorded`;
- сохранить `stock_audit_observations`;
- для расхождений создать `StockAuditAdjusted`;
- создать соответствующие строки `stock_ledger_entries`;
- обновить `stock_balances`;
- проверить, что ни один баланс не стал отрицательным.

## 10. Защита инвариантов

### Инвариант 1

У оборудования один активный держатель.

Защита:

- `asset_current_state` имеет PK по `item_id`;
- нет отдельных полей "текущий человек" и "текущее место";
- передача блокирует текущую строку состояния и атомарно меняет `holder_id`;
- `AssetTransferred` для нескольких объектов проходит целиком или откатывается целиком.

### Инвариант 2

Физика побеждает цифру через добавление событий.

Защита:

- `event_log` append-only;
- старые события не обновляются и не удаляются;
- коррекции создаются отдельными событиями;
- `correction_of_event_id` связывает исправление с предыдущей записью, если это возможно.

### Инвариант 3

Состояние оборудования не меняется в вакууме.

Защита:

- нет команды свободного изменения `condition`;
- `condition_after` допустимо только в передаче, аудите или коррекции, связанной с передачей/аудитом;
- быстрое наблюдение состояния оформляется как `AssetAuditRecorded`;
- `asset_current_state.condition` меняется только вместе с событием.

### Инвариант 4

`Item_ID` только для индивидуально учитываемых объектов.

Защита:

- `asset_items.item_id` - PK индивидуального оборудования;
- stock-таблицы не используют `item_id`;
- мерч и расходники учитываются через `sku_id`, `lot_id`, `holder_id` и ledger;
- даже если партия не важна, используется технический `lot_id`, а не объединение в `Item`.

## 11. Минимальные индексы

Нужны индексы:

- `asset_current_state(holder_id)`;
- `asset_current_state(due_on)`;
- `asset_qr_codes(code_value)`;
- `event_log(event_type, recorded_at)`;
- `event_log(correction_of_event_id)`;
- `asset_event_items(item_id)`;
- `stock_balances(holder_id)`;
- `stock_balances(sku_id, holder_id)`;
- `stock_ledger_entries(sku_id, lot_id, holder_id)`;
- `stock_ledger_entries(event_id)`.

## 12. Что можно отложить

Для первой версии не нужно:

- отдельная сущность мероприятия;
- сложный workflow ремонта;
- подписи участников аудита;
- геометки аудита;
- отрицательные остатки;
- per-unit учет мерча;
- отдельное доменное событие возврата по QR-коду.

## 13. Технический контекст

Первая версия разворачивается как веб-система в локальной сети студии.

Среда:

- физический хост: Windows 11;
- виртуализация: Hyper-V;
- сервер: Ubuntu Server;
- база данных: PostgreSQL;
- backend: Django;
- frontend: Django templates и HTMX для точечной интерактивности;
- админка: Django admin и внутренние страницы для рабочих операций;
- протокол первой версии: HTTP;
- клиенты: браузеры на телефонах и ноутбуках.

Стартовые данные:

- импорт из CSV/Excel не нужен для первого запуска;
- люди, оборудование, QR-коды, `Студия` и `Склад` заполняются вручную.

Резервные копии:

- PostgreSQL бэкапится на внешний диск в отдельную папку;
- расписание: каждый день в 03:00;
- ротация: хранить 3 последних бэкапа.
