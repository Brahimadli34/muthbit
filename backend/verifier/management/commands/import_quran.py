"""استيراد نص المصحف من ملف Tanzil النصي (https://tanzil.net/download).

نزّل الملف بصيغة «Text (with aya numbers)»، فيكون كل سطر: رقم_السورة|رقم_الآية|النص
ترخيص Tanzil يسمح بالنسخ والتوزيع بالنص الحرفي دون تعديل، مع ذكر المصدر ورابطه.

  python manage.py import_quran ../data/quran-uthmani.txt
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from verifier.arabic import normalize
from verifier.models import Source, Text

SURAS = ["الفاتحة", "البقرة", "آل عمران", "النساء", "المائدة", "الأنعام", "الأعراف", "الأنفال", "التوبة", "يونس",
         "هود", "يوسف", "الرعد", "إبراهيم", "الحجر", "النحل", "الإسراء", "الكهف", "مريم", "طه", "الأنبياء", "الحج",
         "المؤمنون", "النور", "الفرقان", "الشعراء", "النمل", "القصص", "العنكبوت", "الروم", "لقمان", "السجدة",
         "الأحزاب", "سبأ", "فاطر", "يس", "الصافات", "ص", "الزمر", "غافر", "فصلت", "الشورى", "الزخرف", "الدخان",
         "الجاثية", "الأحقاف", "محمد", "الفتح", "الحجرات", "ق", "الذاريات", "الطور", "النجم", "القمر", "الرحمن",
         "الواقعة", "الحديد", "المجادلة", "الحشر", "الممتحنة", "الصف", "الجمعة", "المنافقون", "التغابن", "الطلاق",
         "التحريم", "الملك", "القلم", "الحاقة", "المعارج", "نوح", "الجن", "المزمل", "المدثر", "القيامة", "الإنسان",
         "المرسلات", "النبأ", "النازعات", "عبس", "التكوير", "الانفطار", "المطففين", "الانشقاق", "البروج", "الطارق",
         "الأعلى", "الغاشية", "الفجر", "البلد", "الشمس", "الليل", "الضحى", "الشرح", "التين", "العلق", "القدر",
         "البينة", "الزلزلة", "العاديات", "القارعة", "التكاثر", "العصر", "الهمزة", "الفيل", "قريش", "الماعون",
         "الكوثر", "الكافرون", "النصر", "المسد", "الإخلاص", "الفلق", "الناس"]


class Command(BaseCommand):
    help = "استيراد المصحف من ملف Tanzil"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--edition", default="Tanzil - رواية حفص")

    @transaction.atomic
    def handle(self, path, edition, **o):
        p = Path(path)
        if not p.exists():
            raise CommandError(f"الملف غير موجود: {p}")
        src, _ = Source.objects.get_or_create(
            name="القرآن الكريم", edition=edition,
            defaults={"license": "Tanzil: نسخ حرفي مع ذكر المصدر", "url": "https://tanzil.net"})
        Text.objects.filter(source=src).delete()
        items = []
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            parts = line.split("|")
            if len(parts) != 3 or not parts[0].isdigit():
                continue  # أسطر الترخيص والتعليقات في آخر الملف
            sura, aya, text = int(parts[0]), parts[1], parts[2].strip()
            items.append(Text(source=src, kind="quran", number=f"{SURAS[sura - 1]}: {aya}", text=text,
                              text_norm=normalize(text), grade="قرآن", grade_class="quran",
                              url=f"https://quran.com/{sura}/{aya}"))
        Text.objects.bulk_create(items, batch_size=2000)
        self.stdout.write(self.style.SUCCESS(f"استُوردت {len(items)} آية. شغّل build_index الآن."))
