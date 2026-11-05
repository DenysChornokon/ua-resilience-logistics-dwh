# 08. Еталон для експериментів PostgreSQL

`mart.delivery_alert_exposure_reference` — контрольний запит зі звичайним порівнянням меж; він окремо об'єднує перетини для кожної доставки і рахує кількість початкових тривог. На відміну від наявної вітрини він не застосовує діапазонні типи. Це еталон коректності, а не висновок про швидкодію.

Після завантаження даних і створення поточної вітрини:

```powershell
docker compose exec -T db psql -U dwh_user -d logistics_dwh -v ON_ERROR_STOP=1 -f /sql/03_alert_exposure_reference.sql
```

Порівняти всі колонки і рядки в pgAdmin:

```sql
(SELECT * FROM mart.delivery_alert_exposure EXCEPT SELECT * FROM mart.delivery_alert_exposure_reference)
UNION ALL
(SELECT * FROM mart.delivery_alert_exposure_reference EXCEPT SELECT * FROM mart.delivery_alert_exposure);
```

Очікується нуль рядків. Крайові випадки в `tests/test_reference_comparison.sql` додаються всередині транзакції та скасовуються за допомогою `ROLLBACK`.

Для майбутнього benchmark варіанти мають повертати однакові результати для одного набору і включати однакову роботу. Записувати розміри наборів і контрольні суми, версії, плани `EXPLAIN (ANALYZE, BUFFERS)`, метод вимірювання й повторення; розрізняти попередню підготовку та сам запит. Порівнювати медіани повторів і не стверджувати перевагу GiST до вимірювання. На цьому етапі індекси не додавались, навантажувальні набори й benchmark не виконувалися. Покриття історії тривог джерелом не підтверджене; доставки синтетичні.
