# S48c2: S43 re-pins, before and after

PR #252 re-pins the frame and body hashes of 17 of the 20 S43 cases
(`docs/ops/captures/s43/content-repins.json`, entries with `"pr": 252`).
No actor trace, input or successful move changed.

## Why the hashes moved

- **The Wake Hall (2 cases):** c2 restages the hall. Okafor and Vantongeren
  now stand at their posts, so they are in view. This is a content change.
- **The other 15 cases:** the S43 fixture creates its character in the Wake
  Hall before placing it in the measured zone. The two hall talkers now spawn
  during that creation, so every NPC spawned later gets an id two higher.
  Bodies that list actors in id order also list them in a different order.
  With NPC ids masked and lists sorted, every frame and body matches main:
  `scripts/compare_s43_normalised.py --ids` reports `match` for all 18 cases
  outside the hall, on main 49113fb and on c2.

## The captures

`before/` is main 49113fb, `after/` is c2. Each file is the fixture's
character after its idle scene (10 + the case's ticks), drawn through the
presentation door at 132x50 in the 256-colour tier.

- **Every zone:** the log's first entry is the hall's discovery line. c2
  shortens it to the first paragraph of the hall's lore ("You wake on a gurney
  under a number."). That is c2's own change to the hall, not part of S43, and
  it is the only difference outside the hall: the maps, panels and headers are
  identical.
- **The Wake Hall:** Vantongeren shows under NEARBY, her ambient line is in
  the log, and the orderlies stand on different tiles. Not verified: this is
  probably because the two new spawns come first, which changes where the
  orderlies' random spawns land.
