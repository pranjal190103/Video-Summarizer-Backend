from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('videos', '0006_sharepointimport_batch_fields')]

    operations = [
        migrations.AlterField(
            model_name='sharepointimport',
            name='status',
            field=models.CharField(
                choices=[
                    ('queued', 'Queued'),
                    ('processing', 'Processing'),
                    ('cancelling', 'Stopping'),
                    ('cancelled', 'Stopped'),
                    ('completed', 'Completed'),
                    ('completed_with_errors', 'Completed with errors'),
                    ('failed', 'Failed'),
                ],
                default='queued',
                max_length=24,
            ),
        ),
    ]
