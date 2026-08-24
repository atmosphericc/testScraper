# fingerprint-chromium — engine-level per-account device identity (FREE)

**Status:** shipped as code, **default OFF**, validated by unit + construction tests.
Not yet live-validated (needs the user to run the collision test — it launches browsers).

## Why
Shape's **Device ID+** is a hardware-anchored bot score. Our JS spoof
(`account_identity`) leaves the heaviest axes **real and shared** across all 3
Target accounts on this one machine — WebGL untouched, canvas `toDataURL` unhooked,
audio never implemented — so all 3 identities collide onto **one device score**.
That is the ATC 401/429 wall that IP rotation and account swaps could not move
(see `docs/RETAILERS/target.md` "Device ID+" and the 2026-08-11 collision analysis).

[fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium) (an
Ungoogled-Chromium build) spoofs canvas/WebGL/audio/fonts at the **engine level**
via a per-launch `--fingerprint` seed — below JS, so `toString()` still shows
`[native code]` (no detectable hook) and every readback path is coherent. A distinct
seed per account = a distinct, self-consistent device from **one** desktop. Free,
permanent, no concurrency cap.

## The binary
- Windows build extracted to:
  `C:\Users\elric\fp-chromium\win\ungoogled-chromium_148.0.7778.215-1.1_windows_x64\chrome.exe`
- The code auto-discovers it under `~/fp-chromium/win`. Override with
  `TARGET_FP_CHROMIUM_PATH=<path to chrome.exe>` if you move it.

## The switches (all OFF by default)
| Env var | Meaning |
|---------|---------|
| `TARGET_FP_CHROMIUM=1` | Master switch. The **PURCHASE** browsers launch from fingerprint-chromium (per-account `-fp` profile + seed). Login is unaffected. |
| `TARGET_FP_CHROMIUM_LOGIN=1` | Opt-in (needs the master too) to ALSO run the **login** tools under fingerprint-chromium. OFF by default — see the warning below. |
| `TARGET_FP_CHROMIUM_PATH=<chrome.exe>` | Optional. Explicit binary path; else `~/fp-chromium/win` is probed. |
| `TARGET_FP_CHROMIUM_SKIP=business` | (2026-08-21) csv of account ids that run **real Chrome on their real profile** (the pre-08-11 winning configuration) while the master switch keeps the others on fp-chromium — the A/B control arm. Empty = all on fp. |
| `TARGET_FP_SEED_SALT=<str>` | (2026-08-21) mixed into the seed → a different, still-deterministic engine-level device per account for that salt (set it to the run date for a fresh device nightly). Empty = the static 08-11 seeds, bit-identical. NOTE: the `-fp` profile + imported jar still carry persistent ids (visitorId, `3YCzT93n`, PX/TMX), so a salt alone is a *partial* new device. |
| `TARGET_FP_CHROMIUM_ACCOUNTS=a,b` | Optional allowlist (SKIP wins over it). |

When `TARGET_FP_CHROMIUM` is unset/0 **nothing changes** — same system Chrome, same
profiles, same JS spoof. This is the rollback contract.

> **Why login is decoupled (2026-08-11):** fingerprint-chromium on the LOGIN surface
> drew Target's Shape "Something went wrong" block — login is the most aggressively
> inspected page, and it must run on the HOME IP (`RELOGIN_SKIP_PROXY=1`), never a BD
> IP. Login-device coherence is also not proven necessary: the session cookie is
> portable, and the ATC device score (what actually walls you) is fixed by the
> PURCHASE device. So log in the proven way (real Chrome, home IP) and let
> fingerprint-chromium do the purchase. Flip `TARGET_FP_CHROMIUM_LOGIN=1` only to
> experiment with fully-coherent login later — and test it first.

## Step 1 — FREE validation (no login, no Target, no risk)
Proves the 3 accounts present **distinct** WebGL/canvas/audio under fp-chromium.
Uses fresh throwaway profiles, so your logged-in profiles are never touched. Launches
3 browser windows (one per account, sequentially) — **user-run**.

```
venv\Scripts\python.exe check_fingerprint_collision.py --fp-chromium
```
- Auto-finds the binary in `~/fp-chromium/win` (no env needed).
- **Want:** verdict `SUCCESS — fingerprint-chromium gives each account a DISTINCT device`.
- Compare against today's shared-device baseline: `check_fingerprint_collision.py` (no flag).

## Step 2 — hand-login every account the PROVEN way (home IP, real Chrome)
Do NOT log in under fingerprint-chromium — it gets Shape-blocked (see the warning
above). Just double-click **`hand_login_all.bat`**: it sets `RELOGIN_SKIP_PROXY=1`
(home IP) and runs `relogin_one.py all --manual` on real Chrome. Sign in by hand in
each window, clear any one-time device code; it then runs the readiness check. Cookies
save to `target.json` / `target-2.json` / `target-3.json`; at drop time the purchase
browser loads them into its `-fp` fingerprint-chromium profile.

## Step 3 — go live
Set `TARGET_FP_CHROMIUM=1` in `run_bot_with_nightly_restart.bat` (alongside the
existing flags) and start the bot as usual. Each purchase worker launches
fingerprint-chromium on its `-fp` profile with its own seed; the JS `apply_identity`
spoof is auto-skipped (engine-level replaces it).

## What you'll see in the logs (grep `FP_CHROMIUM`)
- `[FP_CHROMIUM] account=primary exec=...chrome.exe args=[--fingerprint=1693239552, ...] profile=...nodriver-profile-fp`
- `[FP_CHROMIUM] JS spoof suppressed (engine-level active) for primary`
- Per-account seeds (stable while `TARGET_FP_SEED_SALT` is empty): primary `1693239552`, business `2122075987`, alt-1 `1509568654`. A skipped identity logs no `FP_CHROMIUM` launch line and its profile has no `-fp` suffix.

## Rollback
- **Instant:** remove `TARGET_FP_CHROMIUM` from the bat (or set `=0`). Everything reverts
  to system Chrome + the real profiles + the JS spoof. No code change needed.
- **Full code revert:** `git checkout -- src/session/session_manager.py src/utils/save_login.py`
  and delete `src/session/fp_chromium.py`, `check_fingerprint_collision.py`,
  `tests/test_fp_chromium.py`, `docs/FP_CHROMIUM.md`.

## Scope (what was and was NOT touched)
- **Touched (all flag-gated):** `src/session/fp_chromium.py` (new helper),
  `src/session/session_manager.py` (purchase launch + profile remap + skip JS spoof
  when fp active), `src/utils/save_login.py` (manual login on the fp device),
  `check_fingerprint_collision.py` (`--fp-chromium` validation mode),
  `tests/test_fp_chromium.py` (31/31).
- **NOT touched:** the 16-IP stock sweep (`multi_session_pool.py`) — it reads inventory,
  doesn't ATC under an account; the burned auto-harvester; `account_identity.py`; the bat.

## Honest caveat
Not a guaranteed Shape beat — F5 is top-tier, arms race. But it is the only free lever
that attacks the actual device-collision wall, and it is validated for free before any
drop. If Step 1 shows distinct devices but a live drop still walls at ATC, the device
axis is not the sole gate — escalate to Camoufox (free, Firefox) or Kameleo (€45/mo).
