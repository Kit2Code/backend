"""The API's URLs, mounted at `api/` in config/urls_public.py only."""

from django.urls import URLPattern, path

from api.views.site import SiteView

urlpatterns: list[URLPattern] = [
    path("site", SiteView.as_view(), name="api-site"),
]
