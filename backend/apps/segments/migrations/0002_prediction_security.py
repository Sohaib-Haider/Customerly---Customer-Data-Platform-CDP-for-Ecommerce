"""Keep Supabase Data API roles out of server-owned prediction results."""

from django.db import migrations


def secure_predictions(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    quote = schema_editor.quote_name
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("ALTER TABLE segment_predictions ENABLE ROW LEVEL SECURITY")
        # Replace only this new table's generated FK constraints. Existing
        # canonical tables/columns and their constraints remain untouched.
        cursor.execute("""SELECT conname FROM pg_constraint
                          WHERE conrelid = 'segment_predictions'::regclass AND contype = 'f'""")
        for (name,) in cursor.fetchall():
            cursor.execute("ALTER TABLE segment_predictions DROP CONSTRAINT " + quote(name))
        for column, table in (("store_id", "stores"), ("customer_id", "customers"), ("segment_id", "segments")):
            cursor.execute("ALTER TABLE segment_predictions ADD CONSTRAINT " + quote("fk_prediction_" + column)
                + " FOREIGN KEY (" + quote(column) + ") REFERENCES " + quote(table)
                + " (id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED")


class Migration(migrations.Migration):
    dependencies = [("segments", "0001_initial")]
    operations = [migrations.RunPython(secure_predictions, migrations.RunPython.noop)]
