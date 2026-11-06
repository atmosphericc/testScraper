# Guest-follow arms for the cross-line limiter-keying test (prepared 2026-11-04, NOT registered)

Nothing here has been run. Mirrors the existing `\TargetBot\guest-follow 2340` / `0135` tasks (queried read-only 2026-11-04):
exe `C:\Users\elric\Desktop\testScraper\venv\Scripts\pythonw.exe`, working directory `C:\Users\elric\Desktop\testScraper`,
daily trigger 23:40 (and 01:35), run as `elric` (interactive logon), ExecutionTimeLimit 8 h, MultipleInstances IgnoreNew,
not stopped on battery. Action args of the existing tasks: `"...\tools\experiments\limkey_guest_follow.py" --scheduled`.
All arms need `state/guest_follow.enable` (shared; deleting it stops every arm). The proxies come from
`config/proxyIps.json` by index (`reserve:0`, `reserve:1`); no credential appears on any command line.

Arms (same grid 5 s, offsets inside one ~250 ms slot):

| arm  | line | tag | lock name | offset ms |
|------|------|-----|-----------|-----------|
| bd0  | reserve:0 | bd0  | guest_follow_bd0  | 40  |
| bd0b | reserve:0 (same-IP positive control) | bd0b | guest_follow_bd0b | 80 |
| bd1  | reserve:1 | bd1  | guest_follow_bd1  | 120 |
| home (existing task, edit) | home line | home | guest_follow | 0 |

Common arguments: `--scheduled --proxy <line> --tag <tag> --lock-name <lock> --proactive-relaunch-s 720 --slot-grid-s 5 --slot-offset-ms <ms>`.
Optional (not in the spec, decide before registering): `--per-window-cap 200 --nightly-cap 4000` so a 16 min window is covered
(default caps stop at 40 shots, about 200 s at a 5 s grid).

## Register (PowerShell, preferred: reproduces the 8 h limit / battery / IgnoreNew settings exactly)

```powershell
$root = 'C:\Users\elric\Desktop\testScraper'
$py   = "$root\venv\Scripts\pythonw.exe"
$arms = @(
  @{ n='bd0';  proxy='reserve:0'; off=40  },
  @{ n='bd0b'; proxy='reserve:0'; off=80  },
  @{ n='bd1';  proxy='reserve:1'; off=120 }
)
foreach ($a in $arms) {
  foreach ($slot in @(@{h='23:40'; s='2340'}, @{h='01:35'; s='0135'})) {
    $args = "`"$root\tools\experiments\limkey_guest_follow.py`" --scheduled --proxy $($a.proxy) --tag $($a.n) " +
            "--lock-name guest_follow_$($a.n) --proactive-relaunch-s 720 --slot-grid-s 5 --slot-offset-ms $($a.off)"
    $act  = New-ScheduledTaskAction -Execute $py -Argument $args -WorkingDirectory $root
    $trg  = New-ScheduledTaskTrigger -Daily -At $slot.h
    $set  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 8) -MultipleInstances IgnoreNew `
              -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $prn  = New-ScheduledTaskPrincipal -UserId 'elric' -LogonType Interactive
    Register-ScheduledTask -TaskPath '\TargetBot\' -TaskName "guest-follow-$($a.n) $($slot.s)" `
      -Action $act -Trigger $trg -Settings $set -Principal $prn
  }
}
```

## Register (schtasks equivalent; defaults for time limit / battery differ: set them afterwards in Task Scheduler or use the above)

```bat
schtasks /Create /TN "\TargetBot\guest-follow-bd0 2340"  /SC DAILY /ST 23:40 /RU elric /IT /TR "\"C:\Users\elric\Desktop\testScraper\venv\Scripts\pythonw.exe\" \"C:\Users\elric\Desktop\testScraper\tools\experiments\limkey_guest_follow.py\" --scheduled --proxy reserve:0 --tag bd0 --lock-name guest_follow_bd0 --proactive-relaunch-s 720 --slot-grid-s 5 --slot-offset-ms 40"
schtasks /Create /TN "\TargetBot\guest-follow-bd0 0135"  /SC DAILY /ST 01:35 /RU elric /IT /TR "\"C:\Users\elric\Desktop\testScraper\venv\Scripts\pythonw.exe\" \"C:\Users\elric\Desktop\testScraper\tools\experiments\limkey_guest_follow.py\" --scheduled --proxy reserve:0 --tag bd0 --lock-name guest_follow_bd0 --proactive-relaunch-s 720 --slot-grid-s 5 --slot-offset-ms 40"
schtasks /Create /TN "\TargetBot\guest-follow-bd0b 2340" /SC DAILY /ST 23:40 /RU elric /IT /TR "\"C:\Users\elric\Desktop\testScraper\venv\Scripts\pythonw.exe\" \"C:\Users\elric\Desktop\testScraper\tools\experiments\limkey_guest_follow.py\" --scheduled --proxy reserve:0 --tag bd0b --lock-name guest_follow_bd0b --proactive-relaunch-s 720 --slot-grid-s 5 --slot-offset-ms 80"
schtasks /Create /TN "\TargetBot\guest-follow-bd0b 0135" /SC DAILY /ST 01:35 /RU elric /IT /TR "\"C:\Users\elric\Desktop\testScraper\venv\Scripts\pythonw.exe\" \"C:\Users\elric\Desktop\testScraper\tools\experiments\limkey_guest_follow.py\" --scheduled --proxy reserve:0 --tag bd0b --lock-name guest_follow_bd0b --proactive-relaunch-s 720 --slot-grid-s 5 --slot-offset-ms 80"
schtasks /Create /TN "\TargetBot\guest-follow-bd1 2340"  /SC DAILY /ST 23:40 /RU elric /IT /TR "\"C:\Users\elric\Desktop\testScraper\venv\Scripts\pythonw.exe\" \"C:\Users\elric\Desktop\testScraper\tools\experiments\limkey_guest_follow.py\" --scheduled --proxy reserve:1 --tag bd1 --lock-name guest_follow_bd1 --proactive-relaunch-s 720 --slot-grid-s 5 --slot-offset-ms 120"
schtasks /Create /TN "\TargetBot\guest-follow-bd1 0135"  /SC DAILY /ST 01:35 /RU elric /IT /TR "\"C:\Users\elric\Desktop\testScraper\venv\Scripts\pythonw.exe\" \"C:\Users\elric\Desktop\testScraper\tools\experiments\limkey_guest_follow.py\" --scheduled --proxy reserve:1 --tag bd1 --lock-name guest_follow_bd1 --proactive-relaunch-s 720 --slot-grid-s 5 --slot-offset-ms 120"
```

The schtasks form has no working directory: the script resolves its own paths from `__file__`, so it still runs, but the
PowerShell form (which sets `WorkingDirectory`) is the exact mirror.

## Home task

The existing `\TargetBot\guest-follow 2340` and `0135` tasks would need `--slot-grid-s 5 --slot-offset-ms 0` appended to
their arguments (after `--scheduled`) to join the grid, e.g.
`Set-ScheduledTask -TaskPath '\TargetBot\' -TaskName 'guest-follow 2340' -Action (New-ScheduledTaskAction -Execute $py -Argument "`"$root\tools\experiments\limkey_guest_follow.py`" --scheduled --slot-grid-s 5 --slot-offset-ms 0" -WorkingDirectory $root)`
(same for `0135`). Not applied.

## Delete

```bat
schtasks /Delete /TN "\TargetBot\guest-follow-bd0 2340"  /F
schtasks /Delete /TN "\TargetBot\guest-follow-bd0 0135"  /F
schtasks /Delete /TN "\TargetBot\guest-follow-bd0b 2340" /F
schtasks /Delete /TN "\TargetBot\guest-follow-bd0b 0135" /F
schtasks /Delete /TN "\TargetBot\guest-follow-bd1 2340"  /F
schtasks /Delete /TN "\TargetBot\guest-follow-bd1 0135"  /F
```

## Readout (after the night)

`python tools/experiments/limkey_guest_follow.py --readout logs/experiments/guest_follow_<stamp>.json logs/experiments/guest_follow_bd0_<stamp>.json logs/experiments/guest_follow_bd0b_<stamp>.json logs/experiments/guest_follow_bd1_<stamp>.json`
prints the legacy member-comparison JSON, then the per (window, tcin, 30 s block, tag) table, pairwise co-movement with a
seeded permutation p, the pooled Wilson level test and the remainder. Expectation to pre-register: bd0 vs bd0b (same IP,
same slot) is the positive control for co-movement.
