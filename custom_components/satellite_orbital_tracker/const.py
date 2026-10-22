"""Constants for the Satellite Orbital Tracker integration."""
DOMAIN = "satellite_orbital_tracker"


def normalize_norad_id(value) -> str | None:
    """Return the NORAD catalog number without leading zeros ("025544" -> "25544"), or None if not digits only.

    Normalizing keeps one satellite from being added twice under two spellings of the same number.
    """
    text = str(value).strip()
    if not (text.isascii() and text.isdigit()):
        return None
    return str(int(text))
