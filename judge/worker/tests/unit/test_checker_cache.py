"""The compiled-checker cache: safe with many threads, and a damaged entry is rebuilt."""

import threading
from pathlib import Path
from typing import cast

import pytest

from grader_common.importer.native import load_problem
from grader_worker import checkers
from grader_worker.slots import Slot

REPO = Path(__file__).resolve().parents[4]
CMS_CODES = REPO / "fixtures" / "judge" / "cms-codes"
FAKE_SLOT = cast(Slot, object())  # the compile step is replaced, so the slot is never used


def fake_binary(size: int = 4 << 20, fill: bytes = b"x") -> bytes:
    """Looks like an ELF executable: the magic number, then filler."""
    return b"\x7fELF" + fill * (size - 4)


def cached_files(cache: Path) -> list[Path]:
    return sorted(p for p in (cache / "checkers").rglob("*") if p.is_file())


def test_threads_writing_the_same_entry_leave_a_complete_checker(tmp_path: Path) -> None:
    path = tmp_path / "checkers" / "slug" / "key" / "checker"
    binaries = [fake_binary(fill=bytes([65 + i])) for i in range(8)]
    errors: list[BaseException] = []
    start = threading.Barrier(len(binaries))

    def write(binary: bytes) -> None:
        start.wait()
        try:
            for _ in range(5):
                checkers._write_cached(path, binary)
        except BaseException as error:  # noqa: BLE001 - any error fails the test below
            errors.append(error)

    threads = [threading.Thread(target=write, args=(b,)) for b in binaries]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert path.read_bytes() in binaries  # one whole binary, never a mix or a truncated one
    # No temporary files are left, only the binary and its checksum.
    assert sorted(p.name for p in path.parent.iterdir()) == ["checker", "checker.sha256"]


def prepare_with(cache: Path, monkeypatch: pytest.MonkeyPatch, binary: bytes) -> list[int]:
    """Run prepare() for cms-codes with a fake compile step; return a list that counts compiles."""
    compiles: list[int] = []

    def fake_compile(slot: Slot, files: dict[str, bytes], command: list[str]) -> bytes:
        compiles.append(1)
        return binary

    monkeypatch.setattr(checkers, "_compile", fake_compile)
    checker = checkers.prepare(
        load_problem(CMS_CODES / "problem.yaml"), CMS_CODES, FAKE_SLOT, cache
    )
    assert checker.binary == binary
    return compiles


def test_a_cached_checker_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = fake_binary()
    assert len(prepare_with(tmp_path, monkeypatch, binary)) == 1
    assert len(prepare_with(tmp_path, monkeypatch, binary)) == 0


@pytest.mark.parametrize("damaged", [b"", b"#!/bin/sh\n", b"\x7fEL", b"MZ" + b"\0" * 100])
def test_a_damaged_cache_entry_is_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damaged: bytes
) -> None:
    binary = fake_binary()
    prepare_with(tmp_path, monkeypatch, binary)
    [entry] = [p for p in cached_files(tmp_path) if p.name == "checker"]
    entry.write_bytes(damaged)  # e.g. a power cut before the data reached the disk
    assert len(prepare_with(tmp_path, monkeypatch, binary)) == 1
    assert entry.read_bytes() == binary


def test_a_cache_entry_that_does_not_match_its_checksum_is_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = fake_binary()
    prepare_with(tmp_path, monkeypatch, binary)
    [entry] = [p for p in cached_files(tmp_path) if p.name == "checker"]
    entry.write_bytes(binary[: len(binary) // 2])  # half written, but still starts like an ELF
    assert len(prepare_with(tmp_path, monkeypatch, binary)) == 1
    assert entry.read_bytes() == binary
