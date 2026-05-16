"""Unit tests for :mod:`kohakuterrarium.cli.service`.

Render-only paths — every test runs ``_render_*`` directly so no
systemd interaction is required.  The point is to pin the SHAPE of
the generated unit files (DynamicUser, EnvironmentFile, instance
template token) so the next refactor cannot quietly break operator
deploys.
"""

from kohakuterrarium.cli import service


class TestRenderHostUnit:
    def test_host_unit_carries_required_directives(self):
        unit = service._render_host_unit(
            home_dir="/var/lib/kohakuterrarium",
            http_host="0.0.0.0",
            http_port=8001,
            lab_bind="0.0.0.0:8100",
        )
        assert "Type=simple" in unit
        assert "DynamicUser=yes" in unit
        assert "ProtectSystem=strict" in unit
        assert "NoNewPrivileges=yes" in unit
        assert "WantedBy=multi-user.target" in unit

    def test_host_unit_includes_home_dir_and_bind(self):
        unit = service._render_host_unit(
            home_dir="/var/lib/test-home",
            http_host="127.0.0.1",
            http_port=9001,
            lab_bind="127.0.0.1:9100",
        )
        # Home dir reaches both ExecStart and Environment block.
        assert "/var/lib/test-home" in unit
        # Bind + port reach the ExecStart line via --lab-bind / --port.
        assert "127.0.0.1:9100" in unit
        assert "9001" in unit
        assert "lab-host" in unit

    def test_host_unit_environment_file_present(self):
        unit = service._render_host_unit(
            home_dir="/var/lib/x",
            http_host="0.0.0.0",
            http_port=8001,
            lab_bind="0.0.0.0:8100",
        )
        assert "EnvironmentFile=" in unit
        assert "/etc/kohakuterrarium/host.env" in unit


class TestRenderClientUnit:
    def test_client_is_instance_template(self):
        unit = service._render_client_unit(home_dir_base="/var/lib/workers")
        # ``%i`` is systemd's instance-name expansion; mandatory for
        # @-templates.  Without it, every instance shares state.
        assert "%i" in unit
        assert "Description=KohakuTerrarium Lab Client (worker %i)" in unit
        assert "lab-client" in unit

    def test_client_environment_files_layered(self):
        unit = service._render_client_unit(home_dir_base="/var/lib/workers")
        # Shared envfile (URL + token) THEN per-instance envfile.
        assert "EnvironmentFile=/etc/kohakuterrarium/client.env" in unit
        # ``-`` prefix means "ignore-if-missing" — required for the
        # template default when no per-instance overrides exist.
        assert "EnvironmentFile=-/etc/kohakuterrarium/client.%i.env" in unit


class TestRenderEnv:
    def test_env_renders_kv_lines(self):
        env = service._render_env({"KT_HOST_TOKEN": "abc123", "KT_HTTP_PORT": "9001"})
        assert "KT_HOST_TOKEN=abc123" in env
        assert "KT_HTTP_PORT=9001" in env
        assert env.endswith("\n")
