"""حالة حقيقية: آية تُتلى في خطبة الحاجة، فأرجعت الدرر أحاديث مختلفة تتضمنها، فجُمعت أحكامها خطأً."""
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from verifier import verdict as V
from verifier.models import DorarCache, GradeMapping
from verifier.services import verify_claim

from .test_dorar_providers import SETTINGS

AYAH = "يا أيها الذين آمنوا اتقوا الله حق تقاته"
TAFSIR = "في قوله عز وجل يا أيها الذين آمنوا اتقوا الله حق تقاته قالوا يا رسول الله وما حق تقاته قال أن يذكر فلا ينسى ويطاع فلا يعصى"
KHUTBA = ("إن الحمد لله نستعينه ونستغفره ونعوذ بالله من شرور أنفسنا من يهده الله فلا مضل له ومن يضلل فلا هادي له "
          "وأشهد أن لا إله إلا الله وأشهد أن محمدا عبده ورسوله يا أيها الذين آمنوا اتقوا الله حق تقاته ولا تموتن إلا وأنتم مسلمون")


def _entry(text, muhaddith, book, grade):
    return (f'<div class="hadith">{text}</div><div class="hadith-info">'
            f'<span class="info-subtitle">المحدث:</span> <span>{muhaddith}</span>'
            f'<span class="info-subtitle">المصدر:</span> <span>{book}</span>'
            f'<span class="info-subtitle">الصفحة أو الرقم:</span> <span>1</span>'
            f'<span class="info-subtitle">خلاصة حكم المحدث:</span> <span>{grade}</span></div>')


HTML = (_entry(KHUTBA, "محدث أ", "سنن تجريبية", "صحيح")
        + _entry(TAFSIR, "محدث ب", "أطراف تجريبية", "تفرد به فلان")
        + _entry(KHUTBA, "محدث ج", "سنن أخرى", "ضعيف"))


class GroupingTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = override_settings(MUTHBIT={**SETTINGS, "INDEX_DIR": Path(self.tmp.name)})
        self.ctx.enable()
        for phrase, cls, prio in (("ضعيف", "daif", 40), ("صحيح", "sahih", 60)):
            GradeMapping.objects.create(phrase=phrase, match_type="contains", grade_class=cls,
                                        priority=prio, reviewed=True)

    def tearDown(self):
        self.ctx.disable()
        self.tmp.cleanup()

    @mock.patch("verifier.dorar.requests.get")
    def test_different_hadiths_not_merged(self, get):
        get.return_value.json.return_value = {"ahadith": {"result": HTML}}
        get.return_value.raise_for_status.return_value = None
        res = verify_claim(AYAH)  # النص المتداول آية بلا نسبة، فترد في كل الروايات
        self.assertEqual(res["match"]["grader"], "محدث ب")
        self.assertEqual(res["match"]["other_grades"], [])          # أحكام خطبة الحاجة لم تُجمع معه
        other = [r for r in res["matn_routes"] if not r["is_primary"]]
        self.assertTrue(any(g["grader"] in ("محدث أ", "محدث ج") for r in other for g in r["grades"]))
        self.assertEqual(other[0]["relation"], "النص المتداول جزء من هذا الحديث")

    @mock.patch("verifier.dorar.requests.get")
    def test_same_hadith_grades_merged(self, get):
        get.return_value.json.return_value = {"ahadith": {"result": HTML}}
        get.return_value.raise_for_status.return_value = None
        res = verify_claim(KHUTBA)
        graders = {res["match"]["grader"]} | {g["grader"] for g in res["match"]["other_grades"]}
        self.assertEqual(graders, {"محدث أ", "محدث ج"})
        self.assertEqual(res["verdict"], V.CONFLICT)

    @mock.patch("verifier.dorar.requests.get")
    def test_quran_attribution_skips_dorar(self, get):
        res = verify_claim(AYAH, attributed_to="القرآن")
        get.assert_not_called()
        self.assertEqual(res["verdict"], V.ABSTAIN)
        self.assertTrue(any("import_quran" in n for n in res["notes"]))


class QuranImportTests(TestCase):
    def test_import_and_match(self):
        tmp = tempfile.TemporaryDirectory()
        f = Path(tmp.name) / "q.txt"
        f.write_text("1|1|بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ\n3|102|يَا أَيُّهَا الَّذِينَ آمَنُوا اتَّقُوا اللَّهَ حَقَّ تُقَاتِهِ وَلَا تَمُوتُنَّ إِلَّا وَأَنتُم مُّسْلِمُونَ\n# license\n", encoding="utf-8")
        with override_settings(MUTHBIT={**SETTINGS, "INDEX_DIR": Path(tmp.name) / "idx", "DORAR_ENABLED": False}):
            call_command("import_quran", str(f), stdout=StringIO())
            call_command("build_index", stdout=StringIO())
            res = verify_claim(AYAH, attributed_to="القرآن")
        self.assertEqual(res["verdict"], V.CONFIRM)
        self.assertEqual(res["match"]["number"], "آل عمران: 102")
        tmp.cleanup()


class GradePhrasesTests(TestCase):
    def test_lists_unmapped(self):
        DorarCache.objects.create(query="x", results=[{"text": "t", "grade": "تفرد به فلان"},
                                                      {"text": "t", "grade": "صحيح"}])
        GradeMapping.objects.create(phrase="صحيح", match_type="contains", grade_class="sahih", reviewed=True)
        out = StringIO()
        call_command("grade_phrases", stdout=out)
        self.assertIn("تفرد به فلان", out.getvalue())
        self.assertNotIn("  صحيح\n", out.getvalue())
