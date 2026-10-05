"""عبارات الأحكام الواردة في الذاكرة المؤقتة للدرر، مع تصنيفها الحالي وعدد مرات ورودها.

  python manage.py grade_phrases                 # غير المصنّفة فقط
  python manage.py grade_phrases --all --csv out.csv
"""
import csv
from collections import Counter

from django.core.management.base import BaseCommand

from verifier.dorar import classify_grade
from verifier.models import DorarCache, GradeMapping


class Command(BaseCommand):
    help = "عرض عبارات الأحكام الواردة من الدرر لمراجعتها"

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="عرض المصنّفة أيضاً")
        parser.add_argument("--csv", help="حفظ النتيجة في ملف CSV")

    def handle(self, all, csv_path=None, **o):
        csv_path = o.get("csv")
        counts = Counter()
        for c in DorarCache.objects.all():
            for h in c.results:
                if h.get("grade"):
                    counts[h["grade"].strip()] += 1
        reviewed = list(GradeMapping.objects.filter(reviewed=True))
        rows = []
        for phrase, n in counts.most_common():
            cls = classify_grade(phrase, reviewed)
            if cls and not all:
                continue
            rows.append({"count": n, "phrase": phrase, "current_class": cls or "غير مصنّفة"})
        for r in rows:
            self.stdout.write(f"{r['count']:>5}  {r['current_class']:<12}  {r['phrase']}")
        if csv_path:
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["count", "phrase", "current_class"])
                w.writeheader()
                w.writerows(rows)
            self.stdout.write(self.style.SUCCESS(f"حُفظت في {csv_path}"))
        self.stdout.write(f"عبارات مختلفة: {len(counts)}، المعروض: {len(rows)}")
