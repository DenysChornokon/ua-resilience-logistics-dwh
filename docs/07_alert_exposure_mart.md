# 07. Вітрина експозиції доставок до тривог

`mart.delivery_alert_exposure` — SQL-представлення з одним рядком на доставку та полями `delivery_id`, `alert_overlap_seconds`, `alert_exposure_ratio`, `overlap_alert_count`. Це поточне представлення над `core`, а не матеріалізована таблиця: показники оновлюються під час запиту, окремого `build_id` і знімка ще немає.

Застосувати після завантаження даних:

```powershell
docker compose exec -T db psql -U dwh_user -d logistics_dwh -v ON_ERROR_STOP=1 -f /sql/02_delivery_alert_exposure.sql
python -m scripts.verify_alert_exposure
```

Перша команда створює або оновлює представлення. Друга порівнює всі рядки з Python-алгоритмом і CSV. У pgAdmin вибери `logistics_dwh` → Query Tool:

```sql
SELECT * FROM mart.delivery_alert_exposure ORDER BY delivery_id LIMIT 20;
```

```sql
SELECT COUNT(*) AS deliveries,
       COUNT(*) FILTER (WHERE alert_overlap_seconds > 0) AS exposed,
       SUM(alert_overlap_seconds) AS total_exposure_seconds
FROM mart.delivery_alert_exposure;
```

Тривалість рахується за об'єднанням перетинних та дотичних інтервалів одного регіону, щоб уникнути подвійного підрахунку. Кількість рахується за вихідними подіями; дотик меж доставки і тривоги не є перетином. За відсутності успішного завантаження тривог або невідповідності кількості рядків представлення повертає порожній результат. Це не доводить повного охоплення джерела: до його підтвердження нульова експозиція інтерпретується лише для завантаженого набору.

Доставки синтетичні й не свідчать про реальні затримки. Погода не входить у цей розрахунок. SQL-тест `tests/test_delivery_alert_exposure.sql` використовує тимчасові тестові записи в транзакції та завершується `ROLLBACK`; його запускати через `psql -v ON_ERROR_STOP=1` після створення представлення.
