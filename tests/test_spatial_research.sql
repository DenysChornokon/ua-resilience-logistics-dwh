-- All test rows are rolled back, including on a psql error.
BEGIN;
INSERT INTO spatial_research.delivery_points(delivery_id,region_id,started_at,point,synthetic)
SELECT 'spatial-test-boundary','UA80000000000093317','2026-07-01 00:00:00+00',
       ST_PointN(ST_ExteriorRing(geom),1),true
FROM spatial_research.historical_region;
INSERT INTO spatial_research.delivery_points(delivery_id,region_id,started_at,point,synthetic)
VALUES ('spatial-test-outside','UA80000000000093317','2026-07-01 00:00:00+00',
        ST_SetSRID(ST_MakePoint(0,0),4326),true);
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM spatial_research.delivery_region
                WHERE delivery_id='spatial-test-boundary' AND within_historical_shape AND matched_shape_id IS NOT NULL) THEN
   RAISE EXCEPTION 'Polygon boundary must be included';
 END IF;
 IF NOT EXISTS (SELECT 1 FROM spatial_research.delivery_region
                WHERE delivery_id='spatial-test-outside' AND NOT within_historical_shape AND matched_shape_id IS NULL) THEN
   RAISE EXCEPTION 'Outside point must be retained without a match';
 END IF;
END $$;
ROLLBACK;
