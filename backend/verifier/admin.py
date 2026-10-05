from django import forms
from django.contrib import admin, messages

from .llm_client import LLMError, complete
from .models import AIProvider, DorarCache, ErrorReport, GradeMapping, Narrator, Source, Text, TrustedGrader


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("name", "edition", "license")


@admin.register(Text)
class TextAdmin(admin.ModelAdmin):
    list_display = ("__str__", "kind", "grade_class", "grade", "grader", "searchable")
    list_filter = ("kind", "grade_class", "searchable", "source")
    search_fields = ("text", "text_norm", "number", "ext_id")


@admin.register(ErrorReport)
class ErrorReportAdmin(admin.ModelAdmin):
    list_display = ("created_at", "verdict", "claim", "resolved")
    list_filter = ("resolved", "verdict")
    list_editable = ("resolved",)


@admin.register(GradeMapping)
class GradeMappingAdmin(admin.ModelAdmin):
    list_display = ("phrase", "match_type", "grade_class", "priority", "reviewed", "note")
    list_editable = ("match_type", "grade_class", "priority", "reviewed")
    list_filter = ("reviewed", "grade_class", "match_type")
    search_fields = ("phrase", "note")


@admin.register(DorarCache)
class DorarCacheAdmin(admin.ModelAdmin):
    list_display = ("query", "fetched_at")
    search_fields = ("query",)


class AIProviderForm(forms.ModelForm):
    # المفتاح لا يُعرض بعد حفظه، وترك الحقل فارغاً يُبقي المفتاح الحالي
    api_key = forms.CharField(label="مفتاح API", required=False,
                              widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
                              help_text="⚠️ يُخزّن كنص في قاعدة البيانات، فاحصر صلاحيات /admin. "
                                        "اتركه فارغاً للإبقاء على المفتاح الحالي.")
    clear_api_key = forms.BooleanField(label="حذف المفتاح المخزّن", required=False)

    class Meta:
        model = AIProvider
        fields = "__all__"

    def clean(self):
        data = super().clean()
        if data.get("api_style") == AIProvider.API_OPENAI and not data.get("base_url"):
            self.add_error("base_url", "رابط الـ API إلزامي لنمط «متوافق مع OpenAI».")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        new_key = self.cleaned_data.get("api_key")
        if self.cleaned_data.get("clear_api_key"):
            obj.api_key = ""
        elif not new_key and self.instance.pk:
            obj.api_key = AIProvider.objects.get(pk=self.instance.pk).api_key
        if commit:
            obj.save()
        return obj


@admin.register(AIProvider)
class AIProviderAdmin(admin.ModelAdmin):
    form = AIProviderForm
    list_display = ("name", "model", "api_style", "is_active", "use_for_extraction",
                    "use_for_baseline", "priority", "key_status", "last_test_ok")
    list_editable = ("is_active", "use_for_extraction", "use_for_baseline", "priority")
    readonly_fields = ("last_test_ok", "last_test_message")
    actions = ["test_connection"]
    fieldsets = (
        (None, {"fields": ("name", "api_style", "model", "base_url")}),
        ("المفتاح", {"fields": ("api_key", "clear_api_key", "api_key_env")}),
        ("الاستعمال", {"fields": ("is_active", "use_for_extraction", "use_for_baseline", "priority", "timeout")}),
        ("آخر اختبار", {"fields": ("last_test_ok", "last_test_message")}),
    )

    @admin.display(description="المفتاح")
    def key_status(self, obj):
        key = obj.resolved_key()
        if not key:
            return "غير موجود"
        source = f"متغير {obj.api_key_env}" if obj.api_key_env else "مخزّن"
        return f"{source} (…{key[-4:]})"

    @admin.action(description="اختبار الاتصال بالنماذج المحددة")
    def test_connection(self, request, queryset):
        for p in queryset:
            try:
                reply = complete(p.to_config(), "أجب بكلمة واحدة فقط.", "قل: جاهز", max_tokens=20)
                p.last_test_ok, p.last_test_message = True, f"نجح: {reply.strip()[:100]}"
                messages.success(request, f"{p.name}: {p.last_test_message}")
            except LLMError as exc:
                p.last_test_ok, p.last_test_message = False, str(exc)[:500]
                messages.error(request, f"{p.name}: {exc}")
            p.save(update_fields=["last_test_ok", "last_test_message"])


@admin.register(TrustedGrader)
class TrustedGraderAdmin(admin.ModelAdmin):
    list_display = ("name", "aliases", "priority", "is_active")
    list_editable = ("aliases", "priority", "is_active")


@admin.register(Narrator)
class NarratorAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "grade", "tone")
    list_filter = ("tone",)
    search_fields = ("name", "grade")
