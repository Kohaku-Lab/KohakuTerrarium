"""User-scoped pet metadata, image persistence and in-process subscriptions."""

import asyncio
import base64
import copy
import io
import json
import os
import re
import tempfile
import weakref
from pathlib import Path

from PIL import Image

from kohakuterrarium.utils.logging import get_logger

from .atlas import Pet, PetFormatError, interaction_frames, parse_pet

logger = get_logger(__name__)
KEY = re.compile(r"[a-f0-9]{64}")
_SHARED: weakref.WeakValueDictionary = weakref.WeakValueDictionary()


def shared_library(root: Path) -> "PetLibrary":
    """Return the one live library for a resolved user root."""
    key = root.resolve()
    library = _SHARED.get(key)
    if library is None:
        library = PetLibrary(key)
        _SHARED[key] = library
    return library


class PetLibrary:
    """Serialize durable library changes and notify live owner adapters."""

    def __init__(self, root: Path):
        self.root = root
        self._record = {"pets": {}, "selected": None}
        self._loaded = False
        self._lock = asyncio.Lock()
        self._subscribers = {}

    def _read(self) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "index.json"
        record = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else self._record
        )
        for key, pet in record["pets"].items():
            metadata = pet["metadata"]
            if "interactions" not in metadata:
                metadata["interactions"] = {}
                if not KEY.fullmatch(key) or pet["mime"] not in (
                    "image/png",
                    "image/webp",
                ):
                    continue
                try:
                    metadata["interactions"] = interaction_frames(
                        (self.root / f"{key}.{pet['mime'].split('/')[1]}").read_bytes()
                    )
                except (OSError, ValueError, Image.DecompressionBombError):
                    logger.warning("Pet interaction frames unavailable", exc_info=True)
        return record

    async def load(self) -> "PetLibrary":
        async with self._lock:
            if not self._loaded:
                self._record = await asyncio.to_thread(self._read)
                self._loaded = True
        return self

    def snapshot(self) -> dict:
        pets = [
            dict(key=key, **record["metadata"])
            for key, record in self._record["pets"].items()
        ]
        return copy.deepcopy(
            {
                "pets": sorted(pets, key=lambda pet: (pet["displayName"], pet["key"])),
                "selected": self._record["selected"],
            }
        )

    def _write(self, target: Path, content: bytes) -> None:
        descriptor, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _save(self, record: dict, pet: Pet | None, removed: Path | None) -> None:
        if pet is not None:
            extension = pet.mime.split("/")[1]
            self._write(self.root / f"{pet.key}.{extension}", pet.image)
        self._write(
            self.root / "index.json", json.dumps(record, ensure_ascii=False).encode()
        )
        if removed is not None:
            try:
                removed.unlink(missing_ok=True)
            except OSError:
                logger.warning("Unused pet image could not be removed", exc_info=True)

    async def _commit(
        self, record: dict, pet: Pet | None = None, removed: Path | None = None
    ) -> None:
        task = asyncio.create_task(asyncio.to_thread(self._save, record, pet, removed))
        cancelled = False
        while True:
            try:
                await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True
        self._record = record
        for callback in tuple(self._subscribers.values()):
            try:
                callback(self)
            except Exception:
                logger.warning("Pet library subscriber failed", exc_info=True)
        if cancelled:
            raise asyncio.CancelledError

    def subscribe(self, callback):
        """Register one change callback and return its idempotent unsubscribe."""
        token = object()
        self._subscribers[token] = callback

        def unsubscribe():
            self._subscribers.pop(token, None)

        return unsubscribe

    def _pet(self, key: str) -> dict:
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise PetFormatError("Invalid pet key.")
        if key not in self._record["pets"]:
            raise PetFormatError("Pet is not in this user's library.")
        return self._record["pets"][key]

    async def import_archive(self, archive: bytes) -> None:
        pet = await asyncio.to_thread(parse_pet, archive)
        async with self._lock:
            record = copy.deepcopy(self._record)
            record["pets"][pet.key] = {"metadata": pet.metadata, "mime": pet.mime}
            record["selected"] = pet.key
            if record != self._record:
                await self._commit(record, pet)

    async def select(self, key: str) -> None:
        async with self._lock:
            self._pet(key)
            if self._record["selected"] != key:
                await self._commit({**self._record, "selected": key})

    async def remove(self, key: str) -> None:
        """Remove a pet and select the first remaining name, or no pet."""
        async with self._lock:
            mime = self._pet(key)["mime"]
            record = copy.deepcopy(self._record)
            del record["pets"][key]
            if record["selected"] == key:
                record["selected"] = min(
                    record["pets"],
                    key=lambda item: (
                        record["pets"][item]["metadata"]["displayName"],
                        item,
                    ),
                    default=None,
                )
            await self._commit(
                record, removed=self.root / f"{key}.{mime.split('/')[1]}"
            )

    def _thumbnail(self, path: Path) -> bytes:
        with Image.open(path) as image:
            preview = image.crop((0, 0, 192, 208)).convert("RGBA")
            preview.thumbnail((96, 104))
            output = io.BytesIO()
            preview.save(output, "PNG")
            return output.getvalue()

    async def thumbnail(self, key: str) -> str:
        """Return a small still image for the settings picker."""
        async with self._lock:
            mime = self._pet(key)["mime"]
            image = await asyncio.to_thread(
                self._thumbnail, self.root / f"{key}.{mime.split('/')[1]}"
            )
        return f"data:image/png;base64,{base64.b64encode(image).decode('ascii')}"

    async def sprite(self, key: str) -> str:
        async with self._lock:
            mime = self._pet(key)["mime"]
            image = await asyncio.to_thread(
                (self.root / f"{key}.{mime.split('/')[1]}").read_bytes
            )
        return f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
