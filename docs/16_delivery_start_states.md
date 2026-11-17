# 16. Стан регіону на момент початку доставки

`scenario.delivery_start_state` реалізує point-in-interval зіставлення: одна пара `scenario_id` і `delivery_id` для доставки того самого регіону, початок якої потрапляє до напіввідкритого вікна сценарію. Це момент `started_at`, не момент створення замовлення: даних про `created_at` у поточному наборі немає.

Виводяться межі відповідного стану, `state_known`, `alert_active` і `outage_active`. Стан вибирається за `state.started_at <= delivery.started_at < state.finished_at`: якщо доставка почалась рівно на межі, діє новий стан. Коли стани неможливо побудувати через відсутнє або неузгоджене завантаження тривог, доставка не зникає: `state_known=false`, а обидві ознаки й межі стану — `NULL`. Доставки інших регіонів або поза вікном не входять до цього сценарію; одна доставка може з'явитися в кількох різних сценаріях.

У сценарії `kyiv_demo_v1` відключення синтетичні, як і самі доставки; часові стани тривог обчислюються з наявної історичної вибірки. `false` щодо відключення означає лише його відсутність у моделі. Погоду не включено. Відсутність запису тривоги в завантажених даних не доводить повного покриття джерела. Це представлення динамічне, без окремого versioned snapshot.

Після виконання `sql/07_multifactor_states.sql` представлення створюється один раз:

```powershell
docker compose exec -T db psql -U dwh_user -d logistics_dwh -v ON_ERROR_STOP=1 -f /sql/08_delivery_start_states.sql
python -m scripts.verify_delivery_start_states
```

Скрипт звіряє кожну доставку з незалежно побудованими інтервалами й безпосередньо з вихідними подіями. Контрольні SQL-сценарії `tests/test_delivery_start_states.sql` охоплюють початкову й кінцеву межі, зміну факторів, інший регіон і відсутність валідного завантаження; тестова транзакція завершується `ROLLBACK`.

У pgAdmin:

```sql
SELECT * FROM scenario.delivery_start_state
WHERE scenario_id='kyiv_demo_v1'
ORDER BY delivery_started_at;
```

```sql
SELECT state_known,alert_active,outage_active,count(*) AS deliveries
FROM scenario.delivery_start_state
WHERE scenario_id='kyiv_demo_v1'
GROUP BY state_known,alert_active,outage_active
ORDER BY state_known,alert_active,outage_active;
```

Не ототожнювати цей стан на момент початку з тривалістю експозиції протягом доставки: остання обчислюється окремо. Зіставлення й статуси не є доказом фактичної затримки або причинного впливу.
