# 10. Ізольовані таблиці для benchmark

Скрипт `python -m scripts.load_benchmark_datasets` перевіряє manifest, SHA-256, розмір і заголовки трьох CSV із `data/synthetic/benchmark`, а потім завантажує їх в окремі таблиці `benchmark.deliveries_10000`, `benchmark.deliveries_100000`, `benchmark.deliveries_1000000`. У `benchmark.dataset_manifest` зберігаються контрольні суми, seed і час завантаження.

Потрібні запущений сервіс `db`, існуючі CSV і manifest із попереднього кроку. Перевірки обмежень, кількості рядків і `ANALYZE` виконуються в одній транзакції; помилка залишає базу без часткового нового набору. Дані передаються потоково. Скрипт не перезаписує таблиці: якщо вони вже є, він завершується з помилкою. `core`, поточні доставки та тривоги не змінюються.

У pgAdmin перевірити результати:

```sql
SELECT * FROM benchmark.dataset_manifest ORDER BY rows_expected;
SELECT '10k' AS dataset, count(*) FROM benchmark.deliveries_10000
UNION ALL SELECT '100k', count(*) FROM benchmark.deliveries_100000
UNION ALL SELECT '1m', count(*) FROM benchmark.deliveries_1000000;
```

Набори синтетичні; менші є префіксами більшого. Джерельні тривоги залишаються в `core.fact_alert`; ці таблиці ще не містять результату об'єднання, і вимірювання швидкодії не проводилися. Перевірка файлів за контрольними сумами гарантує відповідність manifest, але довіру до самого manifest потрібно забезпечувати окремо.
