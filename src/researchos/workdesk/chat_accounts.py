"""Local records that a user confirmed sign-in in an official Chat web view."""
import json

from .workspace import plain


def connections(locations):
    """Keep user-confirmed Chat sessions distinct from Work authentication."""
    from .partners.launch import PARTNERS
    connected = dict.fromkeys(PARTNERS, False)
    if not locations.user:
        return connected
    root = locations.user / "browser-profiles" / "official-chat"
    for provider in connected:
        try:
            path = plain(root / provider / "workdesk-confirmed.json", locations.user)
            if not path.is_file() or path.stat().st_size > 256:
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
            connected[provider] = (isinstance(value, dict)
                                   and type(value.get("schema")) is int and value["schema"] == 1
                                   and value.get("provider") == provider
                                   and value.get("confirmed") is True and len(value) == 3)
        except (OSError, UnicodeError, ValueError):
            continue
    return connected
