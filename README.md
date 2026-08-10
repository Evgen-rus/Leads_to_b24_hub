# Leads_to_b24_hub

Единый cron-worker для доставки лидов из клиентских Google Sheets в разные Bitrix24. Фронтенда и постоянно работающего HTTP-сервера нет.

Поддерживаемые конфигурации:

- `metalwork`
- `werus`
- `vkc`
- `prolog`
- `hirehub`
- `supermet`
- `tochkap`
- `praktikm`

## Как работает

1. Читает всю клиентскую вкладку Google Sheets.
2. Ищет `Статус_Б24` или `Статус Б24` без учёта регистра, пробелов и `_`.
3. Если колонки нет, добавляет справа `Статус_Б24`.
4. Любая непустая ячейка результата означает: строку не отправлять.
5. Пустые строки обрабатываются независимо от даты — накопленный хвост не теряется.
6. Доставка сохраняется в общей SQLite по ключу `(project_code, source_id)`.
7. После создания лида URL сначала сохраняется в SQLite, затем записывается в Google Sheets.
8. Повторный запуск восстанавливает незавершённую запись ссылки и не создаёт дубликат.

Старые `lr195.db`, `lr182.db` и другие базы клиентских копий не используются.

## Установка на VPS

```bash
cd /opt
git clone <repository-url> Leads_to_b24_hub
cd Leads_to_b24_hub
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
mkdir -p logs data secrets
cp .env.example .env
```

Настройте `.env` на VPS. Реальный `.env` и JSON service account не коммитятся и сохраняются при `git pull`/CI/CD.

Проверка конфигураций без доступа к внешним API:

```bash
./venv/bin/python manage.py validate
./venv/bin/python manage.py validate --environment
./venv/bin/python manage.py list
```

## Безопасный первый запуск

Сначала отключите проект или вызовите его явно в dry-run:

```bash
./venv/bin/python run.py --project praktikm --dry-run --limit 10
```

Dry-run читает таблицу и валидирует будущие поля, но не создаёт колонку, не пишет в Google, не вызывает Bitrix и не создаёт delivery-записи.

После проверки:

```bash
./venv/bin/python run.py --project praktikm
```

## Управление проектами

```bash
./venv/bin/python manage.py list
./venv/bin/python manage.py enable praktikm
./venv/bin/python manage.py disable praktikm
```

`enabled: false` оставляет весь хвост в таблице. После включения будут обработаны все строки с пустым результатом.

## Cron

Один график для всех включённых проектов:

```cron
*/10 8-18 * * * cd /opt/Leads_to_b24_hub && ./venv/bin/python run.py >> logs/cron.log 2>&1
```

Отдельное расписание проекта:

```cron
5 9-17 * * * cd /opt/Leads_to_b24_hub && ./venv/bin/python run.py --project praktikm >> logs/cron.log 2>&1
```

Не добавляйте обе строки одновременно для одного проекта.

## Поля и нестандартные клиенты

Различия хранятся в `config/*.yaml`. Поддерживаются:

```yaml
FIELD_A: {value: постоянное значение}
FIELD_B: {env: VARIABLE_NAME, default: необязательное значение}
FIELD_C: {from: source_lead, omit_if_empty: true}
FIELD_D: {template: "{source_lead}_{phone}"}
PHONE: {phone: true, value_type: WORK}
```

Доступные переменные шаблона: `source_id`, `phone`, `event_dt`, `channel`, `source_lead`, `project_code`, `date`, `date_ddmmyyyy`.

## ПрактикМ LR223

- `STATUS_ID=UC_CT9E1O`
- `SOURCE_ID=14`
- `UTM_SOURCE=leadgenburo_dmp`
- `UTM_MEDIUM={source_lead}` — точное содержимое колонки `Источник`, без телефона
- Пшенникова Дарья: `2751`, вес 3
- Шапочук Людмила: `2767`, вес 2

Цикл `2751, 2751, 2751, 2767, 2767` сохраняется в SQLite между cron-запусками. Дневных лимитов нет.

## CI/CD и секреты

Репозиторий содержит только имена переменных. На VPS отдельно остаются:

- `/opt/Leads_to_b24_hub/.env`;
- service account JSON;
- `data/lead_hub.sqlite3`;
- `logs/`.

Обычный deploy обновляет код и YAML, выполняет `pip install -r requirements.txt` и тесты, но не заменяет `.env`, credentials или SQLite.

## Локальные тесты

```bash
python -m unittest discover -s tests -v
python manage.py validate
```

Тесты используют fake Google/Bitrix gateways и не совершают внешних вызовов.
