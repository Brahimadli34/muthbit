"""اختبارات وحدات. النصوص هنا تجريبية مصطنعة عمداً، وليست نصوصاً شرعية."""
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from verifier import verdict as V
from verifier.arabic import containment, normalize
from verifier.extraction import _is_grounded, heuristic_extract
from verifier.models import Source, Text


class NormalizeTests(TestCase):
    def test_diacritics_and_letters(self):
        self.assertEqual(normalize("إِنَّمَا الأَعْمَالُ"), normalize("انما الاعمال"))
        self.assertEqual(normalize("مدرسةٌ على"), "مدرسه علي")

    def test_honorifics_removed(self):
        self.assertEqual(normalize("قال النبي ﷺ: الكلمة"), normalize("قال النبي صلى الله عليه وسلم الكلمة"))

    def test_containment_fragment(self):
        doc = normalize("هذه جملة تجريبية طويلة تحتوي على مقطع مقتبس في وسطها ثم تستمر")
        self.assertGreater(containment(normalize("مقطع مقتبس في وسطها"), doc), 0.95)


class VerdictTests(TestCase):
    t = V.Thresholds(exact=0.85, match=0.55)

    def test_abstain_below_threshold(self):
        self.assertEqual(V.decide(0.4, "sahih", False, self.t), V.ABSTAIN)
        self.assertEqual(V.decide(0.9, None, False, self.t), V.ABSTAIN)

    def test_classes(self):
        self.assertEqual(V.decide(0.95, "sahih", False, self.t), V.CONFIRM)
        self.assertEqual(V.decide(0.7, "sahih", False, self.t), V.CONFIRM_DIFF_WORDING)
        self.assertEqual(V.decide(0.95, "daif", False, self.t), V.DAIF)
        self.assertEqual(V.decide(0.95, "la_asl", False, self.t), V.MAWDU)
        self.assertEqual(V.decide(0.95, "sahih", True, self.t), V.CONFLICT)
        self.assertEqual(V.decide(0.95, "misattributed", False, self.t), V.MISATTRIBUTED)

    def test_weak_never_confirmed(self):
        for s in (0.56, 0.8, 1.0):
            for g in ("daif", "mawdu", "la_asl", "misattributed"):
                self.assertNotIn(V.decide(s, g, False, self.t), V.CONFIRMING)


class ExtractionTests(TestCase):
    def test_grounding_rejects_invented_text(self):
        post = normalize("صباح الخير. قال: «جملة تجريبية أولى هنا» انشرها")
        self.assertTrue(_is_grounded("جملة تجريبية أولى هنا", post))
        self.assertFalse(_is_grounded("جملة لم ترد في المنشور", post))

    def test_heuristic_quotes(self):
        claims = heuristic_extract("مرحبا «النص التجريبي الأول هنا» ثم «النص التجريبي الثاني هنا»")
        self.assertEqual(len(claims), 2)


class ApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        src = Source.objects.create(name="مصدر تجريبي", edition="ط1")
        Text.objects.create(source=src, kind="hadith", number="1", grade="صحيح", grade_class="sahih",
                            text="نص تجريبي أول عن فضل العلم والعمل به ونشره بين الناس بالحكمة")
        Text.objects.create(source=src, kind="hadith", number="2", grade="موضوع", grade_class="mawdu",
                            text="نص تجريبي ثان مكذوب عن ثواب خيالي لمن نشر الرسالة عشر مرات")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = override_settings(MUTHBIT={
            "INDEX_DIR": Path(self.tmp.name), "USE_EMBEDDINGS": False, "EMBEDDING_MODEL": "",
            "THRESHOLD_EXACT": 0.85, "THRESHOLD_MATCH": 0.55, "LEXICAL_WEIGHT": 0.6,
            "MAX_INPUT_CHARS": 4000, "LLM_MODEL": "none", "DORAR_ENABLED": False,
            "GRADE_MAP_REVIEWED_ONLY": True,
        })
        self.ctx.enable()
        call_command("build_index", verbosity=0)
        self.client = APIClient()

    def tearDown(self):
        self.ctx.disable()
        self.tmp.cleanup()

    def _verify(self, text):
        r = self.client.post("/api/verify", {"text": text, "extract": False}, format="json")
        self.assertEqual(r.status_code, 200)
        return r.json()["results"][0]

    def test_exact_confirm(self):
        res = self._verify("نصٌّ تجريبيٌّ أول عن فضل العلم والعمل به ونشره بين الناس بالحكمة")
        self.assertEqual(res["verdict"], V.CONFIRM)
        self.assertEqual(res["match"]["number"], "1")

    def test_fragment_confirm(self):
        res = self._verify("فضل العلم والعمل به ونشره بين الناس")
        self.assertEqual(res["verdict"], V.CONFIRM)

    def test_mawdu(self):
        res = self._verify("نص تجريبي ثان مكذوب عن ثواب خيالي لمن نشر الرسالة عشر مرات")
        self.assertEqual(res["verdict"], V.MAWDU)

    def test_unknown_abstains(self):
        res = self._verify("كلام لا علاقة له بأي شيء في قاعدة البيانات إطلاقاً يا صديقي")
        self.assertEqual(res["verdict"], V.ABSTAIN)
        self.assertIsNone(res["match"])

    def test_report(self):
        r = self.client.post("/api/reports", {"claim": "x", "verdict": "abstain", "comment": "y"}, format="json")
        self.assertEqual(r.status_code, 201)

    def test_input_limit(self):
        r = self.client.post("/api/verify", {"text": "ا" * 5000}, format="json")
        self.assertEqual(r.status_code, 400)
