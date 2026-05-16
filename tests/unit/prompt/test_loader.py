"""Unit tests for :mod:`kohakuterrarium.prompt.loader`.

The loader reads markdown prompt files off disk. Its contract is small
but load-bearing: ``load_prompt`` must return the *exact* file contents
and raise ``FileNotFoundError`` for a missing path; ``load_prompts_folder``
keys results by stem and only picks up ``.md`` / ``.txt``;
``load_prompt_with_fallback`` swaps to the fallback string only on a
missing file, never on success.
"""

import pytest

from kohakuterrarium.prompt.loader import (
    load_prompt,
    load_prompt_with_fallback,
    load_prompts_folder,
)


class TestLoadPrompt:
    def test_returns_exact_file_contents(self, tmp_path):
        f = tmp_path / "system.md"
        f.write_text("You are a SWE agent.\nUse tools.", encoding="utf-8")
        assert load_prompt(f) == "You are a SWE agent.\nUse tools."

    def test_accepts_string_path(self, tmp_path):
        f = tmp_path / "p.txt"
        f.write_text("hello", encoding="utf-8")
        assert load_prompt(str(f)) == "hello"

    def test_preserves_utf8_content(self, tmp_path):
        f = tmp_path / "u.md"
        f.write_text("日本語のプロンプト", encoding="utf-8")
        assert load_prompt(f) == "日本語のプロンプト"

    def test_missing_file_raises_filenotfound(self, tmp_path):
        missing = tmp_path / "nope.md"
        with pytest.raises(FileNotFoundError) as exc:
            load_prompt(missing)
        assert "nope.md" in str(exc.value)

    def test_empty_file_returns_empty_string(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("", encoding="utf-8")
        assert load_prompt(f) == ""


class TestLoadPromptsFolder:
    def test_keys_by_stem_for_md_and_txt(self, tmp_path):
        (tmp_path / "alpha.md").write_text("A", encoding="utf-8")
        (tmp_path / "beta.txt").write_text("B", encoding="utf-8")
        result = load_prompts_folder(tmp_path)
        assert result == {"alpha": "A", "beta": "B"}

    def test_ignores_non_prompt_extensions(self, tmp_path):
        (tmp_path / "keep.md").write_text("keep", encoding="utf-8")
        (tmp_path / "skip.py").write_text("print()", encoding="utf-8")
        (tmp_path / "skip.json").write_text("{}", encoding="utf-8")
        assert load_prompts_folder(tmp_path) == {"keep": "keep"}

    def test_extension_match_is_case_insensitive(self, tmp_path):
        (tmp_path / "upper.MD").write_text("U", encoding="utf-8")
        assert load_prompts_folder(tmp_path) == {"upper": "U"}

    def test_missing_folder_returns_empty_dict(self, tmp_path):
        assert load_prompts_folder(tmp_path / "does_not_exist") == {}

    def test_empty_folder_returns_empty_dict(self, tmp_path):
        assert load_prompts_folder(tmp_path) == {}


class TestLoadPromptWithFallback:
    def test_none_primary_returns_fallback(self):
        assert load_prompt_with_fallback(None, "default prompt") == "default prompt"

    def test_existing_primary_returns_file_contents_not_fallback(self, tmp_path):
        f = tmp_path / "real.md"
        f.write_text("real content", encoding="utf-8")
        assert load_prompt_with_fallback(f, "default") == "real content"

    def test_missing_primary_returns_fallback(self, tmp_path):
        missing = tmp_path / "gone.md"
        assert load_prompt_with_fallback(missing, "fallback text") == "fallback text"
