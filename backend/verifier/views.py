from django.conf import settings
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import ErrorReport, Narrator, Source, Text
from .services import verify_post


class VerifyIn(serializers.Serializer):
    text = serializers.CharField(trim_whitespace=True)
    extract = serializers.BooleanField(default=True)

    def validate_text(self, value):
        limit = settings.MUTHBIT["MAX_INPUT_CHARS"]
        if len(value) > limit:
            raise serializers.ValidationError(f"النص أطول من الحد المسموح ({limit} حرف).")
        return value


class ReportIn(serializers.ModelSerializer):
    matched_text = serializers.PrimaryKeyRelatedField(queryset=Text.objects.all(), required=False, allow_null=True)

    class Meta:
        model = ErrorReport
        fields = ["claim", "verdict", "matched_text", "comment"]


@api_view(["POST"])
def verify(request):
    data = VerifyIn(data=request.data)
    data.is_valid(raise_exception=True)
    return Response(verify_post(data.validated_data["text"], data.validated_data["extract"]))


@api_view(["POST"])
def report(request):
    data = ReportIn(data=request.data)
    data.is_valid(raise_exception=True)
    data.save()
    return Response({"ok": True}, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def sources(request):
    return Response([
        {"name": s.name, "edition": s.edition, "license": s.license, "url": s.url,
         "count": s.texts.count()}
        for s in Source.objects.all().order_by("name")
    ])


@api_view(["GET"])
def health(request):
    return Response({"ok": True, "texts": Text.objects.count()})


MAX_RELATED = 150


def _route_payload(t: Text, narrators: dict, is_self: bool) -> list:
    routes = []
    for chain in t.asaneed or []:
        people = []
        for nid in chain:
            n = narrators.get(nid)
            people.append({"id": nid, "name": n.name if n else f"راوٍ غير معروف ({nid})",
                           "grade": n.grade if n else "", "tone": n.tone if n else "unknown"})
        weak = [i for i, p in enumerate(people) if p["tone"] == "weak"]
        majhul = [i for i, p in enumerate(people) if p["tone"] == "majhul"]
        routes.append({"text_id": t.id, "ext_id": t.ext_id, "book": t.source.name, "number": t.number,
                       "is_self": is_self, "has_matn": t.searchable,
                       # من الصحابي إلى صاحب الكتاب، كما في القاعدة
                       "chain": people, "weak_positions": weak, "majhul_positions": majhul})
    return routes


@api_view(["GET"])
def specialist(request, text_id: int):
    """الأسانيد ودرجات الرواة لحديث من القاعدة المحلية ولمواضعه في الكتب الأخرى (التخريج)."""
    try:
        t = Text.objects.select_related("source").get(pk=text_id)
    except Text.DoesNotExist:
        return Response({"detail": "الحديث غير موجود."}, status=status.HTTP_404_NOT_FOUND)
    related = list(Text.objects.select_related("source")
                   .filter(ext_id__in=(t.takhreeg or [])[:MAX_RELATED]).order_by("source__name", "id"))
    ids = {nid for x in [t] + related for chain in (x.asaneed or []) for nid in chain}
    narrators = Narrator.objects.in_bulk(list(ids))
    routes = _route_payload(t, narrators, True)
    for x in related:
        routes += _route_payload(x, narrators, False)
    return Response({
        "hadith": {"id": t.id, "book": t.source.name, "number": t.number, "chapter": t.chapter,
                   "full_text": t.full_text, "matn": t.text if t.searchable else ""},
        "related": [{"id": x.id, "book": x.source.name, "number": x.number, "chapter": x.chapter,
                     "matn": x.text if x.searchable else "", "has_matn": x.searchable} for x in related],
        "routes": routes,
        "narrators_loaded": Narrator.objects.exists(),
        "truncated": len(t.takhreeg or []) > MAX_RELATED,
        "note": "تلوين الرواة تقريبي من نص الدرجة، للتوجيه فقط، والحكم للمتخصص.",
    })
