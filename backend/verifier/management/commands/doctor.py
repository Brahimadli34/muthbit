"""فحص جاهزية التطبيق على الخادم: القاعدة، والفهرس، والدرر، والنماذج.

شغّله بالمستخدم نفسه الذي يشغّل الخادم (مثل www-data)، ومن مجلد backend وبالبيئة نفسها:
  sudo -u www-data /path/to/.venv/bin/python manage.py doctor
"""
import os
import socket
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from verifier import dorar
from verifier.arabic import normalize
from verifier.models import AIProvider, GradeMapping, Narrator, Text
from verifier.retrieval import INDEX_FILE, get_index


class Command(BaseCommand):
    help = "فحص جاهزية التطبيق"

    def ok(self, msg):
        self.stdout.write(self.style.SUCCESS(f"  ✓ {msg}"))

    def bad(self, msg):
        self.stdout.write(self.style.ERROR(f"  ✗ {msg}"))

    def info(self, msg):
        self.stdout.write(f"    {msg}")

    def handle(self, **o):
        c = settings.MUTHBIT
        import getpass
        self.stdout.write(f"المستخدم الحالي: {getpass.getuser()}")

        self.stdout.write("\nقاعدة البيانات")
        db = settings.DATABASES["default"]
        self.info(f"{db['ENGINE']} — {db.get('NAME')}")
        n = Text.objects.filter(searchable=True).count()
        (self.ok if n else self.bad)(f"نصوص قابلة للبحث: {n}")
        self.info(f"رواة: {Narrator.objects.count()}، عبارات أحكام مُراجَعة: "
                  f"{GradeMapping.objects.filter(reviewed=True).count()}")
        name = str(db.get("NAME") or "")
        if "sqlite" in db["ENGINE"] and name:
            (self.ok if os.access(name, os.W_OK) else self.bad)(f"صلاحية الكتابة على ملف القاعدة: {name}")
            folder = os.path.dirname(name)
            (self.ok if os.access(folder, os.W_OK) else self.bad)(f"صلاحية الكتابة على مجلدها: {folder}")

        self.stdout.write("\nفهرس البحث")
        path = c["INDEX_DIR"] / INDEX_FILE
        self.info(str(path))
        if not path.exists():
            self.bad("الفهرس غير موجود: شغّل build_index")
        else:
            idx = get_index(c["INDEX_DIR"])
            self.ok(f"الفهرس يُحمَّل: {len(idx.ids)} نصاً")
            if n and len(idx.ids) != n:
                self.bad(f"عدد الفهرس ({len(idx.ids)}) يختلف عن القاعدة ({n}): أعد build_index")
            hits = idx.search(normalize("إنما الأعمال بالنيات"), k=1)
            (self.ok if hits and hits[0].score > 0.8 else self.bad)(
                f"بحث تجريبي «إنما الأعمال بالنيات»: {hits[0].score if hits else 'لا نتيجة'}")

        self.stdout.write("\nالدرر السنية")
        if not c["DORAR_ENABLED"]:
            self.info("معطّل (MUTHBIT_DORAR_ENABLED=0)")
        else:
            for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
                if os.environ.get(var):
                    self.info(f"وكيل مضبوط: {var}={os.environ[var]}")
            try:
                ip = socket.gethostbyname("dorar.net")
                self.ok(f"DNS: dorar.net ← {ip}")
            except OSError as exc:
                self.bad(f"DNS: تعذّر حل dorar.net: {exc}")
            t = time.time()
            try:
                data = dorar.request_api("انما الاعمال بالنيات", max(c["DORAR_TIMEOUT"], 15))
                items = dorar.parse_result_html((data.get("ahadith") or {}).get("result", ""))
                self.ok(f"الخدمة تستجيب في {time.time() - t:.1f} ثانية، وأرجعت {len(items)} رواية")
                if not items:
                    self.bad("لم تُحلَّل أي رواية: ربما تغيّرت بنية الاستجابة")
                if time.time() - t > c["DORAR_TIMEOUT"]:
                    self.bad(f"الاستجابة أبطأ من المهلة الحالية ({c['DORAR_TIMEOUT']} ث): "
                             f"زد MUTHBIT_DORAR_TIMEOUT")
            except dorar.DorarUnavailable as exc:
                self.bad(f"فشل الطلب بعد {time.time() - t:.1f} ثانية: {exc}")

        self.stdout.write("\nالنماذج اللغوية")
        for p in AIProvider.objects.filter(is_active=True):
            (self.ok if p.resolved_key() else self.bad)(
                f"{p.name}: {'المفتاح موجود' if p.resolved_key() else 'لا مفتاح (تحقق من متغير البيئة ' + (p.api_key_env or '') + ')'}")
        if not AIProvider.objects.filter(is_active=True).exists():
            self.info("لا نموذج مفعّل: يعمل الاستخراج القاعدي")
