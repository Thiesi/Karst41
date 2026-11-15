# S48c1b training packet (re-pinned by S48c2)

Execution head: `11742a723d0c368577f8e92fd5f9a6496821093c` (PR #252).
Driver: `scripts/accept_p5_netbbs.py`, all sizes and tiers (no `--size`
or `--tier`). Transport: real NetBBS v7.45.0 Telnet to a registered native
stdio door, source-matched wheel, `DOOR_KARST41_PRESENTATION=1`.

The run was made on the runner (`docs/ops/remote-runner.md`: WSL2 Ubuntu
24.04 on the owner's second workstation), as job
`p5-acceptance:native`, run ID `c2-p5-4`, both matrix entries. The node,
world and accounts are private; NPCs, rules, funds and clock are normal.
Human and presentation acceptance are not granted.

The frames stay valid across the merge of `origin/main` into the branch
(`550d9aaa6bba2634f22f59fe6e096933e681955f`). That merge brings only #251 (canon text) and #248 (an
acceptance script and a test), with no change under `src/`.

## History

- **PR #245 (S48c1b)** made this packet at `3ee2f70`, run locally on Windows.
  It replaced the earlier P5 and S48c1a captures, which are listed in
  `capture-retirements.json`, because c1b changed the input sequence
  behind every P5 capture: the unread body now fights the drone before the
  record. Those old files stay where they were.
- **PR #252 (S48c2)** re-pins 42 of the 48 frames from `c2-p5-4`. Each one is
  listed in `capture-repins.json` with its reason and PR. The six
  unchanged frames (inventory, contracts and help at 80x24 in 16 and 256)
  are byte-identical. The previous frames stay in git history.

## Why these are re-pins, not retirements

The technical lead's loop rule (S48c2, recorded in `result.json` as
`inputs_record.comparison`) decides whether a rerun has the same inputs.
Each capture's inputs are stored as `segments`:
- a `{"keys": [...]}` segment must be byte-identical;
- a loop segment (`{"loop", "goal", "may_press", "presses", "reached"}`)
  matches when the loop kind, the goal and the `may_press` set are equal
  and the goal was reached. `presses` (the count, and a walk's detour) is
  run variance.

The c1b packet predates segments, so it was compared step by step, with
each old step matched against the new segments. Fixed keys were found
byte-identical. Each loop's stretch held only that loop's `may_press`
keys. The connect was equal. Between `3ee2f70` and `558ea36` the driver
changed only its recording, and its walk goals are literal or exit tiles
that S48c2 does not move, so the goals are equal by construction. All six
cases match on all nine steps, so the frames are re-pinned.

What changed on screen is S48c2's own work:
- the opening's script lines, in the runner contract's order, with the
  five owner-approved amendments listed in #252;
- the two runner bug fixes listed in #252. Before them, beats 2-5 never
  ran, so earlier c2 hall captures predate them;
- canon #251's rule that a script line never merges;
- the hall talkers;
- #220 4.12 text.

Live variance (the clock, hits and misses, NPC positions) also differs, as
in any rerun.

## Two waits, no keys

`c2-p5-4`'s driver waits twice. It never sends a key while waiting, so the
stored inputs and the loop rule are unaffected.
- **At the decant record**, before the `wake-record` capture, it waits
  until no record line is waiting and the speech gap is over. It does this
  only when it captures the record (`capture_wake`). The frame shows 9.2
  as a player who pauses at the record sees it.
- **Before the `wake-hall` capture**, it waits until the opening's queue is
  empty and the speech gap is over. The frame shows beat 9 in the creative
  lead's order: 9.8, 9.9, Vantongeren's lease lines, the kit, then 9.11
  last.

Both waits then hold until the last spoken line is on screen, because log
lines are not views. P5 otherwise names at once, which is a real path, so
9.3 to 9.7 can be skipped (an approved drop).

## Captures

There are six cases (80x24 and 132x50, each in 16, 256 and truecolor), with
eight frames per case, in input order:

1. `training-drone`: the unread body has walked to the ring, targeted the drone with Tab and fired once.
2. `training-complete`: F until `wake_combat` is `done`.
3. `wake-record`: the decant record, opened with E beside the street door.
4. `wake-hall`: after the name, once the opening's lines are said. The character keeps `done` from the fight.
5. `meridian-plaza`: Clinic, then Plaza, walking to (31, 5).
6. `inventory`, 7. `contracts`, 8. `help`: I, Q and ?, each closed with Esc.

Only the `.txt` frames are committed. `result.json`'s manifest hashes every
file the run wrote; the `.ans` and `.json` files are run artifacts kept on
the runner. The run wrote LF frames, so each `.txt` hash in the manifest is
also the committed file's hash. `result.json` also records the platform,
the run ID and the SHA.

The character names (`P界80`, `Q界132` and so on) are deliberate: P5 names
each character with a wide CJK glyph (U+754C, UTF-8 `e7 95 8c`) to prove
wide-cell handling in the live name field, as in the earlier P5 packet.

## Stored inputs

`result.json` records its own inputs, so a later change can be checked
against them instead of being reconstructed from the script:

- `inputs_record`: the script path, the commit it ran at, the format and
  the comparison rule.
- Per case, `inputs.connect`: the Telnet NAWS size, TERM, COLORTERM and
  whether the account is 16-colour.
- Per case, `inputs.steps`: one entry per capture, with every string the
  caller wrote since the previous capture (`inputs`) and the same keys
  split into `segments`. Server-side moves appear as `{"move": ...}`
  (create()'s disclosed `return_to` to the arrival tile).
