"""Test-harness launcher (scratch, not product code): same ASGI app, plus a
SIGUSR1 all-threads stack dump so a wedged event loop can be diagnosed."""
import faulthandler
import signal

import uvicorn

faulthandler.enable()
faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)

if __name__ == "__main__":
    uvicorn.run("app.start:app", host="127.0.0.1", port=8000, log_level="info")
