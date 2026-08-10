# Leads_to_b24_hub architecture

## Source of Truth

| Область | Источник истины |
| --- | --- |
| Клиентские настройки без секретов | `config/*.yaml` |
| Секреты и ссылки на таблицы | переменные окружения / `.env` на VPS |
| Чтение и запись Google Sheets | `lead_hub/google_sheets.py` |
| Создание и поиск лидов Bitrix | `lead_hub/bitrix.py` |
| Сборка полей Bitrix | `lead_hub/fields.py` |
| Доставка и порядок действий | `lead_hub/engine.py` |
| SQLite и распределение | `lead_hub/storage.py` |
| Cron entrypoint | `run.py` |
| Enable/disable и проверка | `manage.py` |

## Critical Invariants

- Google-таблица клиента — источник входных строк и ручной отметки об отправке.
- Любая непустая отметка результата означает skip без изменения её содержимого.
- Новая SQLite стартует с нуля; старые базы клиентских репозиториев не читаются.
- Пустые строки результата обрабатываются независимо от даты. Date lookback отсутствует.
- Сначала фиксируется созданный Bitrix-лид в SQLite, затем записывается URL в Google.
- При неопределённом результате `crm.lead.add` следующий запуск сначала ищет лид по origin fields.
- Секреты и персональные данные не попадают в YAML, Git и логи.

## Processing Flow

1. `run.py` загружает включённые YAML-конфигурации.
2. Google gateway читает всю вкладку и находит результат по нормализованному заголовку.
3. При отсутствии результата gateway добавляет справа `Статус_Б24`.
4. Engine пропускает любую строку с непустым результатом.
5. Для пустой строки SQLite закрепляет ответственного и delivery state.
6. Bitrix gateway ищет `ORIGINATOR_ID + ORIGIN_ID`, затем при необходимости вызывает `crm.lead.add`.
7. SQLite сохраняет ID/URL, Google получает URL, delivery становится completed.
8. Если Google недоступен, delivery остаётся created и следующий запуск повторяет только Google write.

## Client Configuration

Обычные различия описываются декларативно:

- `value` — постоянное значение;
- `env` + optional `default` — значение из окружения;
- `from` — значение из нормализованной входной колонки;
- `template` — строка с переменными строки;
- `phone: true` — Bitrix multi-field PHONE.

Новый Python-обработчик допустим только для поведения, которое нельзя выразить этими правилами.
