"""منطق الحكم: دالة نقية بلا اعتماد على Django ولا على نموذج لغوي.

مبدأ التصميم: النموذج اللغوي لا يصدر الحكم أبداً. الحكم والمصدر يُنقلان
من السجل المطابق كما هما، والقرار هنا حتمي وقابل للاختبار وإعادة الإنتاج.
سكربت التقييم يستورد هذا الملف نفسه لضبط العتبات.
"""
from dataclasses import dataclass

CONFIRM = "confirm"
CONFIRM_DIFF_WORDING = "confirm_diff_wording"
DAIF = "daif"
MAWDU = "mawdu"
ABSTAIN = "abstain"
CONFLICT = "conflict"
MISATTRIBUTED = "misattributed"
ISNAD = "isnad"
NARRATED = "narrated"

# تطابق حرفياً خيارات عمود «المخرج المتوقع من الأداة» في ملف الاختبار
LABELS = {
    CONFIRM: "تأكيد مع المصدر والحكم",
    CONFIRM_DIFF_WORDING: "تأكيد مع بيان الاختلاف عن اللفظ الأصلي",
    DAIF: "تضعيف مع المصدر والحكم",
    MAWDU: "بيان الوضع مع المصدر والحكم",
    ABSTAIN: "امتناع وإحالة إلى مختص",
    CONFLICT: "عرض الأحكام المتعارضة دون ترجيح",
    MISATTRIBUTED: "بيان النسبة الصحيحة للقول",
    ISNAD: "عرض الحكم على الإسناد دون المتن",
    NARRATED: "عزو إلى المصدر دون حكم",
}
CONFIRMING = {CONFIRM, CONFIRM_DIFF_WORDING}

# تسميات قصيرة لعرض طرق المتن
SHORT_LABELS = {
    "confirm": "ثابت", "confirm_diff_wording": "ثابت بلفظ آخر", "daif": "ضعيف", "mawdu": "موضوع",
    "abstain": "لم يُصنَّف حكمه", "conflict": "مختلف فيه", "misattributed": "منسوب خطأً",
    "isnad": "حُكم على إسناده", "narrated": "دون حكم",
}

EXPLANATIONS = {
    CONFIRM: "وُجد النص في المصدر المذكور بلفظه أو بلفظ قريب جداً منه.",
    CONFIRM_DIFF_WORDING: "وُجد أصل النص، لكن لفظه المتداول يختلف عن اللفظ الوارد في المصدر. انقل اللفظ الأصلي.",
    DAIF: "النص مروي، لكن حكم عليه أهل الاختصاص بالضعف كما هو موضح.",
    MAWDU: "نصّ أهل الاختصاص على أن هذا النص موضوع أو لا أصل له مرفوعاً. لا تجوز نسبته إلى النبي ﷺ.",
    ABSTAIN: "لم نجد لهذا النص مرجعاً كافياً في المصادر المعتمدة لدينا. هذا لا يعني بالضرورة أنه غير ثابت، فيُرجع فيه إلى مختص.",
    CONFLICT: "اختلف أهل الاختصاص في الحكم على هذا النص، ونعرض الأحكام كما وردت دون ترجيح.",
    MISATTRIBUTED: "هذا القول ثابت، لكن نسبته المتداولة غير صحيحة. النسبة الصحيحة موضحة أدناه.",
    ISNAD: "الأحكام المنقولة على إسناد هذه الرواية لا على الحديث نفسه، والحكم على الإسناد لا يستلزم الحكم على المتن. يُرجع فيه إلى مختص.",
    NARRATED: "الحديث مروي في المصدر المذكور، ولم نجد عليه حكماً مُراجَعاً. وجوده في المصدر لا يعني ثبوته، فيُرجع فيه إلى مختص.",
}


@dataclass
class Thresholds:
    exact: float = 0.85
    match: float = 0.55


def decide(score: float, grade_class: str | None, has_other_grades: bool,
           t: Thresholds) -> str:
    """يقرر نوع المخرج من درجة التشابه وتصنيف الحكم المخزّن للسجل الأقرب."""
    if grade_class is None or score < t.match:
        return ABSTAIN
    if has_other_grades:
        return CONFLICT
    if grade_class == "misattributed":
        return MISATTRIBUTED
    if grade_class == "ungraded":
        return NARRATED
    if grade_class == "isnad":
        return ISNAD
    if grade_class in ("mawdu", "la_asl"):
        return MAWDU
    if grade_class == "daif":
        return DAIF
    # صحيح أو حسن أو قرآن
    return CONFIRM if score >= t.exact else CONFIRM_DIFF_WORDING


STRONG = {"sahih", "hasan", "quran"}
WEAK = {"daif"}
FABRICATED = {"mawdu", "la_asl"}


def decide_group(score: float, classes: list[str | None], t: Thresholds) -> str:
    """الحكم على مجموعة روايات للنص نفسه (مثل نتائج الدرر لعدة محدّثين).

    - الأحكام غير المصنّفة (None) تُعرض ولا تدخل في القرار.
    - الحكم على الإسناد لا يدخل في القرار إن وُجد حكم على الحديث نفسه.
    - أي اختلاف بين الأحكام يُعرض تعارضاً دون ترجيح.
    """
    if score < t.match:
        return ABSTAIN
    known = {c for c in classes if c}
    if not known:
        return ABSTAIN
    if known != {"isnad"}:
        known.discard("isnad")
    else:
        return ISNAD
    if known <= STRONG:
        return CONFIRM if score >= t.exact else CONFIRM_DIFF_WORDING
    if known <= WEAK:
        return DAIF
    if known <= FABRICATED:
        return MAWDU
    if known == {"misattributed"}:
        return MISATTRIBUTED
    return CONFLICT


VERBS = {"sahih": "صحّحه", "hasan": "حسّنه", "quran": "صحّحه", "daif": "ضعّفه",
         "mawdu": "حكم بوضعه", "la_asl": "قال: لا أصل له", "isnad": "حكم على إسناده"}


def _summary(pairs):
    """[(الاسم، التصنيف)] ← «صحّحه الألباني وأحمد شاكر، وضعّفه شعيب الأرناؤوط»"""
    by_verb = {}
    for name, cls in pairs:
        verb = VERBS.get(cls)
        if verb and name not in by_verb.setdefault(verb, []):
            by_verb[verb].append(name)
    parts = [f"{verb} {' و'.join(names)}" for verb, names in by_verb.items()]
    return "، و".join(parts)


def decide_with_trusted(score: float, entries: list, t: Thresholds) -> tuple[str, str]:
    """entries: [(اسم_المحدّث_المعتمد_أو_None، التصنيف)] لروايات الحديث نفسه.

    إن حكم محدّث معتمد بالصحة أو الحسن ولم يخالفه معتمد آخر: تأكيد مع «صحّحه فلان».
    وإن اتفق المعتمدون على التضعيف أو الوضع: الحكم كذلك. وإن اختلفوا: تعارض يُعرض دون ترجيح.
    وإن لم يحكم أحد منهم: القاعدة العامة (decide_group).
    """
    if score < t.match:
        return ABSTAIN, ""
    trusted = [(n, c) for n, c in entries if n and c]
    on_matn = [(n, c) for n, c in trusted if c != "isnad"]
    summary = _summary(trusted)
    if on_matn:
        strong = {c for _, c in on_matn if c in STRONG}
        weak = {c for _, c in on_matn if c in WEAK | FABRICATED}
        if strong and not weak:
            return (CONFIRM if score >= t.exact else CONFIRM_DIFF_WORDING), summary
        if weak and not strong:
            return (MAWDU if weak <= FABRICATED else DAIF), summary
        if strong and weak:
            return CONFLICT, summary
    return decide_group(score, [c for _, c in entries], t), summary
