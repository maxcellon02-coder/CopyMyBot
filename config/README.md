# config/

Сюда кладите `service_account.json` — ключ сервисного аккаунта Google.

## Как получить:
1. Google Cloud Console → IAM & Admin → Service Accounts → Create
2. Дать роль "Editor" или отдельно "Sheets API"
3. Keys → Add Key → JSON → скачать → переименовать в `service_account.json`
4. Открыть Google Таблицу "Maxcellon Заявки" → Поделиться → добавить email сервисного аккаунта как Редактор

Файл `service_account.json` добавлен в .gitignore (не попадёт в репозиторий).
