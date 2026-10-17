"""Hand-written DDL for `core_forecast` (D-072), the same pattern as
`core_timeseries` (0002, D-032): `PARTITION BY RANGE (issued_at)`, with
monthly partitions created lazily by `core.swxkit._ensure_partition`. The
`Forecast` model is `managed = False`, so this file is the only place that
touches the table's schema.

The unique key is what makes `publish_forecast` idempotent
(`ON CONFLICT DO NOTHING`); PostgreSQL requires it to contain the
partition key, which it does. BRIN on `issued_at` suits time-ordered
inserts; the btree on (product_id, lead_seconds, valid_at) serves the
verification reads (one lead, a window of valid times). Both are declared
on the parent, so every partition added later gets them too.
"""

from django.db import migrations

CREATE_SQL = """
CREATE TABLE core_forecast (
    id BIGSERIAL NOT NULL,
    product_id VARCHAR(128) NOT NULL REFERENCES core_product(id) ON DELETE CASCADE,
    model_version VARCHAR(64) NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL,
    valid_at TIMESTAMPTZ NOT NULL,
    lead_seconds INTEGER NOT NULL,
    site VARCHAR(64) NOT NULL DEFAULT '',
    "values" JSONB NOT NULL,
    provenance JSONB NOT NULL,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT core_forecast_unique_issue UNIQUE (product_id, issued_at, valid_at, site, model_version)
) PARTITION BY RANGE (issued_at);

CREATE INDEX core_forecast_issued_brin ON core_forecast USING BRIN (issued_at);
CREATE INDEX core_forecast_verify ON core_forecast (product_id, lead_seconds, valid_at);
"""

DROP_SQL = "DROP TABLE IF EXISTS core_forecast;"


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_product_description_en_product_description_es_and_more"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
