"""Turning calibration times into a suggested time scale.

Rule (docs/DECISIONS.md): a reference solution should need at most half of its time limit on our
judge CPUs. So for each test it passed in every run, scale ≥ 2 × slowest time ÷ limit at scale
1.0. Tests it didn't pass (a brute force meant to TLE) are left out: their times were cut off.
The suggestion is the largest of these, rounded up to 0.1, and never below 1.0 (the contest's
own limits) or above the largest scale the judge accepts.
"""

import math
from typing import Any

from core.models import MAX_TIME_SCALE
from grader_common.api import CalibrationReport

HEADROOM = 2.0
MIN_SUGGESTION = 1.0


def suggested_time_scale(result: dict[str, Any]) -> float | None:
    """The scale this calibration result asks for, or None when it has no times."""
    report = CalibrationReport.model_validate(result)
    needed = [
        HEADROOM * test.max_time / solution.limit
        for solution in report.solutions
        for test in solution.tests
        if test.accepted
    ]
    if not needed:
        return None
    # round() first: 2 x 0.8 / 1.0 is 1.6000000000000001 in floats, which must stay 1.6.
    tenths = math.ceil(round(max(needed) * 10, 6))
    return min(max(tenths / 10, MIN_SUGGESTION), MAX_TIME_SCALE)
