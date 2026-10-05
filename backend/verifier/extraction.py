"""استخراج النصوص المنسوبة من منشور كامل.

النموذج اللغوي يُستخدم هنا فقط، وكل ما يُرجعه يُتحقق منه: أي نص مستخرج
لا يوجد حرفياً (بعد التطبيع) داخل المنشور الأصلي يُحذف. بذلك لا يستطيع
النموذج إضافة نص لم يكتبه المستخدم.
"""
import json
import logging
import re

from .arabic import normalize
from .llm_client import LLMError, complete

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """أنت أداة استخراج فقط. مهمتك: استخراج كل نص منسوب إلى القرآن أو إلى النبي ﷺ أو إلى صحابي أو عالم، من داخل منشور.
القواعد:
- انسخ كل نص حرفياً كما ورد في المنشور، دون تصحيح أو إكمال أو تشكيل.
- لا تحكم على صحة أي نص ولا تذكر مصادر.
- تجاهل الكلام المحيط مثل التحية والدعاء وعبارات "انشرها تؤجر".
- attributed_to: "النبي" أو "القرآن" أو اسم القائل كما ورد، أو "غير محدد".
أجب بـ JSON فقط بهذه الصيغة: {"claims": [{"text": "...", "attributed_to": "..."}]}"""

_ATTRIBUTION = re.compile(
    r"(?:قال|يقول|عن)\s+(?:رسول\s+الله|النبي|الرسول|المصطفى)[^:«\"“]*[:،]?\s*"
    r"|قال\s+(?:الله\s+)?تعالى\s*[:،]?\s*|ﷺ\s*[:،]\s*"
)
_QUOTED = re.compile(r"[«\"“]([^»\"”]{8,})[»\"”]")
_QURAN_BRACKETS = re.compile(r"[﴿{]([^﴾}]{4,})[﴾}]")


def _is_grounded(claim: str, post_norm: str) -> bool:
    c = normalize(claim)
    return bool(c) and c in post_norm


def heuristic_extract(post: str) -> list[dict]:
    claims = []
    for m in _QURAN_BRACKETS.finditer(post):
        claims.append({"text": m.group(1).strip(), "attributed_to": "القرآن"})
    for m in _QUOTED.finditer(post):
        claims.append({"text": m.group(1).strip(), "attributed_to": "غير محدد"})
    if not claims:
        # نص بعد صيغة الإسناد حتى نهاية الجملة
        for m in _ATTRIBUTION.finditer(post):
            rest = post[m.end():]
            sentence = re.split(r"[.!؟\n]", rest, maxsplit=1)[0].strip()
            if len(normalize(sentence)) >= 8:
                claims.append({"text": sentence, "attributed_to": "النبي"})
    return _dedupe(claims)


def _dedupe(claims):
    seen, out = set(), []
    for c in claims:
        key = normalize(c["text"])
        if key and key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _parse_claims(raw: str) -> list[dict]:
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise
        data = json.loads(m.group(0))
    return [c for c in data.get("claims", []) if isinstance(c, dict) and c.get("text")]


def llm_extract(post: str, providers: list) -> tuple[list[dict] | None, str | None]:
    """يجرّب المزوّدين بالترتيب. يرجع (النصوص، اسم المزوّد) أو (None, None) إن فشلوا جميعاً."""
    for cfg in providers:
        try:
            claims = _parse_claims(complete(cfg, SYSTEM_PROMPT, post))
        except (LLMError, ValueError) as exc:
            log.warning("Extraction with %s failed: %s", cfg.name, exc)
            continue
        post_norm = normalize(post)
        grounded = [c for c in claims if _is_grounded(c["text"], post_norm)]
        if len(grounded) < len(claims):
            log.info("Dropped %d ungrounded claims from %s", len(claims) - len(grounded), cfg.name)
        return _dedupe(grounded), cfg.name
    return None, None


def extract_claims(post: str, providers: list) -> tuple[list[dict], str]:
    """يرجع (النصوص، طريقة الاستخراج). إذا لم يُستخرج شيء يُعامل المنشور كله نصاً واحداً."""
    claims, used = llm_extract(post, providers) if providers else (None, None)
    method = f"llm:{used}" if used else "heuristic"
    if claims is None:
        claims = heuristic_extract(post)
    if not claims:
        return [{"text": post.strip(), "attributed_to": "غير محدد"}], "whole_input"
    return claims, method
