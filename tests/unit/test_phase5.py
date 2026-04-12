"""
Phase 5 Unit Tests - Agent Assembly

Tests for:
- Config loading
- Prompt templating
- Input/Output modules
- Agent initialization
"""

import os
import tempfile
from pathlib import Path

import pytest

from kohakuterrarium.core.config import (
    AgentConfig,
    ToolConfigItem,
    load_agent_config,
)
from kohakuterrarium.prompt import (
    PromptTemplate,
    aggregate_system_prompt,
    load_prompt,
    render_template,
)


class TestConfigLoading:
    """Tests for configuration loading."""

    def test_create_agent_config(self):
        """Test creating agent config."""
        config = AgentConfig(
            name="test_agent",
            model="gpt-4o-mini",
        )
        assert config.name == "test_agent"
        assert config.model == "gpt-4o-mini"
        assert config.temperature == 0.7

    def test_config_defaults(self):
        """Test config default values."""
        config = AgentConfig(name="test")
        assert config.version == "1.0"
        assert config.api_key_env == ""  # empty default, resolved via profile system
        assert config.input.type == "cli"
        assert config.output.type == "stdout"

    def test_load_yaml_config(self):
        """Test loading config from YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                """
name: yaml_agent
version: "2.0"
model: test-model
temperature: 0.5
input:
  type: cli
  prompt: ">>> "
tools:
  - name: bash
    type: builtin
output:
  type: stdout
"""
            )

            config = load_agent_config(tmpdir)
            assert config.name == "yaml_agent"
            assert config.version == "2.0"
            assert config.model == "test-model"
            assert config.temperature == 0.5
            assert config.input.prompt == ">>> "
            assert len(config.tools) == 1
            assert config.tools[0].name == "bash"

    def test_env_var_interpolation(self):
        """Test environment variable interpolation."""
        os.environ["TEST_MODEL"] = "env-model"

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                """
name: env_test
model: "${TEST_MODEL}"
"""
            )

            config = load_agent_config(tmpdir)
            assert config.model == "env-model"

        del os.environ["TEST_MODEL"]

    def test_env_var_with_default(self):
        """Test environment variable with default value."""
        # Ensure var doesn't exist
        if "NONEXISTENT_VAR" in os.environ:
            del os.environ["NONEXISTENT_VAR"]

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                """
name: default_test
model: "${NONEXISTENT_VAR:default-model}"
"""
            )

            config = load_agent_config(tmpdir)
            assert config.model == "default-model"

    def test_config_not_found(self):
        """Test error when config not found."""
        with pytest.raises(FileNotFoundError):
            load_agent_config("/nonexistent/path")

    def test_load_system_prompt_from_file(self):
        """Test loading system prompt from file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create prompts folder
            prompts_dir = tmpdir / "prompts"
            prompts_dir.mkdir()

            # Create prompt file
            (prompts_dir / "system.md").write_text(
                "# Test Prompt\n\nYou are a test agent."
            )

            # Create config
            (tmpdir / "config.yaml").write_text(
                """
name: prompt_test
system_prompt_file: prompts/system.md
"""
            )

            config = load_agent_config(tmpdir)
            assert "Test Prompt" in config.system_prompt
            assert "test agent" in config.system_prompt


class TestPromptTemplating:
    """Tests for prompt templating."""

    def test_simple_variable(self):
        """Test simple variable substitution."""
        result = render_template("Hello {{ name }}!", name="World")
        assert result == "Hello World!"

    def test_conditional(self):
        """Test conditional rendering."""
        template = "{% if show %}Visible{% endif %}"
        assert render_template(template, show=True) == "Visible"
        assert render_template(template, show=False) == ""

    def test_loop(self):
        """Test loop rendering."""
        template = "{% for item in items %}{{ item }} {% endfor %}"
        result = render_template(template, items=["a", "b", "c"])
        assert result == "a b c "

    def test_prompt_template_class(self):
        """Test PromptTemplate class."""
        pt = PromptTemplate("Hello {{ name }}!")
        assert pt.render(name="Test") == "Hello Test!"
        assert pt.source == "Hello {{ name }}!"


class TestPromptLoading:
    """Tests for prompt loading."""

    def test_load_prompt(self):
        """Test loading prompt from file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_path = Path(tmpdir) / "test.md"
            prompt_path.write_text("# Test\n\nContent")

            content = load_prompt(prompt_path)
            assert "Test" in content
            assert "Content" in content

    def test_load_prompt_not_found(self):
        """Test error when prompt not found."""
        with pytest.raises(FileNotFoundError):
            load_prompt("/nonexistent/prompt.md")


class TestPromptAggregation:
    """Tests for prompt aggregation."""

    def test_aggregate_basic(self):
        """Test basic prompt aggregation."""
        result = aggregate_system_prompt(
            "You are a helpful assistant.",
            registry=None,
            include_tools=False,
            include_hints=False,
        )
        assert "helpful assistant" in result

    def test_aggregate_with_hints(self):
        """Test aggregation includes hints."""
        result = aggregate_system_prompt(
            "Base prompt",
            include_hints=True,
        )
        # Check for function call syntax hints
        assert "[/read]" in result or "[/info]" in result
        assert "function" in result.lower() or "tool" in result.lower()


class TestInputModule:
    """Tests for input module."""

    def test_cli_input_creation(self):
        """Test creating CLI input."""
        from kohakuterrarium.builtins.inputs.cli import CLIInput

        cli = CLIInput(prompt=">>> ")
        assert cli.prompt == ">>> "
        assert not cli.is_running

    def test_cli_exit_commands(self):
        """Test CLI exit commands."""
        from kohakuterrarium.builtins.inputs.cli import CLIInput

        cli = CLIInput(exit_commands=["/quit", "bye"])
        assert "/quit" in cli.exit_commands
        assert "bye" in cli.exit_commands


class TestOutputModule:
    """Tests for output module."""

    def test_stdout_output_creation(self):
        """Test creating stdout output."""
        from kohakuterrarium.builtins.outputs.stdout import StdoutOutput

        out = StdoutOutput(prefix=">> ", suffix="\n")
        assert out.prefix == ">> "
        assert out.suffix == "\n"

    def test_output_router_creation(self):
        """Test creating output router."""
        from kohakuterrarium.builtins.outputs.stdout import StdoutOutput
        from kohakuterrarium.modules.output import OutputRouter

        stdout = StdoutOutput()
        router = OutputRouter(stdout)
        assert router.default_output is stdout


class TestAgentCreation:
    """Tests for agent creation (without running)."""

    def test_agent_config_validation(self):
        """Test agent config validation."""
        config = AgentConfig(
            name="test",
            tools=[ToolConfigItem(name="bash", type="builtin")],
        )
        assert len(config.tools) == 1

    @pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="API key not available",
    )
    def test_agent_initialization(self):
        """Test agent initialization with API key."""
        from kohakuterrarium.core import Agent

        config = AgentConfig(
            name="init_test",
            system_prompt="Test prompt",
            tools=[ToolConfigItem(name="bash", type="builtin")],
        )

        agent = Agent(config)
        assert agent.config.name == "init_test"
        assert not agent.is_running
