"""GPU device helpers (scenario fixture)."""

import time


def wait_for_device_ready(device_id: str, *, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _device_reports_ready(device_id):
            return
        time.sleep(0.1)
    raise TimeoutError(f"device {device_id} not ready after {timeout_s}s")


def _device_reports_ready(device_id: str) -> bool:
    # Scenario fixture: pretend the device is always ready.
    return True
