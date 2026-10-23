"""A small server-drawn SVG chart for the profile: problems solved over time.

No JavaScript and no chart library: the view computes the points, the template draws one
<polyline>. Coordinates are in the SVG's own units (its viewBox), so it scales to any width.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import date

WIDTH, HEIGHT = 900, 220
LEFT, RIGHT, TOP, BOTTOM = 44, 884, 16, 190


@dataclass(frozen=True)
class SolvedChart:
    points: list[tuple[float, float]]
    total: int
    first: date
    last: date
    left: float = LEFT
    right: float = RIGHT
    top: float = TOP
    bottom: float = BOTTOM
    width: int = WIDTH
    height: int = HEIGHT

    @property
    def points_attribute(self) -> str:
        """The `points` attribute of the <polyline>: "x,y x,y ..."."""
        return " ".join(f"{x:g},{y:g}" for x, y in self.points)


def solved_over_time(days: list[date]) -> SolvedChart | None:
    """A staircase of how many problems were solved by each day (`days`: one per solve).

    None when nothing is solved yet.
    """
    if not days:
        return None
    per_day = sorted(Counter(days).items())
    first, last = per_day[0][0], per_day[-1][0]
    span = (last - first).days
    total = len(days)

    def x(day: date) -> float:
        if span == 0:
            return RIGHT  # one day only: draw a flat line, then the step at the right
        return round(LEFT + (day - first).days / span * (RIGHT - LEFT), 1)

    def y(count: int) -> float:
        return round(BOTTOM - count / total * (BOTTOM - TOP), 1)

    points = [(float(LEFT), y(0))]
    solved = 0
    for day, count in per_day:
        points.append((x(day), y(solved)))
        solved += count
        points.append((x(day), y(solved)))
    return SolvedChart(points=points, total=total, first=first, last=last)
