"""The API's URLs, mounted at `api/` in config/urls_public.py only."""

from django.urls import URLPattern, path

from accounts.views import photo
from api.views.archive import GroupDetailView, GroupListView
from api.views.auth import MeView
from api.views.problem_detail import ProblemDetailView
from api.views.problems import ProblemListView
from api.views.site import SiteView
from problems.views import problem_file

urlpatterns: list[URLPattern] = [
    path("site", SiteView.as_view(), name="api-site"),
    path("auth/me", MeView.as_view(), name="api-me"),
    path("groups", GroupListView.as_view(), name="api-groups"),
    path("groups/<slug:slug>", GroupDetailView.as_view(), name="api-group"),
    path("groups/<slug:slug>/<slug:sub_slug>", GroupDetailView.as_view(), name="api-subgroup"),
    path("problems", ProblemListView.as_view(), name="api-problems"),
    path("problems/<slug:slug>", ProblemDetailView.as_view(), name="api-problem"),
    # The old pages' file view: the same allow-list (problems/files.py).
    path("problems/<slug:slug>/files/<path:name>", problem_file, name="api-problem-file"),
    # The old pages' photo view: same rules and caching (contract §10, "Photos").
    path("users/<slug:username>/photo", photo, name="api-user-photo"),
]
