# S48c1b training packet

Execution head: `3ee2f70e4f466ded5b1316cdcf9861430d3c9b19` (PR #245).
Driver: `scripts/accept_p5_netbbs.py`, all sizes and tiers (no `--size`
or `--tier`). Transport: real NetBBS v7.45.0 Telnet to a registered native
stdio door, source-matched wheel, `DOOR_KARST41_PRESENTATION=1`, run
locally on Windows. The node, world and accounts are private; NPCs, rules,
funds and clock are normal. Human and presentation acceptance are not
granted. The presentation lead accepts these frames as new captures.

## Why a new packet

S48c1b (#220 item 6, #217 ruling, creative lead beat (a)): the training
drone wakes only for an unread body. P5 therefore fights the drone first,
as the unread body, then reads the decant record and names the character.
That changes the input sequence behind every P5 capture, so under S43 none
of the old captures can be re-pinned. They are retired, not overwritten.
`capture-retirements.json` lists each one with its reason, this PR and the
file that supersedes it. The old files stay where they were.

| Retired from | Captures |
|---|---|
| `../p5-client-foundations/` | `training-drone`, `training-complete`, `meridian-plaza`, `inventory`, `contracts` and `help` at both sizes and all tiers; `wake-hall` at 132x50 (39 files) |
| `../s48c1a-creation/` | `wake-record` and `wake-hall` at 80x24 (6 files) |

`issue67-recruiters-*` in `../s48c1a-creation/` is unchanged: it is taken
in the hall before any input this slice changed.

## Captures

There are six cases (80x24 and 132x50, each in 16, 256 and truecolor), with
eight frames per case, in input order:

1. `training-drone`: the unread body has walked to the ring, targeted the drone with Tab and fired once.
2. `training-complete`: F until `wake_combat` is `done`.
3. `wake-record`: the decant record, opened with E beside the street door.
4. `wake-hall`: after the name. The character keeps `done` from the fight.
5. `meridian-plaza`: Clinic, then Plaza, walking to (31, 5).
6. `inventory`, 7. `contracts`, 8. `help`: I, Q and ?, each closed with Esc.

Only the `.txt` frames are committed, as in the earlier packets. The
`.ans` and `.json` files that `result.json`'s manifest hashes are run
artifacts. The manifest hashes the files as the Windows run wrote them,
with CRLF in the `.txt` files. The committed `.txt` frames are normalised
to LF like the earlier packets. Each `.txt` manifest entry therefore also
has `committed_lf` (bytes and sha256 of the committed file, added after the
run), so a plain checkout can be verified directly. Converting a committed
frame back to CRLF reproduces the run hash (checked for all 48).

The character names (`P界80`, `Q界132` and so on) are deliberate: P5 names
each character with a wide CJK glyph (U+754C, UTF-8 `e7 95 8c`) to prove
wide-cell handling in the live name field, as in the earlier P5 packet.

## Stored inputs

`result.json` records its own inputs, so a later change can be checked
against them instead of being reconstructed from the script:

- `inputs_record`: the script path, the commit it ran at, and the format.
- Per case, `inputs.connect`: the Telnet NAWS size, TERM, COLORTERM and
  whether the account is 16-colour.
- Per case, `inputs.steps`: one entry per capture, listing every string
  the caller wrote since the previous capture, in order. The first entry
  starts at the door menu selection right after connect. Server-side moves
  appear in the same sequence as `{"move": ...}` (create()'s disclosed
  `return_to` to the arrival tile).

The number of `F` presses and the walk keys are the real ones from this
run. The drone fight and pathing are live, so a rerun of the same script
may record a different count. That count is recorded, so a later comparison
can tell this run variance apart from a changed sequence (keys added,
removed or reordered, a different connect, a new move).
