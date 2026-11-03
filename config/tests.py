from django.test import SimpleTestCase
from django.urls import reverse


class HomePageTests(SimpleTestCase):
    def test_root_displays_welcome_and_api_overview(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome to Video Summarizer")
        self.assertContains(response, "GET /api/health/")
        self.assertContains(response, "Authorization: Token")
