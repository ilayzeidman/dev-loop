"""GPU encoder init path (scenario fixture)."""

from .device import wait_for_device_ready


def init_encoder(device_id: str) -> None:
    wait_for_device_ready(device_id, timeout_s=5.0)
