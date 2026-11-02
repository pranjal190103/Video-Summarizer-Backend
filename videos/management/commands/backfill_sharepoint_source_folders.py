from django.core.management.base import BaseCommand

from videos.models import Video
from videos.sharepoint import GraphClient


class Command(BaseCommand):
    help = 'Populate source folder paths on existing SharePoint video records without analyzing them.'

    def handle(self, *args, **options):
        graph = GraphClient()
        try:
            items = graph.list_videos()
        finally:
            graph.close()

        paths_by_url = {
            str(item.get('webUrl')): str(item.get('sourceFolder') or '')[:500]
            for item in items if item.get('webUrl')
        }
        updated = 0
        for source_url, source_folder in paths_by_url.items():
            if source_folder:
                updated += Video.objects.filter(
                    source_type='sharepoint', source_url=source_url,
                ).exclude(source_folder=source_folder).update(source_folder=source_folder)
        self.stdout.write(self.style.SUCCESS(
            f'Updated source folder paths on {updated} existing video record(s).'
        ))
