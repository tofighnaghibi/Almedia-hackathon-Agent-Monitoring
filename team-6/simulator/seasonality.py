"""Daily and weekly traffic shapes."""

from __future__ import annotations

import numpy as np

# (peak local hour, width in hours, height). Mobile peaks in the evening,
# web is stronger during working hours.
_PEAKS = {
    # broad daytime level, then morning commute, lunch and evening peaks
    "mobile": [(14.5, 4.5, 0.55), (8.0, 1.2, 0.25), (12.5, 1.2, 0.30), (20.5, 1.8, 0.95)],
    "web": [(13.5, 4.0, 0.60), (10.0, 1.5, 0.45), (14.5, 1.5, 0.40), (20.0, 1.8, 0.45)],
}
_FLOOR = 0.10


def hourly_profile(platform: str) -> np.ndarray:
    """Relative traffic for local hours 0..23, normalised to mean 1."""
    kind = "web" if platform == "web" else "mobile"
    hours = np.arange(24, dtype=float)
    curve = np.full(24, _FLOOR)
    for peak, width, height in _PEAKS[kind]:
        # circular distance so 23:00 and 01:00 are 2 hours apart
        d = np.abs(hours - peak)
        d = np.minimum(d, 24 - d)
        curve += height * np.exp(-0.5 * (d / width) ** 2)
    return curve / curve.mean()


def weekday_profile(weekday_factor: list[float]) -> np.ndarray:
    """Day-of-week factors (Mon..Sun) normalised to mean 1."""
    w = np.asarray(weekday_factor, dtype=float)
    if w.shape != (7,):
        raise ValueError("weekday_factor needs 7 values (Mon..Sun)")
    return w / w.mean()
