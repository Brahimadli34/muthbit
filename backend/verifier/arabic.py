"""تطبيع النص العربي قبل المطابقة.

الهدف: أن تتطابق الصيغ المختلفة كتابياً للنص نفسه (التشكيل، أشكال الهمزة،
التاء المربوطة، الألف المقصورة، التطويل، علامات الترقيم، صيغ الصلاة على النبي).
"""
import re
import unicodedata

_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_TATWEEL = "\u0640"
_NON_ARABIC_WORD = re.compile(r"[^\u0621-\u063A\u0641-\u064A0-9\s]")
_SPACES = re.compile(r"\s+")

# عبارات تتكرر في المنشورات ولا تنتمي إلى متن النص
_HONORIFICS = [
    "صلى الله عليه وسلم", "صلى الله عليه واله وسلم", "صلى الله عليه وعلى اله وسلم",
    "عليه الصلاة والسلام", "عليه السلام", "رضي الله عنهما", "رضي الله عنها",
    "رضي الله عنه", "رضي الله عنهم", "سبحانه وتعالى", "عز وجل", "جل جلاله",
]
_SYMBOLS = {"\uFDFA": " ", "\uFDFB": " ", "ﷺ": " ", "\uFDF2": "الله"}


def normalize(text: str, strip_honorifics: bool = True) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    for sym, rep in _SYMBOLS.items():
        text = text.replace(sym, rep)
    text = _DIACRITICS.sub("", text).replace(_TATWEEL, "")
    text = re.sub("[إأآٱ]", "ا", text)
    text = text.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    text = _NON_ARABIC_WORD.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    if strip_honorifics:
        for h in _HONORIFICS:
            text = text.replace(normalize(h, strip_honorifics=False), " ")
        text = _SPACES.sub(" ", text).strip()
    return text


def char_ngrams(text: str, n: int = 3) -> set:
    t = f" {text} "
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def containment(query_norm: str, doc_norm: str, n: int = 3) -> float:
    """نسبة مقاطع الاستعلام الموجودة في النص الأصلي.

    مفيدة حين يتداول الناس جزءاً من حديث طويل: التشابه الجيبي يعاقب
    فرق الطول، أما الاحتواء فيقيس هل الجزء موجود داخل الأصل.
    """
    q = char_ngrams(query_norm, n)
    if not q:
        return 0.0
    return len(q & char_ngrams(doc_norm, n)) / len(q)


def word_ngrams(text: str, n: int = 2) -> set:
    w = text.split()
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def phrase_containment(query_norm: str, doc_norm: str) -> float:
    """نسبة أزواج الكلمات المتتالية في الاستعلام الموجودة في النص.

    بخلاف احتواء مقاطع الحروف، لا ترفع الكلمات الشائعة المتفرقة (أحدكم، إذا، أن)
    الدرجة، لأن المطلوب تتابع الكلمات كما في الأصل.
    """
    words = query_norm.split()
    if len(words) < 2:
        return 1.0 if words and words[0] in doc_norm.split() else 0.0
    q = word_ngrams(query_norm)
    return len(q & word_ngrams(doc_norm)) / len(q)
