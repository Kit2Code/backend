"""The production edge image (deploy/edge/): zones, blocked paths, body limits, security headers,
and the :8090 Google allow-list.

The image runs on its own throw-away Docker network with small echo servers in place of the two web
roles and Google (the Caddyfile reads their addresses from environment variables whose defaults are
the production ones). Each echo server answers with the request it got, so a test can tell whether
a request reached a backend and what headers it carried. Requests are sent from inside those
containers, so the source address is the one :8090 checks.
"""

import json
import shutil
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest

from deploy.tests.conftest import REPO

DOCKER = shutil.which("docker") or "docker"
EDGE_IMAGE = "grader-edge:prod-test"
# The web image's Python base, already pulled for the dev stack.
PYTHON_IMAGE = (
    "python:3.12-slim-bookworm"
    "@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258"
)
NETWORK = "grader-edge-test"
SUBNET = "10.89.92"
EDGE, WEB, ADMIN, GOOGLE, OTHER = (f"{SUBNET}.{n}" for n in (10, 20, 21, 30, 40))
PREFIX = "grader-edge-test-"

ECHO_SERVER = """
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Echo(BaseHTTPRequestHandler):
    def answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = json.dumps({"echo": True, "method": self.command, "path": self.path,
                           "headers": {k.lower(): v for k, v in self.headers.items()},
                           "body_len": length}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    do_GET = do_POST = do_HEAD = do_PUT = answer
    def log_message(self, *args):
        pass

ThreadingHTTPServer(("0.0.0.0", 8000), Echo).serve_forever()
"""

# Runs in a client container: one HTTP request, prints the status, headers and body as JSON.
REQUEST = """
import json, sys, urllib.error, urllib.request
method, url, size, headers = sys.argv[1], sys.argv[2], int(sys.argv[3]), json.loads(sys.argv[4])
body = b"x" * size if size else None
request = urllib.request.Request(url, data=body, method=method, headers=headers)
try:
    with urllib.request.urlopen(request, timeout=10) as response:
        status, head, text = response.status, dict(response.headers), response.read().decode()
except urllib.error.HTTPError as error:
    status, head = error.code, dict(error.headers)
    try:
        text = error.read().decode()
    except OSError:
        text = ""
except Exception as error:
    print(json.dumps({"error": type(error).__name__}))
    raise SystemExit
head = {k.lower(): v for k, v in head.items()}
print(json.dumps({"status": status, "headers": head, "body": text}))
"""


def docker(
    *argv: str, check: bool = True, timeout: float = 600
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv list
        [DOCKER, *argv], capture_output=True, text=True, timeout=timeout, check=check
    )


def remove_everything() -> None:
    names = docker("ps", "-aq", "--filter", f"name={PREFIX}", check=False).stdout.split()
    if names:
        docker("rm", "-f", *names, check=False)
    docker("network", "rm", NETWORK, check=False)


@pytest.fixture(scope="module", autouse=True)
def edge() -> Iterator[None]:
    remove_everything()
    docker("build", "-q", "-f", str(REPO / "deploy/edge/Dockerfile"), "-t", EDGE_IMAGE, str(REPO))
    docker("network", "create", "--internal", "--subnet", f"{SUBNET}.0/24", NETWORK)
    for name, ip in (("web", WEB), ("admin", ADMIN), ("google", GOOGLE), ("other", OTHER)):
        docker("run", "-d", "--name", PREFIX + name, "--network", NETWORK, "--ip", ip,
               PYTHON_IMAGE, "python", "-c", ECHO_SERVER)  # fmt: skip
    env = {
        "GRADER_WEB_UPSTREAM": f"{WEB}:8000",
        "GRADER_WEB_ADMIN_UPSTREAM": f"{ADMIN}:8000",
        "GRADER_EDGE_APP_IP": EDGE,
        "GRADER_WEB_IP": WEB,
        "GRADER_WEB_ADMIN_IP": ADMIN,
        "GRADER_GOOGLE_TOKEN_UPSTREAM": f"http://{GOOGLE}:8000",
        "GRADER_GOOGLE_CERTS_UPSTREAM": f"http://{GOOGLE}:8000",
    }
    env_args = [arg for key, value in env.items() for arg in ("-e", f"{key}={value}")]
    docker("run", "-d", "--name", PREFIX + "edge", "--network", NETWORK, "--ip", EDGE, *env_args,
           EDGE_IMAGE)  # fmt: skip
    # Wait until the edge answers.
    for _ in range(50):
        if "status" in send("GET", f"http://{EDGE}:8080/healthz"):
            break
        subprocess.run(["sleep", "0.2"], check=False)  # noqa: S607 - plain sleep
    yield
    remove_everything()


def send(
    method: str,
    url: str,
    size: int = 0,
    headers: dict[str, str] | None = None,
    source: str = "other",
) -> dict[str, Any]:
    """One request from the container `source` (its address is what :8090 checks)."""
    result = docker(
        "exec", PREFIX + source, "python", "-c", REQUEST, method, url, str(size),
        json.dumps(headers or {}), check=False, timeout=60,
    )  # fmt: skip
    return dict(json.loads(result.stdout or '{"error": "no output"}'))


def reached(answer: dict[str, Any]) -> dict[str, Any] | None:
    """The request the backend saw, or None if the edge answered by itself."""
    if answer.get("status") != 200:
        return None
    body = json.loads(answer["body"])
    return body if body.get("echo") else None


# ---- the public site (:8080) ----


@pytest.mark.parametrize(
    "path",
    [
        "/admin", "/admin/", "/ADMIN", "/Admin/login/", "//admin", "///admin/", "/%61dmin",
        "/%41DMIN/", "/admin%2F", "/api/admin/x", "/internal", "/internal/judge/claim",
        "/INTERNAL/judge/claim", "//internal/judge/claim", "/setup", "/setup?token=x", "/SETUP",
        "/./admin", "/x/../admin", "/problems/../admin/",
    ],
)  # fmt: skip
def test_admin_setup_and_internal_paths_never_reach_the_public_site(path: str) -> None:
    answer = send("GET", f"http://{EDGE}:8080{path}")
    assert answer.get("status") == 404, answer
    assert reached(answer) is None


def test_public_requests_carry_the_public_zone_whatever_the_client_sent() -> None:
    answer = send("GET", f"http://{EDGE}:8080/problems", headers={"X-Edge-Zone": "admin"})
    seen = reached(answer)
    assert seen is not None, answer
    assert seen["headers"]["x-edge-zone"] == "public"
    assert seen["headers"]["x-forwarded-proto"] == "https"


def test_ordinary_public_paths_that_look_similar_still_work() -> None:
    for path in ("/problems/administration", "/users/admin_fan", "/problems/setup-2"):
        assert reached(send("GET", f"http://{EDGE}:8080{path}")) is not None, path


def test_public_request_bodies_are_capped() -> None:
    assert reached(send("POST", f"http://{EDGE}:8080/problems/x/submit", size=64 * 1024))
    assert (
        send("POST", f"http://{EDGE}:8080/problems/x/submit", size=2 * 1024 * 1024)["status"] == 413
    )


# ---- the admin site (:8081) ----


def test_admin_requests_carry_the_admin_zone() -> None:
    seen = reached(send("GET", f"http://{EDGE}:8081/admin/", headers={"X-Edge-Zone": "public"}))
    assert seen is not None
    assert seen["headers"]["x-edge-zone"] == "admin"


@pytest.mark.parametrize(
    "path", ["/internal", "/internal/judge/claim", "/Internal/judge/claim", "//internal/x",
             "/%69nternal/judge/claim"],
)  # fmt: skip
def test_the_judge_api_is_never_reachable_through_the_admin_port(path: str) -> None:
    answer = send("POST", f"http://{EDGE}:8081{path}", size=10)
    assert answer.get("status") == 404, answer
    assert reached(answer) is None


def test_only_the_import_page_takes_big_uploads() -> None:
    big = 2 * 1024 * 1024
    assert (
        send("POST", f"http://{EDGE}:8081/admin/core/sitesetting/1/change/", size=big)["status"]
        == 413
    )
    seen = reached(send("POST", f"http://{EDGE}:8081/admin/grader/import/", size=big))
    assert seen is not None and seen["body_len"] == big


# ---- headers on both sites ----


@pytest.mark.parametrize("port", [8080, 8081])
def test_security_headers(port: int) -> None:
    headers = send("GET", f"http://{EDGE}:{port}/healthz")["headers"]
    csp = headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert "server" not in headers


# ---- Google egress (:8090) ----


def test_the_public_web_role_may_swap_a_code_and_fetch_the_keys() -> None:
    token = reached(send("POST", f"http://{EDGE}:8090/token", size=100, source="web"))
    assert token is not None and token["path"] == "/token"
    certs = reached(send("GET", f"http://{EDGE}:8090/certs?x=1", source="web"))
    assert certs is not None and certs["path"] == "/oauth2/v3/certs"  # query string dropped


def test_web_admin_may_only_fetch_the_keys() -> None:
    assert reached(send("GET", f"http://{EDGE}:8090/certs", source="admin")) is not None
    for method, path in (("POST", "/token"), ("GET", "/token"), ("POST", "/certs"), ("GET", "/")):
        answer = send(method, f"http://{EDGE}:8090{path}", size=10 if method == "POST" else 0,
                      source="admin")  # fmt: skip
        assert answer.get("status") == 404, (method, path, answer)


def test_anyone_else_gets_nothing_from_the_egress() -> None:
    for method, path in (("POST", "/token"), ("GET", "/certs")):
        answer = send(method, f"http://{EDGE}:8090{path}", size=10 if method == "POST" else 0)
        assert answer.get("status") == 404, answer


def test_the_egress_answers_only_its_two_requests() -> None:
    for method, path in (("GET", "/token"), ("POST", "/certs"), ("GET", "/"),
                         ("GET", "/oauth2/v3/certs"), ("GET", "/token/../certs/x")):  # fmt: skip
        answer = send(method, f"http://{EDGE}:8090{path}", size=10 if method == "POST" else 0,
                      source="web")  # fmt: skip
        assert answer.get("status") == 404, (method, path, answer)


def test_token_request_bodies_are_capped() -> None:
    assert send("POST", f"http://{EDGE}:8090/token", size=20 * 1024, source="web")["status"] == 413


def test_the_edge_health_listener_is_only_inside_the_container() -> None:
    assert "error" in send("GET", f"http://{EDGE}:8079/")
    inside = docker("exec", PREFIX + "edge", "wget", "-qO-", "http://127.0.0.1:8079/", check=False)
    assert inside.stdout.strip() == "ok"
