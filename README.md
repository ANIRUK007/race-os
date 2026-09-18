# race-os

Systems that run on `buildvm` — a headless Ubuntu 24.04 VM on the TUF laptop.

## How code gets here

Write code anywhere. Push to a branch. Open a PR. Once it is merged into
`main`, the VM pulls it within 60 seconds and restarts the service.

Nobody pushes to `main` directly. The VM clones `--single-branch main`, so no
other branch exists on the VM and nothing but `main` can ever deploy.

## Conventions

| file | meaning |
|---|---|
| `run.sh` | the long-running process. Its presence creates a systemd service `app@race-os`, `Restart=always`. |
| `deploy.sh` | optional build step, run after every pull, before the restart. |

## Checking it on the VM

    ssh buildvm
    deploy-status                     # commit + service state per app
    journalctl -u app@race-os -f      # this app's logs
    journalctl -u deploy-sync -f      # the sync loop
    deploy-sync                       # force a pull instead of waiting
