"""استيراد الرواة ودرجاتهم والتخريج، لعرض المتخصصين.

  python manage.py import_isnad_data --narrators ../data/narrators.xlsx --takhreeg ../data/takhreeg.xlsx

narrators: rawi_index, name, grade
takhreeg: hadithID, takhreegIDs (معرّفات مفصولة بفاصلة)
يُستورد التخريج بعد الكتب التسعة، لأنه يُربط بمعرّفات hadithID المستوردة.
"""
import re
import time
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from verifier.management.commands.import_nine_books import iter_rows
from verifier.models import Narrator, Text, narrator_tone


def _clean(v) -> str:
    return re.sub(r"\s+", " ", str(v or "").replace("_x000D_", " ")).strip()


def _int(v):
    try:
        return int(float(str(v).strip()))
    except ValueError:
        return None


class Command(BaseCommand):
    help = "استيراد الرواة والتخريج"

    def add_arguments(self, parser):
        parser.add_argument("--narrators")
        parser.add_argument("--takhreeg")

    def handle(self, narrators, takhreeg, **o):
        if not narrators and not takhreeg:
            raise CommandError("حدّد --narrators أو --takhreeg أو كليهما")
        if narrators:
            self._narrators(Path(narrators))
        if takhreeg:
            self._takhreeg(Path(takhreeg))

    @transaction.atomic
    def _narrators(self, p: Path):
        if not p.exists():
            raise CommandError(f"الملف غير موجود: {p}")
        t = time.time()
        Narrator.objects.all().delete()
        items = []
        for row in iter_rows(p, None):
            rid = _int(row.get("rawi_index"))
            if rid is None:
                continue
            grade = _clean(row.get("grade"))
            items.append(Narrator(id=rid, name=_clean(row.get("name")), grade=grade, tone=narrator_tone(grade)))
        Narrator.objects.bulk_create(items, batch_size=2000)
        tones = Counter(n.tone for n in items)
        no_grade = sum(1 for n in items if not n.grade)
        self.stdout.write(self.style.SUCCESS(
            f"الرواة: {len(items)} (ثقة أو صحابي {tones['thiqa']}، صدوق {tones['saduq']}، "
            f"مجهول أو مستور {tones['majhul']}، ضعيف {tones['weak']}، غير مصنّف {tones['unknown']}، "
            f"منهم {no_grade} بلا درجة في الملف) في {time.time() - t:.0f} ثانية"))
        unknown = Counter(n.grade or "(بلا درجة)" for n in items if n.tone == "unknown")
        if unknown:
            self.stdout.write("أكثر الدرجات غير المصنّفة (لمراجعة دالة narrator_tone):")
            for grade, c in unknown.most_common(25):
                self.stdout.write(f"  {c:>6}  {grade[:80]}")

    @transaction.atomic
    def _takhreeg(self, p: Path):
        if not p.exists():
            raise CommandError(f"الملف غير موجود: {p}")
        t = time.time()
        known = dict(Text.objects.exclude(ext_id=None).values_list("ext_id", "id"))
        if not known:
            raise CommandError("لا توجد أحاديث بمعرّفات القاعدة. شغّل import_nine_books أولاً.")
        links = {}
        for row in iter_rows(p, None):
            hid = _int(row.get("hadithID"))
            if hid not in known:
                continue
            ids = [int(x) for x in re.split(r"[,\s]+", str(row.get("takhreegIDs") or "")) if x.isdigit()]
            ids = [i for i in ids if i in known and i != hid]
            if ids:
                links[hid] = ids
        # لا نمرّر عشرات آلاف المعرّفات في استعلام واحد (SQLite يحدّ عدد المتغيرات)، بل نصفّي هنا
        texts = []
        for x in Text.objects.exclude(ext_id=None).only("id", "ext_id", "takhreeg").iterator(chunk_size=5000):
            if x.ext_id in links:
                x.takhreeg = links[x.ext_id]
                texts.append(x)
        Text.objects.bulk_update(texts, ["takhreeg"], batch_size=2000)
        self.stdout.write(self.style.SUCCESS(
            f"التخريج: {len(texts)} حديثاً مرتبطاً بمواضع أخرى، في {time.time() - t:.0f} ثانية"))
