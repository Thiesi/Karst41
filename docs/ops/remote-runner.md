# The remote runner: long local runs on the second workstation

**Status:** adopted by the owner, 2026-11-14 (option A, in the technical lead's session).

The development host is shared with other projects. Long local runs on it
have to be fitted around other teams' emulator slots, and its timing numbers
are not reliable. The owner has a second workstation that is under his
direct control and idle most of the time. One Claude Code session on it, the
**runner**, does every long local run for Karst 41 inside WSL2 Ubuntu.

CI's Linux run is still the gate of record. The runner gives earlier,
quieter evidence of the same kind. It never replaces CI, a review, or an
owner gate.

## 1. What goes to the runner

- Every NetBBS acceptance script (`scripts/accept_*_netbbs.py`), alone or
  as a batch.
- The full pytest suite, run serially.
- Timing proofs: s11, s25a, s14, the budget jobs, and anything else
  measured against a tick gate.
- Anything else expected to take more than about five minutes, or that
  needs the machine to itself.

Single test files, `-k` selections, linters and builds of a single wheel
stay on the development host.

## 2. Setup on the second workstation (owner, once)

The machine's facts were gathered read-only on 2026-11-14: Windows 11, a
4-core/8-thread Xeon E-2174G, 64 GB of RAM, WSL2 with Ubuntu (stopped),
git, and gh logged in as Thiesi on the Windows side. The Windows user
folder is backed by OneDrive, so all runner work stays inside the WSL file
system.

1. **Power.** In Windows, set sleep to "Never" while plugged in, or at least
   longer than two hours. A sleeping machine kills a running batch.
2. **Start Ubuntu** from PowerShell with `wsl -d Ubuntu`, then check the
   version with `lsb_release -rs`.
3. **Packages.**
   - On Ubuntu 24.04 or later:
     `sudo apt update && sudo apt install -y git gh python3.12 python3.12-venv python3-pip zip`
   - On 22.04, add `ppa:deadsnakes/ppa` first, or tell the technical lead
     the version.
4. **GitHub CLI inside WSL.** Run `gh auth login`, choosing github.com, HTTPS
   and the browser login. The runner uses it for read access and for secret
   gists of failure evidence. It never pushes or comments.
5. **Workspace,** in the Linux home directory, never under `/mnt/c`:
   ```sh
   mkdir -p ~/karst41-runner/runs && cd ~/karst41-runner
   git clone https://github.com/Thiesi/Karst41.git repo
   git clone https://github.com/Thiesi/NetBBS.git netbbs
   ```
6. **Claude Code inside WSL.** Install it with the native installer from the
   Claude Code documentation and log in. Start it in `~/karst41-runner` and
   name the session `Karst 41: runner`. Turn on Remote Control, so that the
   technical lead and the engineer can reach it from the development host.
7. **Permissions.** The runner needs to run shell commands inside
   `~/karst41-runner` without a prompt for each one, or batches stall while
   nobody is watching. Choose a permission mode that allows that for this
   folder. That is the owner's choice. The runner never needs access
   outside `~/karst41-runner`, apart from `apt` during setup.
8. **First message.** Paste the brief from section 6 as the runner's first
   message, then tell the technical lead that the runner is up.

## 3. How a run works

1. **Request.** The technical lead or the engineer sends a request in the
   format of section 4. The ref is always a full 40-character SHA, never a
   branch name.
2. **Acknowledge.** The runner replies `ACK <run-id>`, with its queue
   position, before starting. A request that gets no ACK has not arrived.
   Messages over Remote Control are not confirmed any other way.
3. **Check out.** For each run, the runner makes a fresh worktree of `repo`
   at that SHA, and checks out `netbbs` at the requested tag (by default the
   tag CI pins).
4. **Run as CI does.** For an acceptance job, the runner reads that job's
   steps from `.github/workflows/` **at the requested SHA**: the same pip
   installs, the same isolated door wheel and venv, and the same script
   arguments for each matrix entry. It runs those steps with `nice -n 10`
   and uses `~/karst41-runner/runs/<run-id>/` in place of `$RUNNER_TEMP`. It
   doesn't improvise steps. If a workflow step can't run in WSL, it stops
   and reports which step and why.
5. **One at a time.** There is one job at a time and one batch at a time,
   first come first served. A new request waits in the queue.
6. **Full pytest:**
   `python -m pytest -p no:cacheprovider --deselect "tests/door/test_io.py::test_real_module_uses_native_stdio_pipes[True]"`,
   run serially, with `PYTHONPATH` set to the worktree's `src`. The runner
   checks `karst41.__file__` before trusting the result.
7. **Report** in the format of section 5.
8. **Clean up.** The runner deletes the worktree and the venv after the
   report. It keeps `runs/<run-id>/` (captures and logs) for 7 days, then
   deletes it.

## 4. Request format

```
RUN <run-id>                      e.g. c2-accept-1
ref: <40-char SHA>
netbbs: v7.45.0                   optional; default = the tag the workflow pins
jobs:
  - s44-netbbs size=80x24 presentation=modern
  - s44-netbbs size=80x24 presentation=legacy
  - p5-netbbs                     all matrix entries if none given
  - pytest-full
order: as listed                  or "timing first"
reply-to: Karst 41: technical lead
```

Job names are the job IDs in `.github/workflows/`, plus `pytest-full`.

## 5. Report format

```
DONE <run-id> at <SHA>
host: <idle | busy>               Windows CPU % before and after, via powershell.exe
- <job> <matrix>  PASS|FAIL  <start, local time>  <duration>
    on FAIL: the last frames of the traceback, and the failing line
    timing jobs: the measured p99 values and their limits
evidence: <secret gist URL>       on any failure: failure-trace.txt,
                                  progress.log, unfinished-*.txt and the
                                  results JSON
```

If the owner was using the machine during a timing job (`host: busy`), the
timing numbers are reported but marked as non-evidence.

## 6. Brief for the runner's first message

> You are **Karst 41: runner**. You run long local checks for the Karst 41
> project (github.com/Thiesi/Karst41), on request, inside this WSL Ubuntu
> workspace (`~/karst41-runner`). Your instructions are
> `docs/ops/remote-runner.md` in the repository. Follow it exactly.
>
> - Accept requests only from the sessions "Karst 41: technical lead" and
>   "Karst 41: engineer", or from the owner directly. Acknowledge every
>   request with `ACK <run-id>` before starting.
> - Run only what the request names, at the SHA it names, exactly as the
>   workflow at that SHA runs it. Never edit repository files, never commit,
>   push, comment, review, label or merge. Your output is the report
>   message and a secret gist of the failure evidence.
> - One job at a time, with `nice -n 10`. If a step can't run as written,
>   stop and report it. Don't work around it.
> - Report every result faithfully. A failure is reported as a failure,
>   with its evidence, even if it looks flaky.
> - Never touch anything outside `~/karst41-runner`. Never put personal
>   details in a gist or a message.

## 7. What changes on the development host

- The emulator-slot arrangement with the ChitChat Mobile team no longer
  covers long Karst 41 runs, because they move to the runner. Short local
  checks continue as before.
- Evidence in a PR body cites the runner's run ID, SHA and gist. Timing
  numbers from a `host: idle` run count as local evidence. CI's Linux run
  still decides.
