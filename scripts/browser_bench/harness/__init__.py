"""The real-model harness: runs an agent with the ``browser`` plugin over the bench tasks.

``runner`` builds and drives one agent per run against its own site,
``verdict`` holds the system prompt and parses the agent's final verdict,
``score`` classifies a run, ``store`` keeps ``runs.jsonl`` resumable,
``report`` writes ``report.md`` and ``cli`` is the command line
(``python -m scripts.browser_bench.harness``). ``dryrun`` proves the wiring
with a scripted model.
"""
