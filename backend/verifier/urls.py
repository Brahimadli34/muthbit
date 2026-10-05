from django.urls import path

from . import views

urlpatterns = [
    path("verify", views.verify),
    path("reports", views.report),
    path("sources", views.sources),
    path("health", views.health),
    path("specialist/<int:text_id>", views.specialist),
]
