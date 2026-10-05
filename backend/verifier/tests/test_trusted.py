from unittest import mock
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from verifier import verdict as V
from verifier.models import GradeMapping, TrustedGrader
from verifier.services import verify_claim

from .test_dorar_providers import SETTINGS

T = V.Thresholds()


class TrustedVerdictTests(TestCase):
    def test_rules(self):
        d = V.decide_with_trusted
        # صحّحه الألباني وضعّفه محدّث غير معتمد: تأكيد
        self.assertEqual(d(0.95, [("الألباني", "sahih"), (None, "daif")], T),
                         (V.CONFIRM, "صحّحه الألباني"))
        # اتفاق معتمدَين
        code, summ = d(0.95, [("الألباني", "sahih"), ("أحمد شاكر", "sahih")], T)
        self.assertEqual((code, summ), (V.CONFIRM, "صحّحه الألباني وأحمد شاكر"))
        # اختلاف المعتمدين: تعارض مع خلاصة الطرفين
        code, summ = d(0.95, [("الألباني", "daif"), ("شعيب الأرناؤوط", "hasan")], T)
        self.assertEqual(code, V.CONFLICT)
        self.assertEqual(summ, "ضعّفه الألباني، وحسّنه شعيب الأرناؤوط")
        # تضعيف المعتمد
        self.assertEqual(d(0.95, [("الألباني", "mawdu"), (None, "sahih")], T)[0], V.MAWDU)
        # الحكم على الإسناد وحده لا يُعدّ تصحيحاً
        code, summ = d(0.95, [("شعيب الأرناؤوط", "isnad")], T)
        self.assertEqual((code, summ), (V.ISNAD, "حكم على إسناده شعيب الأرناؤوط"))
        # بلا معتمد: القاعدة العامة
        self.assertEqual(d(0.95, [(None, "sahih"), (None, "daif")], T)[0], V.CONFLICT)
        self.assertEqual(d(0.3, [("الألباني", "sahih")], T)[0], V.ABSTAIN)

    def test_seeded_and_alias(self):
        names = set(TrustedGrader.objects.values_list("name", flat=True))
        self.assertEqual(names, {"الألباني", "أحمد شاكر", "شعيب الأرناؤوط"})
        self.assertTrue(TrustedGrader.objects.get(name="شعيب الأرناؤوط").matches("شعيب الارنؤوط"))


TEXT = "نص تجريبي عن فضل العلم والعمل به ونشره بين الناس بالحكمة"


def _e(muhaddith, grade):
    return (f'<div class="hadith">{TEXT}</div><div class="hadith-info">'
            f'<span class="info-subtitle">المحدث:</span> <span>{muhaddith}</span>'
            f'<span class="info-subtitle">المصدر:</span> <span>كتاب</span>'
            f'<span class="info-subtitle">خلاصة حكم المحدث:</span> <span>{grade}</span></div>')


class TrustedServiceTests(TestCase):
    @mock.patch("verifier.dorar.requests.get")
    def test_summary_from_dorar(self, get):
        for p, c, pr in (("ضعيف", "daif", 40), ("صحيح", "sahih", 60)):
            GradeMapping.objects.create(phrase=p, match_type="contains", grade_class=c, priority=pr, reviewed=True)
        get.return_value.json.return_value = {"ahadith": {"result": _e("ابن الجوزي", "ضعيف") + _e("الألباني", "صحيح")}}
        get.return_value.raise_for_status.return_value = None
        with tempfile.TemporaryDirectory() as d, override_settings(MUTHBIT={**SETTINGS, "INDEX_DIR": Path(d)}):
            res = verify_claim(TEXT)
        self.assertEqual(res["verdict"], V.CONFIRM)
        self.assertEqual(res["summary"], "صحّحه الألباني")


class TrustedUnmappedTests(TestCase):
    @mock.patch("verifier.dorar.requests.get")
    def test_raw_trusted_grades_shown_first_when_unmapped(self, get):
        html = _e("شعيب الأرناؤوط", "[فيه] فلان لم يوثقه غير ابن حبان") + _e("البوصيري", "[فيه] فلان ضعيف") \
            + _e("الألباني", "ضعيف جداً") + _e("الألباني", "ضعيف")
        get.return_value.json.return_value = {"ahadith": {"result": html}}
        get.return_value.raise_for_status.return_value = None
        with tempfile.TemporaryDirectory() as d, override_settings(MUTHBIT={**SETTINGS, "INDEX_DIR": Path(d)}):
            res = verify_claim(TEXT)
        self.assertEqual(res["verdict"], V.ABSTAIN)          # لا جدول مُراجَع: لا خلاصة مصنّفة
        self.assertIn("الألباني: ضعيف جداً", res["summary"])
        self.assertEqual(res["match"]["grader"], "الألباني")  # المعتمد أولاً في العرض

    @mock.patch("verifier.dorar.requests.get")
    def test_albani_two_weak_grades(self, get):
        GradeMapping.objects.create(phrase="ضعيف", match_type="contains", grade_class="daif", priority=40, reviewed=True)
        GradeMapping.objects.create(phrase="صحيح", match_type="contains", grade_class="sahih", priority=60, reviewed=True)
        html = _e("الزرقاني", "صحيح") + _e("الألباني", "ضعيف جداً") + _e("الألباني", "ضعيف")
        get.return_value.json.return_value = {"ahadith": {"result": html}}
        get.return_value.raise_for_status.return_value = None
        with tempfile.TemporaryDirectory() as d, override_settings(MUTHBIT={**SETTINGS, "INDEX_DIR": Path(d)}):
            res = verify_claim(TEXT)
        self.assertEqual((res["verdict"], res["summary"]), (V.DAIF, "ضعّفه الألباني"))
