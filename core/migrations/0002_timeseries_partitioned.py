"""Hand-written DDL for `core_timeseries` (D-032): a table declared
`PARTITION BY RANGE (time_tag)` with monthly partitions created lazily by
`core.swxkit._ensure_partition`. The `TimeSeriesPoint` model (core/models.py)
is `managed = False` precisely so 0001's CreateModel is a no-op for it and
`makemigrations --check` never sees drift here — this file is the only
place that touches this table's schema.

A BRIN index suits append-mostly, naturally time-ordered inserts (D-032);
a btree index on (product_id, time_tag) serves "last N points for product
X", the portal's main read pattern. Both are declared on the partitioned
parent, so PostgreSQL 11+ auto-creates a matching index on every partition
added afterwards.
"""

from django.db import migrations

CREATE_SQL = """
CREATE TABLE core_timeseries (
    id BIGSERIAL NOT NULL,
    product_id VARCHAR(128) NOT NULL REFERENCES core_product(id) ON DELETE CASCADE,
    time_tag TIMESTAMPTZ NOT NULL,
    source VARCHAR(64) NOT NULL DEFAULT '',
    payload JSONB NOT NULL,
    quality JSONB,
    provenance JSONB,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (time_tag);

CREATE INDEX core_timeseries_time_brin ON core_timeseries USING BRIN (time_tag);
CREATE INDEX core_timeseries_product_time ON core_timeseries (product_id, time_tag DESC);
"""

DROP_SQL = "DROP TABLE IF EXISTS core_timeseries;"


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
