"""Direction of the Sun and the Earth's shadow, computed without an ephemeris file.

Skyfield's own sunlight check needs a planetary ephemeris (a download of several MB, from the network, in
a blocking call). The satellite only needs to know whether it is in the Earth's shadow, which needs the
direction of the Sun to a fraction of a degree, so the Astronomical Almanac's low precision formula is used.
It is good to about 0.01 degrees from 1950 to 2050, which moves the moment a satellite enters or leaves the
shadow by a second or two.
"""
from __future__ import annotations

import numpy as np

EARTH_RADIUS_KM = 6378.137  # the shadow is treated as a cylinder of this radius; the penumbra lasts seconds
_J2000_JD = 2451545.0
_ARCSEC = np.pi / (180 * 3600)


def sun_direction(jd_tt) -> np.ndarray:
    """Return unit vectors (shape 3 x N) from the Earth's centre to the Sun in the GCRS frame.

    jd_tt is a Julian date in terrestrial time, or an array of them.
    """
    n = np.atleast_1d(np.asarray(jd_tt, dtype=float)) - _J2000_JD
    mean_longitude = np.radians((280.460 + 0.9856474 * n) % 360)
    mean_anomaly = np.radians((357.528 + 0.9856003 * n) % 360)
    longitude = mean_longitude + np.radians(1.915) * np.sin(mean_anomaly) + np.radians(0.020) * np.sin(2 * mean_anomaly)
    obliquity = np.radians(23.439 - 0.0000004 * n)
    of_date = np.stack([
        np.cos(longitude),
        np.cos(obliquity) * np.sin(longitude),
        np.sin(obliquity) * np.sin(longitude),
    ])

    # The formula gives the position on the equator and equinox of the date. Turn it back to J2000 (the
    # frame of the satellite positions) with the precession angles of the IAU 1976 model.
    t = n / 36525.0
    zeta = (2306.2181 * t + 0.30188 * t**2) * _ARCSEC
    z = (2306.2181 * t + 1.09468 * t**2) * _ARCSEC
    theta = (2004.3109 * t - 0.42665 * t**2) * _ARCSEC
    cz, sz, cs, ss, ct, st = np.cos(z), np.sin(z), np.cos(zeta), np.sin(zeta), np.cos(theta), np.sin(theta)
    # Rows of the J2000 -> date matrix
    p = np.array([
        [cz * ct * cs - sz * ss, -cz * ct * ss - sz * cs, -cz * st],
        [sz * ct * cs + cz * ss, -sz * ct * ss + cz * cs, -sz * st],
        [st * cs, -st * ss, ct],
    ])
    return np.einsum("jin,jn->in", p, of_date)  # transpose: date -> J2000


def is_sunlit(position_km: np.ndarray, sun: np.ndarray) -> np.ndarray:
    """Return, for positions (3 x N, km from the Earth's centre) and Sun directions, whether the Sun shines on them.

    A point is in shadow when it is on the night side and within the cylinder of the Earth's radius behind it.
    """
    along = np.sum(position_km * sun, axis=0)
    off_axis = np.sqrt(np.maximum(np.sum(position_km**2, axis=0) - along**2, 0.0))
    return (along > 0) | (off_axis > EARTH_RADIUS_KM)
