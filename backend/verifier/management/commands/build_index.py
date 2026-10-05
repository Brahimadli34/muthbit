from django.conf import settings
from django.core.management.base import BaseCommand

from verifier.models import Text
from verifier.retrieval import SearchIndex


class Command(BaseCommand):
    help = "بناء فهرس البحث من النصوص المخزنة"

    def handle(self, **opts):
        c = settings.MUTHBIT
        rows = list(Text.objects.filter(searchable=True).values_list("id", "text_norm"))
        if not rows:
            self.stdout.write(self.style.WARNING("لا توجد نصوص. شغّل import_sources أولاً."))
            return
        ids, norms = zip(*rows)
        model = c["EMBEDDING_MODEL"] if c["USE_EMBEDDINGS"] else None
        SearchIndex.build(ids, norms, embedding_model=model).save(c["INDEX_DIR"])
        mode = f"لفظي + دلالي ({model})" if model else "لفظي"
        self.stdout.write(self.style.SUCCESS(f"بُني الفهرس لـ {len(ids)} نصاً ({mode})."))
