"""حالة حقيقية: «كصلصلة الجرس» بلفظ مسند أحمد، والحديث نفسه في البخاري ومسلم حسب التخريج."""
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings

from verifier import dorar
from verifier import verdict as V
from verifier.models import Source, Text
from verifier.services import _establish_from_routes, verify_claim

from .test_dorar_providers import SETTINGS

AHMAD = "يأتيني أحيانا له صلصلة كصلصلة الجرس فينفصم عني وقد وعيت وذلك أشده علي"
BUKHARI = "أحيانا يأتيني مثل صلصلة الجرس وهو أشده علي فيفصم عني وقد وعيت عنه ما قال"


class SahihaynPreferenceTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = override_settings(MUTHBIT={**SETTINGS, "INDEX_DIR": Path(self.tmp.name), "DORAR_ENABLED": False})
        self.ctx.enable()
        a = Source.objects.create(name="مسند أحمد بن حنبل", edition="t")
        b = Source.objects.create(name="صحيح البخاري", edition="t")
        m = Source.objects.create(name="صحيح مسلم", edition="t")
        Text.objects.create(source=a, kind="hadith", number="25887", ext_id=10, text=AHMAD, grade_class="ungraded",
                            asaneed=[[3026, 4361, 1]], takhreeg=[20, 30, 40])
        Text.objects.create(source=b, kind="hadith", number="2", ext_id=20, text=BUKHARI, grade_class="sahih",
                            asaneed=[[3026, 4361, 2]], takhreeg=[10, 30])
        Text.objects.create(source=m, kind="hadith", number="2333", ext_id=30, text=BUKHARI, grade_class="sahih",
                            asaneed=[[3026, 4361, 3]], takhreeg=[20])
        # شاهد من صحابي آخر في البخاري: لا يُقدَّم
        Text.objects.create(source=b, kind="hadith", number="99", ext_id=40, text="نص شاهد آخر", grade_class="sahih",
                            asaneed=[[555, 2]])
        call_command("build_index", stdout=StringIO())

    def tearDown(self):
        self.ctx.disable()
        self.tmp.cleanup()

    def test_sahih_preferred(self):
        res = verify_claim("له صلصلة كصلصلة الجرس فينفصم عني وقد وعيت")
        self.assertEqual((res["match"]["source"], res["match"]["number"]), ("صحيح البخاري", "2"))
        self.assertEqual(res["verdict"], V.CONFIRM_DIFF_WORDING)
        self.assertEqual(res["match"]["grader"], "متفق عليه")
        self.assertEqual(res["match"]["grade_ref"], "البخاري 2، مسلم 2333")
        self.assertEqual(res["wording_source"]["number"], "25887")

    def test_other_companion_not_preferred(self):
        Text.objects.filter(ext_id__in=[20, 30]).update(asaneed=[[777, 2]])
        res = verify_claim("له صلصلة كصلصلة الجرس فينفصم عني وقد وعيت")
        self.assertEqual(res["match"]["number"], "25887")
        self.assertEqual(res["verdict"], V.NARRATED)


class SamePersonTests(TestCase):
    def test_aisha(self):
        self.assertTrue(dorar.same_person("عائشة أم المؤمنين", "عائشة بنت أبي بكر الصديق"))
        self.assertFalse(dorar.same_person("أم سلمة", "سلمة بن الأكوع"))


class EstablishTests(TestCase):
    def _res(self, routes, verdict=V.DAIF):
        return {"verdict": verdict, "verdict_label": V.LABELS[verdict], "explanation": "", "summary": "",
                "notes": [], "matn_routes": routes}

    def _r(self, rawi, verdict, relation, primary=False, summary=""):
        return {"rawi": rawi, "verdict": verdict, "verdict_label": V.SHORT_LABELS[verdict], "relation": relation,
                "is_primary": primary, "summary": summary}

    def test_established_by_other_companion(self):
        res = self._res([self._r("أبو أمامة", V.DAIF, "بلفظه", True),
                         self._r("ابن عباس", V.CONFIRM, "النص المتداول جزء من هذا الحديث", summary="صحّحه الألباني")])
        _establish_from_routes(res)
        self.assertEqual(res["verdict"], V.CONFIRM_DIFF_WORDING)
        self.assertEqual(res["summary"], "المتن ثابت من حديث ابن عباس (والنص المتداول جزء منه): صحّحه الألباني")
        self.assertIn("من حديث أبو أمامة فحكمها: ضعيف", res["notes"][0])

    def test_partial_not_upgraded(self):
        res = self._res([self._r("أبو أمامة", V.DAIF, "بلفظه", True),
                         self._r("ابن عباس", V.CONFIRM, "هذا الحديث جزء من النص المتداول")])
        _establish_from_routes(res)
        self.assertEqual(res["verdict"], V.DAIF)
        self.assertIn("ثبت جزء من النص المتداول", res["notes"][0])
