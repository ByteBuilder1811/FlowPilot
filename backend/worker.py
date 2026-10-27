"""Stand-alone worker process: `python -m backend.worker`. Run several against the same (Postgres) database for horizontal scale.
Set FLOWPILOT_SINGLE_PROCESS=0 on the API process so it does not force-recover runs owned by these workers."""
import logging
import os
import signal
import threading

from backend.db.migrate import upgrade_head
from backend.execution.worker import Worker


def main():
    logging.basicConfig(level=logging.INFO)
    upgrade_head()
    w = Worker(concurrency=int(os.environ.get("FLOWPILOT_WORKER_CONCURRENCY", "4")))
    w.start()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    w.shutdown()


if __name__ == "__main__":
    main()
