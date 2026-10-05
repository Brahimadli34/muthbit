"""استيراد النصوص من ملف CSV أو JSONL.

الأعمدة: source_name, source_edition, source_license, source_url, kind, number,
text, grade, grade_class, grader, grade_ref, other_grades, actual_attribution, url

other_grades: نص JSON مثل [{"grade": "حسن", "grader": "...", "ref": "..."}] أو فارغ.
"""
import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from verifier.models import GradeClass, Kind, Source, Text

REQUIRED = ["source_name", "kind", "text", "grade_class"]


def _rows(path: Path):
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
    else:
        with path.open(encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f)


class Command(BaseCommand):
    help = "استيراد نصوص المصادر المعتمدة"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--replace", action="store_true", help="حذف النصوص الحالية قبل الاستيراد")

    @transaction.atomic
    def handle(self, path, replace, **opts):
        p = Path(path)
        if not p.exists():
            raise CommandError(f"الملف غير موجود: {p}")
        if replace:
            Text.objects.all().delete()
        valid_grades, valid_kinds = set(GradeClass.values), set(Kind.values)
        n = 0
        for i, row in enumerate(_rows(p), start=2):
            missing = [k for k in REQUIRED if not (row.get(k) or "").strip()]
            if missing:
                raise CommandError(f"السطر {i}: حقول ناقصة {missing}")
            if row["grade_class"] not in valid_grades:
                raise CommandError(f"السطر {i}: grade_class غير معروف: {row['grade_class']}")
            if row["kind"] not in valid_kinds:
                raise CommandError(f"السطر {i}: kind غير معروف: {row['kind']}")
            other = row.get("other_grades") or []
            if isinstance(other, str):
                other = json.loads(other) if other.strip() else []
            source, _ = Source.objects.get_or_create(
                name=row["source_name"].strip(), edition=(row.get("source_edition") or "").strip(),
                defaults={"license": row.get("source_license") or "", "url": row.get("source_url") or ""},
            )
            Text.objects.create(
                source=source, kind=row["kind"], number=str(row.get("number") or "").strip(),
                text=row["text"].strip(), grade=row.get("grade") or "", grade_class=row["grade_class"],
                grader=row.get("grader") or "", grade_ref=row.get("grade_ref") or "",
                other_grades=other, actual_attribution=row.get("actual_attribution") or "",
                url=row.get("url") or "",
            )
            n += 1
        self.stdout.write(self.style.SUCCESS(f"تم استيراد {n} نصاً. شغّل build_index الآن."))
