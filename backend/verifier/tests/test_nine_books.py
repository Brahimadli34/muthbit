"""اختبارات استيراد الكتب التسعة. الصفان الأولان من عينة القاعدة، والباقي مصطنع للاختبار."""
import csv
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from verifier import verdict as V
from verifier.management.commands.import_nine_books import clean_matn, parse_number
from verifier.models import GradeMapping, Text

from .test_dorar_providers import SETTINGS

BUKHARI_1 = "إِنَّمَا الْأَعْمَالُ بِالنِّيَّاتِ ، وَإِنَّمَا لِكُلِّ امْرِئٍ مَا نَوَى ، فَمَنْ كَانَتْ هِجْرَتُهُ إِلَى دُنْيَا يُصِيبُهَا ، أَوْ إِلَى امْرَأَةٍ يَنْكِحُهَا ، فَهِجْرَتُهُ إِلَى مَا هَاجَرَ إِلَيْهِ"
UNGRADED = "نص تجريبي في كتاب من السنن عن فضل العلم والعمل به ونشره بين الناس"
ROWS = [
    {"hadithID": "4", "BookID": "1", "title": "صحيح البخاري - بدء الوحي - باب كيف كان بدء الوحي",
     "asaneed": "", "hadithTxt": "[1/6] بسم الله الرحمن الرحيم قال الشيخ...", "Matn": ""},
    {"hadithID": "5", "BookID": "1", "title": "صحيح البخاري - بدء الوحي - باب كيف كان بدء الوحي",
     "asaneed": "[['4677']]", "hadithTxt": "1  1 - حدثنا الحميدي ...", "Matn": BUKHARI_1},
    {"hadithID": "900", "BookID": "2", "title": "صحيح مسلم - كتاب الإمارة - باب قوله إنما الأعمال بالنية",
     "asaneed": "", "hadithTxt": "1907 4970 - حدثنا ...", "Matn": BUKHARI_1},
    {"hadithID": "901", "BookID": "3", "title": "سنن أبي داود - كتاب العلم - باب فضل العلم",
     "asaneed": "", "hadithTxt": "3641  3641 - حدثنا ...", "Matn": UNGRADED},
]

DORAR_HTML = f"""
<div class="hadith">1 - {UNGRADED}</div>
<div class="hadith-info"><span class="info-subtitle">المحدث:</span> <span>محدث أ</span>
<span class="info-subtitle">المصدر:</span> <span>كتاب تخريج</span>
<span class="info-subtitle">الصفحة أو الرقم:</span> <span>7</span>
<span class="info-subtitle">خلاصة حكم المحدث:</span> <span>ضعيف</span></div>
"""


class HelpersTests(TestCase):
    def test_parse_number(self):
        self.assertEqual(parse_number("1  1 - حدثنا", "second"), "1")
        self.assertEqual(parse_number("5036  1907 - حدثنا", "second"), "1907")
        self.assertEqual(parse_number("5036  1907 - حدثنا", "first"), "5036")
        self.assertEqual(parse_number("[1/6] بسم الله", "second"), "")

    def test_clean_matn(self):
        self.assertEqual(clean_matn("قال [1/8] تعالى { يا أيها * قم }"), "قال تعالى يا أيها قم")


class ImportAndVerifyTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "nine.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(ROWS[0]))
            w.writeheader()
            w.writerows(ROWS)
        self.ctx = override_settings(MUTHBIT={**SETTINGS, "INDEX_DIR": Path(self.tmp.name) / "idx"})
        self.ctx.enable()
        call_command("import_nine_books", str(path), "--replace-books", verbosity=0)
        call_command("build_index", verbosity=0)
        GradeMapping.objects.create(phrase="ضعيف", match_type="contains", grade_class="daif",
                                    priority=40, reviewed=True)
        self.client = APIClient()

    def tearDown(self):
        self.ctx.disable()
        self.tmp.cleanup()

    def _verify(self, text):
        r = self.client.post("/api/verify", {"text": text, "extract": False}, format="json")
        return r.json()["results"][0]

    def test_import(self):
        self.assertEqual(Text.objects.count(), 3)  # صف الترجمة تُخطي
        b = Text.objects.get(source__name="صحيح البخاري")
        self.assertEqual((b.number, b.grade_class), ("1", "sahih"))
        self.assertEqual(b.chapter, "بدء الوحي - باب كيف كان بدء الوحي")
        self.assertEqual(Text.objects.get(source__name="سنن أبي داود").grade_class, "ungraded")

    def test_muttafaq(self):
        res = self._verify("إنما الأعمال بالنيات وإنما لكل امرئ ما نوى")
        self.assertEqual(res["verdict"], V.CONFIRM)
        self.assertEqual(res["match"]["grader"], "متفق عليه")
        self.assertIn("1907", res["match"]["grade_ref"])

    @mock.patch("verifier.dorar.requests.get")
    def test_ungraded_takes_dorar_grade(self, get):
        get.return_value.json.return_value = {"ahadith": {"result": DORAR_HTML}}
        get.return_value.raise_for_status.return_value = None
        res = self._verify(UNGRADED)
        self.assertEqual(res["verdict"], V.DAIF)
        self.assertEqual(res["match"]["source"], "سنن أبي داود")   # العزو محلي
        self.assertEqual(res["match"]["number"], "3641")
        self.assertEqual(res["match"]["grader"], "محدث أ")        # الحكم من الدرر

    @mock.patch("verifier.dorar.requests.get")
    def test_ungraded_without_grade_is_narrated(self, get):
        import requests
        get.side_effect = requests.ConnectionError("down")
        res = self._verify(UNGRADED)
        self.assertEqual(res["verdict"], V.NARRATED)
        self.assertNotIn(res["verdict"], V.CONFIRMING)
        self.assertEqual(res["match"]["number"], "3641")


class BookResolveTests(TestCase):
    def test_by_id(self):
        from verifier.management.commands.import_nine_books import resolve_book
        row = {"BookID": "4.0", "title": "جامع الترمذي - أبواب الصلاة - باب ما جاء في النوم عن الصلاة"}
        self.assertEqual(resolve_book(row, "id"), ("جامع الترمذي", "أبواب الصلاة - باب ما جاء في النوم عن الصلاة"))
        row = {"BookID": "3", "title": "سنن ابي داود - كتاب المناسك"}  # عنوان بإملاء مختلف
        self.assertEqual(resolve_book(row, "id")[0], "سنن أبي داود")
        row = {"BookID": "99", "title": "كتاب آخر - باب"}
        self.assertEqual(resolve_book(row, "id")[0], "كتاب آخر")  # رقم غير معروف: من العنوان

    def test_skipped_log_and_keep_unnumbered(self):
        tmp = tempfile.TemporaryDirectory()
        path, log = Path(tmp.name) / "n.csv", Path(tmp.name) / "skipped.csv"
        rows = ROWS + [{"hadithID": "77", "BookID": "8", "title": "مسند أحمد بن حنبل - مسند",
                        "asaneed": "", "hadithTxt": "حدثنا بلا رقم", "Matn": "متن تجريبي بلا رقم"}]
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(ROWS[0]))
            w.writeheader()
            w.writerows(rows)
        call_command("import_nine_books", str(path), "--replace-books", "--skipped-log", str(log), verbosity=0)
        self.assertEqual(Text.objects.count(), 3)
        self.assertEqual(sum(1 for _ in log.open(encoding="utf-8-sig")) - 1, 2)  # الترجمة + بلا رقم
        call_command("import_nine_books", str(path), "--replace-books", "--keep-unnumbered",
                     "--skipped-log", str(log), verbosity=0)
        self.assertTrue(Text.objects.filter(number="بلا رقم (77)").exists())
        tmp.cleanup()


ITQAN_CLAIM = "إنَّ اللهَ عزَّ وجلَّ يُحِبُّ إذا عمِل أحَدُكم عمَلًا أنْ يُتقِنَه"
DARIMI_UNRELATED = ("أَيُحِبُّ أَحَدُكُمْ إِذَا أَتَى أَهْلَهُ أَنْ يَجِدَ ثَلَاثَ خَلِفَاتٍ سِمَانٍ؟ قَالُوا : نَعَمْ ، "
                    "يَا رَسُولَ اللهِ ، قَالَ : فَثَلَاثُ آيَاتٍ يَقْرَؤُهُنَّ أَحَدُكُمْ خَيْرٌ لَهُ مِنْهُنَّ")
ITQAN_DORAR = """
<div class="hadith">1 - إنَّ اللهَ تعالى يُحبُّ إذا عمِلَ أحدُكم عملًا أن يُتقِنَه</div>
<div class="hadith-info"><span class="info-subtitle">الراوي:</span> عائشة
<span class="info-subtitle">المحدث:</span> <span>محدث تجريبي</span>
<span class="info-subtitle">المصدر:</span> <span>كتاب تجريبي</span>
<span class="info-subtitle">الصفحة أو الرقم:</span> <span>1</span>
<span class="info-subtitle">خلاصة حكم المحدث:</span> <span>حسن</span></div>
"""


class RegressionItqanTests(TestCase):
    """حالة حقيقية: حديث ليس في الكتب التسعة طابق خطأً حديثاً في الدارمي لاشتراكهما في كلمات شائعة."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "n.csv"
        rows = [{"hadithID": "1", "BookID": "9", "title": "مسند الدارمي - كتاب فضائل القرآن - باب",
                 "asaneed": "", "hadithTxt": "3357  3357 - حدثنا", "Matn": DARIMI_UNRELATED}]
        # فهرس بحجم معقول: في فهرس من نص واحد لا معنى لوزن ندرة المقاطع (IDF)
        rows += [{"hadithID": str(10 + i), "BookID": "8", "title": "مسند أحمد بن حنبل - مسند",
                  "asaneed": "", "hadithTxt": f"{i + 1}  {i + 1} - حدثنا",
                  "Matn": f"نص تجريبي رقم {i} فيه كلمات شائعة مثل أحدكم وإذا وأن الله يحب العمل"} for i in range(200)]
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        self.ctx = override_settings(MUTHBIT={**SETTINGS, "INDEX_DIR": Path(self.tmp.name) / "idx"})
        self.ctx.enable()
        call_command("import_nine_books", str(path), "--replace-books", verbosity=0)
        call_command("build_index", verbosity=0)
        self.map = GradeMapping.objects.create(phrase="حسن", match_type="contains", grade_class="hasan",
                                               priority=60, reviewed=True)
        self.client = APIClient()

    def tearDown(self):
        self.ctx.disable()
        self.tmp.cleanup()

    def _verify(self):
        r = self.client.post("/api/verify", {"text": ITQAN_CLAIM, "extract": False}, format="json")
        return r.json()["results"][0]

    @mock.patch("verifier.dorar.requests.get")
    def test_unrelated_local_not_matched_and_dorar_used(self, get):
        get.return_value.json.return_value = {"ahadith": {"result": ITQAN_DORAR}}
        get.return_value.raise_for_status.return_value = None
        res = self._verify()
        self.assertEqual(res["provider"], "dorar")
        self.assertIn("يتقنه", res["match"]["original_text"].replace("ُ", "").replace("ِ", "").replace("َ", ""))
        self.assertEqual(res["verdict"], V.CONFIRM)

    @mock.patch("verifier.dorar.requests.get")
    def test_unreviewed_shows_dorar_text_but_abstains(self, get):
        self.map.reviewed = False
        self.map.save()
        get.return_value.json.return_value = {"ahadith": {"result": ITQAN_DORAR}}
        get.return_value.raise_for_status.return_value = None
        res = self._verify()
        self.assertEqual(res["verdict"], V.ABSTAIN)
        self.assertEqual(res["match"]["grade"], "حسن")  # الحكم الخام يُعرض
        self.assertTrue(any("غير مصنّفة" in n for n in res["notes"]))

    @mock.patch("verifier.dorar.requests.get")
    def test_dorar_down_abstains_not_darimi(self, get):
        import requests
        get.side_effect = requests.ConnectionError("down")
        res = self._verify()
        self.assertEqual(res["verdict"], V.ABSTAIN)
        self.assertIsNone(res["match"])


class RealFormatNumberTests(TestCase):
    """صيغ حقيقية من قاعدة الكتب التسعة: الرقم بعد عنوان الباب، وترقيم داخل الباب، وعلامات المكرر."""

    def test_formats(self):
        cases = [
            ("باب : دعاؤكم إيمانكم 8 8 - حدثنا عبيد الله", "second", "8"),
            ("باب النية في الأيمان 6451 6689 - حدثنا قتيبة", "second", "6689"),
            ("باب قوله: إنما الأعمال بالنية . وأنه يدخل فيه الغزو 1907 4970 - حدثنا", "first", "1907"),
            ("60 / 60 - باب النية في الوضوء 75 75 / 1 - أخبرنا يحيى", "second", "75"),
            ("[2/230] باب : فيما عني به الطلاق والنيات 2201 2198 - حدثنا", "first", "2201"),
            ("( 2 ) باب ما جاء في فضل الطهور 2 2 - حدثنا إسحاق", "second", "2"),
            ("26 - باب النية 4349 4227 - حدثنا أبو بكر", "second", "4227"),
            ("403   402 (م) - أخبرنا القاسم", "second", "402"),
            ("1891 1770 (م 2) - حدثنا أبو كريب", "second", "1770"),
            ("382  388    حدثنا   [1/126]  وكيع", "first", "382"),
            ("2   6 / 3 - مالك عن زيد بن أسلم", "first", "2"),
            ("         2 - وحدثنا أبو بكر", "second", "2"),
            ("[1/6] بسم الله الرحمن الرحيم قال الشيخ", "second", ""),
            ("27 / 585 - باب مخاطبة الإمام رعيته", "second", ""),
        ]
        for text, which, expected in cases:
            self.assertEqual(parse_number(text, which), expected, text)
