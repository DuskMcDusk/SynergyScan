"""Entry point.

    python -m synergyscan              start the app (checks for updates first)
    python -m synergyscan --doctor     write the diagnostics report and exit
    python -m synergyscan --no-update  start without checking for updates
    python -m synergyscan --version    print the release version

The update check runs here, before uvicorn starts and before the app touches
the database. If an update is installed we exit with RESTART_EXIT_CODE and
launcher.py starts the new release; we never try to swap code inside a running
process.

Checking synchronously at startup costs the user 20-30 seconds on the days a
release lands, on a machine that boots once a morning. That is the right trade
for update code that is simple enough to be obviously correct. Staging in the
background and applying at next launch is the optimisation to reach for if the
delay ever becomes the complaint.
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import config, db, doctor, lifecycle, logs, migrations, selfupdate
from .selfupdate import migrate
from .selfupdate import version as relver

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m synergyscan")
    ap.add_argument("--doctor", action="store_true",
                    help="write a diagnostics report to stdout and exit")
    ap.add_argument("--no-update", action="store_true",
                    help="skip the update check for this start")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("--host", help="override the configured bind address")
    ap.add_argument("--port", type=int, help="override the configured port")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    if a.version:
        print(relver.current())
        return 0

    log_path = logs.setup(a.verbose)
    settings = config.load()

    if a.doctor:
        # No update check, no migrations: the doctor has to work on exactly the
        # machine that is broken, without changing it.
        sys.stdout.write(doctor.report_text())
        return 0

    log.info("starting SynergyScan release %s (log: %s)", relver.current(), log_path)

    if not a.no_update:
        applied = selfupdate.check_and_apply(
            settings.channel_url,
            enabled=settings.auto_update,
            timeout=settings.update_timeout_s,
        )
        if applied is not None:
            log.info("restarting into %s", applied.version)
            return selfupdate.RESTART_EXIT_CODE

    # A release the updater did not install still has to bring the schema up: a
    # fresh install, a source checkout, or a manual reinstall. Route it through
    # the same backup-first path the updater uses rather than migrating bare -
    # there must be exactly one way the schema ever changes.
    con = db.connect()
    try:
        pending = [p.name for _, p in migrations.pending(con)]
    finally:
        con.close()
    if pending:
        log.info("applying %d pending migration(s) at startup: %s",
                 len(pending), ", ".join(pending))
        report = migrate.run()
        if not report.get("ok"):
            log.critical("could not bring the database up to date: %s",
                         report.get("error"))
            print("SynergyScan cannot start: the database could not be updated.\n"
                  f"Details are in {log_path}\n"
                  "Run rollback.bat to go back to the previous version, "
                  "then contact support.", file=sys.stderr)
            return 1

    host = a.host or ("0.0.0.0" if settings.allow_lan else settings.host)
    port = a.port or settings.port

    import uvicorn
    # A Server rather than uvicorn.run(), so the UI's Restart button can ask it
    # to stop (lifecycle.request_restart) and we can tell that stop apart from
    # an ordinary shutdown by the exit code the launcher is waiting for.
    server = uvicorn.Server(uvicorn.Config(
        "synergyscan.app:app", host=host, port=port, log_config=None,
        access_log=False, timeout_graceful_shutdown=5))
    lifecycle.register_server(server)
    server.run()
    if lifecycle.restart_requested():
        log.info("exiting so the launcher restarts the app")
        return selfupdate.RESTART_EXIT_CODE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
