The GPU streaming encoder times out during E2E because device initialization
does not wait for `nvidia-smi --query-gpu=ready` to return ready before the
first frame is pushed into the encoder. Fix the init path so the encoder
waits for device readiness.
