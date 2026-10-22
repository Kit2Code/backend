"""URLs of the public role (the practice site). Admin, setup and internal URLs never go here."""

from django.urls import URLPattern, URLResolver, include, path

from accounts.views import google_callback, google_start, logout_everywhere, logout_view, welcome
from core.views import home, privacy
from judging.views import (
    all_submissions,
    my_submissions,
    profile,
    ranking,
    submission_detail,
    submission_results,
    submission_source,
    submit,
)
from problems.views import problem_detail, problem_file, problem_list

urlpatterns: list[URLPattern | URLResolver] = [
    path("", home, name="home"),
    # Django's language switch (set_language): POST with a CSRF token.
    path("i18n/", include("django.conf.urls.i18n")),
    path("privacy", privacy, name="privacy"),
    path("problems", problem_list, name="problems"),
    path("problems/<slug:slug>", problem_detail, name="problem"),
    path("problems/<slug:slug>/files/<path:name>", problem_file, name="problem-file"),
    path("problems/<slug:slug>/submit", submit, name="submit"),
    path("ranking", ranking, name="ranking"),
    path("users/<slug:username>", profile, name="profile"),
    path("submissions", all_submissions, name="submissions"),
    path("submissions/mine", my_submissions, name="my-submissions"),
    path("submissions/<int:pk>", submission_detail, name="submission"),
    path("submissions/<int:pk>/results", submission_results, name="submission-results"),
    path("submissions/<int:pk>/source", submission_source, name="submission-source"),
    path("auth/google/start", google_start, name="google-start"),
    path("auth/google/callback", google_callback, name="google-callback"),
    path("auth/welcome", welcome, name="welcome"),
    path("auth/logout", logout_view, name="logout"),
    path("auth/logout-everywhere", logout_everywhere, name="logout-everywhere"),
]
