# S48c3b acceptance captures (#261, #256, #236)

Headless frames through the presentation door and the production Core,
from `tests/door/test_s48c3b_captures.py`. The **after** frames are its
goldens, in [`tests/presentation/golden/s48c3b`](../../../../tests/presentation/golden/s48c3b).
The **before** frames here come from the same test file run unchanged
against main at `6861b042171e603638415e4a02b2e298fce82c7a`. Its own
assertions fail there, which is the point; every frame is written before
the asserts.

Each scene is captured at 80x24 and 132x50 in the 256-colour tier, plus
one 16-colour frame at 80x24, as #261 asks. Single-size scenes are
noted.

| Scene | What to look at | Before | After |
|---|---|---|---|
| Row 1, HERE | `Public terminal (Plaza 1)` at the plaza: `§ Public... (Plaza 1)` becomes `§ terminal (Plaza 1)` with its right margin | [before](before/) `s48c3b_here_*` | `s48c3b_here_*` |
| Row 2, NEARBY | `Dr. Ilse Vant... ~` becomes `Vantongeren ~` | `s48c3b_nearby_*` | `s48c3b_nearby_*` |
| Row 3, header | 80x24, HUNTED with the truce notice: `S...` becomes `Sodium...`, and the notice shows whole to one cell before the corner | `s48c3b_header_*` | `s48c3b_header_*` |
| Row 4, stutter | `SODIUM ROW─Sodium Row` becomes `Sodium Row` | `s48c3b_stutter_*` | `s48c3b_stutter_*` |
| NEARBY collision | Two different names whose short forms would both read `Vantongeren`: both use the prefix | `s48c3b_collision_*` | `s48c3b_collision_*` |
| Collision, drawn or hidden (80x24 only) | With no target, both rows are drawn and both use the prefix. A target's rows push the farther one off the panel, and the nearer, alone again, shows `Vantongeren` | `s48c3b_collision_{drawn,hidden}_80x24_256` | same |
| Handle dropped (132x50 only) | `Measure · Measurement-Dept` doesn't fit beside the grade: the name row shows `Measure`, never `Measure · Mea...` | `s48c3b_handle_dropped_132x50_256` | same |
| #256 log trade | A tall older entry doesn't fit: its rows stay blank, and the shorter lines older than it are no longer shown under the gap | `s48c3b_log_stop_*` | `s48c3b_log_stop_*` |

The scenes rename one hall NPC for the collision cases, as a fixture
only, and inject log lines for the #256 trade. Nothing else is staged.

The beat-9 frame `9_10` at 80x24
([`tests/presentation/golden/s48c2-beat9`](../../../../tests/presentation/golden/s48c2-beat9))
also moves under #256: 9.7 no longer shows under the two-row 9.8. The
presentation lead re-accepts it; it was interim since #252.

H1 and S25 layout approval: NOT GRANTED.
