# Contributing

Read this before your first push. It is short.

## What this repo is

Code in this repo runs on a real machine — a headless Ubuntu VM called `buildvm`
living on a laptop. **Anything merged into `main` is running in production
within 60 seconds.** There is no staging environment. Treat `main` accordingly.

## The rules

1. **Never push to `main`.** It is protected — the push will be rejected.
2. Work on a branch, open a pull request, get it reviewed and merged.
3. **This repository is public.** No secrets, no API keys, no tokens, no
   passwords, no customer data — not in code, not in commit messages, not in
   test fixtures. A secret pushed here is a secret leaked, even if you delete it
   in the next commit.
4. Do not force-push shared branches. Do not rewrite history on `main`.
5. Changes under `/.github/` need owner review. Those files control what
   executes on the VM.

## Workflow

    git checkout main
    git pull
    git checkout -b your-feature

    # ... make your change, commit ...

    git push -u origin your-feature

Then open a pull request against `main`. One approval is required, and it has to
come from someone other than you — GitHub does not let you approve your own PR.

When your PR is merged, the VM notices within 60 seconds, pulls the new `main`,
runs the build step, and restarts the service. You do not need to deploy
anything or have access to the VM.

## How the code is expected to be laid out

The VM looks for two optional files at the repo root.

| file | what it does |
|---|---|
| `run.sh` | The long-running process. This is what the service actually executes. It must stay in the foreground — do not background it or the service will be treated as dead and restarted forever. |
| `deploy.sh` | Optional build step. Runs after every pull, before the restart. Put dependency installs, compilation, and migrations here. |

Both run with the repo root as the working directory. `run.sh` currently starts
a Python HTTP server on port 8080 serving `public/`.

If `deploy.sh` exits non-zero, the VM rolls the checkout back to the previous
commit and does not restart the service — a broken build cannot take the running
service down. The VM retries on the next cycle, so it recovers on its own once
you push a fix. But it also means **a merged PR is not proof the deploy worked.**
Check after merging.

## Test before you open the PR

Run it the same way the VM will:

    ./deploy.sh && ./run.sh

If that works on your machine, it will almost certainly work on the VM. The VM
runs Ubuntu 24.04 with Python 3.12 and a deliberately minimal package set — if
your code needs something installed, install it in `deploy.sh` rather than
assuming it is there.

## When something breaks after a merge

You do not have VM access; ask the owner to check. What they will look at:

    deploy-status                     # what commit each app is on, service state
    journalctl -u app@race-os -n 50   # the app's own logs
    journalctl -u deploy-sync -n 50   # the pull/deploy loop

The most common causes, in order: `run.sh` exited instead of staying in the
foreground, a dependency that exists locally but was never added to `deploy.sh`,
or a hardcoded path that only exists on your machine.

## Commit messages

One line, present tense, says what changed and why it matters.
`Fix timeout on slow uploads` — not `fixes` or `update stuff`.
