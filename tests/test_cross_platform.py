"""Portability: configuration, paths, timezone and the installer scripts.

Nothing here depends on the operating system it runs on, and nothing here
depends on a specific directory: every path comes from ``tmp_path``. Where a
platform-specific branch exists, ``sys.platform`` is monkeypatched so all three
branches are exercised wherever the suite runs.
"""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tracker import platform_utils as pu
from tracker.config import DEFAULTS, InvalidConfig, load_config
from tracker.project_detector import ProjectDetector, _basename, normalise_separators
from tracker.storage import iter_records, prompts_path
from tracker.utils import date_key, resolve_timezone, set_timezone

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# The repository itself must contain no absolute path
# --------------------------------------------------------------------------
class TestNoHardcodedPaths:
    """The project must run from any directory, on any machine."""

    SOURCE_FILES = sorted(
        set(REPO_ROOT.glob("*.py"))
        | set((REPO_ROOT / "tracker").glob("*.py"))
        | set((REPO_ROOT / "scripts").glob("*.py"))
    )

    def test_source_files_were_found(self):
        assert len(self.SOURCE_FILES) > 10

    @pytest.mark.parametrize("needle", [
        "D:\\\\New_Projects",          # this checkout's own location
        "C:\\\\Users\\\\",             # a specific Windows profile
        "/home/",                      # a specific Linux home
        "/Users/",                     # a specific macOS home
    ])
    def test_no_source_file_hardcodes_a_machine_path(self, needle):
        offenders = []
        for path in self.SOURCE_FILES:
            text = path.read_text(encoding="utf-8")
            if needle.replace("\\\\", "\\") in text:
                offenders.append(path.name)
        assert offenders == [], "hardcoded path %r in %s" % (needle, offenders)

    def test_config_json_holds_only_relative_paths(self):
        data = json.loads((REPO_ROOT / "config.json").read_text(encoding="utf-8"))
        for key, value in data["paths"].items():
            assert not Path(value).is_absolute(), "%s is absolute" % key
            assert "\\" not in value, "%s uses a Windows separator" % key

    def test_the_tracker_root_is_derived_from_the_package_location(self):
        from tracker.config import ROOT

        assert ROOT == REPO_ROOT


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
class TestConfiguration:
    def test_defaults_cover_every_documented_setting(self):
        for key in ("store_prompt_text", "timezone", "paths", "server",
                    "dashboard", "project_detection", "ignore_paths"):
            assert key in DEFAULTS

    def test_paths_resolve_against_the_root_not_the_working_directory(self, config):
        assert config.usage_dir == config.root / "data" / "usage"
        assert config.logs_dir == config.root / "logs"
        assert config.index_path.parent == config.root / "data"

    def test_a_config_written_with_forward_slashes_works_everywhere(self, tracker_root):
        (tracker_root / "config.json").write_text(
            json.dumps({"paths": {"usage_dir": "custom/nested/usage"}}), encoding="utf-8")
        config = load_config()
        assert config.usage_dir == tracker_root / "custom" / "nested" / "usage"

    def test_a_config_written_with_backslashes_also_works(self, tracker_root):
        (tracker_root / "config.json").write_text(
            json.dumps({"paths": {"usage_dir": "custom\\\\nested\\\\usage"}}),
            encoding="utf-8")
        config = load_config()
        assert config.usage_dir == tracker_root / "custom" / "nested" / "usage"

    def test_an_absolute_path_is_honoured(self, tracker_root, tmp_path):
        elsewhere = tmp_path / "elsewhere" / "usage"
        (tracker_root / "config.json").write_text(
            json.dumps({"paths": {"usage_dir": str(elsewhere)}}), encoding="utf-8")
        assert load_config().usage_dir == elsewhere

    def test_a_corrupt_config_falls_back_to_defaults(self, tracker_root):
        (tracker_root / "config.json").write_text("{ not json", encoding="utf-8")
        config = load_config()
        assert config.port == 8765
        assert config.usage_dir == tracker_root / "data" / "usage"

    def test_a_missing_config_falls_back_to_defaults(self, tracker_root):
        (tracker_root / "config.json").unlink()
        assert load_config().store_prompt_text is True

    def test_environment_overrides_the_data_directory(self, tracker_root, tmp_path, monkeypatch):
        monkeypatch.setenv("CCTRACKER_DATA_DIR", str(tmp_path / "envdata"))
        config = load_config()
        assert config.usage_dir == tmp_path / "envdata" / "usage"
        assert config.index_path == tmp_path / "envdata" / "index.sqlite3"

    def test_environment_overrides_the_port(self, tracker_root, monkeypatch):
        monkeypatch.setenv("CCTRACKER_PORT", "9101")
        assert load_config().port == 9101

    def test_a_nonsense_port_falls_back(self, tracker_root, monkeypatch):
        monkeypatch.setenv("CCTRACKER_PORT", "not-a-number")
        assert load_config().port == 8765

    def test_an_out_of_range_port_falls_back(self, tracker_root):
        (tracker_root / "config.json").write_text(
            json.dumps({"server": {"port": 99999}}), encoding="utf-8")
        assert load_config().port == 8765

    def test_a_reachable_host_is_refused(self, tracker_root):
        (tracker_root / "config.json").write_text(
            json.dumps({"server": {"host": "0.0.0.0"}}), encoding="utf-8")
        with pytest.raises(InvalidConfig):
            _ = load_config().host

    def test_dashboard_settings_are_exposed(self, tracker_root):
        (tracker_root / "config.json").write_text(json.dumps(
            {"dashboard": {"auto_refresh_seconds": 60, "page_size": 100}}), encoding="utf-8")
        config = load_config()
        assert config.auto_refresh_seconds == 60
        assert config.page_size == 100

    def test_an_unsupported_refresh_interval_is_ignored(self, tracker_root):
        (tracker_root / "config.json").write_text(
            json.dumps({"dashboard": {"auto_refresh_seconds": 7}}), encoding="utf-8")
        assert load_config().auto_refresh_seconds == 0

    def test_ensure_directories_creates_everything_it_writes_to(self, config):
        config.ensure_directories()
        for path in (config.usage_dir, config.state_dir,
                     config.logs_dir, config.reports_dir):
            assert path.is_dir()

    def test_ensure_directories_never_raises_on_a_bad_path(self, config, monkeypatch):
        def explode(*_args, **_kwargs):
            raise PermissionError("read-only")

        monkeypatch.setattr(Path, "mkdir", explode)
        config.ensure_directories()  # must not propagate


class TestEnvExample:
    def test_the_example_file_exists(self):
        assert (REPO_ROOT / ".env.example").is_file()

    def test_it_documents_every_environment_variable_the_code_reads(self):
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        for name in ("CCTRACKER_ROOT", "CCTRACKER_DATA_DIR", "CCTRACKER_LOGS_DIR",
                     "CCTRACKER_HOST", "CCTRACKER_PORT", "CCTRACKER_TIMEZONE",
                     "CCTRACKER_NO_BROWSER", "CLAUDE_CONFIG_DIR"):
            assert name in text, name

    def test_it_contains_no_real_value_for_any_variable(self):
        """Every line must be a comment - this file must never hold a secret."""
        for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            assert not line.strip() or line.lstrip().startswith("#")


# --------------------------------------------------------------------------
# Timezone handling
# --------------------------------------------------------------------------
class TestTimezone:
    def teardown_method(self):
        set_timezone("local")

    def test_local_follows_the_machine(self):
        assert resolve_timezone("local") is None
        assert resolve_timezone("") is None
        assert resolve_timezone(None) is None

    def test_utc_is_recognised(self):
        assert resolve_timezone("UTC") == _dt.timezone.utc

    @pytest.mark.parametrize("spec,hours,minutes", [
        ("+05:30", 5, 30), ("-08:00", -8, 0), ("+0530", 5, 30), ("+01:00", 1, 0),
    ])
    def test_a_fixed_offset_works_without_a_timezone_database(self, spec, hours, minutes):
        """Fixed offsets need no tzdata, so they work identically on Windows."""
        resolved = resolve_timezone(spec)
        expected = _dt.timedelta(hours=abs(hours), minutes=minutes)
        if hours < 0:
            expected = -expected
        assert resolved.utcoffset(None) == expected

    def test_an_unknown_zone_falls_back_rather_than_raising(self):
        assert resolve_timezone("Not/AZone") is None

    def test_the_configured_timezone_decides_the_day_a_record_lands_in(self, tracker_root):
        # 20:00 UTC is the same day in UTC and the next day at +05:30.
        moment = _dt.datetime(2026, 9, 3, 20, 0, tzinfo=_dt.timezone.utc)

        (tracker_root / "config.json").write_text(
            json.dumps({"timezone": "UTC"}), encoding="utf-8")
        load_config()
        assert date_key(moment) == "2026-09-03"

        (tracker_root / "config.json").write_text(
            json.dumps({"timezone": "+05:30"}), encoding="utf-8")
        load_config()
        assert date_key(moment) == "2026-09-04"

    def test_the_storage_layout_follows_the_configured_timezone(self, tracker_root):
        moment = _dt.datetime(2026, 9, 3, 20, 0, tzinfo=_dt.timezone.utc)
        (tracker_root / "config.json").write_text(
            json.dumps({"timezone": "+05:30"}), encoding="utf-8")
        config = load_config()
        target = prompts_path(config.usage_dir, moment, "demo")
        assert target.parent.parent.name == "04"
        assert target.parent.parent.parent.name == "09"


# --------------------------------------------------------------------------
# Path and project handling across platforms
# --------------------------------------------------------------------------
class TestPathHandling:
    @pytest.mark.parametrize("path,expected", [
        # Windows-shaped paths resolve the same way on every OS, which is what
        # lets a Linux dashboard read data that Windows collected.
        (r"D:\Projects\data-platform", "data-platform"),
        ("D:/Projects/data-platform", "data-platform"),
        (r"\\server\share\ReviewMint", "ReviewMint"),
        # POSIX paths.
        ("/home/user/projects/AccentHRP", "AccentHRP"),
        ("/Users/user/projects/AccentHRP", "AccentHRP"),
        ("/home/user/projects/ReviewMint/", "ReviewMint"),
        ("/opt/src/my.app-v2", "my.app-v2"),
    ])
    def test_project_name_is_the_last_component(self, path, expected):
        assert _basename(path) == expected

    def test_a_posix_filename_containing_a_backslash_is_not_split(self, monkeypatch):
        """On Linux and macOS a backslash is a legal filename character."""
        monkeypatch.setattr(sys, "platform", "linux")
        assert _basename("/home/me/we\\ird") == "we\\ird"

    def test_a_windows_path_is_still_split_when_read_on_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert _basename(r"D:\Projects\alpha") == "alpha"

    def test_separator_normalisation_is_idempotent(self):
        once = normalise_separators(r"D:\a\b")
        assert normalise_separators(once) == once

    def test_detection_works_for_a_posix_style_directory(self, config, tmp_path):
        workdir = tmp_path / "posix-style-project"
        workdir.mkdir()
        project, _root = ProjectDetector(config).detect(workdir.as_posix())
        assert project == "posix-style-project"

    def test_ignore_patterns_match_regardless_of_separator(self, config):
        config.data["ignore_paths"] = ["*/secret-project"]
        detector = ProjectDetector(config)
        assert detector.is_ignored(r"D:\work\secret-project") is True
        assert detector.is_ignored("/home/me/work/secret-project") is True
        assert detector.is_ignored("/home/me/work/open-project") is False


class TestGitDetection:
    def test_missing_git_degrades_to_the_directory_name(self, config, tmp_path, monkeypatch):
        """Git is optional on every platform."""
        monkeypatch.setattr(
            "tracker.project_detector.find_git_executable", lambda: None
        )
        repo = tmp_path / "no-git-here"
        repo.mkdir()
        project, root = ProjectDetector(config).detect(str(repo), git_branch_hint="main")
        assert project == "no-git-here"
        assert root is None

    @pytest.mark.skipif(pu.find_git_executable() is None, reason="git is not installed")
    def test_the_repository_root_wins_over_the_working_directory(self, config, tmp_path):
        repo = tmp_path / "outer-repo"
        nested = repo / "packages" / "inner"
        nested.mkdir(parents=True)
        subprocess.run([pu.find_git_executable(), "init", "-q"], cwd=repo, check=True)

        config.data["project_detection"]["use_git_root"] = True
        project, root = ProjectDetector(config).detect(str(nested), git_branch_hint="main")
        assert project == "outer-repo"
        assert root is not None


# --------------------------------------------------------------------------
# Storage layout portability
# --------------------------------------------------------------------------
class TestStorageLayout:
    def test_the_layout_is_identical_on_every_platform(self, config):
        moment = _dt.datetime(2026, 9, 3, 12, 0, tzinfo=_dt.timezone.utc)
        target = prompts_path(config.usage_dir, moment, "data-platform")
        parts = target.relative_to(config.usage_dir).parts
        assert parts[0].isdigit() and len(parts[0]) == 4      # YYYY
        assert parts[1].isdigit() and len(parts[1]) == 2      # MM
        assert parts[2].isdigit() and len(parts[2]) == 2      # DD
        assert parts[3] == "data-platform"                    # PROJECT
        assert parts[4] == "prompts.jsonl"

    def test_a_data_tree_written_elsewhere_is_readable_here(self, tmp_path):
        """Copying data/ between machines must just work."""
        usage = tmp_path / "usage"
        target = usage / "2026" / "09" / "03" / "data-platform" / "prompts.jsonl"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({
            "id": "abc", "date": "2026-09-03", "project": "data-platform",
            "working_directory": "D:\\Projects\\data-platform",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }) + "\n", encoding="utf-8")

        records = list(iter_records(usage))
        assert len(records) == 1
        assert records[0]["project"] == "data-platform"


# --------------------------------------------------------------------------
# The installer and launcher scripts
# --------------------------------------------------------------------------
class TestScriptsExist:
    @pytest.mark.parametrize("relative", [
        "scripts/install_hooks.py",
        "scripts/uninstall_hooks.py",
        "scripts/install_windows.ps1",
        "scripts/install_linux.sh",
        "scripts/install_macos.sh",
        "scripts/install_hooks.ps1",
        "scripts/uninstall_hooks.ps1",
        "scripts/uninstall_hooks.sh",
        "start_dashboard.bat",
        "start_dashboard.ps1",
        "start_dashboard.sh",
    ])
    def test_every_platform_has_its_entry_point(self, relative):
        assert (REPO_ROOT / relative).is_file()

    @pytest.mark.parametrize("relative", [
        "scripts/_python.sh",
        "scripts/install_linux.sh",
        "scripts/install_macos.sh",
        "scripts/uninstall_hooks.sh",
        "start_dashboard.sh",
    ])
    def test_shell_scripts_use_unix_line_endings(self, relative):
        """A CRLF shebang makes a script unrunnable on Linux and macOS."""
        assert b"\r\n" not in (REPO_ROOT / relative).read_bytes()

    @pytest.mark.parametrize("relative", [
        "scripts/install_linux.sh",
        "scripts/install_macos.sh",
        "scripts/uninstall_hooks.sh",
        "start_dashboard.sh",
    ])
    def test_shell_scripts_delegate_to_python(self, relative):
        """No business logic may be duplicated in a wrapper."""
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert text.startswith("#!/bin/sh")
        assert 'exec "$PYTHON"' in text


class TestInstallerScript:
    """The Python installer is the implementation every platform runs."""

    def test_installing_is_idempotent(self, tmp_path, capsys):
        from scripts import install_hooks as installer

        settings = tmp_path / "settings.json"
        assert installer.main(["--settings", str(settings), "--quiet"]) == 0
        first = json.loads(settings.read_text(encoding="utf-8"))
        assert installer.main(["--settings", str(settings), "--quiet"]) == 0
        assert json.loads(settings.read_text(encoding="utf-8")) == first

    def test_it_registers_both_events(self, tmp_path):
        from scripts import install_hooks as installer
        from tracker import hooks

        settings = tmp_path / "settings.json"
        installer.main(["--settings", str(settings), "--quiet"])
        assert sorted(hooks.installed_events(settings)) == ["SessionEnd", "Stop"]

    def test_it_preserves_unrelated_hooks_and_settings(self, tmp_path):
        from scripts import install_hooks as installer

        settings = tmp_path / "settings.json"
        original = {
            "theme": "dark",
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]},
        }
        settings.write_text(json.dumps(original), encoding="utf-8")
        installer.main(["--settings", str(settings), "--quiet"])

        written = json.loads(settings.read_text(encoding="utf-8"))
        assert written["theme"] == "dark"
        commands = [e["command"] for g in written["hooks"]["Stop"] for e in g["hooks"]]
        assert "echo mine" in commands

    def test_it_backs_the_file_up_before_changing_it(self, tmp_path):
        from scripts import install_hooks as installer

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        installer.main(["--settings", str(settings), "--quiet"])
        assert list(tmp_path.glob("settings.backup-cctracker-*.json"))

    def test_it_refuses_an_unparseable_settings_file(self, tmp_path, capsys):
        from scripts import install_hooks as installer

        settings = tmp_path / "settings.json"
        settings.write_text("{ broken", encoding="utf-8")
        assert installer.main(["--settings", str(settings), "--quiet"]) == 1
        assert settings.read_text(encoding="utf-8") == "{ broken"

    def test_it_rejects_a_missing_interpreter(self, tmp_path):
        from scripts import install_hooks as installer

        assert installer.main([
            "--settings", str(tmp_path / "settings.json"),
            "--python", str(tmp_path / "no-such-python"),
            "--quiet",
        ]) == 2

    def test_a_custom_interpreter_is_baked_into_the_command(self, tmp_path):
        from scripts import install_hooks as installer

        fake = tmp_path / "python-here"
        fake.write_text("", encoding="utf-8")
        settings = tmp_path / "settings.json"
        installer.main(["--settings", str(settings), "--python", str(fake), "--quiet"])

        command = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert "python-here" in command
        assert "--cctracker" in command


class TestUninstallerScript:
    def test_it_removes_only_this_project_s_hooks(self, tmp_path):
        from scripts import install_hooks as installer
        from scripts import uninstall_hooks as uninstaller

        settings = tmp_path / "settings.json"
        original = {
            "theme": "dark",
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]},
        }
        settings.write_text(json.dumps(original), encoding="utf-8")

        installer.main(["--settings", str(settings), "--quiet"])
        assert uninstaller.main(["--settings", str(settings), "--quiet"]) == 0
        assert json.loads(settings.read_text(encoding="utf-8")) == original

    def test_it_is_idempotent(self, tmp_path):
        from scripts import uninstall_hooks as uninstaller

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        assert uninstaller.main(["--settings", str(settings), "--quiet"]) == 0
        assert uninstaller.main(["--settings", str(settings), "--quiet"]) == 0
        assert json.loads(settings.read_text(encoding="utf-8")) == {"theme": "dark"}

    def test_a_missing_settings_file_is_not_an_error(self, tmp_path):
        from scripts import uninstall_hooks as uninstaller

        assert uninstaller.main(["--settings", str(tmp_path / "nope.json"), "--quiet"]) == 0

    def test_it_leaves_collected_data_alone_by_default(self, tmp_path, config):
        from scripts import uninstall_hooks as uninstaller

        config.ensure_directories()
        marker = config.usage_dir / "keep-me.txt"
        marker.write_text("data", encoding="utf-8")

        uninstaller.main(["--settings", str(tmp_path / "settings.json"), "--quiet"])
        assert marker.is_file()

    def test_purge_data_removes_it_when_confirmed(self, tmp_path, config):
        from scripts import uninstall_hooks as uninstaller

        config.ensure_directories()
        (config.usage_dir / "gone.txt").write_text("data", encoding="utf-8")

        uninstaller.main([
            "--settings", str(tmp_path / "settings.json"), "--purge-data", "--yes", "--quiet",
        ])
        assert not config.data_dir.exists()
