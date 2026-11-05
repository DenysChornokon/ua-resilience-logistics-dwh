# 17. Синтетичні сценарії відключень

Три відтворювані сценарії містять 50, 250 і 1 000 модельованих інтервалів відключень у Києві за `[2026-07-01, 2026-10-01)` UTC. Це не фактичні відключення і не модель реального графіка. Початок і тривалість генеруються незалежно від тривог; тривалість 30–240 хвилин. Фіксоване `seed=42`; сценарії є вкладеними префіксами одного потоку подій і призначені для порівняння кількості подій, а не незалежних вибірок.

```powershell
python -m scripts.generate_outage_scenarios
python -m scripts.load_outage_scenarios
python -m scripts.verify_outage_scenarios
```

Перший скрипт створює CSV і manifest з SHA-256 у `data/synthetic/outage_scenarios/` (виключено з Git). Він не перезаписує файли; `--output-dir` і `--seed` дозволяють створити окремий набір. Перед імпортом loader перевіряє тип джерела, ідентифікатори, хеші, кількості, межі й тривалість. Усі сценарії та журнал їх походження завантажуються однією транзакцією в `scenario.definitions`, `scenario.outage_events`, `scenario.synthetic_source_manifest`; наявні сценарії не перезаписуються. При повторному запуску імпорту через наявні ідентифікатори транзакція скасовується.

Сценарії використовують існуюче представлення `scenario.region_state`, яке зберігає окремі ознаки історичних тривог і синтетичних відключень. Перевірка звіряє всі події зі збереженими CSV, метадані з manifest і кожен сегмент стану з незалежним Python-еталоном. Погода не входить у ці сценарії.

У pgAdmin:

```sql
SELECT scenario_id, event_count, seed, sha256
FROM scenario.synthetic_source_manifest ORDER BY event_count;
```

```sql
SELECT scenario_id, alert_active, outage_active,
       count(*) AS states, sum(duration_seconds) AS seconds
FROM scenario.region_state
WHERE scenario_id LIKE 'kyiv_synthetic_%_v1'
GROUP BY scenario_id, alert_active, outage_active
ORDER BY scenario_id, alert_active, outage_active;
```

Часові частки не інтерпретуються як реальна частота знеструмлень. Завантаження і побудова станів — перевірка моделі; порівняння швидкодії на цих сценаріях ще не виконувалось. Реальні та синтетичні джерела потрібно продовжувати відрізняти за походженням.
