"""Authenticated storage scope for the pet UI adapters."""

from kohakuterrarium.modules.ui_owner import OwnerForbiddenError
from kohakuterrarium.utils.config_dir import config_dir


def user_root(context):
    """Resolve the caller's user identity and library directory."""
    user_id = getattr(context, "user_id", None)
    if getattr(context, "multi_user", False) and type(user_id) is not int:
        raise OwnerForbiddenError("Sign in to manage your pets.")
    if user_id is not None and (type(user_id) is not int or user_id < 1):
        raise OwnerForbiddenError("Invalid pet library user.")
    root = config_dir()
    if user_id is not None:
        root = root / "users" / str(user_id)
        root.mkdir(parents=True, exist_ok=True)
    return user_id, root / "virtual-pet"
