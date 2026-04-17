# Python-native

Every module in a KohakuTerrarium creature is a Python class. An
agent is an async Python object. A terrarium is a Python runtime. A
plugin is a Python class. A tool is a Python class. The compose
algebra is a Python API.

This is not a coincidence. It is a deliberate property of the
framework: because everything is Python, agents and their parts can
be used as values inside *other* parts. A plugin can embed an agent.
A trigger can embed an agent. A tool can embed an agent. That is how
a lot of interesting patterns — smart guards, adaptive watchers,
seamless memory — become a few dozen lines of composition instead of
new frameworks.

Two docs in this section:

- [Agent as a Python object](agent-as-python-object.md) — the
  first-class-value story; what "agents are Python" unlocks.
- [Composition algebra](composition-algebra.md) — the ergonomic API
  for stitching agents into pipelines (`>>`, `&`, `|`, `*`, `.iterate`).

Read the first doc to get the principle. Read the second only if you
plan to write pipelines of agents in Python — if you are writing
creature configs, you can skip it.
