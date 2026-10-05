import csv
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from verifier.management.commands.import_nine_books import parse_asaneed
from verifier.models import Narrator, Text, narrator_tone

MATN = "إنما الأعمال بالنيات وإنما لكل امرئ ما نوى"
ROWS = [
    {"hadithID": "5", "BookID": "1", "title": "صحيح البخاري - بدء الوحي - باب",
     "asaneed": "[['4677', '4494', '5495']]", "hadithTxt": "1  1 - حدثنا الحميدي ...", "Matn": MATN},
    {"hadithID": "6", "BookID": "1", "title": "صحيح البخاري - بدء الوحي - باب",
     "asaneed": "[['4677', '9001', '5495']]", "hadithTxt": "2  2 - وحدثنا فلان بهذا الإسناد مثله", "Matn": ""},
    {"hadithID": "900", "BookID": "2", "title": "صحيح مسلم - كتاب الإمارة - باب",
     "asaneed": "[['4677', '4494', '7000'], ['4677', '8000', '7000']]", "hadithTxt": "1907 4970 - حدثنا", "Matn": MATN},
]


class ToneTests(TestCase):
    def test_tones(self):
        self.assertEqual(narrator_tone("ثقة ثبت"), "thiqa")
        self.assertEqual(narrator_tone("صحابي"), "thiqa")
        self.assertEqual(narrator_tone("صدوق يخطئ"), "saduq")
        self.assertEqual(narrator_tone("ليس بثقة"), "weak")
        self.assertEqual(narrator_tone("ضعيف الحديث"), "weak")
        self.assertEqual(narrator_tone("لين الحديث"), "weak")
        self.assertEqual(narrator_tone("روى له الجماعة"), "unknown")
        self.assertEqual(narrator_tone(""), "unknown")
        # من قائمة الدرجات الحقيقية غير المصنّفة سابقاً
        for g, tone in [("مستور", "majhul"), ("مستور .", "majhul"), ("لا يعرف حالها", "majhul"),
                        ("لا تعرف", "majhul"), ("ذكره ابن حبان في ثقات التابعين", "majhul"),
                        ("له صحبة", "thiqa"), ("لها صحبة وحديث", "thiqa"), ("له ولأبيه صحبة", "thiqa"),
                        ("أم المؤمنين", "thiqa"), ("أحد العشرة", "thiqa"), ("شهد بدرا", "thiqa"),
                        ("من مسلمة الفتح", "thiqa"), ("أحد الحفاظ", "thiqa"),
                        ("مختلف في صحبته", "unknown"), ("يقال : له صحبة", "unknown"),
                        ("له رؤية", "unknown"), ("يقال : له رؤية", "unknown"),
                        ("ليس به بأس", "saduq"), ("صالح الحديث", "saduq"),
                        ("كذبوه", "weak"), ("فيه ضعف", "weak"), ("ثقة ضعفه فلان", "thiqa")]:
            self.assertEqual(narrator_tone(g), tone, g)

    def test_parse_asaneed(self):
        self.assertEqual(parse_asaneed("[['1', '2'], ['3']]"), [[1, 2], [3]])
        self.assertEqual(parse_asaneed(""), [])
        self.assertEqual(parse_asaneed("garbage"), [])


class SpecialistTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        with (d / "nine.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(ROWS[0]))
            w.writeheader()
            w.writerows(ROWS)
        with (d / "narr.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["rawi_index", "name", "grade"])
            for rid, name, grade in [(4677, "عمر بن الخطاب", "صحابي"), (4494, "علقمة بن وقاص", "ثقة ثبت"),
                                     (5495, "البخاري", "إمام حافظ"), (8000, "راو تجريبي", "ضعيف"),
                                     (7000, "مسلم", "إمام")]:
                w.writerow([rid, name, grade])
        with (d / "takh.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["hadithID", "takhreegIDs"])
            w.writerow(["5", "900,99999"])
        out = StringIO()
        call_command("import_nine_books", str(d / "nine.csv"), "--replace-books", "--skipped-log",
                     str(d / "s.csv"), stdout=out)
        call_command("import_isnad_data", "--narrators", str(d / "narr.csv"), "--takhreeg",
                     str(d / "takh.csv"), stdout=out)

    def tearDown(self):
        self.tmp.cleanup()

    def test_import(self):
        self.assertEqual(Text.objects.count(), 3)
        iso = Text.objects.get(ext_id=6)
        self.assertFalse(iso.searchable)                         # إسناد بلا متن
        self.assertEqual(iso.asaneed, [[4677, 9001, 5495]])
        self.assertEqual(Text.objects.get(ext_id=5).takhreeg, [900])  # 99999 غير موجود فحُذف
        self.assertEqual(Narrator.objects.get(id=8000).tone, "weak")

    def test_endpoint(self):
        b = Text.objects.get(ext_id=5)
        r = APIClient().get(f"/api/specialist/{b.id}").json()
        self.assertEqual(r["hadith"]["number"], "1")
        self.assertIn("حدثنا الحميدي", r["hadith"]["full_text"])
        self.assertEqual(len(r["routes"]), 3)                   # طريق البخاري + طريقا مسلم
        own = [x for x in r["routes"] if x["is_self"]][0]
        self.assertEqual([p["name"] for p in own["chain"]], ["عمر بن الخطاب", "علقمة بن وقاص", "البخاري"])
        muslim_weak = [x for x in r["routes"] if x["book"] == "صحيح مسلم" and x["weak_positions"]]
        self.assertEqual(len(muslim_weak), 1)
        self.assertEqual(muslim_weak[0]["chain"][1]["name"], "راو تجريبي")

    def test_missing_narrator(self):
        iso = Text.objects.get(ext_id=6)
        r = APIClient().get(f"/api/specialist/{iso.id}").json()
        self.assertIn("غير معروف", r["routes"][0]["chain"][1]["name"])


class LargeTakhreegTests(TestCase):
    def test_many_ids_no_sqlite_limit(self):
        from verifier.models import Source
        src = Source.objects.create(name="مسند أحمد بن حنبل", edition="t")
        Text.objects.bulk_create([Text(source=src, kind="hadith", number=str(i), text="x", text_norm="x",
                                       ext_id=i) for i in range(1, 3001)])
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.csv"
            with f.open("w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["hadithID", "takhreegIDs"])
                for i in range(1, 3001):
                    w.writerow([i, f"{i % 3000 + 1}"])
            call_command("import_isnad_data", "--takhreeg", str(f), stdout=StringIO())
        self.assertEqual(Text.objects.exclude(takhreeg=[]).count(), 3000)


class MuttafaqTakhreegTests(TestCase):
    """متفق عليه حسب التخريج ولو اختلف اللفظ."""

    def setUp(self):
        from django.test import override_settings
        from verifier.models import Source
        self.tmp = tempfile.TemporaryDirectory()
        from .test_dorar_providers import SETTINGS
        self.ctx = override_settings(MUTHBIT={**SETTINGS, "INDEX_DIR": Path(self.tmp.name), "DORAR_ENABLED": False})
        self.ctx.enable()
        b = Source.objects.create(name="صحيح البخاري", edition="t")
        m = Source.objects.create(name="صحيح مسلم", edition="t")
        d = Source.objects.create(name="سنن أبي داود", edition="t")
        mk = lambda src, num, ext, text, cls, takh: Text.objects.create(
            source=src, kind="hadith", number=num, ext_id=ext, text=text, grade_class=cls, takhreeg=takh)
        mk(b, "1", 1, "إنما الأعمال بالنيات وإنما لكل امرئ ما نوى فمن كانت هجرته", "sahih", [2, 3, 4])
        mk(m, "1907", 2, "إنما الأعمال بالنية وإنما لامرئ ما نوى", "sahih", [1])
        mk(m, "1907", 3, "(إسناد بلا متن مستقل)", "sahih", [1])
        mk(d, "2201", 4, "إنما الأعمال بالنية وإنما لكل امرئ ما نوى فمن كانت هجرته", "ungraded", [1, 2])
        mk(b, "50", 5, "حديث تجريبي آخر في البخاري عن الصدق والأمانة في البيع", "sahih", [])
        Text.objects.filter(ext_id=3).update(searchable=False)
        call_command("build_index", stdout=StringIO())

    def tearDown(self):
        self.ctx.disable()
        self.tmp.cleanup()

    def test_bukhari_muttafaq_despite_wording(self):
        from verifier.services import verify_claim
        res = verify_claim("إنما الأعمال بالنيات وإنما لكل امرئ ما نوى")
        self.assertEqual(res["match"]["grader"], "متفق عليه")
        self.assertEqual(res["match"]["grade_ref"], "البخاري 1، مسلم 1907")
        self.assertEqual(res["match"]["muttafaq_basis"], "takhreeg")

    def test_no_takhreeg_not_muttafaq(self):
        from verifier.services import verify_claim
        res = verify_claim("حديث تجريبي آخر في البخاري عن الصدق والأمانة في البيع")
        self.assertEqual(res["match"]["number"], "50")
        self.assertNotEqual(res["match"]["grader"], "متفق عليه")
