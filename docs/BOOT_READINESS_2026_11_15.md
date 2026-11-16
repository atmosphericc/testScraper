# Boot readiness — 2026-11-15 (post Wall-A fix + gate sweep; auto-arm re-enabled)

Read this before the next drop. It is the single operator checklist: what is DONE
(code + automation), what still needs YOU (host/human), and how the drop PROVES each gate.

**AUTO-ARM IS LIVE (re-enabled 2026-11-15 23:27 CT).** The `state/ensure_armed.disable`
opt-out was removed, so the bot self-arms every 23:15 / 01:30 CT with NO hand steps: it
starts the bot when it is down / RED / stale, force-logs-in any guest or expired account
(alt-1 included), and follows boot_check to GREEN. Next engagement after a down bot is the
upcoming 01:30 run; the nightly login-repair pass is 23:15. Opt back out only by recreating
`state/ensure_armed.disable`.

Re-run the read-only preflight any time: `python tools/predrop/preflight.py`
(add `--drop "YYYY-MM-DD HH:MM"` once you know the window).

---

## What is DONE (code + automation, committed + pushed on `audit_1106_harvest_probe_fs1`)

- **Auto-arm re-enabled** — the hands-free overnight drop system (boot + login + follow) is
  back on; see the header.
- **Wall A (the 11-06 bank drain): FIXED + 7-cycle reviewed + re-audited.** Harvest parks
  only on a persistent STRONG PX marker; a container-only flash rotates. This is the thing
  that starved the bank to 0 on 11-06 and 11-12.
- **Detection: repointed to the home IP** (`apps_raw`, ≤0.5/s) after BD was cancelled.
- **Gate sweep done, nothing left to fix in code:** dispatch "BUILD C2" was a multi-TCIN
  artifact (irrelevant to single-SKU); the FS levers are built, correct, and measured to
  convert 0 — no code trick beats FAST_SELLING.
- **Proof instrument: `tools/analysis/gate_scorecard.py`** — see the readout section.
- Offline suite **86/86**.

## What still needs YOU (everything else is now automated)

The auto-arm covers boot + login (incl. alt-1). Two things remain host/human:

1. **Pause Windows Update — REQUIRED, needs admin (I am not elevated, so I cannot do it).**
   Active hours are 8–2, so a reboot can land 2–8 AM (the drop band), and updates are NOT
   paused. A WU reboot killed the 10-14 window. Run ELEVATED (Run as Administrator):
   `powershell -ExecutionPolicy Bypass -File tools\ops\wu_drop_guard.ps1 -Days 7`
   Pauses updates 7 days; re-run each drop week — the active-hours half is not durable.
2. **Close Parsec / any remote session** before the window (20/27 GPU-crash wakes carried a
   remote-wake code). Your call — it is your own remote access to this host.

**alt-1 is no longer a required pre-step.** Its login-session is expired, which the auto-arm
treats as needs-login and repairs via the nightly 23:15 force-login pass (the 01:30 run
defers it inside the 00–06 band to avoid burning the account mid-window). A typed login is
Shape-scored (2/2 on 10-14, weaker before), so if alt-1 still reads guest after a boot, the
reliable backstop is a hand-login: `hand_login_alt1_force.bat`. Not a blocker to arming.

*(optional, GB permitting)* re-prove the proxy pool — command in the preflight output
(`monitor.last_proof`); the pool is 1 home exit by design now.

## Armed flags that are UNPROVEN LIVE (watch them at boot)

Two flags changed since the last boot — each has a kill switch:
- `RESILIENT_HARVEST_VIA_LOCAL_IP=1` (home-IP sweep). Kill: `=0`. Backstopped by the
  trusted-reader. Watch the first boot for clean sweeps, no 404-ban.
- `TARGET_HARVEST_PX_RECONFIRM=1` (the Wall A fix). Kill: `=0`. Watch for
  `MISS-PROBE px re-confirm` log lines parking only on strong markers.

Follow the first boot to GREEN: `python tools/predrop/boot_check.py --follow`

## AT THE DROP — the proof (pre-registered readout)

After the run, build the store and print the whole per-gate board from one command:

```
python tools/events/build.py
python tools/analysis/gate_scorecard.py --run <run_id>
```

Each gate PASSES when:
- **G0 Detection** — sweep covered the window, 0 rate-limit bans.
- **G1 Edge/dispatch** — shots reach the cart service (`admits` past>0), early; `dispatch_lag`
  SKIP or small busy lag.
- **G2 / Wall A** — `cred_wall2` shows `banked_replay` present (past_limiter ≥20) and
  reached_cart in the ~29–43% band. **CONFOUNDED = the bank was walled empty again (Wall A
  failed).** This is the single most important number — it directly tests the fix.
- **G3 FAST_SELLING** — `first_po_age` reading: a win ≤3s window-age, or NO_WIN (gate held).

## Honest read

The one FAIL you can ignore for now is `last_run.boot` — it reports the **11-12 run**, which
ran *before* the Wall A fix and couldn't buy for exactly the reason now fixed. It clears at
the next boot **if** the fix holds live — which is what G2/Wall A on the scorecard will tell
you. Everything that can be proven offline is proven; the rest is this one boot.
