from django.test import TestCase


class HealthTests(TestCase):
    def test_health_reports_all_services(self):
        response = self.client.get('/api/health/')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(set(body['checks']), {'database', 'cache', 'channel_layer', 'storage'})

    def test_health_only_allows_get(self):
        self.assertEqual(self.client.post('/api/health/', HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 405)


class ApiErrorTests(TestCase):
    def test_unknown_api_path_returns_json_404(self):
        response = self.client.get('/api/does-not-exist/')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'not_found')
