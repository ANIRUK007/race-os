# Race OS — Research Findings

Synthesis of eight parallel research streams, September 2026. Where sources
disagree or evidence is thin, that is stated rather than smoothed over.

**Read this first if you read nothing else:**

1. **Move the base from Ubuntu 24.04 to 26.04 LTS.** 24.04 blocks three separate
   layers at once. Highest-leverage decision in the project.
2. **Four items in the spec are technically wrong** and should be rewritten before
   submission.
3. **There is a genuine, unfilled novelty gap** — image-based Ubuntu — achievable
   by a student team.
4. **Build vertically, not module by module.** The module list is a good report
   structure and a terrible build order.

---

## 1. Corrections to the spec

Stated bluntly because a faculty examiner will find them.

### 1.1 "RAID 10 data integrity for important files" — incoherent

RAID 10 requires **at least four physical block devices**. A laptop has one SSD.
RAID protects against *device failure*, not what the spec describes (bit rot,
accidental deletion). **RAID is not backup.**

| Goal | Technology | Limit |
|---|---|---|
| Detect corruption | Btrfs checksums (always on) | detection only without redundancy |
| Repair corruption | Btrfs `dup` profile | 2x space, 2x write wear |
| Undo mistakes | Btrfs snapshots + snapper | same device only |
| Survive device loss | `restic` / `borg` to external disk | **the only real backup** |
| Verify periodically | `btrfs scrub` monthly | needs redundancy to repair |

**Non-obvious:** Btrfs profiles are **per-filesystem, not per-subvolume**. "dup only
for important files" therefore needs a *separate Btrfs filesystem* mounted at e.g.
`~/Vault` — not a subvolume flag. Plan partitioning accordingly.

**Tension:** deduplication is *antagonistic* to this. Reflinked extents mean one
corrupt block damages N files, and dedup undoes the duplicate copies you paid for.
Do not ship both on the same data.

### 1.2 "New Language for compiler" — cut it

"Adopting GCC/LLVM for internal computation" is not a meaningful sentence; a desktop
OS does not "use LLVM internally" for anything a user sees.

Scale check: Rust took ~3 years to 1.0 with a funded team. **Zig is 10 years old and
still not 1.0**, with a paid foundation. A toy LLVM frontend is a ~1-month solo
project and adds *zero* to a productivity OS.

**Defensible version:** a small **declarative configuration DSL** for Race OS
environments — what Nix flakes, apx stacks and BlueBuild recipes do. ~500 lines of
parser, serves the product thesis. Ship that, call it a DSL, drop "new programming
language and compiler".

### 1.3 "Lightweight" contradicts the rest of the spec

Local LLM runtime, containerised GUI apps, filesystem vector indexing and threaded
AI agents are each memory-hungry. Pick one claim: lightweight **or** AI-native.

### 1.4 Module 1's base options are the wrong family

Buildroot, Yocto and Alpine are **embedded** build systems. Buildroot produces a
monolithic rootfs with no on-target package manager by default; Yocto's first clean
build is 2-4 hours. Keep the Ubuntu base.

"Custom minimal kernel configuration" is a trap — weeks of work, nothing on screen,
and it **breaks Secure Boot** (see 3.4).

### 1.5 Housekeeping

The spec's "Similar Projects" field links to your own `CONTRIBUTING.md`. Replace
with the prior-art table in section 9.

---

## 2. The base decision: Ubuntu 24.04 to 26.04 LTS

Three independent research streams converged on the same blocker.

| Component | 24.04 ships | Needed | Consequence |
|---|---|---|---|
| libadwaita | **1.5.0** | 1.6+ for CSS variables, accent API | Designer cannot recolour GTK apps the modern way |
| Qt | **6.4.2** | >=6.6 for Quickshell | Permanent Qt backport maintenance |
| Podman | **4.9** | 5.x for `.build`/`.pod`/`.image` Quadlet units | Backport or vendor |
| GTK | 4.14 | >=4.15.2 for current Yaru GTK4 theme | Explicitly incompatible |

Any one is survivable. All four together means spending the project backporting
instead of building. **Move to 26.04 LTS.**

Bonus: Canonical announced post-26.04 AI features including local inference by
default and agents for automated file management — relevant to Module 5 both as
prior art and as inherited infrastructure.

---

## 3. Image build pipeline

### 3.1 Tool choice: Debian live-build

**This corrects an earlier recommendation of mkosi. mkosi has no ISO9660 output
format** — it is a systemd-boot/UKI/GPT-image tool. ISO output is an open request
(systemd/mkosi#1220). It cannot produce the artifact you need.

| Tool | Native on Ubuntu? | Verdict |
|---|---|---|
| **Debian live-build** | Yes, `apt install live-build` | **Use this** |
| Ubuntu livecd-rootfs | Yes, but designed for Launchpad builders | Only to match Ubuntu byte layout |
| mkosi | Runs, but **cannot emit an ISO** | Rejected |
| archiso | No — needs Arch packages in a container | Wrong family |

What derivatives actually do: Ubuntu uses livecd-rootfs (which drives live-build).
**Pop!_OS does not use live-build** — custom GNU Make system with germinate seed
resolution; their docs quote a ~34 GiB build tree. Mint is *reported* to use
live-build but this is community-sourced. Zorin's build system is not public.

### 3.2 Minimal working config

```bash
mkdir raceos && cd raceos
lb config \
  --distribution noble \
  --mode ubuntu \
  --archive-areas "main restricted universe multiverse" \
  --binary-images iso-hybrid \
  --bootappend-live "boot=casper quiet splash console=ttyS0,115200"
echo "ubuntu-desktop-minimal firefox" >> config/package-lists/my.list.chroot
sudo lb build 2>&1 | tee build.log
```

Customisation in `config/hooks/normal/*.hook.chroot`; file overlays in
`config/includes.chroot/`.

### 3.3 Headless testing

```bash
qemu-system-x86_64 -enable-kvm -m 3072 -smp 2 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=OVMF_VARS.fd \
  -cdrom live-image-amd64.hybrid.iso -boot d \
  -display none -serial file:serial.log \
  -qmp unix:/tmp/qmp.sock,server,nowait
```

Smoke test: boot with `console=ttyS0,115200`, have a systemd unit echo
`RACEOS_BOOT_OK` to serial, grep `serial.log` under a hard timeout. Screenshots via
QMP `screendump`.

**Cost on the build VM:** first build 25-50 min, incremental 5-15 min, **30-40 GB
per build tree**. Use `--compression zstd`, not xz. Do not build in tmpfs.

**Nested KVM caveat:** the build VM runs inside VirtualBox under Hyper-V. If
`/dev/kvm` is absent, drop `-enable-kvm` and expect 5-10x slower boots.

### 3.4 Secure Boot — do not break it

Ship Ubuntu's `shim-signed` + `grub-efi-amd64-signed` + a **stock Canonical-signed
kernel** and Secure Boot works with no Microsoft relationship. Rebranding GRUB's
config and theme is fine.

**What breaks it:** a self-compiled kernel, a rebuilt GRUB binary, or DKMS /
out-of-tree modules. Users then face a MOK enrolment blue screen at first boot,
which will torpedo the install experience.

The **Microsoft UEFI CA 2011 expired 27 June 2026**. New shims use the 2023 CA, and
older firmware without that cert will not trust them — another reason to consume
`shim-signed` as-is and keep it current.

### 3.5 Reproducibility

Pin to **snapshot.ubuntu.com** (apt supports snapshots natively in 24.04+). Commit
the timestamp string and builds months apart resolve identical package versions. Add
`SOURCE_DATE_EPOCH` and `mksquashfs -all-time`.

**Do not overclaim:** this gives an identical *package set*, not a bit-identical
ISO. Full ISO reproducibility is not solved in live-build.

---

## 4. Desktop shell and GUI

### 4.1 Compositor — take one, never write one

COSMIC took **~4 years with a funded full-time team**. Writing a compositor is off
the table.

| | Control API | Risk |
|---|---|---|
| **niri** | JSON IPC + events; not semver-stable but additive | Pre-1.0, opinionated scrollable tiling |
| **Hyprland** | Good socket IPC; **plugin ABI hostile** — ABI hash from git commit, so a distro rebuild at the same version breaks plugins | Highest churn |
| **sway** | i3 IPC, most stable here | Feature-frozen; limits a branded look |
| **labwc** | **No IPC by design** | Blocks shell/WM integration |

**Pick niri.** Clean IPC, no plugin ABI to break, shipped by Fedora, CachyOS, KaOS.
Never depend on Hyprland *plugins*.

### 4.2 Shell framework — the open decision

| | Language | Hot reload | React/CSS ramp |
|---|---|---|---|
| **Quickshell** | QML + JS | Yes, file-watch | Medium — 1-2 weeks useful, 6-10 weeks fluent |
| **AGS / Astal** | TypeScript + JSX + GTK CSS | Yes | **Lowest — literally JSX and CSS** |
| eww | `yuck` S-expressions + SCSS | Yes | Odd language, tiny |
| GNOME Shell | GJS + CSS | No (restart) | Easy language, brutal platform churn |

Both leading options carry real churn risk:

- **Quickshell**: maintainer states the API "is still very much not stable". Uses
  **private Qt APIs**; must be rebuilt against every Qt point release or it
  ABI-crashes.
- **AGS/Astal**: two full rewrites (v1→v2 moved to Astal; v2→v3 replaced `astalify`
  with JSX and `Variable` with `Accessor`). Historically *more* disruptive.
- **Noctalia v5** — a flagship Quickshell shell — did a ground-up C++/OpenGL ES
  rewrite dropping Qt and Quickshell entirely, citing ~300 MB RAM per monitor and Qt
  packaging pain.

Counterweight: **Omarchy 4.0 (Aug 2026) rewrote its shell onto Quickshell**, and
DankMaterialShell ships in CachyOS. The ecosystem is consolidating on Quickshell
despite the instability.

**Recommendation is conditional on one unanswered fact — what the frontend developer
actually works in:**

- **React/TypeScript developer** → start with **AGS/Astal**. JSX + GTK CSS is a
  near-zero ramp; on a part-time timeline the 6-10 week QML ramp is a material risk.
- **Any native-UI background, or the team can absorb the ramp** → **Quickshell** is
  the better long-term bet: ecosystem momentum, and one language covers bar,
  launcher, notifications, lock screen and settings.

Non-optional mitigations either way: pin framework and toolkit versions in the image;
keep a **CSS-like token/theme layer** so restyling never means rewriting component
code; wrap compositor IPC behind one module so a schema change is a one-file fix.

### 4.3 Web-based shell — closed

**Not viable.** Tauri and Electron have **no `wlr-layer-shell` support**, so neither
can draw a bar or lock screen. A lock screen in a webview is a security non-starter —
`ext-session-lock` requires a crash-proof client.

Decisive proof point: **LG webOS**, the most successful web-first OS ever shipped,
writes its *shell* in **QML on a Qt Wayland compositor**. Only *apps* are web. Nobody
has shipped a webview shell.

### 4.4 Development workflow

No dedicated machine needed. Run the target compositor **nested in a window** on a
normal desktop session — niri's own docs call this the primary dev method — with
shell hot-reload on save. TTY only for real-hardware checks.

**The rule that decides whether a part-time project survives: shell changes must be
visible in under 60 seconds.** If seeing a CSS tweak requires an ISO rebuild, the
project stalls. Three tiers: nested compositor (seconds), cached QEMU rootfs (~1
min), full ISO rebuild (nightly / on merge only).

---

## 5. Theming and the design-asset pipeline

### 5.1 libadwaita — the constraint that shapes the design plan

**GNOME's official position is that custom themes are unsupported.** libadwaita sets
`gtk-theme-name` itself, so `~/.themes/.../gtk-4.0/gtk.css` does *nothing*.
Maintainers describe third-party theming as "an unsupported hack".

**Possible (libadwaita 1.6+, i.e. requires the 26.04 move):**
- Whole palette exposed as **CSS variables** — `--accent-bg-color`,
  `--window-bg-color`, `--card-bg-color`, `--window-radius`, `--dim-opacity`.
- **System accent colour** is a first-class setting (GNOME 47+).
- `~/.config/gtk-4.0/gtk.css` is still loaded at user priority — overriding those
  variables genuinely recolours everything. Breaks under Flatpak sandboxing.
- **The distro-grade route, and what Ubuntu actually does:** recompile libadwaita's
  SCSS with a swapped palette and patch libadwaita downstream. Yaru symlinks ~95% of
  the theme to upstream SCSS and changes only the palette.

**Not possible:** changing widget *structure*, spacing, per-widget border radii, or
control shapes. No drop-in "GTK4 theme" the way GTK3 allowed. The stylesheet has been
frozen-as-API since 1.0.

**Consequence:** "the designer designs every widget" holds for **your own shell** and
**your icon set**. For third-party GTK apps it reduces to palette, fonts, icons and
cursors. Tell the designer now.

### 5.2 Qt is far more permissive

Qt styles are plugins, and **Kvantum** is an SVG-driven style engine — a designer
edits SVG plus a `.kvconfig` and gets real control over widget *shapes*.
`QGnomePlatform`/`adwaita-qt` are discontinued; `QAdwaitaDecorations` is current.

**Mixed GTK+Qt coherence: approximately, not exactly.** You can match palette, fonts,
icons, cursors. You cannot match widget geometry, because libadwaita will not move
and Kvantum must chase it. Plan for "same family", not "pixel identical".

### 5.3 Icon theme — a real, well-defined deliverable

```ini
[Icon Theme]
Name=Race OS
Comment=Race OS icon theme
Inherits=Adwaita,hicolor
Directories=scalable/apps,symbolic/apps

[scalable/apps]
Size=48
Type=Scalable
MinSize=16
MaxSize=512
Context=Applications

[symbolic/apps]
Size=16
Type=Scalable
MinSize=8
MaxSize=512
Context=Applications
```

Install to `/usr/share/icons/RaceOS/`, run `gtk-update-icon-cache -f -t`. Symbolic
icons are 16px single-fill SVGs named `foo-symbolic.svg`; the toolkit recolours them.
`hicolor` is the mandatory fallback.

No polished Figma-to-theme pipeline exists. Real projects use Inkscape SVGs + a
Makefile + `svgo`. **Papirus is the best structural model**; **Pop!_OS is the best
repo model** — separate `gtk-theme`, `icon-theme`, `fonts` repos tied by a `theme`
metapackage.

### 5.4 Terminals — fully themeable, zero code

Colours, fonts, padding, cursor style are plain config in all of them. `foot` has a
real system default at `/etc/xdg/foot/foot.ini`; Alacritty reads
`/etc/alacritty/alacritty.toml`; kitty has no `/etc` path, so ship via `/etc/skel/`.
**Pick one default terminal.**

You do not "design a terminal". Writing a terminal emulator — pseudo-terminals,
escape sequences, font shaping — is a project on its own.

### 5.5 Proposed `gui/` layout

```
gui/
  colors/palette.json     # single source of truth
  icons/                  # SVG icon theme
  cursors/                # SVG -> XCursor via kcursorgen/clickgen
  fonts/
  wallpapers/
  terminal/
  shell/                  # QML or TSX, per 4.2
```

One build script generates `index.theme`, the libadwaita CSS-variable override, the
Kvantum config and the terminal themes **from `palette.json`**. The designer touches
SVG, PNG and JSON only.

---

## 6. System services — reuse almost everything

| Need | Use | Build? |
|---|---|---|
| Login/greeter | **greetd + ReGreet** (JSON-over-socket IPC) | theme only |
| Network | NetworkManager | no |
| Audio | PipeWire + WirePlumber | no — a session manager is a multi-month sinkhole |
| Bluetooth | BlueZ + libgnome-bluetooth | pairing agent only |
| Power | UPower + logind + power-profiles-daemon | no |
| Screenshot/record | xdg-desktop-portal + grim/slurp/wf-recorder | no |
| Notifications | mako / swaync | no |
| File manager | Nemo or Thunar (cheapest to rebrand) | **never build** |
| Polkit agent | mate-polkit / lxqt-policykit | **never build — security path** |
| Brightness/volume OSD | — | **build, small** |
| Settings panel | gnome-control-center is not detachable | **build, 6 pages max** |

**Commonly forgotten and non-optional:** xdg-desktop-portal-gtk (file chooser), gvfs
+ udisks2 (mounts/trash), accountsservice, gnome-keyring, XWayland, CUPS,
ibus/fcitx5, Orca.

**Installer:** Calamares is what small distros actually ship (EndeavourOS, Garuda,
Nobara, Manjaro). **1-2 weeks to a demo; 1-2 person-months to production.** Time goes
to Ubuntu-specific glue: casper live session, partitioning/LUKS/LVM, ubuntu-drivers,
OEM mode. **Traps: Secure Boot and NVIDIA.**

**Packaging:** apt for the base, **Flatpak/Flathub for third-party apps** — what
Mint, Zorin, Pop!_OS and Tuxedo all do. GNOME Software dropped Snap in 2019.
Firefox/Thunderbird are snap-only on Ubuntu; use Mozilla's official apt repo.

**Updates:** ship **only your own packages** in your own repo; leave
`archive.ubuntu.com` and `security.ubuntu.com` enabled so CVE fixes arrive free.
**Launchpad PPA is free and signs for you.** Self-hosted aptly is ~$10-20/mo idle.
ISO bandwidth, not apt, is the launch-day bill.

---

## 7. Containers

**Podman, not Docker.** Podman is daemonless, so a container started by a systemd
unit *is* a child of systemd with real supervision, journald logging and cgroup
accounting. **Quadlet** (declarative `.container` files to systemd generator) is the
decisive feature and is exactly the primitive "containerise any process" needs.

**Wayland GUI in containers:** bind-mount `$XDG_RUNTIME_DIR/wayland-0`, run with
`--userns=keep-id`. Performance is native.

**Do not use Waypipe for local containers** — it is a *remote* forwarding proxy that
serialises and re-encodes buffers, costing latency and breaking the zero-copy GPU
path. (The spec lists it; this is a correction.)

**Security caveat:** handing over the Wayland socket is **not** a security boundary.
Sandboxing needs xdg-desktop-portal over a filtered D-Bus — in practice the Flatpak
model.

**Distrobox over Toolbx** — runs any image, and `distrobox assemble` takes a
declarative manifest, which *is* the "reproducible dev environment" feature nearly
for free. `distrobox-export` publishes `.desktop` entries to the host.

**Be honest in the report:** Distrobox is **deliberately not isolated** — it mounts
`$HOME`, the session bus, `/dev`. It solves *reproducibility*, not *containment*. You
cannot sell one tool as both.

**Gap requiring new code:** idle detection for GUI workloads does not exist. Budget a
small custom timer.

**Realistic split: ~80% integration, ~20% new code.** The genuinely new work is the
policy/UI layer — "containerise this app" to Quadlet file, mounts, desktop entry,
idle expiry. A few hundred lines plus a GUI. Defensible major project. Claiming novel
container *technology* is not.

---

## 8. AI agentic layer

### 8.1 Prior art — state novelty honestly

**Already shipped:**
- **Omarchy 4 "Quattro"** (Aug 2026, ~$18.7M raised) — Arch + Hyprland + Quickshell,
  ~10 coding-agent CLIs as system citizens. **Your novelty claim must be stated
  explicitly against Omarchy.**
- **`agent-sh/computer-use-linux`** — Rust MCP server, Wayland-first, AT-SPI tree +
  portals + ydotool + D-Bus. Exposes `screenshot`, `click`, `type_text`,
  `activate_window`, `list_apps`, gated `run_shell`. MIT. **This is essentially "AI
  overlay for OS-level tasks", already built.**
- **Ubuntu itself** — post-26.04, local inference by default and agents for automated
  file management.

**Not shipped:** KDE's "AI-native" plan is a talk with community opposition; GNOME has
no policy and is pushing to *ban* LLM-generated code from infrastructure.

**Defensible novelty = integration + a session-wide permission/consent model**, not
the agent itself.

### 8.2 MCP — use it, do not invent a protocol

Spec **2026-07-28** is final. Use the **stdio** transport for a local OS agent.
Governed by the Linux Foundation's Agentic AI Foundation.

Writing a new protocol costs the entire client ecosystem and buys nothing. Real
criticism exists — MCP tool sets cost 32k-82k tokens versus ~200 for a CLI call, so
**CLI-as-tools is a legitimate cheaper path on small local models**.

### 8.3 Local models — blunt

On 8-16 GB RAM with integrated graphics:

| Model class | RAM | Speed | Verdict |
|---|---|---|---|
| 3B Q4 | ~2 GB | 10-15 tok/s | routing/classification only |
| 7-9B Q4_K_M | 5-6.6 GB | 4-8 tok/s | leaves nothing for the desktop on 8 GB |
| 12-14B | spills | 4-11 tok/s | not viable |

Worse than speed is **tool-calling reliability**: BFCL V4 shows a cliff — ~66% correct
at 9B, ~50% at 4B, ~44% at 2B. **A 50%-correct tool call on a filesystem agent is a
data-loss generator.**

**Architect API-first**, with local models as (a) offline degraded mode, (b) intent
routing, (c) embeddings. Ship the fallback as the primary path and *say so in the
report* — examiners respect that more than a demo taking 40 s per action.

### 8.4 Retrieval — do not embed the filesystem

Ubuntu/GNOME already ships **LocalSearch** (ex-tracker-miners) with a D-Bus SPARQL
endpoint and content extractors. Wrap it as an MCP tool; add `plocate` for paths and
`ripgrep` for on-demand grep. **That is v1, near-zero cost.**

v2 only if needed: **sqlite-vec** (single file, in-process) over *documents only* with
EmbeddingGemma (308M, ~200 MB quantised). Full-filesystem embedding on this hardware
is hours of pegged CPU for marginal gain over BM25.

### 8.5 Habit memory

**Zeitgeist is dead** — last stable 1.0.3, October 2020. Do not build on it. KDE
Activities is KDE-only. **Build your own SQLite table** (timestamp, app, window title,
file, action) fed from D-Bus signals + LocalSearch. ~100 lines.

### 8.6 Security — the part that must not be hand-waved

**The session bus is a flat trust domain.** Anything on it can reach
`org.freedesktop.secrets` (keyring), `org.freedesktop.Flatpak` (a documented sandbox
escape) and systemd user units. **An agent with unrestricted session-bus access is
equivalent to full user-account compromise.** Run the MCP server inside
bubblewrap/Flatpak with an explicit bus allow-list (`--talk-name=`); never
`--socket=session-bus`.

**Indirect prompt injection** arrives via file contents, filenames, notification text,
window titles and OCR'd screen regions. OWASP LLM Top 10 **2026** still ranks prompt
injection first. 2026 research is blunt that surface defences fail. The MCP ecosystem
itself is poor: **43% of tested implementations had command injection**, 30% SSRF, 22%
arbitrary file access.

Build in from day one:
- deny-by-default tool scoping
- read-only vs destructive tool annotations, **human approval on every
  write/delete/exec**
- **no network-egress tool in the same agent that reads untrusted files** — breaks the
  exfiltration path
- shell tool off unless explicitly env-gated
- treat all file and screen content as *data, never instructions*
- append-only audit log of every tool call

---

## 9. Where the genuine novelty is

**Ubuntu has no BlueBuild.** Universal Blue's model — declarative recipe to GitHub
Actions to signed OCI image to `bootc` rebase, with trivial rollback and zero drift —
exists for Fedora and **essentially nothing equivalent exists for Ubuntu**.
Canonical's own answer (Ubuntu Core Desktop) is explicitly a **5-10 year** effort and
is not default in 26.04 or 28.04.

*"A reproducible, image-based Ubuntu desktop for research and design workstations,
rebuilt nightly in CI"* is a real, unfilled gap, achievable by a student team because
the heavy lifting is **podman + a Containerfile + GitHub Actions** — infrastructure
you already run.

Second candidate: a **per-project environment manifest** provisioning the whole
workstation from one file — Nix dev shell + Flatpak GUI apps + Distrobox containers +
dotfiles. apx does containers only; Nix does terminals only; nobody covers the
designer-*and*-programmer case.

**Nothing new in:** another GTK theme, another app-store reskin, another language.

Note `apx` (Vanilla OS 2) already wraps apt/dnf/pacman/apk inside Distrobox
"subsystems". If you build a unified installer you are re-implementing apx — **wrap it
or contribute to it, don't rewrite it.**

### Team-size calibration

| Project | Team | Time |
|---|---|---|
| elementary OS | **1 full-time employee** | ~10 years |
| Zorin OS | 8 employees | ~10 years |
| COSMIC | funded full-time | ~4 years |
| SnowflakeOS | ~1 person | useful GUI tools, alpha distro |

---

## 10. Build sequence — vertical, not horizontal

The eight modules are a good **report taxonomy** and a terrible **build order**.
Module-by-module means nothing runs until everything is done.

### The "micrograd equivalent"

A scripted ISO build that **boots to a black screen with one terminal window.** Under
~300 lines, no designer, no branding. It proves debootstrap to package set to
initramfs to session to DRM/KMS to Wayland to a client drawing pixels. Every later
failure then localises, because that chain passed once.

### Staged sequence — every stage boots

| Stage | Ships | Verified by |
|---|---|---|
| 0 | Reproducible ISO of stock Ubuntu minimal | Boots to TTY in QEMU |
| 1 | + stock desktop | Boots to a desktop — **this is your baseline** |
| 2 | + compositor + terminal | One window on real hardware |
| 3 | + xdg-shell, layer-shell, output/input config | Firefox, keyboard, trackpad work |
| 4 | Shell surfaces as layer-shell clients | **Designer's first real target** |
| 5 | Lock, logout, sleep/resume, monitor hotplug | Manual checklist on one laptop |
| 6 | Calamares + update path | Install to a spare SSD, reboot, still works |

Keep the shell UI in a **separate process** talking layer-shell to the compositor.
That is the one abstraction worth having early — the designer iterates without
rebuilding an ISO.

### Strong baseline

Stock Ubuntu + GNOME on your reference laptop, measured on **boot-to-usable time,
idle RAM, battery drain, frame pacing under video + scroll, and suspend/resume
survival.** A custom shell that loses on any of those has no case yet.

This is also a defensible *evaluation methodology* for the report — most student OS
projects have none.

### Do not build early

Your own package manager, display protocol extensions, a theming engine, a settings
daemon API, a plugin system, multi-distro support, an app store, custom kernel
patches, your own installer, or a "design system" abstraction before three real
surfaces exist.

### On AI assistance

Packaging boilerplate, systemd units, build scripts and CI are high-frequency
patterns where agents are strong. Compositor internals and protocol handling are
exactly the "code that has never been written before" case — review line by line.

---

## 11. Effort and the cut list

| Path | Cost |
|---|---|
| Reuse everything, reskin an existing DE (Zorin-style) | **2-4 person-months** |
| Custom shell on an existing compositor | **6-12 person-months to alpha**, 18+ before non-technical users |
| Own compositor | ~4 years funded — **off the table** |

At 3 people x ~10 h/week (~1.5 FTE-months per calendar month): **4-6 calendar months
to a first usable ISO** on the reuse path; roughly double with a custom shell.

**Must demo:** the vertical slice, a coherent visual identity, 4-6 real MCP tools.

**Integrate, do not build:** file manager, browser, PDF viewer, calculator, music
player, system monitor. **Module 4 is almost entirely "ship an existing app and theme
it."**

**Write up as future work:** new language, RAID, Library of Alexandria, council of
agents, n8n workflows, accessibility. Documented ambition costs nothing.

**Time sinks to refuse early:** the settings panel, Secure Boot + NVIDIA, installer
partitioning and dual-boot edge cases, fractional scaling + multi-monitor + HDR,
portal/Flatpak plumbing, input methods and accessibility, and the per-point-release
QA treadmill.

---

## 12. Open decisions

1. **Ubuntu 26.04 base** — confirm the move. Everything above assumes it.
2. **Shell framework** — blocked on what the frontend developer actually works in
   (4.2). Single highest-risk unknown.
3. **Is the AI layer the thesis, or the GUI?** If AI, the shell is a thin surface over
   the tool schema. If GUI, the AI is one panel. Attempting both equally is how
   projects die.
4. **Deadline and headcount** — sets the cut line in section 11.

---

## Caveats on this research

- Mint's use of live-build is community-sourced; Mint publishes no official build
  docs. Zorin's build system is not public.
- First-person React-to-QML accounts are scarce; most writing is vendor marketing. The
  1-2 week / 6-10 week estimate is inferred, not measured.
- Some 2026 model names and tok/s figures in secondary blogs could not be corroborated
  against primary sources — verify on ollama.com before citing in the dissertation.
- Karpathy's 2026 "agentic engineering" quotes come from write-ups of his Sequoia
  talk, not a primary transcript.
