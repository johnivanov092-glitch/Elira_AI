"""Technical snippets must retain syntax and nesting through page extraction."""
from bs4 import BeautifulSoup
import pytest

from webskill.infrastructure.search.web_runtime import _extract_readable_text


@pytest.mark.parametrize("fragment", ["", "settings", "example"])
def test_highlighted_yaml_retains_hierarchy_and_blank_lines(fragment):
    code = "use_default_settings:\n  engines:\n    keep_only:\n      - google\n\nserver:\n  port: 8080\n"
    html = ('<main><h2 id="settings">Settings</h2><p>Configuration example.</p>'
            '<pre id="example"><span>use_default_settings</span>:\n'
            '  <span>engines</span>:\n    <span>keep_only</span>:\n      - google\n\n'
            'server:\n  port: 8080\n</pre><h2>Next</h2></main>')
    extracted = _extract_readable_text(BeautifulSoup(html, "html.parser"), None, fragment=fragment)
    assert "```text\n" + code + "```" in extracted
    assert "use_default_settings\n:" not in extracted
    assert "__ELIRA_PREFORMATTED" not in extracted


def test_preformatted_text_obeys_budget_and_cannot_close_its_own_fence():
    html = '<main><pre>if True:\n    print("```text")\n</pre></main>'
    soup = BeautifulSoup(html, "html.parser")
    full = _extract_readable_text(soup, None)
    assert full.startswith('````text\nif True:\n    print("```text")')
    short = _extract_readable_text(BeautifulSoup(html, "html.parser"), 24)
    assert short == full[:24]


def test_nested_code_anchor_and_html_line_breaks_are_preserved():
    html = '<main><pre><code><span id="example">if True:</span><br>    print(1)<br>print(2)</code></pre></main>'
    text = _extract_readable_text(BeautifulSoup(html, "html.parser"), None, fragment="example")
    assert '```text\nif True:\n    print(1)\nprint(2)\n```' == text
