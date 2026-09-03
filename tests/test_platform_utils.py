"""Cross-platform behaviour: paths, interpreter discovery, hook quoting.

Every test here runs on Windows, Linux and macOS and asserts the *rule* rather
than one platform's answer. Where a platform-specific branch is being checked,
``sys.platform`` is monkeypatched so the branch is exercised on whatever machine
the suite happens to be running on - which is what makes it possible to verify
the Linux and macOS code paths from a Windows checkout, and vice versa.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tracker import platform_utils as pu


@pytest.fixture
def as_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


@pytest.fixture
def as_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")


@pytest.fixture
def as_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


class TestOsDetection:
    def test_recognises_this_machine(self):
        assert pu.current_os() in (pu.WINDOWS, pu.LINUX, pu.MACOS)

    def test_exactly_one_platform_predicate_is_true(self):
        assert sum([pu.is_windows(), pu.is_linux(), pu.is_macos()]) == 1

    @pytest.mark.parametrize("platform,expected", [
        ("win32", pu.WINDOWS),
        ("cygwin", pu.WINDOWS),
        ("darwin", pu.MACOS),
        ("linux", pu.LINUX),
        ("linux2", pu.LINUX),
    ])
    def test_maps_every_supported_platform(self, monkeypatch, platform, expected):
        monkeypatch.setattr(sys, "platform", platform)
        assert pu.current_os() == expected

    def test_posix_is_the_complement_of_windows(self):
        assert pu.is_posix() is not pu.is_windows()

    def test_every_platform_has_a_label(self):
        for key in (pu.WINDOWS, pu.LINUX, pu.MACOS, pu.UNKNOWN):
            assert pu.os_label(key)


class TestHomeAndClaudeLocations:
    def test_home_directory_is_absolute(self):
        assert pu.home_dir().is_absolute()

    def test_claude_config_defaults_under_the_home_directory(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        assert pu.claude_config_dir() == pu.home_dir() / ".claude"

    def test_claude_config_dir_env_override_is_honoured(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
        assert pu.claude_config_dir() == tmp_path / "elsewhere"

    def test_override_expands_a_tilde(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", os.path.join("~", "cc-config"))
        assert "~" not in str(pu.claude_config_dir())

    def test_settings_and_transcripts_sit_under_the_config_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert pu.claude_settings_path() == tmp_path / "settings.json"
        assert pu.claude_projects_dir() == tmp_path / "projects"

    def test_no_platform_hardcodes_a_user_directory(self, monkeypatch, tmp_path):
        """The same mechanism must apply on all three operating systems."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
        for platform in ("win32", "linux", "darwin"):
            monkeypatch.setattr(sys, "platform", platform)
            assert pu.claude_settings_path() == tmp_path / "cfg" / "settings.json"


class TestVirtualenvDiscovery:
    def _make_venv(self, root: Path, bin_dir: str, exe: str) -> Path:
        target = root / ".venv" / bin_dir / exe
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
        return target

    def test_finds_a_windows_layout_virtualenv(self, tmp_path):
        made = self._make_venv(tmp_path, "Scripts", "python.exe")
        assert pu.venv_python(tmp_path) == made

    def test_finds_a_posix_layout_virtualenv(self, tmp_path):
        made = self._make_venv(tmp_path, "bin", "python3")
        assert pu.venv_python(tmp_path) == made

    def test_returns_none_when_there_is_no_virtualenv(self, tmp_path):
        assert pu.venv_python(tmp_path) is None

    def test_falls_back_to_the_running_interpreter(self, tmp_path):
        assert pu.python_executable(tmp_path) == sys.executable

    def test_prefers_the_virtualenv_over_the_running_interpreter(self, tmp_path):
        made = self._make_venv(tmp_path, "bin", "python3")
        assert pu.python_executable(tmp_path) == str(made)

    def test_bin_directory_name_matches_the_platform(self, as_windows):
        assert pu.venv_bin_dirname() == "Scripts"

    def test_bin_directory_name_on_posix(self, as_linux):
        assert pu.venv_bin_dirname() == "bin"


class TestCommandNames:
    def test_windows_says_python(self, as_windows):
        assert pu.python_command_name() == "python"

    def test_linux_says_python3(self, as_linux):
        assert pu.python_command_name() == "python3"

    def test_macos_says_python3(self, as_macos):
        assert pu.python_command_name() == "python3"

    def test_windows_activation_covers_powershell_and_cmd(self, as_windows):
        commands = pu.activate_commands()
        assert any("Activate.ps1" in c for c in commands)
        assert any("activate.bat" in c for c in commands)

    def test_posix_activation_is_source(self, as_linux):
        assert pu.activate_commands() == ["source .venv/bin/activate"]

    def test_launcher_names_the_right_script(self, as_windows):
        assert pu.dashboard_launcher().endswith("start_dashboard.bat")

    def test_posix_launcher_names_the_shell_script(self, as_macos):
        assert pu.dashboard_launcher() == "./start_dashboard.sh"

    def test_install_and_uninstall_commands_reference_the_python_installer(self, as_linux):
        assert pu.install_command() == "python3 scripts/install_hooks.py"
        assert pu.uninstall_command() == "python3 scripts/uninstall_hooks.py"


class TestHookQuoting:
    def test_windows_normalises_separators(self, as_windows):
        assert pu.normalise_hook_path(r"C:\Users\me\python.exe") == "C:/Users/me/python.exe"

    def test_posix_leaves_backslashes_alone(self, as_linux):
        """On Linux a backslash is a legal character in a filename."""
        assert pu.normalise_hook_path("/home/me/od\\d/python") == "/home/me/od\\d/python"

    def test_windows_quotes_with_double_quotes(self, as_windows):
        assert pu.quote_hook_arg("C:/Program Files/python.exe") == '"C:/Program Files/python.exe"'

    def test_posix_leaves_a_simple_path_unquoted(self, as_linux):
        assert pu.quote_hook_arg("/usr/bin/python3") == "/usr/bin/python3"

    def test_posix_quotes_a_path_containing_a_space(self, as_linux):
        assert pu.quote_hook_arg("/home/me/my tools/python3") == "'/home/me/my tools/python3'"

    def test_posix_escapes_an_embedded_single_quote(self, as_linux):
        quoted = pu.quote_hook_arg("/home/o'brien/python3")
        assert quoted.startswith("'") and quoted.endswith("'")
        assert "o'\"'\"'brien" in quoted


class TestLoopbackValidation:
    @pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.5", "localhost", "::1", "[::1]"])
    def test_loopback_addresses_are_accepted(self, host):
        assert pu.is_loopback_host(host) is True

    @pytest.mark.parametrize("host", [
        "0.0.0.0", "::", "192.168.1.10", "10.0.0.1", "example.com", "", None,
    ])
    def test_everything_reachable_is_refused(self, host):
        assert pu.is_loopback_host(host) is False


class TestExecutableDiscovery:
    def test_git_lookup_never_raises(self):
        found = pu.find_git_executable()
        assert found is None or Path(found).exists()

    def test_claude_lookup_never_raises(self):
        found = pu.find_claude_executable()
        assert found is None or isinstance(found, str)


class TestDiagnostics:
    def test_describe_platform_reports_every_field(self, tmp_path):
        described = pu.describe_platform(tmp_path)
        for key in ("os", "platform", "python", "claude_config_dir",
                    "claude_settings", "claude_transcripts",
                    "claude_executable", "git_executable"):
            assert described.get(key), key

    def test_describe_platform_contains_no_hardcoded_user_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
        described = pu.describe_platform(tmp_path)
        assert str(tmp_path / "cfg") in described["claude_config_dir"]


class TestPortProbe:
    def test_a_free_port_reads_as_free(self):
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        assert pu.port_is_free("127.0.0.1", port) is True

    def test_a_bound_port_reads_as_taken(self):
        import socket

        with socket.socket() as held:
            held.bind(("127.0.0.1", 0))
            held.listen(1)
            port = held.getsockname()[1]
            assert pu.port_is_free("127.0.0.1", port) is False
