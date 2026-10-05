import re

from django.db import models


class Source(models.Model):
    """كتاب أو مجموعة معتمدة، مع طبعتها وترخيصها (مطلوب لسجل المصادر في التسليم)."""
    name = models.CharField("اسم المصدر", max_length=200)
    edition = models.CharField("الطبعة أو نظام الترقيم", max_length=200, blank=True)
    license = models.CharField("الترخيص", max_length=200, blank=True)
    url = models.URLField("رابط المصدر", blank=True)

    class Meta:
        unique_together = ("name", "edition")
        verbose_name = "مصدر"
        verbose_name_plural = "المصادر"

    def __str__(self):
        return f"{self.name} ({self.edition})" if self.edition else self.name


class GradeClass(models.TextChoices):
    QURAN = "quran", "قرآن"
    SAHIH = "sahih", "صحيح"
    HASAN = "hasan", "حسن"
    DAIF = "daif", "ضعيف"
    MAWDU = "mawdu", "موضوع"
    LA_ASL = "la_asl", "لا أصل له"
    MISATTRIBUTED = "misattributed", "منسوب خطأً"
    ISNAD = "isnad", "حكم على الإسناد فقط"
    UNGRADED = "ungraded", "مروي دون حكم"


class Kind(models.TextChoices):
    QURAN = "quran", "آية"
    HADITH = "hadith", "حديث"
    ATHAR = "athar", "أثر أو قول"


class Text(models.Model):
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="texts")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    number = models.CharField("الرقم", max_length=50, blank=True)
    chapter = models.CharField("الكتاب والباب", max_length=500, blank=True)
    # بيانات المتخصصين من قاعدة الكتب التسعة
    ext_id = models.IntegerField("معرّف القاعدة (hadithID)", null=True, blank=True, unique=True)
    full_text = models.TextField("النص بإسناده", blank=True)
    asaneed = models.JSONField("الأسانيد", default=list, blank=True,
                               help_text="قوائم معرّفات الرواة، من الصحابي إلى صاحب الكتاب")
    takhreeg = models.JSONField("التخريج", default=list, blank=True,
                                help_text="معرّفات القاعدة (hadithID) لمواضع الحديث في الكتب الأخرى")
    searchable = models.BooleanField("يدخل في البحث", default=True,
                                     help_text="روايات الإسناد بلا متن مستقل تُحفظ للمتخصصين ولا تُفهرس")
    text = models.TextField("النص")
    text_norm = models.TextField(editable=False)
    grade = models.CharField("الحكم كما ورد", max_length=200, blank=True)
    grade_class = models.CharField(max_length=15, choices=GradeClass.choices)
    grader = models.CharField("الحاكم", max_length=200, blank=True)
    grade_ref = models.CharField("مرجع الحكم", max_length=300, blank=True)
    # [{"grade": "...", "grader": "...", "ref": "..."}] عند اختلاف المحدّثين
    other_grades = models.JSONField(default=list, blank=True)
    # للأقوال المنسوبة خطأً: القائل الحقيقي
    actual_attribution = models.CharField(max_length=300, blank=True)
    url = models.URLField("رابط التحقق", blank=True)

    class Meta:
        verbose_name = "نص"
        verbose_name_plural = "النصوص"
        indexes = [models.Index(fields=["grade_class"])]

    def save(self, *args, **kwargs):
        from .arabic import normalize
        self.text_norm = normalize(self.text)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.source.name} {self.number}".strip()


class ErrorReport(models.Model):
    """بلاغ من المستخدم عن نتيجة خاطئة، يراجعه المختص الشرعي من لوحة الإدارة."""
    claim = models.TextField()
    verdict = models.CharField(max_length=40)
    matched_text = models.ForeignKey(Text, null=True, blank=True, on_delete=models.SET_NULL)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)
    reviewer_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "بلاغ خطأ"
        verbose_name_plural = "بلاغات الأخطاء"


class GradeMapping(models.Model):
    """تحويل عبارة الحكم كما ترد في المصادر الخارجية (مثل الدرر) إلى تصنيف الأداة.

    يعدّه المختص الشرعي ويعدّله من لوحة الإدارة. العبارة غير الموجودة هنا
    لا تدخل في القرار، فتمتنع الأداة بدل أن تخمّن.
    """
    MATCH_EXACT = "exact"
    MATCH_CONTAINS = "contains"

    phrase = models.CharField("العبارة", max_length=200)
    phrase_norm = models.CharField(max_length=200, editable=False, db_index=True)
    match_type = models.CharField("طريقة المطابقة", max_length=10, default=MATCH_EXACT,
                                  choices=[(MATCH_EXACT, "مطابقة تامة"), (MATCH_CONTAINS, "تحتوي العبارة")])
    grade_class = models.CharField("التصنيف", max_length=15, choices=GradeClass.choices)
    priority = models.IntegerField("الأولوية", default=100,
                                   help_text="الأصغر يُفحص أولاً. ضع العبارات الأطول والأخص أولاً، مثل «إسناده صحيح» قبل «صحيح».")
    note = models.CharField("ملاحظة", max_length=300, blank=True)
    reviewed = models.BooleanField("تمت مراجعته", default=False)

    class Meta:
        ordering = ["priority", "-match_type"]
        verbose_name = "تحويل حكم"
        verbose_name_plural = "جدول تحويل الأحكام"

    def save(self, *args, **kwargs):
        from .arabic import normalize
        self.phrase_norm = normalize(self.phrase.strip("[] "))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.phrase} ← {self.get_grade_class_display()}"


class DorarCache(models.Model):
    query = models.CharField(max_length=500, unique=True)
    results = models.JSONField(default=list)
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ذاكرة الدرر المؤقتة"
        verbose_name_plural = "ذاكرة الدرر المؤقتة"


class AIProvider(models.Model):
    API_OPENAI = "openai"
    API_ANTHROPIC = "anthropic"

    name = models.CharField("الاسم المعروض", max_length=100,
                            help_text="مثال: DeepSeek، OpenAI GPT-4o، Claude Sonnet")
    api_style = models.CharField("نمط الـ API", max_length=10, default=API_OPENAI, choices=[
        (API_OPENAI, "متوافق مع OpenAI (DeepSeek/OpenAI/Groq/Mistral/Gemini...)"),
        (API_ANTHROPIC, "Anthropic (Claude)"),
    ])
    model = models.CharField("اسم النموذج (model)", max_length=150,
                             help_text="مثال: deepseek-chat، gpt-4o، gemini-3.1-flash-lite")
    base_url = models.URLField("رابط الـ API", blank=True,
                               help_text="إلزامي لـ «متوافق مع OpenAI» (مثال: https://api.deepseek.com). اتركه فارغاً لـ Anthropic.")
    api_key = models.CharField("مفتاح API", max_length=500, blank=True,
                               help_text="يُخزّن كنص في قاعدة البيانات. الأفضل استعمال متغير بيئة (الحقل التالي).")
    api_key_env = models.CharField("اسم متغير البيئة للمفتاح", max_length=100, blank=True,
                                   help_text="مثال: GEMINI_API_KEY. إن مُلئ يُقدَّم على المفتاح المخزّن.")
    use_for_extraction = models.BooleanField("يُستعمل لاستخراج النصوص", default=True)
    use_for_baseline = models.BooleanField("يُستعمل نموذجاً مرجعياً في التقييم", default=False)
    is_active = models.BooleanField("مفعّل", default=True)
    priority = models.IntegerField("الأولوية", default=100, help_text="الأصغر يُجرَّب أولاً، والباقي احتياط عند الفشل.")
    timeout = models.PositiveIntegerField("مهلة الاتصال (ثانية)", default=60)
    last_test_ok = models.BooleanField(null=True, editable=False)
    last_test_message = models.CharField(max_length=500, blank=True, editable=False)

    class Meta:
        ordering = ["priority", "id"]
        verbose_name = "نموذج ذكاء اصطناعي"
        verbose_name_plural = "نماذج الذكاء الاصطناعي"

    def __str__(self):
        return f"{self.name} — {self.model}"

    def resolved_key(self) -> str:
        import os
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return self.api_key

    def to_config(self):
        from .llm_client import ProviderConfig
        return ProviderConfig(name=self.name, api_style=self.api_style, model=self.model,
                              api_key=self.resolved_key(), base_url=self.base_url, timeout=self.timeout)


class TrustedGrader(models.Model):
    """محدّث معتمد: إن صحّح الحديث أو ضعّفه، تُبنى الخلاصة على حكمه («صححه فلان»)."""
    name = models.CharField("الاسم المعروض", max_length=100, help_text="مثال: الألباني")
    aliases = models.CharField("صيغ الاسم في المصادر", max_length=300, blank=True,
                               help_text="مفصولة بفاصلة، مثل: الارناؤوط، الأرنؤوط")
    priority = models.IntegerField("الترتيب في الخلاصة", default=100)
    is_active = models.BooleanField("مفعّل", default=True)

    class Meta:
        ordering = ["priority", "id"]
        verbose_name = "محدّث معتمد"
        verbose_name_plural = "المحدّثون المعتمدون"

    def __str__(self):
        return self.name

    def matches(self, grader_text: str) -> bool:
        from .arabic import normalize
        g = normalize(grader_text or "")
        forms = [self.name] + [a for a in re.split(r"[،,]", self.aliases) if a.strip()]
        return any(normalize(f.strip()) and normalize(f.strip()) in g for f in forms)


NARRATOR_TONES = [("thiqa", "ثقة أو صحابي"), ("saduq", "صدوق"), ("majhul", "مجهول أو مستور"),
                  ("weak", "ضعيف"), ("unknown", "غير مصنّف")]

# تُطبَّق على النص بعد التطبيع (ة←ه، أ←ا، ؤ←و، ى←ي). الترتيب مقصود: الجرح أولاً،
# ثم الصحبة المختلف فيها، ثم الصحبة الثابتة، ثم الجهالة، ثم التعديل.
_WEAK = re.compile(r"ضعيف|فيه ضعف|\bضعف\b|متروك|كذاب|كذبوه|منكر|وضاع|متهم|ليس بثقه|ليس بالقوي"
                   r"|\b(?:لين|واه|ساقط|هالك)\b")
_DISPUTED_SAHABA = re.compile(r"يقال\s*:?\s*(?:له|لها) (?:صحبه|رويه)|مختلف في صحبته|\b(?:له|لها) رويه\b")
_SAHABA = re.compile(r"صحابي|\b(?:له|لها) صحبه\b|ولابيه صحبه|ام المومنين|احد العشره|شهد بدرا|من مسلمه الفتح")
_MAJHUL = re.compile(r"مجهول|مستور|لا يعرف|لا تعرف|لا يدري من هو|ذكره ابن حبان|لم يوثق")
_GOOD = re.compile(r"ثقه|امام|حافظ|الحفاظ|حجه|متقن|ثبت")
_MID = re.compile(r"صدوق|لا باس|ليس به باس|مقبول|حسن الحديث|صالح الحديث|\bشيخ\b")


def narrator_tone(grade: str) -> str:
    """تصنيف لوني تقريبي لدرجة الراوي، للعرض فقط ولا يدخل في أي حكم."""
    from .arabic import normalize
    g = normalize(grade or "")
    if not g:
        return "unknown"
    if _WEAK.search(g):
        return "weak"
    if _DISPUTED_SAHABA.search(g):
        return "unknown"  # صحبة أو رؤية مختلف فيها: تُترك لنظر المتخصص
    if _SAHABA.search(g):
        return "thiqa"
    if _MAJHUL.search(g):
        return "majhul"
    if _GOOD.search(g):
        return "thiqa"
    if _MID.search(g):
        return "saduq"
    return "unknown"


class Narrator(models.Model):
    id = models.IntegerField("rawi_index", primary_key=True)
    name = models.TextField("الاسم")
    grade = models.TextField("الدرجة", blank=True)
    tone = models.CharField(max_length=10, choices=NARRATOR_TONES, default="unknown")

    class Meta:
        verbose_name = "راوٍ"
        verbose_name_plural = "الرواة"

    def save(self, *args, **kwargs):
        self.tone = narrator_tone(self.grade)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name[:80]

