"""اختبارات الربط مع الدرر والنماذج. النصوص والأحكام هنا تجريبية مصطنعة."""
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from verifier import dorar
from verifier import verdict as V
from verifier.admin import AIProviderForm
from verifier.extraction import extract_claims
from verifier.llm_client import LLMError, ProviderConfig, complete
from verifier.models import AIProvider, GradeMapping

# بنية مطابقة لما ترجعه خدمة الدرر: متن ثم .hadith-info بعناوين .info-subtitle
SAMPLE_HTML = """
<div class="hadith">1 - نص تجريبي أول عن فضل العلم والعمل به ونشره بين الناس</div>
<div class="hadith-info">
 <span class="info-subtitle">الراوي:</span> راو تجريبي
 <span class="info-subtitle">المحدث:</span> <span>محدث أ</span> -
 <span class="info-subtitle">المصدر:</span> <span>كتاب تجريبي</span> -
 <span class="info-subtitle">الصفحة أو الرقم:</span> <span>12</span>
 <br><span class="info-subtitle">خلاصة حكم المحدث:</span> <span>[صحيح]</span>
</div>
<div class="hadith">2 - نص تجريبي أول عن فضل العلم والعمل به ونشره بين الناس</div>
<div class="hadith-info">
 <span class="info-subtitle">الراوي:</span> راو تجريبي
 <span class="info-subtitle">المحدث:</span> <span>محدث ب</span> -
 <span class="info-subtitle">المصدر:</span> <span>كتاب آخر</span> -
 <span class="info-subtitle">الصفحة أو الرقم:</span> <span>99</span>
 <br><span class="info-subtitle">خلاصة حكم المحدث:</span> <span>ضعيف جداً</span>
</div>
"""

SETTINGS = {
    "USE_EMBEDDINGS": False, "EMBEDDING_MODEL": "", "THRESHOLD_EXACT": 0.85, "THRESHOLD_MATCH": 0.55,
    "LEXICAL_WEIGHT": 0.6, "MAX_INPUT_CHARS": 4000, "LLM_MODEL": "none", "DORAR_ENABLED": True,
    "DORAR_TIMEOUT": 5, "DORAR_CACHE_HOURS": 1, "DORAR_QUERY_WORDS": 8, "GRADE_MAP_REVIEWED_ONLY": True,
}


def _map(phrase, cls, prio, mt="contains", reviewed=True):
    return GradeMapping.objects.create(phrase=phrase, match_type=mt, grade_class=cls,
                                       priority=prio, reviewed=reviewed)


class ParserTests(TestCase):
    def test_parse(self):
        items = dorar.parse_result_html(SAMPLE_HTML)
        self.assertEqual(len(items), 2)
        a = items[0]
        self.assertTrue(a.text.startswith("نص تجريبي أول"))
        self.assertEqual((a.muhaddith, a.book, a.number, a.grade), ("محدث أ", "كتاب تجريبي", "12", "[صحيح]"))
        self.assertEqual(items[1].grade, "ضعيف جداً")

    def test_parse_entities(self):
        escaped = SAMPLE_HTML.replace("<", "&lt;").replace(">", "&gt;")
        self.assertEqual(len(dorar.parse_result_html(escaped)), 2)

    def test_parse_empty(self):
        self.assertEqual(dorar.parse_result_html(""), [])


class GradeMapTests(TestCase):
    def setUp(self):
        _map("إسناده", "isnad", 20)
        _map("ليس بصحيح", "daif", 15)
        _map("ضعيف", "daif", 40)
        _map("صحيح", "sahih", 60)

    def test_priority_order(self):
        self.assertEqual(dorar.classify_grade("[صحيح]"), "sahih")
        self.assertEqual(dorar.classify_grade("إسناده صحيح"), "isnad")
        self.assertEqual(dorar.classify_grade("ليس بصحيح"), "daif")
        self.assertEqual(dorar.classify_grade("ضعيفٌ جداً"), "daif")
        self.assertIsNone(dorar.classify_grade("عبارة غير معروفة"))


class GroupVerdictTests(TestCase):
    t = V.Thresholds()

    def test_cases(self):
        self.assertEqual(V.decide_group(0.95, ["sahih", "hasan"], self.t), V.CONFIRM)
        self.assertEqual(V.decide_group(0.95, ["sahih", "daif"], self.t), V.CONFLICT)
        self.assertEqual(V.decide_group(0.95, ["daif", "mawdu"], self.t), V.CONFLICT)
        self.assertEqual(V.decide_group(0.95, ["isnad"], self.t), V.ISNAD)
        self.assertEqual(V.decide_group(0.95, ["isnad", "sahih"], self.t), V.CONFIRM)
        self.assertEqual(V.decide_group(0.95, [None, None], self.t), V.ABSTAIN)
        self.assertEqual(V.decide_group(0.3, ["sahih"], self.t), V.ABSTAIN)

    def test_weak_never_confirmed(self):
        for cls in (["daif"], ["mawdu"], ["la_asl"], ["isnad"], ["daif", "isnad"], [None]):
            self.assertNotIn(V.decide_group(1.0, cls, self.t), V.CONFIRMING)


class DorarServiceTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = override_settings(MUTHBIT={**SETTINGS, "INDEX_DIR": Path(self.tmp.name)})
        self.ctx.enable()
        _map("ضعيف", "daif", 40)
        _map("صحيح", "sahih", 60)
        self.client = APIClient()

    def tearDown(self):
        self.ctx.disable()
        self.tmp.cleanup()

    def _verify(self, text):
        r = self.client.post("/api/verify", {"text": text, "extract": False}, format="json")
        self.assertEqual(r.status_code, 200)
        return r.json()["results"][0]

    @mock.patch("verifier.dorar.requests.get")
    def test_conflict_from_dorar_and_cache(self, get):
        get.return_value.json.return_value = {"ahadith": {"result": SAMPLE_HTML}}
        get.return_value.raise_for_status.return_value = None
        res = self._verify("نص تجريبي أول عن فضل العلم والعمل به ونشره بين الناس")
        self.assertEqual(res["provider"], "dorar")
        self.assertEqual(res["verdict"], V.CONFLICT)
        self.assertEqual(res["match"]["grader"], "محدث أ")
        self.assertEqual(len(res["match"]["other_grades"]), 1)
        self.assertIn("dorar.net/hadith/search", res["match"]["url"])
        self._verify("نص تجريبي أول عن فضل العلم والعمل به ونشره بين الناس")
        self.assertEqual(get.call_count, 1)  # الاستعلام الثاني من الذاكرة المؤقتة

    @mock.patch("verifier.dorar.requests.get")
    def test_unreviewed_mapping_ignored(self, get):
        GradeMapping.objects.update(reviewed=False)
        get.return_value.json.return_value = {"ahadith": {"result": SAMPLE_HTML}}
        get.return_value.raise_for_status.return_value = None
        res = self._verify("نص تجريبي أول عن فضل العلم والعمل به ونشره بين الناس")
        self.assertEqual(res["verdict"], V.ABSTAIN)

    @mock.patch("verifier.dorar.requests.get")
    def test_unrelated_results_abstain(self, get):
        get.return_value.json.return_value = {"ahadith": {"result": SAMPLE_HTML}}
        get.return_value.raise_for_status.return_value = None
        res = self._verify("كلام مختلف تماماً عن موضوع آخر لا يشبه شيئاً مما سبق ذكره")
        self.assertEqual(res["verdict"], V.ABSTAIN)

    @mock.patch("verifier.dorar.requests.get")
    def test_dorar_down(self, get):
        import requests
        get.side_effect = requests.ConnectionError("down")
        res = self._verify("نص تجريبي أول عن فضل العلم والعمل به")
        self.assertEqual(res["verdict"], V.ABSTAIN)
        self.assertTrue(res["notes"])


class LLMClientTests(TestCase):
    @mock.patch("verifier.llm_client.requests.post")
    def test_openai_style(self, post):
        post.return_value.json.return_value = {"choices": [{"message": {"content": "جاهز"}}]}
        post.return_value.raise_for_status.return_value = None
        cfg = ProviderConfig("Gemini", "openai", "gemini-x", "k",
                             "https://generativelanguage.googleapis.com/v1beta/openai/")
        self.assertEqual(complete(cfg, "s", "u"), "جاهز")
        self.assertEqual(post.call_args.args[0],
                         "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")

    @mock.patch("verifier.llm_client.requests.post")
    def test_anthropic_style(self, post):
        post.return_value.json.return_value = {"content": [{"type": "text", "text": "جاهز"}]}
        post.return_value.raise_for_status.return_value = None
        cfg = ProviderConfig("Claude", "anthropic", "claude-x", "k")
        self.assertEqual(complete(cfg, "s", "u"), "جاهز")
        self.assertIn("x-api-key", post.call_args.kwargs["headers"])

    def test_missing_key(self):
        with self.assertRaises(LLMError):
            complete(ProviderConfig("x", "openai", "m", "", "https://a"), "s", "u")


class ExtractionProviderTests(TestCase):
    POST = "صباح الخير. قال: «جملة تجريبية أولى هنا» انشرها"

    @mock.patch("verifier.extraction.complete")
    def test_fallback_and_grounding(self, comp):
        comp.side_effect = [
            LLMError("down"),
            '{"claims": [{"text": "جملة تجريبية أولى هنا", "attributed_to": "النبي"},'
            ' {"text": "نص مختلق لم يرد", "attributed_to": "النبي"}]}',
        ]
        p1 = ProviderConfig("A", "openai", "m", "k", "https://a")
        p2 = ProviderConfig("B", "openai", "m", "k", "https://b")
        claims, method = extract_claims(self.POST, [p1, p2])
        self.assertEqual(method, "llm:B")
        self.assertEqual([c["text"] for c in claims], ["جملة تجريبية أولى هنا"])

    def test_no_providers_heuristic(self):
        claims, method = extract_claims(self.POST, [])
        self.assertEqual(method, "heuristic")
        self.assertEqual(len(claims), 1)


class AdminFormTests(TestCase):
    def _data(self, **kw):
        base = {"name": "Gemini", "api_style": "openai", "model": "gemini-3.1-flash-lite",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                "api_key": "", "api_key_env": "", "use_for_extraction": True, "is_active": True,
                "priority": 100, "timeout": 60}
        base.update(kw)
        return base

    def test_keep_existing_key(self):
        p = AIProvider.objects.create(name="G", api_style="openai", model="m",
                                      base_url="https://x", api_key="secret123")
        form = AIProviderForm(data=self._data(), instance=p)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().api_key, "secret123")

    def test_openai_requires_url(self):
        form = AIProviderForm(data=self._data(base_url=""))
        self.assertFalse(form.is_valid())
