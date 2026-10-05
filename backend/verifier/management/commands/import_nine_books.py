"""استيراد قاعدة الكتب التسعة من ملف Excel أو CSV.

الأعمدة المتوقعة: hadithID, BookID, title, asaneed, hadithTxt, Matn
- اسم الكتاب: من BookID حسب جدول BOOKS (أو من أول title مع --books-by title).
- الرقم: من بداية hadithTxt، مثل «1  1 - حدثنا...».
- النص المفهرس: Matn (المتن دون الإسناد).
- الصفوف التي لا رقم لها أو لا متن (تراجم الأبواب) تُتخطى.

أحاديث الكتب المحددة في --sahih-books تُصنّف «صحيح»، وما سواها «مروي دون حكم»،
فتبحث الأداة عن حكمه في الدرر وتعرض أحكامه إن وُجدت في جدول التحويل المُراجَع.

مثال:
  python manage.py import_nine_books ../data/nine_books.xlsx --replace-books
"""
import ast
import csv
import re
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from verifier.arabic import normalize
from verifier.models import Kind, Source, Text

DEFAULT_SAHIH = ["صحيح البخاري", "صحيح مسلم"]

# أسماء الكتب حسب BookID في القاعدة. عدّل الأسماء هنا إن أردت صيغة أخرى للعرض.
BOOKS = {
    "1": "صحيح البخاري",
    "2": "صحيح مسلم",
    "3": "سنن أبي داود",
    "4": "جامع الترمذي",
    "5": "سنن النسائي",
    "6": "سنن ابن ماجه",
    "7": "موطأ الإمام مالك",
    "8": "مسند أحمد بن حنبل",
    "9": "مسند الدارمي",
}
COLUMNS = ["hadithID", "BookID", "title", "asaneed", "hadithTxt", "Matn"]


def iter_rows(path: Path, sheet: str | None):
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[sheet] if sheet else wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else "" for h in next(rows)]
        for r in rows:
            yield {h: ("" if v is None else str(v)) for h, v in zip(headers, r)}
    else:
        with path.open(encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f, delimiter="\t" if path.suffix.lower() in (".tsv", ".txt") else ",")


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_PAGE_MARK = re.compile(r"\[\s*\d+\s*/\s*\d+\s*\]")
_FOOTNOTE = re.compile(r"\(\s*\d+\s*\)(?!\s*-)")
# علامة رقم الحديث: رقمان (أو رقم) مع ترقيم داخل الباب اختياري «/ 1» وعلامة «(م)» وشَرطة،
# بشرط ألا يليها عنوان باب أو كتاب (فتلك أرقام أبواب لا أحاديث).
_PUNCT = r"[\s،,.\"'\-–—()]*"
_MARKER = re.compile(
    r"(?<![\d/])(\d+)(?:\s+(\d+))?(?:\s*/\s*\d+)?\s*(?:\(\s*م\s*\d*\s*\))?\s*([-–—])?"
    rf"(?!{_PUNCT}(?:باب|كتاب|أبواب|ذكر)\b)(?={_PUNCT}[\u0621-\u064A\[])"
)

# أي الرقمين هو الترقيم المشهور، لكل كتاب حسب BookID.
# حُدّد بمطابقة حديث «إنما الأعمال بالنيات» في القاعدة مع أرقامه المعروفة:
# البخاري 1 و6689 و6953، مسلم 1907، أبو داود 2201، الترمذي 1647، النسائي 75، ابن ماجه 4227، أحمد 168.
# مالك والدارمي لم يُتحقق منهما: راجعهما بأحاديث تعرف أرقامها.
NUMBER_POSITION = {"1": "second", "2": "first", "3": "first", "4": "second", "5": "second",
                   "6": "second", "7": "first", "8": "first", "9": "second"}


def _prepare(hadith_txt: str) -> str:
    t = (hadith_txt or "").replace("_x000D_", " ").translate(_ARABIC_DIGITS)
    t = _FOOTNOTE.sub(" ", _PAGE_MARK.sub(" ", t))
    return re.sub(r"\s+", " ", t)


def parse_number(hadith_txt: str, which: str) -> str:
    """رقم الحديث من أول علامة رقم صالحة في النص، ولو سبقها عنوان الباب."""
    for m in _MARKER.finditer(_prepare(hadith_txt)[:600]):
        first, second, dash = m.group(1), m.group(2), m.group(3)
        if not second and not dash:
            continue  # رقم منفرد بلا شَرطة: ليس علامة حديث
        if not second:
            return first
        return first if which == "first" else second
    return ""


def book_id(value) -> str:
    """Excel قد يقرأ الرقم 1 على أنه 1.0"""
    v = str(value or "").strip()
    try:
        return str(int(float(v)))
    except ValueError:
        return v


def resolve_book(row, by: str) -> tuple[str, str]:
    """يرجع (اسم الكتاب، الكتاب والباب)."""
    title = (row.get("title") or "").strip()
    head, _, chapter = title.partition(" - ")
    if by == "id":
        name = BOOKS.get(book_id(row.get("BookID")))
        if name:
            # إن بدأ العنوان باسم الكتاب نحذفه من الموضع، وإلا نبقي العنوان كاملاً
            return name, (chapter if head.strip() == name else title)
    return head.strip(), chapter


def parse_asaneed(value) -> list[list[int]]:
    """«[['4677', '4494', ...], [...]]» ← قوائم أعداد، من الصحابي إلى صاحب الكتاب."""
    v = str(value or "").strip()
    if not v or v == "nan":
        return []
    try:
        chains = ast.literal_eval(v)
    except (ValueError, SyntaxError):
        return []
    out = []
    for chain in chains if isinstance(chains, (list, tuple)) else []:
        ids = [int(x) for x in chain if str(x).strip().isdigit()]
        if ids:
            out.append(ids)
    return out


def ext_id(value):
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return None


def clean_full_text(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").replace("_x000D_", " ")).strip()


def clean_matn(matn: str) -> str:
    matn = re.sub(r"\[\d+/\d+\]", " ", matn or "")       # علامات الجزء/الصفحة
    matn = re.sub(r"[{}*]", " ", matn)                    # أقواس الآيات وفواصلها
    return re.sub(r"\s+", " ", matn).strip()


class Command(BaseCommand):
    help = "استيراد الكتب التسعة من Excel أو CSV"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--sheet", help="اسم الورقة في Excel (الافتراضي: الأولى)")
        parser.add_argument("--number", choices=["auto", "first", "second"], default="auto",
                            help="أي الرقمين هو رقم الحديث المعتمد. auto: حسب جدول NUMBER_POSITION لكل كتاب")
        parser.add_argument("--sahih-books", nargs="*", default=DEFAULT_SAHIH,
                            help="الكتب التي تُصنّف أحاديثها «صحيح»")
        parser.add_argument("--edition", default="ترقيم قاعدة الكتب التسعة المستوردة",
                            help="وصف الترقيم أو الطبعة، يظهر للمستخدم مع المصدر")
        parser.add_argument("--license", default="", help="ترخيص قاعدة البيانات، لسجل المصادر")
        parser.add_argument("--replace-books", action="store_true",
                            help="حذف نصوص هذه الكتب قبل الاستيراد (لا يمس بقية القاعدة المحلية)")
        parser.add_argument("--books-by", choices=["id", "title"], default="id",
                            help="تحديد الكتاب بـ BookID (الافتراضي) أو من أول العنوان")
        parser.add_argument("--keep-unnumbered", action="store_true",
                            help="استيراد الصفوف التي لها متن بلا رقم، برقم «بلا رقم (hadithID)»")
        parser.add_argument("--skipped-log", default="skipped_rows.csv",
                            help="ملف تُكتب فيه الصفوف المتخطاة وسبب تخطيها")
        parser.add_argument("--limit", type=int, default=0, help="للتجربة: عدد الصفوف الأقصى")
        parser.add_argument("--batch", type=int, default=2000)

    def handle(self, path, sheet, number, sahih_books, edition, license, replace_books, limit, batch,
               books_by, keep_unnumbered, skipped_log, **o):
        p = Path(path)
        if not p.exists():
            raise CommandError(f"الملف غير موجود: {p}")
        sahih = {normalize(b) for b in sahih_books}
        started = time.time()
        sources, pending, stats = {}, [], {"imported": 0, "no_number": 0, "no_matn": 0, "sahih": 0,
                                          "isnad_only": 0}
        per_book = {}
        skipped = []

        def skip(reason, row, book):
            stats[reason] += 1
            per_book.setdefault(book, {"imported": 0, "no_number": 0, "no_matn": 0})[reason] += 1
            skipped.append({"reason": {"no_number": "بلا رقم", "no_matn": "بلا متن"}[reason],
                            "hadithID": row.get("hadithID", ""), "BookID": row.get("BookID", ""),
                            "book": book, "title": (row.get("title") or "")[:150],
                            "hadithTxt": (row.get("hadithTxt") or "")[:200].replace("\n", " "),
                            "Matn": (row.get("Matn") or "")[:200].replace("\n", " ")})

        with transaction.atomic():
            for i, row in enumerate(iter_rows(p, sheet), start=2):
                if i == 2:
                    missing = [c for c in ("title", "hadithTxt", "Matn") if c not in row]
                    if missing:
                        raise CommandError(f"أعمدة ناقصة: {missing}. الموجود: {list(row)}")
                if limit and stats["imported"] >= limit:
                    break
                book, chapter = resolve_book(row, books_by)
                which = NUMBER_POSITION.get(book_id(row.get("BookID")), "second") if number == "auto" else number
                num = parse_number(row.get("hadithTxt", ""), which)
                matn = clean_matn(row.get("Matn", ""))
                asaneed = parse_asaneed(row.get("asaneed"))
                isnad_only = False
                if not matn:
                    if not (num and asaneed):
                        skip("no_matn", row, book)
                        continue
                    isnad_only = True  # متابعة بإسناد بلا متن مستقل: تُحفظ للمتخصصين ولا تُفهرس
                if not num:
                    if not keep_unnumbered:
                        skip("no_number", row, book)
                        continue
                    num = f"بلا رقم ({row.get('hadithID', '')})"
                per_book.setdefault(book, {"imported": 0, "no_number": 0, "no_matn": 0})["imported"] += 1
                if book not in sources:
                    src, _ = Source.objects.get_or_create(name=book, edition=edition,
                                                          defaults={"license": license})
                    if replace_books:
                        deleted, _ = Text.objects.filter(source=src).delete()
                        if deleted:
                            self.stdout.write(f"حُذف {deleted} نصاً قديماً من {book}")
                    sources[book] = src
                is_sahih = normalize(book) in sahih
                stats["sahih"] += is_sahih
                stats["isnad_only"] += isnad_only
                pending.append(Text(
                    source=sources[book], kind=Kind.HADITH, number=num, chapter=chapter.strip()[:500],
                    text=matn or "(إسناد بلا متن مستقل)", text_norm=normalize(matn),
                    ext_id=ext_id(row.get("hadithID")), full_text=clean_full_text(row.get("hadithTxt")),
                    asaneed=asaneed, searchable=not isnad_only,
                    grade="صحيح" if is_sahih else "",
                    grade_class="sahih" if is_sahih else "ungraded",
                    grader=f"رواه {book.replace('صحيح ', '')}" if is_sahih else "",
                ))
                stats["imported"] += 1
                if len(pending) >= batch:
                    Text.objects.bulk_create(pending)
                    pending.clear()
                    self.stdout.write(f"\r{stats['imported']} حديثاً...", ending="")
            if pending:
                Text.objects.bulk_create(pending)

        self.stdout.write("")
        self.stdout.write("الكتاب: مستورد / تُخطي بلا رقم / تُخطي بلا متن")
        for name, c in per_book.items():
            self.stdout.write(f"  {name or '(بلا اسم)'}: {c['imported']} / {c['no_number']} / {c['no_matn']}")
        if skipped:
            with open(skipped_log, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(skipped[0]))
                w.writeheader()
                w.writerows(skipped)
            self.stdout.write(f"الصفوف المتخطاة في: {Path(skipped_log).resolve()}")
        self.stdout.write(self.style.SUCCESS(
            f"استُورد {stats['imported']} حديثاً (منها {stats['sahih']} من الصحيحين) "
            f"في {time.time() - started:.0f} ثانية. تُخطي {stats['no_number']} صفاً بلا رقم "
            f"و{stats['no_matn']} بلا متن ولا إسناد. وحُفظ {stats['isnad_only']} إسناداً بلا متن مستقل "
            f"للمتخصصين، ولا يدخل البحث. شغّل build_index الآن."))
