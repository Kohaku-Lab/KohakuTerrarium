"""List a provider's current models or save one as a local preset."""

import asyncio

from kohakuterrarium.llm.discovery import discover_models
from kohakuterrarium.studio.identity.llm.profiles import save_discovered_model


def models_cli(provider: str, save: str | None = None) -> int:
    try:
        models = asyncio.run(discover_models(provider))
        if save is None:
            for model in models:
                print(f"{model.id}\t{model.name or model.id}")
            if not models:
                print("The provider returned no models.")
            return 0
        match = next((model for model in models if model.id == save), None)
        if match is None:
            raise ValueError(f"Model not found in the provider catalog: {save}")
        preset, created = save_discovered_model(provider, match)
        print(f"{'Saved' if created else 'Existing preset:'} {provider}/{preset.name}")
        return 0
    except ValueError as exc:
        print(str(exc))
        return 1
