"""The API's URLs, mounted at `api/` in config/urls_public.py only."""

from django.urls import URLPattern, path

from accounts.views import photo
from api.views.archive import GroupDetailView, GroupListView
from api.views.auth import MeView
from api.views.problem_detail import ProblemDetailView
from api.views.problems import ProblemListView
from api.views.signin import (
    LogoutEverywhereView,
    LogoutView,
    MyAccountView,
    RegisterView,
    google_callback,
    google_start,
)
from api.views.site import SiteView
from api.views.submissions import MySubmissionListView, SubmissionDetailView, SubmissionListView
from api.views.submit import SubmitView
from api.views.users import ProfileView, RankingView
from judging.views import submission_source
from problems.views import problem_file

urlpatterns: list[URLPattern] = [
    path("submissions", SubmissionListView.as_view(), name="api-submissions"),
    path("submissions/mine", MySubmissionListView.as_view(), name="api-my-submissions"),
    path("submissions/<int:pk>", SubmissionDetailView.as_view(), name="api-submission"),
    # The old pages' source view: the author only, 404 for anyone else.
    path("submissions/<int:pk>/source", submission_source, name="api-submission-source"),
    path("site", SiteView.as_view(), name="api-site"),
    path("auth/me", MeView.as_view(), name="api-me"),
    path("auth/google/start", google_start, name="api-google-start"),
    path("auth/google/callback", google_callback, name="api-google-callback"),
    path("auth/register", RegisterView.as_view(), name="api-register"),
    path("auth/logout", LogoutView.as_view(), name="api-logout"),
    path("auth/logout-everywhere", LogoutEverywhereView.as_view(), name="api-logout-everywhere"),
    path("me", MyAccountView.as_view(), name="api-my-account"),
    path("groups", GroupListView.as_view(), name="api-groups"),
    path("groups/<slug:slug>", GroupDetailView.as_view(), name="api-group"),
    path("groups/<slug:slug>/<slug:sub_slug>", GroupDetailView.as_view(), name="api-subgroup"),
    path("problems", ProblemListView.as_view(), name="api-problems"),
    path("problems/<slug:slug>", ProblemDetailView.as_view(), name="api-problem"),
    path("problems/<slug:slug>/submissions", SubmitView.as_view(), name="api-submit"),
    # The old pages' file view: the same allow-list (problems/files.py).
    path("problems/<slug:slug>/files/<path:name>", problem_file, name="api-problem-file"),
    # The old pages' photo view: same rules and caching (contract §10, "Photos").
    path("users/<slug:username>/photo", photo, name="api-user-photo"),
    path("users/<slug:username>", ProfileView.as_view(), name="api-user"),
    path("ranking", RankingView.as_view(), name="api-ranking"),
]
