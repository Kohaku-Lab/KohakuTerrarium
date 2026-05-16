"""Unit tests for :mod:`kohakuterrarium.prompt.template`.

The template layer is Jinja2 with safe defaults (``autoescape=False``,
``trim_blocks``, ``lstrip_blocks``). Contract:

- ``render_template`` substitutes vars / conditionals / loops and
  re-raises ``TemplateSyntaxError`` on malformed syntax.
- ``render_template_safe`` swallows *any* render error and returns the
  *original* template string untouched.
- ``PromptTemplate`` compiles once and renders repeatedly; ``.source``
  exposes the original.
- ``PackagePromptLoader`` resolves ``{% include %}`` via the package
  manifest, then falls back to a raw file path, else ``TemplateNotFound``.
"""

import pytest
from jinja2 import TemplateNotFound, TemplateSyntaxError

from kohakuterrarium.prompt.template import (
    PackagePromptLoader,
    PromptTemplate,
    render_template,
    render_template_safe,
)


class TestRenderTemplate:
    def test_variable_substitution(self):
        assert render_template("Hello {{ name }}", name="world") == "Hello world"

    def test_conditional_true_branch(self):
        out = render_template("{% if flag %}yes{% else %}no{% endif %}", flag=True)
        assert out == "yes"

    def test_conditional_false_branch(self):
        out = render_template("{% if flag %}yes{% else %}no{% endif %}", flag=False)
        assert out == "no"

    def test_loop_renders_each_item(self):
        out = render_template(
            "{% for t in tools %}{{ t }};{% endfor %}", tools=["a", "b"]
        )
        assert out == "a;b;"

    def test_missing_variable_renders_empty(self):
        # Jinja2 default Undefined renders as empty string.
        assert render_template("X{{ missing }}Y") == "XY"

    def test_no_html_escaping(self):
        # autoescape=False — prompts are not HTML.
        assert render_template("{{ v }}", v="<tag> & 'q'") == "<tag> & 'q'"

    def test_plain_text_passthrough(self):
        assert render_template("no templating here") == "no templating here"

    def test_syntax_error_reraised(self):
        # render_template must re-raise TemplateSyntaxError after logging.
        # (Regression guard for B-prompt-1: the log call must not pass a
        # reserved 'message' LogRecord kwarg, or the handler would crash
        # with KeyError before reaching `raise`.)
        with pytest.raises(TemplateSyntaxError):
            render_template("{% if %}broken")


class TestRenderTemplateSafe:
    def test_valid_template_renders_normally(self):
        assert render_template_safe("Hi {{ x }}", x="there") == "Hi there"

    def test_syntax_error_returns_original_unchanged(self):
        broken = "{% for %}{{ unclosed }}"
        assert render_template_safe(broken) == broken

    def test_unknown_include_returns_original_unchanged(self):
        # An include that cannot be resolved raises at render time;
        # safe variant must hand back the original source verbatim.
        src = '{% include "definitely-not-a-real-fragment-xyz" %}'
        assert render_template_safe(src) == src


class TestPromptTemplate:
    def test_source_returns_original(self):
        t = PromptTemplate("Hello {{ name }}")
        assert t.source == "Hello {{ name }}"

    def test_render_substitutes(self):
        t = PromptTemplate("Hello {{ name }}")
        assert t.render(name="Kohaku") == "Hello Kohaku"

    def test_compiled_once_renders_repeatedly_with_different_vars(self):
        t = PromptTemplate("[{{ n }}]")
        assert t.render(n=1) == "[1]"
        assert t.render(n=2) == "[2]"

    def test_invalid_syntax_raises_at_construction(self):
        with pytest.raises(TemplateSyntaxError):
            PromptTemplate("{% if %}")


class TestPackagePromptLoader:
    def test_resolves_raw_file_path_fallback(self, tmp_path):
        frag = tmp_path / "fragment.md"
        frag.write_text("FRAGMENT BODY", encoding="utf-8")
        loader = PackagePromptLoader()
        source, name, uptodate = loader.get_source(None, str(frag))
        assert source == "FRAGMENT BODY"
        assert name == str(frag.resolve())
        assert uptodate() is True

    def test_uptodate_detects_modification(self, tmp_path):
        frag = tmp_path / "frag.md"
        frag.write_text("v1", encoding="utf-8")
        loader = PackagePromptLoader()
        _, _, uptodate = loader.get_source(None, str(frag))
        assert uptodate() is True
        # Bump mtime far enough to be observable.
        import os

        st = frag.stat()
        os.utime(frag, (st.st_atime, st.st_mtime + 100))
        assert uptodate() is False

    def test_missing_template_raises_template_not_found(self):
        loader = PackagePromptLoader()
        with pytest.raises(TemplateNotFound):
            loader.get_source(None, "no-such-fragment-anywhere-12345")

    def test_fragment_collision_surfaces_as_template_not_found(self, monkeypatch):
        # resolve_package_prompt raises ValueError when a fragment name
        # collides across packages; the loader must convert that into
        # TemplateNotFound (so Jinja's traceback points at the manifest).
        import kohakuterrarium.prompt.template as tmpl

        def _raise(name):
            raise ValueError("fragment 'git-safety' defined in 2 packages")

        monkeypatch.setattr(tmpl, "resolve_package_prompt", _raise)
        loader = PackagePromptLoader()
        with pytest.raises(TemplateNotFound):
            loader.get_source(None, "git-safety")

    def test_uptodate_returns_false_when_file_deleted(self, tmp_path):
        # stat() on a since-deleted file raises OSError -> uptodate() False.
        frag = tmp_path / "ephemeral.md"
        frag.write_text("body", encoding="utf-8")
        loader = PackagePromptLoader()
        _, _, uptodate = loader.get_source(None, str(frag))
        frag.unlink()
        assert uptodate() is False

    def test_include_via_raw_path_renders_in_template(self, tmp_path):
        frag = tmp_path / "inc.md"
        frag.write_text("INCLUDED", encoding="utf-8")
        # Forward slashes keep the path Jinja-safe on Windows too.
        frag_path = frag.as_posix()
        out = render_template('before {% include "' + frag_path + '" %} after')
        assert out == "before INCLUDED after"
