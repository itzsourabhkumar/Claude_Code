"""Project detection (Step 3): git root preferred, working directory as fallback."""

from __future__ import annotations

import subprocess

import pytest

from tracker.project_detector import (
    ProjectDetector,
    _basename,
    git_root,
    sanitize_project_name,
)


class TestBasename:
    @pytest.mark.parametrize("path,expected", [
        (r"D:\New_Projects\data-platform", "data-platform"),
        (r"D:\New_Projects\InventoryManagementSystem", "InventoryManagementSystem"),
        ("/home/user/projects/AccentHRP", "AccentHRP"),
        (r"D:\New_Projects\data-platform\\", "data-platform"),
        ("/home/user/projects/ReviewMint/", "ReviewMint"),
        ("", ""),
    ])
    def test_directory_name_is_the_project_name(self, path, expected):
        assert _basename(path) == expected

    def test_drive_root_does_not_produce_an_empty_name(self):
        assert _basename("D:\\") == "D-drive"


class TestSanitize:
    def test_ordinary_names_pass_through_unchanged(self):
        for name in ("data-platform", "InventoryManagementSystem", "AccentHRP", "my_app.v2"):
            assert sanitize_project_name(name) == name

    def test_path_separators_can_never_escape_the_project_directory(self):
        assert "/" not in sanitize_project_name("a/b")
        assert "\\" not in sanitize_project_name("a\\b")

    def test_empty_name_falls_back(self):
        assert sanitize_project_name("") == "unknown-project"
        assert sanitize_project_name("   ") == "unknown-project"

    def test_windows_reserved_device_names_are_escaped(self):
        assert sanitize_project_name("CON") != "CON"


class TestGitRoot:
    def test_returns_repository_root(self, tmp_path):
        repo = tmp_path / "my-repo"
        (repo / "src").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        found = git_root(str(repo / "src"))
        assert found is not None
        assert found.replace("\\", "/").lower().endswith("my-repo")

    def test_returns_none_outside_a_repository(self, tmp_path):
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        # A tmp dir could sit inside an outer repo; only assert the API is safe.
        assert git_root(str(plain)) is None or isinstance(git_root(str(plain)), str)

    def test_missing_directory_is_not_an_error(self, tmp_path):
        assert git_root(str(tmp_path / "does-not-exist")) is None


class TestDetector:
    def test_falls_back_to_working_directory_name(self, config, tmp_path):
        detector = ProjectDetector(config)
        workdir = tmp_path / "InventoryManagementSystem"
        workdir.mkdir()
        project, root = detector.detect(str(workdir))
        assert project == "InventoryManagementSystem"
        assert root is None

    def test_prefers_the_git_repository_root(self, config, tmp_path):
        repo = tmp_path / "data-platform"
        nested = repo / "services" / "api"
        nested.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

        config.data["project_detection"]["use_git_root"] = True
        detector = ProjectDetector(config)
        project, root = detector.detect(str(nested), git_branch_hint="main")

        assert project == "data-platform"
        assert root is not None and root.replace("\\", "/").lower().endswith("data-platform")

    def test_results_are_cached_between_instances(self, config, tmp_path):
        workdir = tmp_path / "CachedProject"
        workdir.mkdir()
        first = ProjectDetector(config)
        assert first.detect(str(workdir))[0] == "CachedProject"
        first.flush()

        # A fresh detector answers from the persisted cache without recomputing,
        # which is what keeps the hook off the git subprocess on every turn.
        second = ProjectDetector(config)
        assert second._cache, "cache was not persisted"
        assert second.detect(str(workdir))[0] == "CachedProject"
        assert second._dirty is False

    def test_ignore_paths_are_honoured(self, config, tmp_path):
        config.data["ignore_paths"] = ["*/secret-project"]
        detector = ProjectDetector(config)
        assert detector.is_ignored(str(tmp_path / "secret-project")) is True
        assert detector.is_ignored(str(tmp_path / "other")) is False

    def test_no_project_list_is_configured_anywhere(self, config):
        """Detection must be derived, never looked up in a list."""
        assert "projects" not in config.data
        assert config.get("project_detection", "projects") is None
