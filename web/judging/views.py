"""Public judging pages: submit, and the submission page."""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Max, QuerySet, Sum
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from accounts.models import User
from core import ratelimit
from core.views import message_page
from judging.forms import SubmissionFilterForm, SubmitForm
from judging.models import JudgeJob, Submission, UserProblemBest
from problems import store
from problems.views import visible_problems

PAGE_SIZE = 50
RANKING_PAGE_SIZE = 100

# Source (64 KB) plus the form around it fits easily; anything bigger is refused before Django
# reads the body.
MAX_SUBMIT_BODY_BYTES = 256 * 1024


def _too_big(request: HttpRequest) -> HttpResponse:
    return message_page(request, _("Too big"), _("Submissions can be at most 64 KB."), status=413)


def _body_too_big(request: HttpRequest) -> bool:
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return True
    return length > MAX_SUBMIT_BODY_BYTES


def _limit_reached(request: HttpRequest, user: User) -> HttpResponse | None:
    """The 429 page if `user` may not submit right now, else None.

    Call it inside the transaction that holds the user's row lock, so two submits sent at the
    same moment are checked one after the other. The cooldown and the waiting count come from
    the user's Submission rows; the hourly count from core.ratelimit (see docs/DECISIONS.md).
    """
    mine = Submission.objects.filter(user=user)
    cooldown = timedelta(seconds=settings.GRADER_SUBMIT_COOLDOWN_SECONDS)
    if mine.filter(created_at__gt=timezone.now() - cooldown).exists():
        message = _("Please wait %(seconds)d seconds between submissions.") % {
            "seconds": settings.GRADER_SUBMIT_COOLDOWN_SECONDS
        }
    elif (
        mine.filter(status__in=[Submission.Status.QUEUED, Submission.Status.JUDGING]).count()
        >= settings.GRADER_SUBMIT_MAX_WAITING
    ):
        message = _("You have %(count)d submissions waiting for the judge. Please wait.") % {
            "count": settings.GRADER_SUBMIT_MAX_WAITING
        }
    elif not ratelimit.hit(
        ratelimit.Limit("submit-hour", settings.GRADER_SUBMIT_PER_HOUR, timedelta(hours=1)),
        str(user.pk),
    ):
        message = _("You have reached %(count)d submissions this hour. Please come back later.") % {
            "count": settings.GRADER_SUBMIT_PER_HOUR
        }
    else:
        return None
    return message_page(request, _("Too many submissions"), message, status=429)


def submit(request: HttpRequest, slug: str) -> HttpResponse:
    """Show the submit form; on POST, queue the submission and go to its page."""
    problem = get_object_or_404(visible_problems().select_related("current_version"), slug=slug)
    version = problem.current_version
    if version is None:  # excluded by visible_problems(); this check is for the type checker
        raise Http404
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), reverse("google-start"))
    languages = store.problem_format(version).languages
    if request.method != "POST":
        form = SubmitForm(languages=languages)
        return render(request, "judging/submit.html", {"problem": problem, "form": form})

    if _body_too_big(request):
        return _too_big(request)
    form = SubmitForm(request.POST, request.FILES, languages=languages)
    if not form.is_valid():
        return render(request, "judging/submit.html", {"problem": problem, "form": form})
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        refused = _limit_reached(request, user)
        if refused is not None:
            return refused
        submission = Submission.objects.create(
            user=user,
            problem=problem,
            problem_version=version,
            language=form.cleaned_data["language"],
            source=form.cleaned_data["code"],
        )
        JudgeJob.objects.create(
            kind=JudgeJob.Kind.JUDGE, priority=JudgeJob.Priority.FRESH, submission=submission
        )
    return redirect("submission", submission.pk)


def _visible_submission(pk: int) -> Submission:
    """The submission, if its problem is visible; 404 otherwise."""
    return get_object_or_404(
        Submission.objects.select_related("problem", "user"),
        pk=pk,
        problem__in=visible_problems(),
    )


def _results_context(request: HttpRequest, submission: Submission) -> dict[str, object]:
    """What the results section needs. The compiler output quotes source lines, so only the author
    sees it (`is_author`); everyone else just sees the verdict."""
    return {
        "submission": submission,
        "tests": submission.tests.order_by("name"),
        "waiting": submission.status != Submission.Status.DONE,
        "is_author": request.user.is_authenticated and submission.user_id == request.user.pk,
    }


@require_safe
@never_cache
def submission_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """One submission: who sent what, the result (live while judging), the code for its author."""
    submission = _visible_submission(pk)
    return render(request, "judging/submission.html", _results_context(request, submission))


@require_safe
@never_cache
def submission_results(request: HttpRequest, pk: int) -> HttpResponse:
    """Just the results section: htmx on the submission page fetches it every 2 s while judging."""
    submission = _visible_submission(pk)
    return render(request, "judging/results.html", _results_context(request, submission))


@require_safe
@never_cache
def submission_source(request: HttpRequest, pk: int) -> HttpResponse:
    """The code as plain text, for its author only. Anyone else gets 404, as if it weren't there."""
    submission = _visible_submission(pk)
    if not request.user.is_authenticated or submission.user_id != request.user.pk:
        raise Http404
    return HttpResponse(submission.source, content_type="text/plain; charset=utf-8")


def _listed() -> QuerySet[Submission]:
    """Submissions on visible problems, with what the list shows (never the code)."""
    return (
        Submission.objects.filter(problem__in=visible_problems())
        .select_related("problem", "user")
        .defer("source", "compile_log")
    )


def _list_page(
    request: HttpRequest, submissions: QuerySet[Submission], context: dict[str, object]
) -> HttpResponse:
    page = Paginator(submissions, PAGE_SIZE).get_page(request.GET.get("page"))
    # The current filters, so the page links keep them.
    query = request.GET.copy()
    query.pop("page", None)
    context = {**context, "page": page, "query": query.urlencode()}
    return render(request, "judging/list.html", context)


@require_safe
def all_submissions(request: HttpRequest) -> HttpResponse:
    """Everyone's submissions, newest first, filtered by problem, user and verdict."""
    form = SubmissionFilterForm(request.GET)
    submissions = _listed().order_by("-pk")
    if not form.is_valid():
        submissions = submissions.none()
    else:
        if form.cleaned_data["problem"]:
            submissions = submissions.filter(problem__slug=form.cleaned_data["problem"])
        if form.cleaned_data["user"]:
            submissions = submissions.filter(user__username=form.cleaned_data["user"])
        if form.cleaned_data["verdict"]:
            submissions = submissions.filter(verdict=form.cleaned_data["verdict"])
    return _list_page(request, submissions, {"title": _("Submissions"), "form": form})


@require_safe
def my_submissions(request: HttpRequest) -> HttpResponse:
    """The signed-in user's own submissions, newest first."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), reverse("google-start"))
    submissions = _listed().filter(user=request.user).order_by("-created_at")
    return _list_page(request, submissions, {"title": _("My submissions")})


@require_safe
def ranking(request: HttpRequest) -> HttpResponse:
    """Users by the sum of their best scores on visible problems.

    Ties go to whoever reached their total first (the latest `reached_at` among their best
    scores), then to the username. Disabled users and zero totals are left out.
    """
    rows = (
        UserProblemBest.objects.filter(problem__in=visible_problems(), user__is_active=True)
        .values("user__username", "user__display_name", "user__school")
        .annotate(total=Sum("best_score"), finished_at=Max("reached_at"))
        .filter(total__gt=0)
        .order_by("-total", "finished_at", "user__username")
    )
    page = Paginator(rows, RANKING_PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "judging/ranking.html", {"page": page})


@require_safe
def profile(request: HttpRequest, username: str) -> HttpResponse:
    """A user's public page: problems solved (full marks) and attempted, on visible problems."""
    person = get_object_or_404(User, username=username, is_active=True)
    bests = (
        UserProblemBest.objects.filter(user=person, problem__in=visible_problems())
        .select_related("problem__current_version")
        .order_by("problem__slug")
    )
    solved: list[UserProblemBest] = []
    attempted: list[UserProblemBest] = []
    for row in bests:
        version = row.problem.current_version
        if version is None:  # excluded by visible_problems(); this check is for the type checker
            continue
        full_marks = row.best_score >= store.problem_format(version).max_score() - 1e-9
        (solved if full_marks else attempted).append(row)
    context = {
        "person": person,
        "solved": solved,
        "attempted": attempted,
        "total": sum(row.best_score for row in bests),
    }
    return render(request, "judging/profile.html", context)
