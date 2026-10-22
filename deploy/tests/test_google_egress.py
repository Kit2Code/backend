"""The edge's :8090 listener: the public web role's only way to reach Google (here: the fake).

It must forward exactly `POST /token` and `GET /certs`, refuse everything else, cap request
bodies, and be usable only by the public web role (web-admin may only `GET /certs`, for the Sign-in
page's test button): not by the worker or the host.
"""

import json
import socket
import subprocess

import pytest

from deploy.tests.conftest import run_in

# Runs inside a container: makes one HTTP request and prints {"status": …} or {"error": …}.
PROBE = """
import json, sys, urllib.error, urllib.request
method, url, size = sys.argv[1], sys.argv[2], int(sys.argv[3])
body = b"x" * size if method == "POST" else None
request = urllib.request.Request(url, data=body, method=method)
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        print(json.dumps({"status": response.status, "body": response.read(65536).decode()}))
except urllib.error.HTTPError as error:
    try:
        text = error.read(65536).decode()
    except OSError:  # the server may close the connection right after an error status
        text = ""
    print(json.dumps({"status": error.code, "body": text}))
except Exception as error:
    print(json.dumps({"error": type(error).__name__}))
"""

EGRESS = "http://edge:8090"


def probe(service: str, method: str, url: str, size: int = 0) -> dict[str, object]:
    result = run_in(service, "python", "-c", PROBE, method, url, str(size))
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


def test_certs_are_forwarded() -> None:
    answer = probe("web", "GET", f"{EGRESS}/certs")
    assert answer["status"] == 200
    keys = json.loads(str(answer["body"]))["keys"]
    assert keys and keys[0]["kty"] == "RSA" and "d" not in keys[0]


def test_token_is_forwarded() -> None:
    # No valid client credentials: the provider itself answers with an OAuth error.
    answer = probe("web", "POST", f"{EGRESS}/token", size=10)
    assert answer["status"] == 401
    assert json.loads(str(answer["body"])) == {"error": "invalid_client"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/token"),
        ("POST", "/certs"),
        ("GET", "/authorize"),
        ("POST", "/authorize"),
        ("GET", "/healthz"),
        ("GET", "/"),
        ("GET", "/certs/"),
        ("GET", "/certs/../authorize"),
        ("GET", "/x/../authorize"),
        ("GET", "/%61uthorize"),
        ("POST", "/token/extra"),
    ],
)
def test_everything_else_is_404(method: str, path: str) -> None:
    answer = probe("web", method, EGRESS + path, size=10 if method == "POST" else 0)
    assert answer["status"] == 404


def test_query_strings_are_not_forwarded() -> None:
    # The fake would show its sign-in page for /authorize?…; /certs?… must still be just the keys.
    answer = probe("web", "GET", f"{EGRESS}/certs?redirect_uri=http://evil.example/")
    assert answer["status"] == 200
    assert "keys" in json.loads(str(answer["body"]))


def test_large_request_bodies_are_refused() -> None:
    answer = probe("web", "POST", f"{EGRESS}/token", size=20 * 1024)
    assert answer["status"] == 413


def test_web_admin_may_only_fetch_the_certs() -> None:
    # The Sign-in page's test button: Google's public keys, nothing else.
    answer = probe("web-admin", "GET", f"{EGRESS}/certs")
    assert answer["status"] == 200
    assert "keys" in json.loads(str(answer["body"]))
    assert probe("web-admin", "POST", f"{EGRESS}/token", size=10)["status"] == 404
    assert probe("web-admin", "GET", f"{EGRESS}/token")["status"] == 404
    assert probe("web-admin", "POST", f"{EGRESS}/certs", size=10)["status"] == 404


def test_worker_cannot_reach_it() -> None:
    result = run_in("worker", "python3", "-c", PROBE, "GET", f"{EGRESS}/certs", "0")
    assert "error" in json.loads(result.stdout)


def edge_addresses() -> list[str]:
    container = run_in("edge", "hostname").stdout.strip()
    inspect = subprocess.run(  # noqa: S603 - fixed argv list
        ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    networks = json.loads(inspect.stdout)
    return [net["IPAddress"] for net in networks.values() if net["IPAddress"]]


def test_host_cannot_reach_it() -> None:
    addresses = edge_addresses()
    assert len(addresses) == 3
    for address in [*addresses, "127.0.0.1"]:
        with socket.socket() as sock:
            sock.settimeout(2)
            if sock.connect_ex((address, 8090)) != 0:
                continue  # refused or unreachable: good
            # Connected (the host shares a bridge with app_net): it must still get nothing.
            sock.sendall(b"GET /certs HTTP/1.1\r\nHost: edge\r\nConnection: close\r\n\r\n")
            reply = sock.recv(200)
            assert reply.startswith(b"HTTP/1.1 404"), (address, reply)


def test_web_cannot_reach_the_provider_directly() -> None:
    answer = probe("web", "GET", "http://fake-oidc:8099/certs")
    assert "error" in answer


def test_web_has_no_internet() -> None:
    answer = probe("web", "GET", "https://www.google.com/")
    assert "error" in answer
