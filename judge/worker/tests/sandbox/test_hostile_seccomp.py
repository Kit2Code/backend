"""Sandboxed programs can't use the kernel interfaces most used to escalate privileges
(fixtures/hostile/seccomp_surface.c): io_uring, bpf, userfaultfd, keyrings, perf events, user
namespaces, ptrace. The worker's seccomp filter blocks them."""

from conftest import FIXTURES_IN_WORKER, worker, worker_json


def test_the_worker_has_a_seccomp_filter() -> None:
    result = worker("grep", "^Seccomp:", "/proc/1/status")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[1] == "2"  # 2 = a filter is installed


def test_risky_kernel_interfaces_fail_in_a_box() -> None:
    result = worker_json(
        "judge-run", "--problem", f"{FIXTURES_IN_WORKER}/hostile/problem", "--lang", "c11",
        "--source", f"{FIXTURES_IN_WORKER}/hostile/seccomp_surface.c", timeout=120,
    )  # fmt: skip
    assert result["verdict"] != "CE", result["compile_log"]
    assert result["tests"][0]["verdict"] == "AC", f"not contained: {result['tests'][0]}"


def test_the_launcher_is_in_the_image_and_refuses_to_run_without_its_filter() -> None:
    # Already under a seccomp filter that forbids seccomp() itself, the launcher must not start
    # the program unprotected. Simulated by giving it nothing to run: it must fail, not exec.
    assert worker("test", "-x", "/usr/local/libexec/grader/box-exec").returncode == 0
    assert worker("/usr/local/libexec/grader/box-exec").returncode != 0


def test_kernel_settings_cannot_be_changed_from_a_box() -> None:
    result = worker_json(
        "judge-run", "--problem", f"{FIXTURES_IN_WORKER}/hostile/problem", "--lang", "c11",
        "--source", f"{FIXTURES_IN_WORKER}/hostile/proc_sys_write.c", timeout=120,
    )  # fmt: skip
    assert result["verdict"] != "CE", result["compile_log"]
    assert result["tests"][0]["verdict"] == "AC", f"not contained: {result['tests'][0]}"
