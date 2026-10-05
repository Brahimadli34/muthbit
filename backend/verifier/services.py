import logging
import os

from django.conf import settings

from . import dorar
from . import verdict as V
from .arabic import normalize
from .extraction import extract_claims
from .llm_client import ProviderConfig
from .models import AIProvider, GradeMapping, Narrator, Text, TrustedGrader
from .retrieval import get_index

log = logging.getLogger(__name__)

DISCLAIMER = ("مُثبِت أداة مساعدة للتحقق، تنقل الأحكام من مصادرها كما هي ولا تُصدر أحكاماً "
              "من عندها. عند الشك أو الامتناع يُرجع إلى أهل الاختصاص.")


def _cfg():
    return settings.MUTHBIT


def thresholds() -> V.Thresholds:
    c = _cfg()
    return V.Thresholds(exact=c["THRESHOLD_EXACT"], match=c["THRESHOLD_MATCH"])


def extraction_providers() -> list[ProviderConfig]:
    """المزوّدون المفعّلون من لوحة الإدارة بالترتيب، ثم متغير البيئة احتياطاً."""
    out = [p.to_config() for p in AIProvider.objects.filter(is_active=True, use_for_extraction=True)]
    out = [p for p in out if p.api_key]
    if not out and os.environ.get("ANTHROPIC_API_KEY"):
        out.append(ProviderConfig(name="Claude (متغير البيئة)", api_style="anthropic",
                                  model=_cfg()["LLM_MODEL"], api_key=os.environ["ANTHROPIC_API_KEY"]))
    return out


def _base(claim_text, attributed_to):
    return {"claim": claim_text, "attributed_to": attributed_to, "verdict": V.ABSTAIN,
            "verdict_label": V.LABELS[V.ABSTAIN], "explanation": V.EXPLANATIONS[V.ABSTAIN],
            "score": 0.0, "provider": None, "match": None, "alternatives": [], "notes": [], "summary": "",
            "matn_routes": [], "matn_summary": "",
            "top_candidate": {"score": 0.0, "grade_class": None, "has_other_grades": False}}


def _set_verdict(res, code):
    res["verdict"], res["verdict_label"], res["explanation"] = code, V.LABELS[code], V.EXPLANATIONS[code]


# ---------------------------------------------------------------- الطبقة الأولى: القاعدة المحلية
def _serialize_text(t: Text) -> dict:
    return {"id": t.id, "kind": t.kind, "kind_label": t.get_kind_display(), "source": t.source.name,
            "chapter": t.chapter, "has_isnad": bool(t.asaneed),
            "edition": t.source.edition, "number": t.number, "original_text": t.text, "grade": t.grade,
            "grade_class": t.grade_class, "grader": t.grader, "grade_ref": t.grade_ref,
            "other_grades": t.other_grades, "actual_attribution": t.actual_attribution,
            "url": t.url or t.source.url}


TIE_MARGIN = 0.03
GRADE_PRIORITY = {"quran": 0, "sahih": 1, "hasan": 2, "misattributed": 3, "mawdu": 3, "la_asl": 3,
                  "daif": 4, "isnad": 5, "ungraded": 6}
SOURCE_PRIORITY = {"صحيح البخاري": 0, "صحيح مسلم": 1}


def _local(q_norm):
    c = _cfg()
    index = get_index(c["INDEX_DIR"])
    if index is None:
        return None, None, []
    cands = index.search(q_norm, k=30, lexical_weight=c["LEXICAL_WEIGHT"],
                         embedding_model=c["EMBEDDING_MODEL"] if c["USE_EMBEDDINGS"] else None)
    recs = Text.objects.select_related("source").in_bulk([x.text_id for x in cands])
    cands = [x for x in cands if x.text_id in recs]
    if cands:
        # النص نفسه يتكرر في كتب عدة بالدرجة نفسها تقريباً: نقدّم الأعلى رتبة في الثبوت
        best = cands[0].score
        cands.sort(key=lambda x: (x.score < best - TIE_MARGIN,
                                  GRADE_PRIORITY.get(recs[x.text_id].grade_class, 9),
                                  SOURCE_PRIORITY.get(recs[x.text_id].source.name, 9),
                                  -x.score))
    return (cands[0] if cands else None), recs, cands[:6]


# ---------------------------------------------------------------- الطبقة الثانية: الدرر
SAME_HADITH_DICE = 0.6


def _trusted_name(muhaddith: str, trusted: list) -> str | None:
    for g in trusted:
        if g.matches(muhaddith):
            return g.name
    return None


def _grade_cluster(cluster, mappings, trusted, t):
    """أحكام حديث واحد (مجموعة روايات صحابي واحد): التصنيف والخلاصة، والمعتمدون أولاً."""
    group = [(sc, h) for sc, _, h in cluster]
    score = cluster[0][0]
    classes = [dorar.classify_grade(h.grade, mappings) for _, h in group]
    entries = [(_trusted_name(h.muhaddith, trusted), c) for (_, h), c in zip(group, classes)]
    code, summary = V.decide_with_trusted(score, entries, t)
    rank = {g.name: i for i, g in enumerate(trusted)}
    order = sorted(range(len(group)), key=lambda i: rank.get(entries[i][0], len(rank) + i))
    group, classes, entries = [group[i] for i in order], [classes[i] for i in order], [entries[i] for i in order]
    if not summary:
        raw = [f"{n}: {h.grade.strip('[] ')}" for (n, _), (_, h) in zip(entries, group) if n and h.grade]
        if raw:
            summary = "حكم المعتمدين كما ورد: " + "؛ ".join(raw)
    return {"group": group, "classes": classes, "code": code, "summary": summary, "score": score,
            "lead": cluster[0][2]}


def _route(g, q_norm, primary: bool) -> dict:
    lead = g["lead"]
    return {
        "rawi": lead.rawi or "صحابي غير مذكور",
        "relation": dorar.relation(q_norm, normalize(lead.text)),
        "verdict": g["code"], "verdict_label": V.SHORT_LABELS[g["code"]], "summary": g["summary"],
        "text": lead.text, "is_primary": primary, "score": g["score"],
        "grades": [{"grade": h.grade, "grader": h.muhaddith, "ref": f"{h.book} {h.number}".strip()}
                   for _, h in g["group"]],
        "same_hadith_texts": [h.text for _, h in g["group"]
                              if normalize(h.text) != normalize(lead.text)][:3],
    }


def _matn_summary(routes) -> str:
    """«المتن مروي من حديث: أبي أمامة (ضعّفه الألباني)، وعبد الله بن زيد (صحّحه الألباني)»"""
    parts = []
    for r in routes:
        verdict = r["summary"] if r["summary"] and not r["summary"].startswith("حكم المعتمدين") else r["verdict_label"]
        parts.append(f"{r['rawi']} ({verdict})")
    return "المتن مروي من حديث: " + "، و".join(parts) if len(parts) > 1 else ""


MAX_ROUTES = 6


def _dorar(res, claim_text, q_norm, companion: str | None = None):
    """companion: صحابي الموضع المحلي، لاختيار أحكام حديثه هو دون أحاديث غيره من الصحابة.

    كل حديث (صحابي) يُحكم عليه وحده، ثم تُعرض كل الأحاديث التي ورد فيها المتن بأحكامها
    وعلاقتها بالنص المتداول (بلفظه، أو هو جزء منها، أو بلفظ آخر)، ليعرف الباحث حكم المتن من كل طرقه.
    """
    t = thresholds()
    try:
        items, query = dorar.search(claim_text)
    except dorar.DorarUnavailable as exc:
        log.warning("Dorar unavailable: %s", exc)
        res["notes"].append("تعذّر الاتصال بالدرر السنية الآن، فاقتصر التحقق على القاعدة المحلية.")
        return
    if not items:
        return
    mappings = GradeMapping.objects.all()
    if _cfg()["GRADE_MAP_REVIEWED_ONLY"]:
        mappings = mappings.filter(reviewed=True)  # لا يُستعمل تحويل لم يراجعه المختص
    mappings = list(mappings)
    # الترتيب: قرب النص المتداول من الرواية، ثم تشابه النصين كاملين
    scored = sorted(((dorar.similarity(q_norm, normalize(h.text)),
                      dorar.dice(q_norm, normalize(h.text)), h) for h in items),
                    key=lambda x: (x[0], x[1]), reverse=True)
    top_score = scored[0][0]
    if top_score < t.match:
        res["top_candidate"] = {"score": top_score, "grade_class": None, "has_other_grades": False}
        return
    # كل مجموعة حديث واحد: الصحابي نفسه، والنص نفسه أو جزء منه
    clusters = dorar.cluster([x for x in scored if x[0] >= t.match])
    chosen_i = 0
    res["overall_score"] = top_score
    if companion:
        same = [i for i, c in enumerate(clusters) if c[0][2].rawi and dorar.same_person(c[0][2].rawi, companion)]
        if same:
            chosen_i = same[0]
        else:
            res["companion_mismatch"] = True
    trusted = list(TrustedGrader.objects.filter(is_active=True))
    graded = [_grade_cluster(c, mappings, trusted, t) for c in clusters[:MAX_ROUTES + 1]]
    if chosen_i >= len(graded):
        graded.append(_grade_cluster(clusters[chosen_i], mappings, trusted, t))
        chosen_i = len(graded) - 1
    g = graded[chosen_i]
    group, classes, code = g["group"], g["classes"], g["code"]
    best = g["lead"]
    grade_entry = group[0][1]
    link = dorar.search_url(query)

    res["summary"] = g["summary"]
    res["score"] = g["score"]
    res["provider"] = "dorar"
    res["top_candidate"] = {"score": g["score"], "grade_class": classes[0],
                            "has_other_grades": len(set(filter(None, classes))) > 1}
    _set_verdict(res, code)
    if code == V.ABSTAIN:
        res["explanation"] = ("وجدنا الحديث في الدرر السنية مع أحكام المحدّثين كما وردت أدناه، لكن عبارات "
                              "هذه الأحكام لم تُصنّف بعد في جدول التحويل المُراجَع، فلا نُصدر خلاصة.")
        res["notes"].append("وُجدت روايات قريبة في الدرر، لكن أحكامها غير مصنّفة في جدول تحويل الأحكام.")
    ge = grade_entry
    res["match"] = {
        "id": None, "kind": "hadith", "kind_label": "حديث", "source": best.book, "edition": "",
        "number": best.number, "original_text": best.text, "grade": ge.grade,
        "grade_class": classes[0], "grader": ge.muhaddith,
        "grade_ref": " ".join(x for x in (ge.book, ge.number) if x),
        "other_grades": [{"grade": h.grade, "grader": h.muhaddith,
                          "ref": f"{h.book} {h.number}".strip()} for _, h in group[1:]],
        "actual_attribution": "", "url": link, "via": dorar.SOURCE_NAME, "rawi": best.rawi,
        "relation": dorar.relation(q_norm, normalize(best.text)),
        "same_hadith_texts": [h.text for _, h in group[1:] if normalize(h.text) != normalize(best.text)][:3],
    }
    routes = [_route(x, q_norm, i == chosen_i and not res.get("companion_mismatch"))
              for i, x in enumerate(graded)]
    res["matn_routes"] = routes[:MAX_ROUTES]
    res["matn_summary"] = _matn_summary(routes[:MAX_ROUTES])
    res["other_companions"] = [r["rawi"] for i, r in enumerate(routes) if i != chosen_i]
    res["alternatives"] = []


SAHIHAYN = {"صحيح البخاري": "البخاري", "صحيح مسلم": "مسلم"}


MAX_TAKHREEG_LOOKUP = 500


def _num_key(n: str):
    return (0, int(n)) if str(n).isdigit() else (1, str(n))


def _takhreeg_in(rec, book_names: set) -> dict:
    """مواضع الحديث في الكتب المحددة حسب التخريج: {اسم الكتاب: [الأرقام]}."""
    ids = (rec.takhreeg or [])[:MAX_TAKHREEG_LOOKUP]
    if not ids:
        return {}
    out = {}
    for name, num in (Text.objects.filter(ext_id__in=ids, source__name__in=book_names)
                      .values_list("source__name", "number")):
        out.setdefault(name, set()).add(num)
    return {k: sorted(v, key=_num_key) for k, v in out.items()}


def _refs(nums: list, limit: int = 3) -> str:
    shown = "، ".join(nums[:limit])
    return shown + (f" (و{len(nums) - limit} مواضع أخرى)" if len(nums) > limit else "")


def _mark_muttafaq(res, rec, top, cands, recs):
    """متفق عليه: إن ربط التخريج موضعه في أحد الصحيحين بموضع في الآخر، ولو اختلف اللفظ.

    إن لم يكن للحديث تخريج في القاعدة، يُرجع إلى تطابق المتن في الكتابين.
    """
    mine = rec.source.name
    if mine not in SAHIHAYN:
        return
    other_book = next(k for k in SAHIHAYN if k != mine)
    found = _takhreeg_in(rec, {other_book}).get(other_book)
    if found:
        refs = {mine: [rec.number], other_book: found}
        res["match"]["grader"] = "متفق عليه"
        res["match"]["grade_ref"] = "، ".join(f"{SAHIHAYN[k]} {_refs(refs[k])}" for k in SAHIHAYN)
        res["match"]["muttafaq_basis"] = "takhreeg"
        return
    if rec.takhreeg:
        return  # للحديث تخريج لا يربطه بالصحيح الآخر: لا نستنتج الاتفاق من تشابه اللفظ
    for x in cands[1:]:
        other = recs[x.text_id]
        if other.source.name == other_book and x.score >= thresholds().exact:
            refs = {mine: [rec.number], other_book: [other.number]}
            res["match"]["grader"] = "متفق عليه"
            res["match"]["grade_ref"] = "، ".join(f"{SAHIHAYN[k]} {_refs(refs[k])}" for k in SAHIHAYN)
            res["match"]["muttafaq_basis"] = "matn"
            return


def _note_sahihayn_takhreeg(res, rec):
    """لحديث من غير الصحيحين: إن ربطه التخريج بموضع فيهما، نُنبّه دون أن نغيّر الحكم."""
    found = _takhreeg_in(rec, set(SAHIHAYN))
    if found:
        where = "، و".join(f"{k} {_refs(v)}" for k, v in found.items())
        res["notes"].append(f"يربطه التخريج بموضع في الصحيحين: {where}. راجع الموضع، فقد يكون بلفظ آخر أو شاهداً.")


# ---------------------------------------------------------------- التجميع
def _companion(rec) -> str | None:
    """الصحابي في أول إسناد الموضع المحلي (الأسانيد مخزّنة من الصحابي إلى صاحب الكتاب)."""
    if not rec or not rec.asaneed or not rec.asaneed[0]:
        return None
    n = Narrator.objects.filter(pk=rec.asaneed[0][0]).first()
    return n.name if n else None


def _looks_quranic(attributed_to: str) -> bool:
    return "قران" in normalize(attributed_to or "")


DEFINITIVE_LOCAL = {"sahih", "hasan", "quran", "daif", "mawdu", "la_asl", "misattributed"}


def verify_claim(claim_text: str, attributed_to: str = "غير محدد") -> dict:
    res = _verify_claim(claim_text, attributed_to)
    _establish_from_routes(res)
    return res


def _verify_claim(claim_text: str, attributed_to: str = "غير محدد") -> dict:
    """القاعدة المحلية أولاً. إن كان سجلها محكوماً عليه (مثل الصحيحين) فهو النتيجة.
    وإلا تُسأل الدرر، وتُعتمد النتيجة الأقرب لفظاً إلى النص المتداول:
    - الدرر أقرب: تُعرض روايتها وأحكامها.
    - المحلي أقرب: يُعرض العزو المحلي، والحكم من الدرر إن وُجد، وإلا «عزو دون حكم».
    """
    res = _base(claim_text, attributed_to)
    q_norm = normalize(claim_text)
    t = thresholds()

    top, recs, cands = _local(q_norm)
    rec = recs.get(top.text_id) if top else None
    if top:
        res["top_candidate"] = {"score": top.score, "lexical": top.lexical, "semantic": top.semantic,
                                "grade_class": rec.grade_class, "has_other_grades": bool(rec.other_grades)}
    local_ok = bool(top and top.score >= t.match)

    if local_ok and rec.grade_class in DEFINITIVE_LOCAL:
        _use_local(res, rec, top, cands, recs)
        return res

    if local_ok:
        sahih = _sahihayn_from_takhreeg(rec, q_norm)
        if sahih:  # الحديث نفسه في الصحيحين حسب التخريج: روايتهما أولى بالعرض
            _use_sahih_via_takhreeg(res, rec, sahih, q_norm)
            return res

    if _looks_quranic(attributed_to):
        # الآية لا تُبحث في موسوعة الحديث: نتائجها أحاديث تتضمن الآية لا الآية نفسها
        res["notes"].append("النص منسوب إلى القرآن الكريم ولم يُطابق آية في القاعدة المحلية. "
                            "تحقق من وجود نص المصحف في القاعدة (أمر import_quran).")
        return res

    dorar_res = None
    companion = _companion(rec) if local_ok else None
    res["companion"] = companion
    if _cfg()["DORAR_ENABLED"]:
        dorar_res = _base(claim_text, attributed_to)
        _dorar(dorar_res, claim_text, q_norm, companion=companion)
        overall = dorar_res.get("overall_score", 0.0)
        if companion and local_ok and overall > top.score + 0.05:
            # رواية في الدرر أقرب بوضوح من الموضع المحلي: هي المقصودة، لا حديث صحابي الموضع المحلي
            dorar_res = _base(claim_text, attributed_to)
            _dorar(dorar_res, claim_text, q_norm)
        res["notes"].extend(dorar_res["notes"])
    dorar_score = dorar_res["score"] if dorar_res and dorar_res["match"] else 0.0

    if dorar_score >= t.match and (not local_ok or dorar_score > top.score + 0.05):
        dorar_res["notes"] = res["notes"]
        if local_ok and rec.asaneed:  # أقرب رواية محلية، ليطّلع المتخصص على أسانيدها
            dorar_res["local_candidate"] = {"id": rec.id, "book": rec.source.name, "number": rec.number,
                                            "score": top.score}
        return dorar_res

    if local_ok:
        _use_local(res, rec, top, cands, recs)
        if dorar_score >= t.match:  # الرواية نفسها تقريباً: نأخذ حكمها من الدرر
            _apply_dorar_grades(res, dorar_res, top.score)
        return res

    if dorar_res:  # لا محلي ولا درر فوق العتبة: نحفظ أقرب درجة لأغراض التقييم
        res["top_candidate"] = max(res["top_candidate"], dorar_res["top_candidate"],
                                   key=lambda c: c.get("score", 0))
    return res


def _first_companion_id(t):
    return t.asaneed[0][0] if t.asaneed and t.asaneed[0] else None


def _sahihayn_from_takhreeg(rec, q_norm):
    """موضع الحديث في الصحيحين حسب التخريج، من حديث الصحابي نفسه (بمعرّف الراوي في الإسناد)."""
    ids = (rec.takhreeg or [])[:MAX_TAKHREEG_LOOKUP]
    if not ids:
        return None
    comp = _first_companion_id(rec)
    found = [x for x in Text.objects.select_related("source").filter(ext_id__in=ids, source__name__in=set(SAHIHAYN))
             if comp is None or _first_companion_id(x) in (None, comp)]
    if not found:
        return None
    order = list(SAHIHAYN)
    found.sort(key=lambda x: (not x.searchable, order.index(x.source.name),
                              -dorar.similarity(q_norm, x.text_norm), _num_key(x.number)))
    return found[0]


def _use_sahih_via_takhreeg(res, rec, sahih, q_norm):
    t = thresholds()
    score = dorar.similarity(q_norm, sahih.text_norm)
    _set_verdict(res, V.CONFIRM if score >= t.exact else V.CONFIRM_DIFF_WORDING)
    res.update(score=max(score, t.match), provider="local", match=_serialize_text(sahih))
    res["match"]["grader"] = f"رواه {SAHIHAYN[sahih.source.name]}"
    _mark_muttafaq(res, sahih, None, [], {})
    res["summary"] = "متفق عليه" if res["match"]["grader"] == "متفق عليه" else f"أخرجه {SAHIHAYN[sahih.source.name]}"
    res["wording_source"] = {"id": rec.id, "book": rec.source.name, "number": rec.number, "text": rec.text}
    res["notes"].append(f"الحديث في {sahih.source.name} برقم {sahih.number} من حديث الصحابي نفسه حسب التخريج، "
                        f"فقُدّمت روايته. واللفظ المتداول أقرب إلى لفظ {rec.source.name} برقم {rec.number}.")


SCOPE = {"بلفظه": "بلفظه", "بلفظ آخر": "بلفظ آخر",
         "النص المتداول جزء من هذا الحديث": "والنص المتداول جزء منه"}


def _establish_from_routes(res):
    """إن ثبت المتن من حديث صحابي آخر، فالنتيجة ثبوت المتن من حديثه، مع بيان حكم الرواية المعروضة.

    إن كان الحديث الثابت جزءاً من النص المتداول فقط، لا يُرفع الحكم، لأن بقية النص لم تثبت.
    """
    routes = res.get("matn_routes") or []
    if res["verdict"] in V.CONFIRMING or len(routes) < 2:
        return
    primary = next((r for r in routes if r["is_primary"]), None)
    for r in routes:
        if r is primary or r["verdict"] not in V.CONFIRMING:
            continue
        if r["relation"] not in SCOPE:
            res["notes"].append(f"ثبت جزء من النص المتداول من حديث {r['rawi']}، ولم نجد ما يثبت بقيته.")
            return
        prior = primary["verdict_label"] if primary else V.SHORT_LABELS.get(res["verdict"], "")
        _set_verdict(res, V.CONFIRM if r["relation"] == "بلفظه" else V.CONFIRM_DIFF_WORDING)
        detail = r["summary"] if r["summary"] and not r["summary"].startswith("حكم المعتمدين") else r["verdict_label"]
        res["summary"] = f"المتن ثابت من حديث {r['rawi']} ({SCOPE[r['relation']]}): {detail}"
        who = f" من حديث {primary['rawi']}" if primary else ""
        res["notes"].append(f"أما الرواية المعروضة أعلاه{who} فحكمها: {prior}.")
        res["established_by"] = r["rawi"]
        return


def _use_local(res, rec, top, cands, recs):
    t = thresholds()
    _set_verdict(res, V.decide(top.score, rec.grade_class, bool(rec.other_grades), t))
    res.update(score=top.score, provider="local", match=_serialize_text(rec))
    res["alternatives"] = [{"score": x.score, **_serialize_text(recs[x.text_id])}
                           for x in cands[1:] if x.score >= t.match]
    if rec.grade_class == "sahih":
        _mark_muttafaq(res, rec, top, cands, recs)
    else:
        _note_sahihayn_takhreeg(res, rec)


def _apply_dorar_grades(res, dorar_res, local_score):
    t = thresholds()
    if dorar_res.get("companion_mismatch"):
        routes = dorar_res.get("matn_routes", [])
        others = "، ".join(dict.fromkeys(r["rawi"] for r in routes)) or "صحابة آخرين"
        res["notes"].append(f"لم نجد في الدرر حكماً على رواية صحابي هذا الموضع. الأحكام الواردة للمتن "
                            f"هي على أحاديث من رواية: {others}، وهي معروضة أدناه كلٌّ بحكمه.")
        local_route = {"rawi": res.get("companion") or "صحابي هذا الموضع", "relation": "الموضع المعروض أعلاه",
                       "verdict": res["verdict"], "verdict_label": V.SHORT_LABELS[res["verdict"]], "summary": "",
                       "text": res["match"]["original_text"], "is_primary": True, "score": local_score,
                       "grades": [], "same_hadith_texts": []}
        res["matn_routes"] = [local_route] + routes
        res["matn_summary"] = _matn_summary(res["matn_routes"])
        return
    code = dorar_res["verdict"]
    if code == V.ABSTAIN:  # أحكام غير مصنّفة: يبقى «عزو دون حكم» مع عرض الأحكام الخام
        res["notes"].append("وُجدت أحكام في الدرر لكنها غير مصنّفة في جدول تحويل الأحكام المُراجَع.")
    else:
        if code in V.CONFIRMING:
            code = V.CONFIRM if local_score >= t.exact else V.CONFIRM_DIFF_WORDING
        _set_verdict(res, code)
    res["summary"] = dorar_res.get("summary", "")
    res["matn_routes"] = dorar_res.get("matn_routes", [])
    res["matn_summary"] = dorar_res.get("matn_summary", "")
    d, m = dorar_res["match"], res["match"]
    m["grade"], m["grade_class"], m["grader"] = d["grade"], d["grade_class"], d["grader"]
    m["grade_ref"] = " ".join(x for x in (d["source"], d["number"]) if x)
    m["other_grades"] = d["other_grades"]
    m["grades_url"], m["grades_via"] = d["url"], dorar.SOURCE_NAME


def verify_post(post: str, extract: bool = True) -> dict:
    if extract:
        claims, method = extract_claims(post, extraction_providers())
    else:
        claims, method = [{"text": post.strip(), "attributed_to": "غير محدد"}], "none"
    return {
        "extraction_method": method,
        "results": [verify_claim(c["text"], c.get("attributed_to", "غير محدد")) for c in claims],
        "index_ready": get_index(_cfg()["INDEX_DIR"]) is not None,
        "dorar_enabled": _cfg()["DORAR_ENABLED"],
        "disclaimer": DISCLAIMER,
    }
