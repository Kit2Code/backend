"""Start-up writes the limits of our whole cgroup subtree (on a fake cgroup tree here)."""

from pathlib import Path

from grader_worker import config, setup


def fake_cgroup_tree(tmp_path: Path) -> Path:
    (tmp_path / "cgroup.controllers").write_text("cpuset cpu io memory hugetlb pids rdma misc\n")
    (tmp_path / "cgroup.subtree_control").write_text("")
    root = tmp_path / "grader-judge"
    root.mkdir()
    (root / "cgroup.subtree_control").write_text("")
    return root


def test_the_subtree_runs_only_on_the_judge_cpus(tmp_path: Path) -> None:
    root = fake_cgroup_tree(tmp_path)
    settings = config.Settings(slots=2, box_cpus=[10, 11], mem_total=1 << 30)
    setup.prepare_cgroup(settings, root)
    assert (root / "cpuset.cpus").read_text().strip() == "10,11"


def test_the_subtree_has_a_process_ceiling(tmp_path: Path) -> None:
    root = fake_cgroup_tree(tmp_path)
    settings = config.Settings(slots=3, box_cpus=[4, 5, 6], mem_total=1 << 30)
    setup.prepare_cgroup(settings, root)
    assert (root / "pids.max").read_text().strip() == str(3 * setup.PIDS_PER_SLOT)
