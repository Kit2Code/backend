"""Reading the worker's settings from the environment."""

import pytest

from grader_worker import limits
from grader_worker.config import ConfigError, Settings, parse_cpu_list, parse_memory


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1024M", 1 << 30),
        ("1G", 1 << 30),
        ("512m", 512 << 20),
        ("768M", 768 << 20),
        ("65536K", 64 << 20),
    ],
)
def test_memory_sizes(text: str, expected: int) -> None:
    assert parse_memory(text) == expected


@pytest.mark.parametrize("text", ["", "lots", "-1M", "0M", "1.5G", "12T", "1024"])
def test_bad_memory_sizes(text: str) -> None:
    with pytest.raises(ConfigError):
        parse_memory(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("4,5", [4, 5]), ("4-6", [4, 5, 6]), ("0,2-3", [0, 2, 3]), (" 7 ", [7])],
)
def test_cpu_lists(text: str, expected: list[int]) -> None:
    assert parse_cpu_list(text) == expected


@pytest.mark.parametrize("text", ["", "a", "5-4", "1,1", "-1", "1,,2"])
def test_bad_cpu_lists(text: str) -> None:
    with pytest.raises(ConfigError):
        parse_cpu_list(text)


def test_settings_from_environment() -> None:
    env = {"JUDGE_SLOTS": "2", "JUDGE_BOX_CPUS": "4,5", "JUDGE_MEM_TOTAL": "1024M"}
    settings = Settings.from_env(env)
    assert settings.slots == 2
    assert settings.box_cpus == [4, 5]
    assert settings.mem_total == 1 << 30
    assert settings.box_ids == [100, 101]


def test_defaults() -> None:
    settings = Settings.from_env({"JUDGE_BOX_CPUS": "4,5"})
    assert (settings.slots, settings.mem_total) == (2, 1 << 30)


@pytest.mark.parametrize(
    "env",
    [
        {"JUDGE_SLOTS": "3", "JUDGE_BOX_CPUS": "4,5"},  # more slots than CPUs
        {"JUDGE_SLOTS": "0", "JUDGE_BOX_CPUS": "4"},
        {"JUDGE_SLOTS": "11", "JUDGE_BOX_CPUS": "0-10"},  # only boxes 100-109 are ours
        {"JUDGE_SLOTS": "two", "JUDGE_BOX_CPUS": "4,5"},
        {"JUDGE_SLOTS": "1"},  # JUDGE_BOX_CPUS is required
        {"JUDGE_BOX_CPUS": "4,5", "JUDGE_MEM_TOTAL": "64M"},  # too small to judge anything
    ],
)
def test_bad_settings_are_refused(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_isolate_config_lines() -> None:
    settings = Settings.from_env({"JUDGE_SLOTS": "2", "JUDGE_BOX_CPUS": "6,7"})
    assert settings.isolate_box_lines() == ["box100.cpus = 6", "box101.cpus = 7"]


def test_memory_total_must_fit_the_largest_fixed_step() -> None:
    """Compiling reserves 512 MB, so a smaller total could never compile anything."""
    largest = max(limits.COMPILE.memory_kb, limits.CHECKER.memory_kb) * 1024
    assert largest == 512 << 20
    env = {"JUDGE_BOX_CPUS": "4,5", "JUDGE_MEM_TOTAL": "256M"}
    with pytest.raises(ConfigError, match="at least 512M"):
        Settings.from_env(env)
    env["JUDGE_MEM_TOTAL"] = "511M"
    with pytest.raises(ConfigError, match="at least 512M"):
        Settings.from_env(env)
    env["JUDGE_MEM_TOTAL"] = "512M"
    assert Settings.from_env(env).mem_total == largest
