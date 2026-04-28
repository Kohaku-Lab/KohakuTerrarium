"""
Reusable test infrastructure for KohakuTerrarium.

Provides fake/mock primitives for testing the agent framework without real LLMs.
"""

from kohakuterrarium.testing.agent import TestAgentBuilder
from kohakuterrarium.testing.events import EventRecorder, RecordedEvent
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry
from kohakuterrarium.testing.output import OutputRecorder
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder

__all__ = [
    "ScriptedLLM",
    "ScriptEntry",
    "OutputRecorder",
    "EventRecorder",
    "RecordedEvent",
    "TestAgentBuilder",
    "TestTerrariumBuilder",
]
