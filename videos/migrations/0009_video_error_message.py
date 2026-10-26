import os

from django.db import migrations, models


def backfill_sharepoint_errors(apps, schema_editor):
    Video = apps.get_model('videos', 'Video')
    SharePointImport = apps.get_model('videos', 'SharePointImport')
    database = schema_editor.connection.alias
    for job in SharePointImport.objects.using(database).select_related('owner').order_by('created_at'):
        for error in job.errors or []:
            name = str(error.get('title') or '').strip()
            detail = str(error.get('detail') or '').strip()
            if not name or not detail:
                continue
            title = os.path.splitext(os.path.basename(name))[0]
            Video.objects.using(database).filter(
                owner_id=job.owner_id, source_type='sharepoint', status='failed', title__iexact=title,
            ).update(error_message=detail[:500])


class Migration(migrations.Migration):
    dependencies = [('videos', '0008_sharepointimport_folder_total')]

    operations = [
        migrations.AddField(
            model_name='video',
            name='error_message',
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.RunPython(backfill_sharepoint_errors, migrations.RunPython.noop),
    ]
