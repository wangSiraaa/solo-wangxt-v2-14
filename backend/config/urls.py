from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import TemplateView
from django.views.static import serve

from config.settings import FRONTEND_DIST

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("refunds.urls")),
    # 前端构建产物（Vite 输出到 frontend/dist）
    re_path(
        r"^assets/(?P<path>.*)$",
        serve,
        {"document_root": str(FRONTEND_DIST / "assets")},
    ),
    re_path(r"^$", TemplateView.as_view(template_name="index.html")),
]
