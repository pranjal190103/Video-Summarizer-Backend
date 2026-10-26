from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('videos', '0007_sharepointimport_cancellation_status')]

    operations = [
        migrations.AddField(
            model_name='sharepointimport',
            name='folder_total',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
