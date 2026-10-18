"""D-080: `core_timeseries.revised_at`, set when a revisable product's row
is updated in place (null until then). Hand-written DDL like 0002: the
model is `managed = False`, so the AddField below only updates Django's
state and the RunSQL changes the table (and every partition with it)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_source_links_reference_fallback"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE core_timeseries ADD COLUMN revised_at TIMESTAMPTZ;",
            reverse_sql="ALTER TABLE core_timeseries DROP COLUMN revised_at;",
        ),
        migrations.AddField(
            model_name="timeseriespoint",
            name="revised_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
