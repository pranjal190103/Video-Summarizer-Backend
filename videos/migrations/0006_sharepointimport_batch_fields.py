from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('videos', '0005_sharepointimport')]

    operations = [
        migrations.AddField(
            model_name='sharepointimport',
            name='batch_size',
            field=models.PositiveSmallIntegerField(default=10),
        ),
        migrations.AddField(
            model_name='sharepointimport',
            name='remaining',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='sharepointimport',
            name='has_more',
            field=models.BooleanField(default=True),
        ),
        migrations.RunSQL(
            sql="UPDATE videos_sharepointimport SET has_more = 0 WHERE status IN ('completed', 'completed_with_errors')",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
