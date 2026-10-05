from django.contrib.auth.models import User
from django.test import Client, TestCase


class CsrfTests(TestCase):
    """من سجّل الدخول إلى /admin على النطاق نفسه يجب أن يستطيع استعمال الواجهة دون رمز CSRF."""

    def test_logged_in_admin_can_verify(self):
        User.objects.create_superuser("admin", "", "pass")
        c = Client(enforce_csrf_checks=True)
        c.login(username="admin", password="pass")
        r = c.post("/api/verify", {"text": "نص تجريبي", "extract": False}, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        r = c.post("/api/reports", {"claim": "x", "verdict": "abstain"}, content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
