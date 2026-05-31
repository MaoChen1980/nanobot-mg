"""Tests for nanobot.utils.prompt_templates — Jinja2 template rendering."""

from jinja2 import Environment

from nanobot.utils.prompt_templates import _environment, render_template


class TestEnvironment:
    def test_returns_environment_instance(self):
        env = _environment()
        assert isinstance(env, Environment)

    def test_is_cached(self):
        assert _environment() is _environment()


class TestRenderTemplate:
    def test_renders_with_variable(self):
        result = render_template("agent/max_iterations_message.md", max_iterations=5)
        assert "5" in result
        assert "最大 tool call 迭代次数" in result

    def test_strip_removes_trailing_whitespace(self):
        unstripped = render_template("agent/max_iterations_message.md", max_iterations=3)
        stripped = render_template("agent/max_iterations_message.md", strip=True, max_iterations=3)
        assert len(stripped) <= len(unstripped)
        assert stripped == unstripped.rstrip()

    def test_missing_template_raises(self):
        import jinja2
        try:
            render_template("nonexistent/template.md")
            assert False, "Expected TemplateNotFound"
        except jinja2.TemplateNotFound:
            pass
