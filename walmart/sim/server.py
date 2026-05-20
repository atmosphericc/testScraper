"""
SimServer — programmatic supervisor for the mitmproxy-based Walmart simulator.

Spawns mitmdump as a subprocess (NOT in-process via DumpMaster) because:
  - mitmproxy's asyncio internals don't play nicely with multiple top-level
    event loops being started from a test runner.
  - Subprocess isolation means a crashed sim doesn't take the test process
    with it.
  - Tests can `kill -9` the subprocess if cleanup hangs.

The addon (walmart/sim/mitm_addon.py) holds state in a module-level singleton.
That state lives in the SUBPROCESS, not the test process. Tests script
behavior by writing a JSON config to a file the addon polls, OR by sending
a control request to a /__sim__/ endpoint (cleaner approach).

For Day 1, we keep it simple: the sim runs with default-state-on-startup,
and the smoke test verifies behavior end-to-end via real HTTP requests.

Day 2+ will add a /__sim__/state endpoint for live test scripting.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


logger = logging.getLogger("walmart.sim.server")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ADDON_PATH = REPO_ROOT / "walmart" / "sim" / "mitm_addon.py"

# Default listen port (test harness can override)
DEFAULT_MITM_PORT = 8089


class SimServer:
    """Spawn + manage a mitmdump subprocess running the Walmart sim addon."""

    def __init__(
        self,
        port: int = DEFAULT_MITM_PORT,
        mitmdump_path: Optional[str] = None,
    ):
        self.port = port
        # Auto-detect mitmdump in current venv or PATH
        if mitmdump_path is None:
            venv_mitmdump = REPO_ROOT / "venv" / "bin" / "mitmdump"
            if venv_mitmdump.exists():
                mitmdump_path = str(venv_mitmdump)
            else:
                mitmdump_path = "mitmdump"
        self.mitmdump_path = mitmdump_path
        self.process: Optional[subprocess.Popen] = None
        self._stdout_log: Optional[Path] = None

    def start(self, wait_ready: bool = True, timeout: float = 10.0) -> None:
        """Start mitmdump as a subprocess. Blocks until the port is listening
        (or raises TimeoutError)."""
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("SimServer already running")

        # Log mitmdump output to a temp file for debugging (don't pipe to
        # subprocess.PIPE which can deadlock on full buffer).
        log_dir = REPO_ROOT / "walmart" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._stdout_log = log_dir / f"mitm_sim_{int(time.time())}.log"

        # Build env so subprocess can import walmart.sim
        env = os.environ.copy()
        pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{REPO_ROOT}:{pythonpath}" if pythonpath else str(REPO_ROOT)

        cmd = [
            self.mitmdump_path,
            "-s", str(ADDON_PATH),
            "--listen-host", "127.0.0.1",
            "--listen-port", str(self.port),
            "--set", "termlog_verbosity=warn",
            "--set", "flow_detail=0",
            "--ssl-insecure",
        ]
        logger.info(f"Starting mitmdump: {' '.join(cmd)}")
        logger.info(f"mitmdump output → {self._stdout_log}")

        with open(self._stdout_log, "wb") as out:
            self.process = subprocess.Popen(
                cmd, stdout=out, stderr=subprocess.STDOUT, env=env,
            )

        if wait_ready:
            self._wait_for_port(timeout)
            logger.info(f"SimServer ready on 127.0.0.1:{self.port}")

    def _wait_for_port(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                # Subprocess died — read log for diagnostics
                log_tail = ""
                if self._stdout_log and self._stdout_log.exists():
                    log_tail = self._stdout_log.read_text()[-2000:]
                raise RuntimeError(
                    f"mitmdump exited early (returncode={self.process.returncode}).\n"
                    f"Last log:\n{log_tail}"
                )
            # Try to connect to the listen port
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except (OSError, ConnectionRefusedError):
                pass
            time.sleep(0.2)
        raise TimeoutError(f"mitmdump did not listen on :{self.port} within {timeout}s")

    def stop(self, timeout: float = 5.0) -> None:
        """SIGTERM then SIGKILL if needed."""
        if self.process is None:
            return
        if self.process.poll() is not None:
            self.process = None
            return
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning("mitmdump didn't exit on SIGTERM, sending SIGKILL")
                self.process.kill()
                self.process.wait(timeout=2.0)
        finally:
            self.process = None
            logger.info("SimServer stopped")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    @property
    def proxy_url(self) -> str:
        """For use as --proxy-server or HTTPS_PROXY."""
        return f"http://127.0.0.1:{self.port}"

    def control(self) -> "SimControlClient":
        """Return a typed client for the /__sim__/ control plane."""
        return SimControlClient(self.proxy_url)


class SimControlClient:
    """Type-friendly wrapper for the /__sim__/ control endpoints.

    All methods POST through the sim proxy to `http://sim.local/__sim__/...`.
    Tests use this to script SimState in the subprocess.

    Usage:
      sim = SimServer()
      sim.start()
      ctl = sim.control()
      ctl.reset()
      ctl.set_item("12345", "in_stock")
      ctl.set_queue(queue_id="q1", state_transitions=[(3, "valid")])
      log = ctl.get_log()
    """

    def __init__(self, proxy_url: str):
        self.proxy_url = proxy_url
        import requests
        self._session = requests.Session()
        self._session.proxies = {"http": proxy_url, "https": proxy_url}
        self._session.verify = False

    def _post(self, endpoint: str, body: Optional[dict] = None) -> dict:
        # http://sim.local is a sentinel host — mitmproxy intercepts the
        # /__sim__/ path before any DNS lookup, so the hostname can be
        # arbitrary; we use http (not https) to skip the TLS handshake.
        url = f"http://sim.local{endpoint}"
        r = self._session.post(url, json=body or {}, timeout=5.0)
        r.raise_for_status()
        return r.json()

    def _get(self, endpoint: str) -> dict:
        url = f"http://sim.local{endpoint}"
        r = self._session.get(url, timeout=5.0)
        r.raise_for_status()
        return r.json()

    def ping(self) -> dict:
        """Health-check the control plane is reachable. Useful as a
        post-start handshake before scripting scenarios."""
        return self._get("/__sim__/ping")

    def reset(self) -> dict:
        return self._post("/__sim__/reset")

    def set_item(self, item_id: str, availability: str) -> dict:
        """availability ∈ {in_stock, oos, third_party, queued}"""
        return self._post("/__sim__/item", {
            "item_id": item_id, "availability": availability,
        })

    def set_queue(
        self,
        queue_id: str = "qa484c0ebd7014",
        item_id: str = "19012610850",
        initial_state: str = "pending",
        initial_likelihood: str = "likely",
        next_refresh_relative_time_ms: int = 2000,
        state_transitions: Optional[list] = None,
        likelihood_transitions: Optional[list] = None,
    ) -> dict:
        """Configure a queue scenario.

        state_transitions: list of [poll_count, state] e.g. [[3, "valid"]]
        likelihood_transitions: same shape with likelihood values
        """
        return self._post("/__sim__/queue", {
            "queue_id": queue_id,
            "item_id": item_id,
            "initial_state": initial_state,
            "initial_likelihood": initial_likelihood,
            "next_refresh_relative_time_ms": next_refresh_relative_time_ms,
            "state_transitions": state_transitions or [],
            "likelihood_transitions": likelihood_transitions or [],
        })

    def set_hash_scenario(
        self,
        op_name: str,
        known_hashes: Optional[list] = None,
        force_miss: bool = False,
    ) -> dict:
        return self._post("/__sim__/hash", {
            "op_name": op_name,
            "known_hashes": known_hashes or [],
            "force_miss": force_miss,
        })

    def get_log(self) -> list:
        return self._get("/__sim__/log").get("log", [])

    def get_graphql_log(self) -> dict:
        return self._get("/__sim__/graphql_log").get("graphql", {})


def main():
    """CLI entrypoint: `python -m walmart.sim.server` for manual testing."""
    logging.basicConfig(level=logging.INFO)
    sim = SimServer()
    sim.start()
    print(f"Walmart sim running on {sim.proxy_url}")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        sim.stop()


if __name__ == "__main__":
    main()
