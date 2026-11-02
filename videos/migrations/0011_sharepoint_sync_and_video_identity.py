import django.db.models.deletion
from django.db import migrations, models


def create_analysis_slots(apps, schema_editor):
    slot_model = apps.get_model('videos', 'SharePointAnalysisSlot')
    slot_model.objects.using(schema_editor.connection.alias).bulk_create(
        [slot_model(slot_number=1), slot_model(slot_number=2)]
    )


class Migration(migrations.Migration):
    dependencies = [('videos', '0010_video_source_folder')]

    operations = [
        migrations.AlterField(
            model_name='sharepointimport',
            name='batch_size',
            field=models.PositiveSmallIntegerField(default=2),
        ),
        migrations.AddField(
            model_name='video',
            name='source_drive_id',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='video',
            name='source_item_id',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='video',
            name='source_modified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='video',
            name='retry_count',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='video',
            name='analysis_completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sharepointimport',
            name='trigger',
            field=models.CharField(
                choices=[('manual', 'Manual'), ('scheduled', 'Scheduled')],
                default='manual',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='sharepointimport',
            name='target_video',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='retry_imports',
                to='videos.video',
            ),
        ),
        migrations.CreateModel(
            name='SharePointSyncState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('last_attempt_at', models.DateTimeField(blank=True, null=True)),
                ('last_success_at', models.DateTimeField(blank=True, null=True)),
                ('next_sync_at', models.DateTimeField(blank=True, null=True)),
                ('discovered_count', models.PositiveIntegerField(default=0)),
                ('last_error', models.CharField(blank=True, max_length=500)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='SharePointAnalysisSlot',
            fields=[
                ('slot_number', models.PositiveSmallIntegerField(primary_key=True, serialize=False)),
                ('lease_token', models.UUIDField(blank=True, null=True)),
                ('lease_expires_at', models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.RunPython(create_analysis_slots, migrations.RunPython.noop),
    ]
