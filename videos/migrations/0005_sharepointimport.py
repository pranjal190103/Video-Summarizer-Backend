import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('videos', '0004_video_owner'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SharePointImport',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('folder_name', models.CharField(max_length=255)),
                ('folder_url', models.URLField(blank=True)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('processing', 'Processing'), ('completed', 'Completed'), ('completed_with_errors', 'Completed with errors'), ('failed', 'Failed')], default='queued', max_length=24)),
                ('total', models.PositiveIntegerField(default=0)),
                ('processed', models.PositiveIntegerField(default=0)),
                ('imported', models.PositiveIntegerField(default=0)),
                ('failed', models.PositiveIntegerField(default=0)),
                ('errors', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sharepoint_imports', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
