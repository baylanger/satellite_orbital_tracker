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


COMPASS_POINTS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def compass_point(azimuth_degrees: float) -> str:
    """Return the compass point (N, NNE, NE ...) for an azimuth in degrees, measured clockwise from north."""
    return COMPASS_POINTS[int(((azimuth_degrees % 360) + 11.25) // 22.5) % 16]
