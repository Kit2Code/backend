"""Google profile photos: fetched once per sign-in through the edge, kept in the database, served by
us, switched off by the user. Only the `picture` claim is used; the name Google sends is ignored.

Google's photo server is faked at the HTTP layer like the token and key requests (unit tests).
"""

import io
import struct
import zlib
from collections.abc import Iterator
from typing import Any

import pytest
import requests
import urllib3
from pytest_django.fixtures import Settings as SettingsWrapper

from accounts import avatars
from accounts.models import Avatar, GoogleIdentity, User
from tests import test_google_signin
from tests.conftest import EdgeClient
from tests.test_google_signin import SUB, FakeGoogle, sign_in

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_role")]

# The sign-in tests' fake Google (token and keys), as a fixture of this module too.
google = test_google_signin.google

PHOTO_URL = "http://edge:8090/photo"
PICTURE = "https://lh3.googleusercontent.com/a/ACg8ocJxYz_12-ab=s96-c"


def png(width: int = 2, height: int = 2) -> bytes:
    """A tiny valid PNG."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\x10\x20\x30" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9"


class PhotoServer:
    """What Google's photo server answers, and what it was asked."""

    def __init__(self, google: FakeGoogle) -> None:
        self.google = google  # answers the token and key requests
        self.status = 200
        self.body = png()
        self.headers: dict[str, str] = {"Content-Type": "image/png"}
        self.requests: list[requests.PreparedRequest] = []


@pytest.fixture
def photos(
    google: FakeGoogle, monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
) -> Iterator[PhotoServer]:
    """Like `google`, plus Google's photo server behind the edge's /photo route."""
    settings.GOOGLE_PHOTO_URL = PHOTO_URL
    server = PhotoServer(google)

    def send(
        adapter: requests.adapters.HTTPAdapter, request: requests.PreparedRequest, **kwargs: Any
    ) -> requests.Response:
        assert request.url is not None
        if request.url.startswith(PHOTO_URL + "/"):
            server.requests.append(request)
            status, body, headers = server.status, server.body, server.headers
        else:
            status, body = google.reply(request)
            headers = {"Content-Type": "application/json"}
        raw = urllib3.HTTPResponse(
            body=io.BytesIO(body), headers=headers, status=status, preload_content=False
        )
        return adapter.build_response(request, raw)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    yield server


@pytest.fixture
def alice() -> User:
    user = User.objects.create_user(username="alice", display_name="Alice A.")
    GoogleIdentity.objects.create(user=user, issuer="https://accounts.google.com", subject=SUB)
    return user


# ---- which picture addresses we accept ----


@pytest.mark.parametrize(
    ("picture", "path"),
    [
        (PICTURE, "/a/ACg8ocJxYz_12-ab=s256-c"),
        ("https://lh3.googleusercontent.com/a/abc", "/a/abc=s256-c"),
        ("https://lh3.googleusercontent.com/a-/AOh14Gh=s96-c", "/a-/AOh14Gh=s256-c"),
    ],
)
def test_google_photo_addresses_become_a_256px_path(picture: str, path: str) -> None:
    assert avatars.photo_path(picture) == path


@pytest.mark.parametrize(
    "picture",
    [
        "",
        "http://lh3.googleusercontent.com/a/abc",  # not https
        "https://lh4.googleusercontent.com/a/abc",
        "https://lh3.googleusercontent.com.evil.example/a/abc",
        "https://evil.example/lh3.googleusercontent.com/a/abc",
        "https://user@lh3.googleusercontent.com/a/abc",
        "https://lh3.googleusercontent.com:8443/a/abc",
        "https://lh3.googleusercontent.com/a/abc?sz=50",
        "https://lh3.googleusercontent.com/a/abc#x",
        "https://lh3.googleusercontent.com/a/../../oauth2/v3/certs",
        "https://lh3.googleusercontent.com/a/%2e%2e/x",
        "https://lh3.googleusercontent.com/",
        "https://lh3.googleusercontent.com/a//abc",
        "https://lh3.googleusercontent.com/a/" + "x" * 600,
        "javascript:alert(1)",
    ],
)
def test_other_addresses_are_refused(picture: str) -> None:
    assert avatars.photo_path(picture) is None


# ---- fetching at sign-in ----


def test_sign_in_fetches_and_keeps_the_photo(photos: PhotoServer, alice: User) -> None:
    client = EdgeClient()
    assert sign_in(client, photos.google, picture=PICTURE).status_code == 302
    [request] = photos.requests
    assert request.url == PHOTO_URL + "/a/ACg8ocJxYz_12-ab=s256-c"
    assert request.method == "GET"
    alice.refresh_from_db()
    assert alice.photo_updated_at is not None
    stored = Avatar.objects.get(user=alice)
    assert bytes(stored.image) == photos.body
    assert stored.content_type == "image/png"
    html = client.get("/").content.decode()
    assert '<img class="avatar" src="/users/alice/photo?v=' in html


def test_the_name_google_sends_is_not_kept(photos: PhotoServer, alice: User) -> None:
    sign_in(EdgeClient(), photos.google, picture=PICTURE, name="Real Name", given_name="R")
    alice.refresh_from_db()
    assert alice.display_name == "Alice A."
    assert "Real Name" not in str(User.objects.filter(pk=alice.pk).values().get())


def test_jpeg_is_kept_as_jpeg(photos: PhotoServer, alice: User) -> None:
    photos.body = JPEG
    photos.headers = {"Content-Type": "image/png"}  # the header is not trusted
    sign_in(EdgeClient(), photos.google, picture=PICTURE)
    assert Avatar.objects.get(user=alice).content_type == "image/jpeg"


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, b"<html>not an image</html>"),
        (200, b"GIF89a" + b"\x00" * 100),
        (200, b"\x89PNG\r\n\x1a\n" + b"\x00" * (256 * 1024)),  # over 256 KB
        (302, b""),
        (404, b""),
        (500, png()),
    ],
)
def test_bad_photos_are_not_kept_and_sign_in_still_works(
    photos: PhotoServer, alice: User, status: int, body: bytes
) -> None:
    photos.status, photos.body = status, body
    client = EdgeClient()
    assert sign_in(client, photos.google, picture=PICTURE).status_code == 302
    assert client.session.get("_auth_user_id") == str(alice.pk)
    assert not Avatar.objects.filter(user=alice).exists()


def test_a_bad_photo_does_not_replace_a_good_one(photos: PhotoServer, alice: User) -> None:
    sign_in(EdgeClient(), photos.google, picture=PICTURE)
    photos.body = b"not an image"
    sign_in(EdgeClient(), photos.google, picture=PICTURE)
    assert bytes(Avatar.objects.get(user=alice).image) == png()


def test_no_picture_claim_means_no_request(photos: PhotoServer, alice: User) -> None:
    sign_in(EdgeClient(), photos.google)
    sign_in(EdgeClient(), photos.google, picture="https://evil.example/a.png")
    assert photos.requests == []


def test_a_user_who_switched_photos_off_is_not_fetched(photos: PhotoServer, alice: User) -> None:
    User.objects.filter(pk=alice.pk).update(show_photo=False)
    sign_in(EdgeClient(), photos.google, picture=PICTURE)
    assert photos.requests == []
    assert not Avatar.objects.filter(user=alice).exists()


def test_a_new_account_gets_its_photo_after_the_first_sign_in_form(photos: PhotoServer) -> None:
    client = EdgeClient()
    assert sign_in(client, photos.google, picture=PICTURE)["Location"] == "/auth/welcome"
    assert photos.requests == []  # no account yet: nothing is fetched or kept
    response = client.post(
        "/auth/welcome",
        {"username": "newbie", "display_name": "New B.", "school": "", "accept_privacy": "on"},
    )
    assert response.status_code == 302
    assert Avatar.objects.filter(user__username="newbie").exists()


# ---- serving ----


def test_the_photo_is_served_by_us(photos: PhotoServer, alice: User) -> None:
    sign_in(EdgeClient(), photos.google, picture=PICTURE)
    response = EdgeClient().get("/users/alice/photo")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response.content == png()
    assert "max-age" in response["Cache-Control"]


def test_no_photo_is_404(alice: User) -> None:
    assert EdgeClient().get("/users/alice/photo").status_code == 404
    assert EdgeClient().get("/users/nobody/photo").status_code == 404


def test_a_hidden_or_disabled_users_photo_is_404(photos: PhotoServer, alice: User) -> None:
    sign_in(EdgeClient(), photos.google, picture=PICTURE)
    User.objects.filter(pk=alice.pk).update(show_photo=False)
    assert EdgeClient().get("/users/alice/photo").status_code == 404
    User.objects.filter(pk=alice.pk).update(show_photo=True, is_active=False)
    assert EdgeClient().get("/users/alice/photo").status_code == 404


def test_without_a_photo_the_avatar_is_initials(alice: User) -> None:
    client = EdgeClient()
    client.force_login(alice)
    html = client.get("/").content.decode()
    assert '<span class="avatar" aria-hidden="true">A</span>' in html
    assert "/users/alice/photo" not in html


# ---- the settings page ----


def signed_in(user: User) -> EdgeClient:
    client = EdgeClient()
    client.force_login(user)
    return client


def test_settings_change_name_and_photo(photos: PhotoServer, alice: User) -> None:
    sign_in(EdgeClient(), photos.google, picture=PICTURE)
    client = signed_in(alice)
    assert client.get("/settings").status_code == 200
    response = client.post("/settings", {"display_name": "Alicia"})
    assert response.status_code == 302
    alice.refresh_from_db()
    assert (alice.display_name, alice.show_photo) == ("Alicia", False)
    # Switching the photo off deletes it at once.
    assert not Avatar.objects.filter(user=alice).exists()
    assert alice.photo_updated_at is None


def test_settings_need_sign_in_and_csrf(alice: User) -> None:
    response = EdgeClient().get("/settings")
    assert response.status_code == 302
    client = EdgeClient(enforce_csrf_checks=True)
    client.force_login(alice)
    assert client.post("/settings", {"display_name": "X", "show_photo": "on"}).status_code == 403


def test_settings_check_the_input(alice: User) -> None:
    response = signed_in(alice).post("/settings", {"display_name": "x" * 101, "show_photo": "on"})
    assert response.status_code == 200
    alice.refresh_from_db()
    assert alice.display_name == "Alice A."


def test_settings_are_rate_limited(alice: User, settings: SettingsWrapper) -> None:
    client = signed_in(alice)
    statuses = [
        client.post("/settings", {"display_name": f"n{i}", "show_photo": "on"}).status_code
        for i in range(40)
    ]
    assert 429 in statuses
