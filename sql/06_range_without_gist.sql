BEGIN;
-- A copy preserves the indexed source and the original experiment.
CREATE TABLE benchmark.alert_events_no_gist (LIKE benchmark.alert_events INCLUDING DEFAULTS INCLUDING CONSTRAINTS);
INSERT INTO benchmark.alert_events_no_gist SELECT * FROM benchmark.alert_events;
ALTER TABLE benchmark.alert_events_no_gist ADD PRIMARY KEY(event_id);
ANALYZE benchmark.alert_events_no_gist;
DO $test$
BEGIN
 IF EXISTS ((SELECT * FROM benchmark.alert_events EXCEPT ALL SELECT * FROM benchmark.alert_events_no_gist)
     UNION ALL (SELECT * FROM benchmark.alert_events_no_gist EXCEPT ALL SELECT * FROM benchmark.alert_events)) THEN
   RAISE EXCEPTION 'Alert copies differ';
 END IF;
END $test$;

-- Derive control views from the actual indexed definitions, changing only the source table.
DO $views$
DECLARE n integer; original text; copy_definition text;
BEGIN
 FOREACH n IN ARRAY ARRAY[10000,100000,1000000] LOOP
   SELECT pg_get_viewdef(format('benchmark.range_%s',n)::regclass, true) INTO original;
   IF (length(original) - length(replace(original,'benchmark.alert_events',''))) / length('benchmark.alert_events') <> 1 THEN
     RAISE EXCEPTION 'Unexpected indexed view definition for %', n;
   END IF;
   copy_definition := replace(original,'benchmark.alert_events','benchmark.alert_events_no_gist');
   EXECUTE format('CREATE VIEW benchmark.%I AS %s', 'range_no_gist_' || n, copy_definition);
 END LOOP;
END $views$;
COMMIT;
