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
3. **Proxy, if the network needs one.**
   - In `/etc/environment`, set `http_proxy`, `https_proxy`, `HTTP_PROXY`
     and `HTTPS_PROXY` to the proxy. Set `no_proxy` and `NO_PROXY` to
     `localhost,127.0.0.1,::1`, because the acceptance scripts connect to
     NetBBS on localhost.
   - `sudo apt` ignores those variables, so also add
     `/etc/apt/apt.conf.d/95proxy` with `Acquire::http::Proxy` and
     `Acquire::https::Proxy`.
   - Alternatively, on Windows 11, set `networkingMode=mirrored` and
     `autoProxy=true` under `[wsl2]` in `%UserProfile%\.wslconfig`, which
     makes WSL inherit the Windows proxy.
   - Either way, restart WSL with `wsl --shutdown`.
   - No proxy address or credentials go in this repository.
4. **Packages.**
   - On Ubuntu 24.04 or later:
     `sudo apt update && sudo apt install -y git gh python3.12 python3.12-venv python3-pip zip`
   - On 22.04, add `ppa:deadsnakes/ppa` first, or tell the technical lead
     the version.
5. **GitHub CLI inside WSL, preferably with a gist-only token.** The runner
   executes code from PR branches, and that code can read any file in the
   WSL home, including the `gh` login.
   - **The exposure, stated plainly.** Code on any branch of
     Thiesi/Karst41 runs here with no prompt. That code can read the `gh`
     token and the runner's Claude Code login in the WSL home. With the
     owner's full `gh` login, a malicious or broken branch could push,
     comment or merge as the owner, and read his private repositories.
     Two identities can push branches to Thiesi/Karst41: the owner's
     account, which every session works as, and the Claude GitHub App
     (`claude[bot]`) when someone writes `@claude` on an issue or PR. The
     runner refuses commits made by any `[bot]` account (section 3), so
     the gate is the sessions the owner runs. It is only as strong as that
     list: a session that took in untrusted text can still push as him.
   - **Windows is reachable too.** By default, WSL2 mounts the Windows
     drives at `/mnt/c` and lets Linux run Windows programs (interop). So
     branch code can also read the owner's Windows user folder, OneDrive
     included, and run `powershell.exe`. That reaches the Windows-side `gh`
     login, which is the owner's full login, and the Windows credential
     store. A gist-only WSL token alone does not close that.
   - On 2026-11-14 the owner accepted this exposure for now, Windows side
     included, and kept the default WSL settings. To close it later, put this in `/etc/wsl.conf`, then run
     `wsl --shutdown`:
     ```
     [interop]
     enabled=false
     appendWindowsPath=false
     [automount]
     enabled=false
     ```
     Then use the gist-only token below. Without interop, the runner can't
     call `powershell.exe`, so it can't see Windows load and the report's
     `host:` line says `unknown` (section 5). Remove
     `Bash(powershell.exe *)` from the allow list too.
   - Whatever is chosen, the runner's Claude Code login stays exposed.
   - The runner knows who sent a request only by session name. Only
     sessions signed in to the owner's account can reach it over Remote
     Control, so a name is a label, not authentication beyond that login.
   - Create a **fine-grained personal access token** with no repository
     access and only the account permission **Gists: read and write**. Both
     repositories are public, so cloning needs no token.
   - Log in with it: `gh auth login --with-token < token.txt`, then delete
     `token.txt`. If a browser login was already done, run `gh auth logout`
     first.
   - The worst a leaked token can then do is create gists.
   - The runner's Claude Code login also lives in the WSL home. Don't keep
     any other secrets there.
6. **Workspace,** in the Linux home directory, never under `/mnt/c`:
   ```sh
   mkdir -p ~/karst41-runner/runs && cd ~/karst41-runner
   git clone https://github.com/Thiesi/Karst41.git repo
   git clone https://github.com/Thiesi/NetBBS.git netbbs
   ```
7. **Claude Code inside WSL.** Install it with the native installer from the
   Claude Code documentation and log in. Start it in `~/karst41-runner` and
   name the session `Karst 41: runner`. Turn on Remote Control, so that the
   technical lead and the engineer can reach it from the development host.
8. **Permissions: auto mode, with the settings file protected.**
   The owner logs on to that workstation about once a day, so a permission
   prompt would stall a batch for hours.
   - Run the runner session in **auto mode**. It ran a full acceptance job
     (smoke-1) with no prompt, and it refuses risky actions instead of
     asking. A refusal is reported, and the run stops.
   - Don't use a refuse-everything-not-listed mode. The workflows' `run:`
     steps use command shapes no fixed list covers: venv interpreters
     called by path, `rm -rf -- "$RUNNER_TEMP/..."`, and `script -q -e -c`.
     So that mode stops real batches.
   - Create `~/karst41-runner/.claude/settings.json`:
     ```json
     {
       "permissions": {
         "allow": [
           "Bash(git *)", "Bash(python *)", "Bash(python3.12 *)",
           "Bash(nice *)", "Bash(gh gist create *)", "Bash(zip *)",
           "Bash(powershell.exe *)", "Bash(date *)", "Bash(mkdir *)",
           "Read(~/karst41-runner/**)"
         ],
         "deny": [
           "Edit(~/karst41-runner/.claude/**)",
           "Bash(rm -rf ~/karst41-runner)",
           "Bash(rm -rf ~/karst41-runner/repo*)",
           "Bash(rm -rf /home/<user>/karst41-runner)",
           "Bash(rm -rf /home/<user>/karst41-runner/repo*)",
           "Bash(rm -rf $HOME/karst41-runner)",
           "Bash(rm -rf $HOME/karst41-runner/repo*)"
         ]
       }
     }
     ```
   - The allow rules only spare the auto-mode check on routine commands.
     They are not a sandbox: `git *`, `python *` and `nice *` can't be
     scoped by path.
   - `<user>` is the WSL user name. The `rm -rf` deny rules only catch these
     exact spellings. The main protection is that `rm` isn't on the allow
     list, so auto mode checks every `rm`.
   - There is no allow rule for `rm`. Cleanup goes through
     `git worktree remove` and a short `python -I` call that deletes only
     a named `runs/<run-id>/` path after checking that it resolves inside
     `runs/`.
   - The deny rules keep the runner's own tools from rewriting this file.
     Claude Code checks file-path rules only as `Edit(...)`, which covers
     every file-writing tool, or as `Read(...)`. So there is no `Write(...)`
     rule.
     Code run from a branch could still rewrite it through the shell, so
     the owner checks the file when starting a new runner session.
   - The runner may propose an addition. Only the owner changes the file.
9. **First message.** Paste the brief from section 6 as the runner's first
   message, then tell the technical lead that the runner is up.

## 3. How a run works

1. **Request.** The technical lead or the engineer sends a request in the
   format of section 4. The ref is always a full 40-character SHA, never a
   branch name.
   - The runner first checks that the SHA is reachable from a branch of
     `Thiesi/Karst41` (`git fetch origin` then
     `git branch -r --contains <sha>`). It refuses any SHA that is only in a
     fork, a pull ref or nowhere.
   - It then lists the commits the SHA adds to main
     (`git log --format='%an %ae %cn %ce' origin/main..<sha>`) and refuses
     the SHA if any author or committer is a `[bot]` account, such as
     `claude[bot]`. It names the commit in the refusal.
   - Before starting, it deletes expired `runs/` entries (section 3,
     step 8), so the disk is pruned on every request.
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
   - `actions/setup-python` becomes a fresh `python3.12 -m venv` placed first
     on `PATH`, because Ubuntu's system Python refuses `pip install` (PEP
     668). Then the workflow's bare `python` is 3.12 with pip.
   - `actions/checkout` becomes local worktrees, NetBBS at
     `.test-tmp/netbbs`.
   - `upload-artifact` becomes keeping the files in `runs/<run-id>/`.
   - Every `run:` step runs as written, in auto mode. If a step is refused,
     the runner stops and reports which step and why.
5. **One at a time.** There is one job at a time and one batch at a time,
   first come first served. A new request waits in the queue.
6. **Full pytest** runs as the `test` job in `.github/workflows/ci.yml`
   does at that SHA: `pip install -e .[test]` in the run's venv, then CI's
   `pytest` command. There's no deselect: the deselect used on the Windows
   development host is not needed in Linux. The runner checks that
   `karst41.__file__` is in the run's worktree before trusting the result.
   Any failure, including `test_real_module_uses_native_stdio_pipes`, is
   reported as a failure.
7. **Report** in the format of section 5.
8. **Clean up.** After the report, the runner removes the worktree with
   `git worktree remove` and deletes the venv. It keeps `runs/<run-id>/`
   (captures and logs) for 7 days. Nothing runs on a timer: the runner
   deletes expired entries at the start of each request. Every deletion is a
   named path, checked to resolve inside `runs/` before it is removed. The
   runner never builds a deletion path from an unchecked variable.

### Confirming delivery both ways

Remote Control confirms delivery in neither direction. The runner
acknowledges every request with `ACK <run-id>`, and the requester answers
every runner message with `RECEIVED`. A message with no reply within 10
minutes is treated as not delivered and sent again once.

### A stuck runner

The requester (the technical lead or the engineer) treats the runner as
stuck in either case:
- no `ACK` within 10 minutes of a request;
- no `DONE` within the job's expected time plus 50%. Expected times come
  from the job's last CI duration, and the full suite counts as 30 minutes.

The requester then sends one status check, `STATUS <run-id>`. If that gets
no answer within 10 minutes, the technical lead tells the owner once, with
the run ID and the time of the last message. Until the owner has looked,
nobody sends more requests, and long runs fall back to the development
host under the usual slot arrangement. A refusal under the permission list
is not "stuck": the runner reports it and the run stops.

## 4. Request format

```
RUN <run-id>                      e.g. c2-accept-1
ref: <40-char SHA>
netbbs: v7.45.0                   optional; default = the tag the workflow pins
jobs:
  - s44-acceptance:s44-netbbs size=80x24 presentation=modern
  - s44-acceptance:s44-netbbs size=80x24 presentation=legacy
  - p5-acceptance:native          all matrix entries if none given
  - pytest-full
order: as listed                  or "timing first"
reply-to: Karst 41: technical lead
```

A job is `<workflow file without .yml>:<job ID>`, because job IDs repeat
across files. For example, `display-netbbs` is in both the issue67 and the
issue86 workflows. The one extra job is `pytest-full`.

## 5. Report format

```
DONE <run-id> at <SHA>
host: <idle | busy | unknown>     Windows CPU % before and after, via powershell.exe
- <job> <matrix>  PASS|FAIL  <start, local time>  <duration>
    on FAIL: the last frames of the traceback, and the failing line
    timing jobs: the measured p99 values and their limits
evidence: <secret gist URL>       on any failure: failure-trace.txt,
                                  progress.log, unfinished-*.txt and the
                                  results JSON
```

If the owner was using the machine during a timing job (`host: busy`), the
timing numbers are reported but marked as non-evidence. If the runner can't
read Windows CPU (interop off, or `powershell.exe` failed), it reports
`host: unknown`, which counts as `busy`: timing numbers are never evidence
then.

When a request asks for captures, for example new or re-pinned frames for
a packet, the runner also puts them in the gist, even on a pass: the
`.txt` frames and `result.json`, unchanged apart from the scrub.

Secret gists are unlisted, not private: anyone with the URL can read them.
Before uploading, the runner replaces the home path with `~` and includes
no environment dumps. If a file would carry anything else personal, it
leaves the file out and says so.

## 6. Brief for the runner's first message

> You are **Karst 41: runner**. You run long local checks for the Karst 41
> project (github.com/Thiesi/Karst41), on request, inside this WSL Ubuntu
> workspace (`~/karst41-runner`). Your instructions are
> `docs/ops/remote-runner.md` in the repository. Follow it exactly.
>
> - Accept requests only from the sessions "Karst 41: technical lead" and
>   "Karst 41: engineer", or from the owner directly. Acknowledge every
>   request with `ACK <run-id>` before starting.
> - Refuse a SHA that isn't reachable from a branch of Thiesi/Karst41, or
>   that adds any commit by a `[bot]` account (section 3).
> - Run only what the request names, at the SHA it names, exactly as the
>   workflow at that SHA runs it. Never edit repository files, never commit,
>   push, comment, review, label or merge. Your output is the report
>   message and a secret gist of the failure evidence, plus captures when
>   a request asks for them (section 5).
> - One job at a time, with `nice -n 10`. If a step can't run as written,
>   or a command is refused (auto mode or a deny rule), stop and report it.
>   Don't work around it, and never edit `.claude/settings.json`. Delete
>   only named paths that you've checked resolve inside `runs/`.
> - Answer `STATUS <run-id>` at once with the job running and how long it
>   has been running.
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
