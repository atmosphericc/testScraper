# E2 from a second device — is Shape's product escalation global, or keyed to this PC? (runbook, 2026-10-22)

Why: the 10-22 E2 test (C-1022-01) showed a hot TCIN admits ~6 guest add-to-carts from this PC, then answers keyless 401
for 50+ minutes across fresh profiles and across two Bright Data lines. One PC cannot tell "global per product" from
"per product x this device's fingerprint". If it is per device, a second device (any laptop, or a Mac mini) gets its
own admits and is worth money; if global, it is not. Nothing to buy for the test. Money risk: none (guest browser, no
login, disabled TCINs, a 2xx would land in a guest cart).

Preconditions: a second Windows PC or laptop on the SAME home network (so the line is held constant), Python 3.12, Chrome,
and this repo checked out there (`git clone`, `python -m venv venv`, `venv\Scripts\pip install -r requirements.txt`).
No account file, no `config/proxyIps.json` needed for the home-line arms. Daytime only (06:00-23:05, the tool refuses
otherwise). Never a TCIN that is enabled in `config/product_config.json`.

Arms (run on the SAME day, within ~1 h; X = 1012055696, Y = 1011209279, both disabled hot TCINs):

| Step | Where | Command | Expect if PER DEVICE | Expect if GLOBAL |
|---|---|---|---|---|
| 1. Baseline, second device, untouched TCIN | laptop | `venv\Scripts\python.exe tools\experiments\limkey_guest_follow.py --tag dev2a --lock-name dev2a --simulate <Z> --sim-s 20 --cadence-s 5` with Z = a THIRD disabled hot TCIN nobody has shot today | first 3-4 shots 424 + hop | same |
| 2. Trip the product from the desktop | desktop | `... --tag deska --lock-name deska --simulate <Z> --sim-s 60 --cadence-s 5` (12 shots: expect 424 x~5 then 401) | 401 after ~5-6 admits | same |
| 3. Same product from the second device, right after | laptop | `... --tag dev2b --lock-name dev2b --simulate <Z> --sim-s 20 --cadence-s 5` | **424 + hop** (its own budget) | **401 from shot 1** |
| 4. Control: an untouched 4th TCIN from the desktop | desktop | `... --tag deskc --lock-name deskc --simulate <W> --sim-s 20 --cadence-s 5` | 424 (not machine-wide) | 424 |

Readout (pre-registered): step 3 with >= 3/4 admitted (424 + hop) = PER DEVICE -> a second buyer host is a real lever
(Refract's Mac doctrine; Mac mini M6 $899) and the per-product budget is per machine. Step 3 with >= 3/4 keyless 401 =
GLOBAL (or per network line: run step 3 again from the laptop over a phone hotspot to separate those two) -> no device
spend; the budget is shared by everyone and only first-shot timing matters at wall 2. Step 4 with 401 = machine-wide
escalation, which changes the reading of everything above (stop and report).

Where the output lands: `logs/experiments/guest_follow_<tag>_<stamp>.log` on each machine; copy the laptop's two files
into `logs/analysis_<date>/e2_device/` on the desktop and record the result as a claim (C-10xx) with the four tags.
