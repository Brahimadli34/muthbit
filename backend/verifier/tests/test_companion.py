"""حالة حقيقية: «الأذنان من الرأس» يُروى عن عدة صحابة، ولكل حديث حكمه، وبعض رواياته جزء من حديث أطول."""
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from verifier import dorar
from verifier import verdict as V
from verifier.models import GradeMapping, Narrator, Source, Text
from verifier.services import verify_claim

from .test_dorar_providers import SETTINGS

SHORT = "الأذنان من الرأس"
LONG = "توضأ النبي صلى الله عليه وسلم فغسل وجهه ثلاثا ويديه ثلاثا ومسح برأسه وقال الأذنان من الرأس"


def _e(text, rawi, muhaddith, grade):
    return (f'<div class="hadith">{text}</div><div class="hadith-info">'
            f'<span class="info-subtitle">الراوي:</span> {rawi} '
            f'<span class="info-subtitle">المحدث:</span> <span>{muhaddith}</span>'
            f'<span class="info-subtitle">المصدر:</span> <span>كتاب</span>'
            f'<span class="info-subtitle">خلاصة حكم المحدث:</span> <span>{grade}</span></div>')


HTML = (_e(SHORT, "أبو أمامة الباهلي", "حماد بن زيد", "من قول أبي أمامة غير مرفوع")
        + _e(LONG, "أبو أمامة الباهلي", "محدث أ", "ضعيف")
        + _e(SHORT, "أبو موسى الأشعري", "العقيلي", "منكر لا يتابع عليه"))


class HelpersTests(TestCase):
    def test_same_person(self):
        self.assertTrue(dorar.same_person("عبد الله بن زيد", "عبدالله بن زيد بن عاصم المازني"))
        self.assertTrue(dorar.same_person("أبو أمامة الباهلي", "صدي بن عجلان أبو أمامة الباهلي"))
        self.assertTrue(dorar.same_person("عائشة أم المؤمنين", "عائشة بنت أبي بكر الصديق أم المؤمنين"))
        self.assertTrue(dorar.same_person("أبو أمامة", "أبو أمامة الباهلي"))
        self.assertFalse(dorar.same_person("عبد الله بن عمر", "عبد الله بن عمرو"))
        self.assertFalse(dorar.same_person("أبو أمامة", "أبو موسى الأشعري"))

    def test_clusters(self):
        items = dorar.parse_result_html(HTML)
        groups = dorar.cluster([(1.0, 0.5, h) for h in items])
        self.assertEqual(len(groups), 2)                         # أبو أمامة (القصير والطويل)، أبو موسى
        self.assertEqual({h.rawi for _, _, h in groups[0]}, {"أبو أمامة الباهلي"})
        self.assertEqual(len(groups[0]), 2)


class CompanionTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = override_settings(MUTHBIT={**SETTINGS, "INDEX_DIR": Path(self.tmp.name)})
        self.ctx.enable()
        for p, c, pr in (("منكر", "daif", 30), ("ضعيف", "daif", 40)):
            GradeMapping.objects.create(phrase=p, match_type="contains", grade_class=c, priority=pr, reviewed=True)
        Narrator.objects.create(id=1, name="عبد الله بن زيد بن عاصم", grade="صحابي")
        Narrator.objects.create(id=2, name="أبو أمامة الباهلي", grade="صحابي")
        Narrator.objects.create(id=9, name="ابن ماجه", grade="إمام")
        src = Source.objects.create(name="سنن ابن ماجه", edition="t")
        self.src = src

    def tearDown(self):
        self.ctx.disable()
        self.tmp.cleanup()

    def _local(self, companion_id, number):
        Text.objects.create(source=self.src, kind="hadith", number=number, text=SHORT, grade_class="ungraded",
                            asaneed=[[companion_id, 9]], ext_id=int(number))
        call_command("build_index", stdout=StringIO())

    def _mock(self, get):
        get.return_value.json.return_value = {"ahadith": {"result": HTML}}
        get.return_value.raise_for_status.return_value = None

    @mock.patch("verifier.dorar.requests.get")
    def test_other_companions_grades_not_attached(self, get):
        self._mock(get)
        self._local(1, "443")                                     # من حديث عبد الله بن زيد
        res = verify_claim(SHORT)
        self.assertEqual(res["verdict"], V.NARRATED)              # لا يُضعَّف بأحكام حديث غيره
        self.assertEqual(res["match"]["number"], "443")
        self.assertEqual(res["match"]["grade"], "")
        self.assertTrue(any("لم نجد في الدرر حكماً على رواية صحابي هذا الموضع" in n for n in res["notes"]))
        # المتن بكل طرقه: الموضع المحلي، ثم حديث أبي أمامة، ثم حديث أبي موسى، كلٌّ بحكمه
        routes = res["matn_routes"]
        self.assertEqual([r["rawi"] for r in routes],
                         ["عبد الله بن زيد بن عاصم", "أبو أمامة الباهلي", "أبو موسى الأشعري"])
        self.assertTrue(routes[0]["is_primary"])
        self.assertEqual(routes[1]["verdict"], V.DAIF)
        self.assertEqual(routes[2]["verdict"], V.DAIF)
        self.assertIn("المتن مروي من حديث", res["matn_summary"])

    @mock.patch("verifier.dorar.requests.get")
    def test_same_companion_long_hadith_grouped(self, get):
        self._mock(get)
        self._local(2, "444")                                     # من حديث أبي أمامة
        res = verify_claim(SHORT)
        self.assertEqual(res["verdict"], V.DAIF)
        graders = {res["match"]["grader"]} | {g["grader"] for g in res["match"]["other_grades"]}
        self.assertEqual(graders, {"حماد بن زيد", "محدث أ"})      # القصير والطويل معاً، دون العقيلي
        abu_umama = [r for r in res["matn_routes"] if r["rawi"] == "أبو أمامة الباهلي"][0]
        self.assertTrue(abu_umama["is_primary"])
        self.assertEqual(len(abu_umama["same_hadith_texts"]), 1)   # الرواية الطويلة
        self.assertTrue(any(r["rawi"] == "أبو موسى الأشعري" for r in res["matn_routes"]))

    def test_relation_labels(self):
        from verifier.arabic import normalize
        self.assertEqual(dorar.relation(normalize(SHORT), normalize(LONG)), "النص المتداول جزء من هذا الحديث")
        self.assertEqual(dorar.relation(normalize(LONG), normalize(SHORT)), "هذا الحديث جزء من النص المتداول")
        self.assertEqual(dorar.relation(normalize(SHORT), normalize(SHORT)), "بلفظه")
