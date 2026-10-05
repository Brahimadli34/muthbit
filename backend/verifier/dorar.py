"""الربط مع خدمة الموسوعة الحديثية في الدرر السنية (https://dorar.net/article/389).

الخدمة مخصصة لعرض نتائج البحث، لذلك:
- نستعلم عند الحاجة فقط (حين لا تكفي القاعدة المحلية)، ولا ننسخ الموسوعة.
- نحفظ نتيجة كل استعلام مدة محدودة لتخفيف الضغط على خادمهم.
- نذكر المصدر ونربط بصفحة البحث في الدرر مع كل نتيجة.
"""
import html
import logging
import re
from dataclasses import asdict, dataclass
from datetime import timedelta
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.utils import timezone

from .arabic import char_ngrams, normalize

log = logging.getLogger(__name__)

API_URL = "https://dorar.net/dorar_api.json"
SEARCH_URL = "https://dorar.net/hadith/search?q="
SOURCE_NAME = "الدرر السنية - الموسوعة الحديثية"

_LABELS = {
    "الراوي": "rawi",
    "المحدث": "muhaddith",
    "المصدر": "book",
    "الصفحة أو الرقم": "number",
    "خلاصة حكم المحدث": "grade",
}
_LEADING_NUMBER = re.compile(r"^\s*\d+\s*-\s*")


class DorarUnavailable(Exception):
    pass


@dataclass
class DorarHadith:
    text: str
    rawi: str = ""
    muhaddith: str = ""
    book: str = ""
    number: str = ""
    grade: str = ""


# ---------------------------------------------------------------- التحليل
def _value_after(label_el) -> str:
    """القيمة هي أول عنصر غير فارغ بعد عنوان الحقل."""
    node = label_el.next_sibling
    while node is not None:
        text = node.get_text(" ", strip=True) if hasattr(node, "get_text") else str(node).strip()
        if text:
            return text.strip(" -:")
        node = node.next_sibling
    return ""


def parse_result_html(raw_html: str) -> list[DorarHadith]:
    soup = BeautifulSoup(html.unescape(raw_html or ""), "html.parser")
    out = []
    for info in soup.select(".hadith-info"):
        text_el = info.find_previous_sibling()
        if text_el is None:
            continue
        item = DorarHadith(text=_LEADING_NUMBER.sub("", text_el.get_text(" ", strip=True)))
        for label in info.select(".info-subtitle"):
            key = _LABELS.get(label.get_text(strip=True).rstrip(":").strip())
            if key:
                setattr(item, key, _value_after(label))
        if item.text:
            out.append(item)
    return out


# ---------------------------------------------------------------- الاستعلام
def build_query(claim: str, max_words: int) -> str:
    words = normalize(claim).split()
    return " ".join(words[:max_words])


def _cfg():
    return settings.MUTHBIT


def fetch(query: str) -> list[DorarHadith]:
    """يستعلم من الدرر مع ذاكرة مؤقتة في قاعدة البيانات."""
    from .models import DorarCache
    c = _cfg()
    ttl = timedelta(hours=c["DORAR_CACHE_HOURS"])
    cached = DorarCache.objects.filter(query=query).first()
    if cached and timezone.now() - cached.fetched_at < ttl:
        return [DorarHadith(**h) for h in cached.results]
    try:
        r = requests.get(API_URL, params={"skey": query}, timeout=c["DORAR_TIMEOUT"],
                         headers={"User-Agent": "Muthbit/0.1 (hadith verification tool)"})
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        if cached:  # نسخة قديمة أفضل من لا شيء
            return [DorarHadith(**h) for h in cached.results]
        raise DorarUnavailable(str(exc)) from exc
    items = parse_result_html((data.get("ahadith") or {}).get("result", ""))
    DorarCache.objects.update_or_create(query=query, defaults={"results": [asdict(h) for h in items]})
    return items


def search(claim: str) -> tuple[list[DorarHadith], str]:
    """يجرّب استعلاماً طويلاً ثم أقصر إن لم توجد نتائج. يرجع (النتائج، الاستعلام المستخدم)."""
    c = _cfg()
    tried = []
    for n in (c["DORAR_QUERY_WORDS"], max(3, c["DORAR_QUERY_WORDS"] // 2)):
        q = build_query(claim, n)
        if not q or q in tried:
            continue
        tried.append(q)
        items = fetch(q)
        if items:
            return items, q
    return [], tried[-1] if tried else ""


def search_url(query: str) -> str:
    return SEARCH_URL + quote(query)


# ---------------------------------------------------------------- التشابه
def dice(a_norm: str, b_norm: str) -> float:
    """تشابه متماثل بين نصين كاملين (مقاطع الحروف الثلاثية)."""
    a, b = char_ngrams(a_norm), char_ngrams(b_norm)
    return round(2 * len(a & b) / (len(a) + len(b)), 4) if a and b else 0.0


def similarity(claim_norm: str, doc_norm: str) -> float:
    """مقياس لفظي بلا فهرس: الأعلى بين تشابه المقاطع (Dice) والاحتواء."""
    from .retrieval import lexical_score
    return round(lexical_score(claim_norm, doc_norm, dice(claim_norm, doc_norm)), 4)


# ---------------------------------------------------------------- تحويل الأحكام
def classify_grade(grade_text: str, mappings=None) -> str | None:
    """يحوّل عبارة الحكم إلى تصنيف الأداة حسب الجدول الذي يعدّه المختص، أو None."""
    from .models import GradeMapping
    g = normalize(grade_text.strip("[] "))
    if not g:
        return None
    rows = mappings if mappings is not None else list(GradeMapping.objects.all())
    for m in sorted(rows, key=lambda x: x.priority):
        if m.match_type == GradeMapping.MATCH_EXACT and g == m.phrase_norm:
            return m.grade_class
        if m.match_type == GradeMapping.MATCH_CONTAINS and m.phrase_norm and m.phrase_norm in g:
            return m.grade_class
    return None


# ---------------------------------------------------------------- تجميع الروايات حسب الحديث
_KUNYA_JOIN = re.compile(r"\bعبد\s+(?=ال)")
_STOP = {"بن", "ابن", "بنت", "رضي", "الله", "عنه", "عنها", "عنهما", "عنهم"}


def person_tokens(name: str) -> list[str]:
    """«عبد الله بن زيد» ← [عبدالله، زيد]؛ الأب والجد يكفيان للتمييز في الغالب."""
    n = _KUNYA_JOIN.sub("عبد", normalize(name or "")).replace("ام المومنين", " ")
    return [w for w in n.split() if w not in _STOP]


def same_person(a: str, b: str) -> bool:
    """أول اسمين مميِّزين من الاسم الأقصر موجودان في الأطول، مثل «أبو أمامة» في «صدي بن عجلان أبو أمامة الباهلي»."""
    ta, tb = person_tokens(a), person_tokens(b)
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return all(w in long_ for w in short[:2])


SAME_TEXT_DICE = 0.6
CONTAINED = 0.8


def same_hadith(a: "DorarHadith", b: "DorarHadith") -> bool:
    """روايتان للحديث نفسه: الصحابي نفسه، والنصان متشابهان أو أحدهما جزء من الآخر.

    - الجزء من الحديث الطويل يأخذ حكمه إذا كان من رواية الصحابي نفسه.
    - النص نفسه من صحابيين مختلفين حديثان مستقلان (شاهد)، لكلٍّ حكمه.
    """
    from .arabic import phrase_containment
    if a.rawi and b.rawi and not same_person(a.rawi, b.rawi):
        return False
    na, nb = normalize(a.text), normalize(b.text)
    if dice(na, nb) >= SAME_TEXT_DICE:
        return True
    if a.rawi and b.rawi:  # الاحتواء يكفي فقط مع اتحاد الصحابي المذكور صراحة
        short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
        return phrase_containment(short, long_) >= CONTAINED
    return False


def cluster(scored: list) -> list[list]:
    """scored: [(درجة، dice، رواية)] مرتبة تنازلياً ← مجموعات، كل مجموعة حديث واحد، وأولها أقربها."""
    groups = []
    for item in scored:
        for g in groups:
            if same_hadith(g[0][2], item[2]):
                g.append(item)
                break
        else:
            groups.append([item])
    return groups


def relation(claim_norm: str, text_norm: str) -> str:
    """علاقة النص المتداول بالرواية، للعرض."""
    from .arabic import phrase_containment
    cw, tw = len(claim_norm.split()), len(text_norm.split())
    if tw >= 1.5 * cw and phrase_containment(claim_norm, text_norm) >= CONTAINED:
        return "النص المتداول جزء من هذا الحديث"
    if cw >= 1.5 * tw and phrase_containment(text_norm, claim_norm) >= CONTAINED:
        return "هذا الحديث جزء من النص المتداول"
    if dice(claim_norm, text_norm) >= 0.85:
        return "بلفظه"
    return "بلفظ آخر"
