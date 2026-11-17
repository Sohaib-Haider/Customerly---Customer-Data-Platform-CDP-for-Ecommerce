from django.db import migrations

TABLES = ("ingestion_sources", "ingestion_batches", "ingestion_files", "ingestion_reviews", "ingestion_segment_jobs")


def enable(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        for table in TABLES:
            schema_editor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def disable(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        for table in TABLES:
            schema_editor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0001_initial")]
    operations = [migrations.RunPython(enable, disable)]
