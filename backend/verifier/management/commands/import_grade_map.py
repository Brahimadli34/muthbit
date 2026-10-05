"""استيراد جدول تحويل الأحكام من CSV.

الأعمدة: phrase, match_type (exact/contains), grade_class, priority, note, reviewed (0/1)
"""
import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from verifier.models import GradeClass, GradeMapping


class Command(BaseCommand):
    help = "استيراد جدول تحويل أحكام المصادر الخارجية"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--replace", action="store_true")
        parser.add_argument("--if-empty", action="store_true",
                            help="لا يستورد إن كان الجدول فيه عبارات (يحفظ تعديلات لوحة الإدارة عند كل نشر)")

    @transaction.atomic
    def handle(self, path, replace, if_empty=False, **opts):
        if if_empty and GradeMapping.objects.exists():
            self.stdout.write("الجدول غير فارغ، تم التخطي.")
            return
        p = Path(path)
        if not p.exists():
            raise CommandError(f"الملف غير موجود: {p}")
        if replace:
            GradeMapping.objects.all().delete()
        n = 0
        with p.open(encoding="utf-8-sig", newline="") as f:
            for i, row in enumerate(csv.DictReader(f), start=2):
                if row["grade_class"] not in GradeClass.values:
                    raise CommandError(f"السطر {i}: grade_class غير معروف: {row['grade_class']}")
                if row.get("match_type", "exact") not in ("exact", "contains"):
                    raise CommandError(f"السطر {i}: match_type يجب أن يكون exact أو contains")
                GradeMapping.objects.create(
                    phrase=row["phrase"].strip(), match_type=row.get("match_type") or "exact",
                    grade_class=row["grade_class"], priority=int(row.get("priority") or 100),
                    note=row.get("note") or "", reviewed=(row.get("reviewed") or "0").strip() == "1",
                )
                n += 1
        reviewed = GradeMapping.objects.filter(reviewed=True).count()
        self.stdout.write(self.style.SUCCESS(f"تم استيراد {n} عبارة. المُراجَع منها: {reviewed}."))
