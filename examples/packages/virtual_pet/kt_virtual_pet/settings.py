"""Package-owned pet management independent of creature lifecycles."""

import base64
import binascii

from kohakuterrarium.modules.ui_owner import OwnerArgumentError, UIOwnerMixin

from .atlas import MAX_ARCHIVE_BYTES, PetFormatError
from .scope import user_root
from .store import shared_library


class PetSettings(UIOwnerMixin):
    """Manage each authenticated caller's pets and publish targeted changes."""

    def __init__(self):
        self._libraries = {}
        self._subscriptions = {}
        self._closed = False

    async def _library(self, context):
        if self._closed:
            raise OwnerArgumentError("Pet settings are closed. Reopen settings.")
        user_id, root = user_root(context)
        root = root.resolve()
        library = self._libraries.get(root)
        if library is None:
            library = shared_library(root)
            self._libraries[root] = library
            self._subscriptions[root] = library.subscribe(
                lambda current, owner=user_id: self._changed(current, owner)
            )
        library = await library.load()
        if self._closed:
            raise OwnerArgumentError("Pet settings are closed. Reopen settings.")
        return library

    def _state(self, library):
        return {**library.snapshot(), "maxArchiveBytes": MAX_ARCHIVE_BYTES}

    def _changed(self, library, user_id):
        if not self._closed:
            self.ui_emit("state", self._state(library), user_id=user_id)

    async def ui_state(self, context=None):
        return self._state(await self._library(context))

    async def ui_call(self, action: str, args: dict, context=None):
        library = await self._library(context)
        try:
            match action:
                case "import":
                    encoded = args.get("archive")
                    if not isinstance(encoded, str) or len(encoded) > 4 * (
                        (MAX_ARCHIVE_BYTES + 2) // 3
                    ):
                        raise PetFormatError("Choose a pet ZIP no larger than 8 MiB.")
                    if not encoded.isascii():
                        raise PetFormatError("Pet ZIP must be ASCII Base64 text.")
                    await library.import_archive(
                        base64.b64decode(encoded, validate=True)
                    )
                case "select":
                    await library.select(args.get("key"))
                case "remove":
                    await library.remove(args.get("key"))
                case "thumbnail":
                    return await library.thumbnail(args.get("key"))
                case "sprite":
                    return await library.sprite(args.get("key"))
                case _:
                    raise PetFormatError("Unknown pet settings action.")
        except (PetFormatError, binascii.Error) as exc:
            raise OwnerArgumentError(str(exc)) from exc
        except OSError as exc:
            raise OwnerArgumentError(
                "Couldn't access pet storage. Check backend disk space and permissions, then retry."
            ) from exc
        return self._state(library)

    async def aclose(self):
        self._closed = True
        for unsubscribe in self._subscriptions.values():
            unsubscribe()
        self._subscriptions.clear()
        self._libraries.clear()
