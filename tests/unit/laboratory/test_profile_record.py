"""The Lab wire record carries the selected preset's raw options and its own transport."""

from kohakuterrarium.laboratory.profile_record import profile_record
from kohakuterrarium.llm.profiles.backends import save_yaml_store


def test_custom_transport_and_variations_survive_the_worker_wire_record():
    groups = {"mode": {"quiet": {"temperature": 0}}}
    save_yaml_store(
        {
            "backends": {
                "team": {
                    "base_url": "https://team.example/v1",
                    "api_key_env": "TEAM_KEY",
                    "provider_name": "custom",
                    "provider_native_tools": ["image_gen"],
                }
            },
            "presets": {
                "team": {
                    "shared": {
                        "model": "ds",
                        "provider_native_tools": [],
                        "variation_groups": groups,
                    }
                }
            },
        }
    )
    record = profile_record("team/shared")
    assert record["model"] == "ds" and record["provider"] == "team"
    assert (
        record["api_key_env"] == "TEAM_KEY"
        and record["base_url"] == "https://team.example/v1"
    )
    assert record["variation_groups"] == groups
    assert record["backend_provider_name"] == "custom" and record[
        "backend_native_tools"
    ] == ["image_gen"]
    assert record["provider_native_tools"] == []
    assert profile_record("other/shared") is None
