"""Hook installation (Step 9): idempotent, additive, and never destructive.

Every test runs against a sandbox settings file; the user's real
``~/.claude/settings.json`` is never opened.
"""

from __future__ import annotations

import copy
import json

import pytest

from tracker import hooks


@pytest.fixture
def settings_file(tmp_path):
    return tmp_path / "settings.json"


@pytest.fixture
def existing_config():
    """A settings file with unrelated settings and an unrelated hook."""
    return {
        "theme": "dark",
        "tui": "fullscreen",
        "permissions": {"allow": ["Bash(git *)"]},
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "echo keep-me"}]}],
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other"}]}
            ],
        },
    }


def write(path, payload):
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class TestCommand:
    def test_command_carries_the_marker(self):
        command = hooks.build_command()
        assert hooks.MARKER in command
        assert "collect_usage.py" in command

    def test_recognises_only_its_own_entries(self):
        ours = {"type": "command", "command": hooks.build_command()}
        theirs = {"type": "command", "command": "echo hello"}
        assert hooks.is_ours(ours) is True
        assert hooks.is_ours(theirs) is False


class TestInstall:
    def test_creates_the_file_when_absent(self, settings_file):
        result = hooks.install(settings_file)
        assert result["changed"] is True
        assert settings_file.is_file()
        assert sorted(hooks.installed_events(settings_file)) == ["SessionEnd", "Stop"]

    def test_is_idempotent(self, settings_file):
        assert hooks.install(settings_file)["changed"] is True
        assert hooks.install(settings_file)["changed"] is False
        assert hooks.install(settings_file)["changed"] is False

        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        for event in ("Stop", "SessionEnd"):
            mine = [
                group for group in settings["hooks"][event]
                if any(hooks.is_ours(entry) for entry in group["hooks"])
            ]
            assert len(mine) == 1, event

    def test_preserves_every_unrelated_setting(self, settings_file, existing_config):
        write(settings_file, existing_config)
        hooks.install(settings_file)
        settings = json.loads(settings_file.read_text(encoding="utf-8"))

        assert settings["theme"] == "dark"
        assert settings["tui"] == "fullscreen"
        assert settings["permissions"] == {"allow": ["Bash(git *)"]}
        assert settings["hooks"]["PreToolUse"] == existing_config["hooks"]["PreToolUse"]

    def test_preserves_an_existing_hook_on_the_same_event(self, settings_file, existing_config):
        write(settings_file, existing_config)
        hooks.install(settings_file)
        settings = json.loads(settings_file.read_text(encoding="utf-8"))

        commands = [entry["command"] for group in settings["hooks"]["Stop"]
                    for entry in group["hooks"]]
        assert "echo keep-me" in commands
        assert any(hooks.MARKER in command for command in commands)

    def test_takes_a_backup_before_changing_anything(self, settings_file, existing_config):
        write(settings_file, existing_config)
        result = hooks.install(settings_file)

        assert result["backup"] is not None
        backups = list(settings_file.parent.glob("settings.backup-cctracker-*.json"))
        assert len(backups) == 1
        assert json.loads(backups[0].read_text(encoding="utf-8")) == existing_config

    def test_no_backup_when_nothing_changes(self, settings_file):
        hooks.install(settings_file)
        for backup in settings_file.parent.glob("settings.backup-*"):
            backup.unlink()
        assert hooks.install(settings_file)["backup"] is None

    def test_refreshes_a_stale_command_without_duplicating(self, settings_file):
        write(settings_file, {"hooks": {"Stop": [
            {"hooks": [{"type": "command",
                        "command": '"C:/old/python.exe" "C:/old/collect_usage.py" --cctracker'}]}
        ]}})
        assert hooks.install(settings_file)["changed"] is True

        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        mine = [entry for group in settings["hooks"]["Stop"]
                for entry in group["hooks"] if hooks.is_ours(entry)]
        assert len(mine) == 1
        assert mine[0]["command"] == hooks.build_command()

    def test_collapses_duplicates_left_by_an_older_install(self, settings_file):
        command = hooks.build_command()
        write(settings_file, {"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": command}]},
            {"hooks": [{"type": "command", "command": command}]},
            {"hooks": [{"type": "command", "command": command}]},
        ]}})
        hooks.install(settings_file)

        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        mine = [entry for group in settings["hooks"]["Stop"]
                for entry in group["hooks"] if hooks.is_ours(entry)]
        assert len(mine) == 1

    def test_a_hook_timeout_is_always_set(self, settings_file):
        hooks.install(settings_file)
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        entry = settings["hooks"]["Stop"][0]["hooks"][0]
        assert entry["timeout"] == hooks.HOOK_TIMEOUT

    def test_refuses_to_rewrite_an_unparseable_settings_file(self, settings_file):
        settings_file.write_text("{ this is not json", encoding="utf-8")
        with pytest.raises(ValueError):
            hooks.install(settings_file)
        assert settings_file.read_text(encoding="utf-8") == "{ this is not json"


class TestUninstall:
    def test_removes_only_our_hooks(self, settings_file, existing_config):
        write(settings_file, existing_config)
        hooks.install(settings_file)
        assert hooks.uninstall(settings_file)["changed"] is True

        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        assert settings == existing_config

    def test_round_trip_restores_the_file_exactly(self, settings_file, existing_config):
        original = copy.deepcopy(existing_config)
        write(settings_file, existing_config)
        hooks.install(settings_file)
        hooks.uninstall(settings_file)
        assert json.loads(settings_file.read_text(encoding="utf-8")) == original

    def test_is_idempotent(self, settings_file):
        hooks.install(settings_file)
        assert hooks.uninstall(settings_file)["changed"] is True
        assert hooks.uninstall(settings_file)["changed"] is False
        assert hooks.installed_events(settings_file) == []

    def test_leaves_a_mixed_group_intact(self, settings_file):
        write(settings_file, {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "echo first"},
            {"type": "command", "command": hooks.build_command()},
            {"type": "command", "command": "echo second"},
        ]}]}})
        hooks.uninstall(settings_file)

        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        commands = [entry["command"] for entry in settings["hooks"]["Stop"][0]["hooks"]]
        assert commands == ["echo first", "echo second"]

    def test_a_missing_settings_file_is_not_an_error(self, tmp_path):
        assert hooks.uninstall(tmp_path / "nope.json")["changed"] is False


class TestCliEntryPoint:
    def test_install_then_status_then_uninstall(self, settings_file, capsys):
        assert hooks.main(["install", "--settings", str(settings_file), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["changed"] is True

        assert hooks.main(["status", "--settings", str(settings_file), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["events"] == ["SessionEnd", "Stop"]

        assert hooks.main(["uninstall", "--settings", str(settings_file), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["changed"] is True
