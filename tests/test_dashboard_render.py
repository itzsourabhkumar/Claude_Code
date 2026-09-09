"""The dashboard actually renders its cost cards.

Every other test in this suite checks the server. This one checks the *client*,
because the two can disagree: a bug shipped where `/api/summary` returned correct
costs and every server-side assertion passed, yet the browser showed four em
dashes - the renderer bailed out unless `/api/meta` had already arrived, and in a
browser the summary request usually wins the race.

`tests/js/render_harness.js` executes dashboard.js against a minimal DOM stub
with that ordering forced, so the regression cannot come back unnoticed. Node is
optional: without it these tests skip rather than fail, since the tracker itself
has no JavaScript dependency.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "tests" / "js" / "render_harness.js"
DASHBOARD_JS = REPO_ROOT / "dashboard" / "js" / "dashboard.js"
DASHBOARD_CSS = REPO_ROOT / "dashboard" / "css" / "dashboard.css"

node = shutil.which("node") or shutil.which("node.exe")
requires_node = pytest.mark.skipif(node is None, reason="node is not installed")


def run_harness(js_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [node, str(HARNESS), str(js_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), timeout=60,
    )


@requires_node
class TestCostCardsRender:
    def test_the_harness_is_present(self):
        assert HARNESS.is_file()

    def test_dashboard_js_is_syntactically_valid(self):
        check = subprocess.run([node, "--check", str(DASHBOARD_JS)],
                               capture_output=True, text=True, timeout=60)
        assert check.returncode == 0, check.stderr

    def test_all_four_cost_cards_are_populated(self):
        """The reported bug: cards visible but every value an em dash."""
        result = run_harness(DASHBOARD_JS)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "ALL COST CARDS POPULATED" in result.stdout

    def test_cost_renders_even_though_meta_arrives_last(self):
        """The harness delays /api/meta on purpose - this is the actual race."""
        out = run_harness(DASHBOARD_JS).stdout
        assert "[EMPTY]" not in out, out

    def test_the_rendered_total_uses_indian_digit_grouping(self):
        out = run_harness(DASHBOARD_JS).stdout
        assert "₹1,26,450.20" in out, out

    def test_the_note_discloses_the_exchange_rate(self):
        out = run_harness(DASHBOARD_JS).stdout
        assert "1 USD = ₹88.00" in out


@requires_node
class TestRegressionGuard:
    def test_the_harness_still_catches_the_original_bug(self):
        """A harness that cannot fail is worthless - prove it detects the fault.

        Reintroduces exactly the original defect (render gated on a flag that
        only /api/meta sets) in a scratch copy and asserts the harness fails.
        """
        broken = DASHBOARD_JS.read_text(encoding="utf-8").replace(
            "pricing: { enabled: true,", "pricing: { enabled: false,", 1)
        target = REPO_ROOT / "tests" / "js" / "_broken_tmp.js"
        target.write_text(broken, encoding="utf-8", newline="\n")
        try:
            result = run_harness(target)
            assert result.returncode != 0, "harness passed a knowingly broken build"
            assert "[EMPTY]" in result.stdout
        finally:
            target.unlink(missing_ok=True)


class TestHiddenAttributeWorks:
    """`hidden` must actually hide, or a bail-out leaves stale cards on screen.

    `.cards { display: grid }` is an author rule and beats the browser's own
    `[hidden] { display: none }` whatever the specificity, so the stylesheet has
    to state it explicitly. This half of the bug needs no Node to check.
    """

    @staticmethod
    def _rules() -> str:
        """The stylesheet with comments stripped, so prose cannot match."""
        import re

        css = DASHBOARD_CSS.read_text(encoding="utf-8")
        return re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    def test_the_stylesheet_defines_a_hidden_rule(self):
        assert "[hidden]" in self._rules(), \
            "no [hidden] rule - hiding a .cards section will not work"

    def test_the_hidden_rule_is_forced_past_author_display_rules(self):
        """`[hidden]` and `.cards` have equal specificity, so source order would
        otherwise decide it - and `.cards` is declared first."""
        block = self._rules().split("[hidden]", 1)[1].split("}", 1)[0]
        assert "display" in block and "none" in block
        assert "!important" in block

    def test_the_cost_section_is_a_cards_grid(self):
        """If this stops being true the interaction above no longer applies."""
        html = (REPO_ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        assert 'id="cost-cards"' in html
        assert "cards cost-cards" in html
