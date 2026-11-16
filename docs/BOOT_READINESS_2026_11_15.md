# Boot readiness — 2026-11-15 (post Wall-A fix + gate sweep)

Read this before the next drop. It is the single operator checklist: what is DONE
(code), what is YOURS to do (host/human — I cannot do these), and how the drop
PROVES each gate. Preflight verdict right now: **NO-GO**, for two reasons, one of
which is just the old data and one of which needs your hand.

Re-run the read-only preflight any time: `python tools/predrop/preflight.py`
(add `--drop "YYYY-MM-DD HH:MM"` once you know the window).

---

## What is DONE (code, committed + pushed on `audit_1106_harvest_probe_fs1`)

- **Wall A (the 11-06 bank drain): FIXED + 7-cycle reviewed + re-audited.** Harvest
  parks only on a persistent STRONG PX marker; a container-only flash rotates. This
  is the thing that starved the bank to 0 on 11-06 and 11-12.
- **Detection: repointed to the home IP** (`apps_raw`, ≤0.5/s) after BD was cancelled.
- **Gate sweep done, nothing left to fix in code:** dispatch "BUILD C2" was a
  multi-TCIN artifact (irrelevant to single-SKU); the FS levers are built, correct,
  and measured to convert 0 — no code trick beats FAST_SELLING.
- **Proof instrument: `tools/analysis/gate_scorecard.py`** — see the readout section.
- Offline suite **86/86**.

## YOUR pre-boot actions (I cannot do these — host/human)

The preflight's two blockers plus the host hygiene it flags:

1. **Hand-login alt-1 — REQUIRED.** alt-1's login session is EXPIRED; without it only
   primary starts hot (half your early shots). Run `hand_login_alt1_force.bat`, then
   re-run the preflight to confirm it goes green.
2. **Pause Windows Update** (Settings → Windows Update → Pause updates, 1 week). It is
   NOT paused; a forced restart mid-window loses the edge.
3. **Close Parsec / any remote session** on the host before the window (20/27 GPU-crash
   wakes carried a remote-wake code).
4. *(optional, if you have GB to spare)* re-prove the proxy pool — the exact command is
   in the preflight output (`monitor.last_proof`). The pool is 1 home exit by design now.

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

The one FAIL you can ignore for now is `last_run.boot` — it reports the **11-12 run**,
which ran *before* the Wall A fix and couldn't buy for exactly the reason now fixed. It
clears at the next boot **if** the fix holds live — which is what G2/Wall A on the
scorecard will tell you. Everything that can be proven offline is proven; the rest is
this one boot.
