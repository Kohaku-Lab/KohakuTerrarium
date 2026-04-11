"""
Pipeline Transforms — chaining agents with data transformations.

Demonstrates the power of ``>>`` auto-wrapping:
  - Plain functions are automatically lifted into the pipeline
  - ``json.loads``, ``str.strip``, lambdas — all work in ``>>``
  - ``.map()`` and ``.contramap()`` for targeted transforms
  - Mix agents and pure functions freely

This example builds a data extraction pipeline:
  1. Agent reads a document and extracts structured data (JSON)
  2. Pure function parses the JSON
  3. Agent enriches each extracted item
  4. Pure function formats the final report

Usage:
    python pipeline_transforms.py
"""

import asyncio
import json

from kohakuterrarium.compose import factory
from kohakuterrarium.core.config import load_agent_config


def make_extractor_config():
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = "extractor"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        "You are a data extractor. Given text, extract all mentioned people "
        "and their roles. Output ONLY valid JSON:\n"
        '[{"name": "...", "role": "..."}, ...]\n'
        "No markdown, no explanation."
    )
    return config


def make_enricher_config():
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = "enricher"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        "You are a data enricher. Given a person's name and role, "
        "provide a one-sentence description of what that role typically involves. "
        "Output ONLY the description, no commentary."
    )
    return config


def parse_json(text: str) -> list[dict]:
    """Parse JSON from potentially messy LLM output."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
    if cleaned.endswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[:-1])
    return json.loads(cleaned)


def format_report(enriched: list[dict]) -> str:
    """Format enriched data as a readable report."""
    lines = ["# Extracted People\n"]
    for item in enriched:
        lines.append(f"**{item['name']}** — {item['role']}")
        if item.get("description"):
            lines.append(f"  {item['description']}")
        lines.append("")
    return "\n".join(lines)


async def main() -> None:
    document = (
        "The project is led by Alice Chen, the CTO, who oversees the technical "
        "direction. Bob Martinez serves as the lead architect, responsible for "
        "system design. Carol Wright is the product manager who coordinates "
        "between engineering and stakeholders. Dave Kim handles DevOps and "
        "infrastructure as the site reliability engineer."
    )

    print(f"Document:\n{document}\n")

    extractor = factory(make_extractor_config())
    enricher = factory(make_enricher_config())

    # Pipeline: extract → parse JSON → enrich each person → format report
    #
    # The >> operator auto-wraps parse_json (a plain function) as Pure.
    # This mix of agents and functions in one pipeline is the key power.

    extract_pipeline = extractor >> parse_json

    print("Extracting people...")
    people = await extract_pipeline(document)
    print(f"Found {len(people)} people\n")

    # Enrich each person — using the enricher agent with .map()
    print("Enriching...")
    for person in people:
        description = await enricher(f"{person['name']}, {person['role']}")
        person["description"] = description.strip()
        print(f"  {person['name']}: {person['description'][:80]}...")

    # Format the final report
    report = format_report(people)
    print(f"\n{report}")


if __name__ == "__main__":
    asyncio.run(main())
