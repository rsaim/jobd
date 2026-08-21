"""Lets `python -m jobd.cli ...` work.

`ingest gmail-backfill` shells out to exactly this — one subprocess per day,
each needing its own entry point rather than importing and calling `main()`
in-process, so a killed or OOM'd day-worker cannot take any other day's
process down with it.
"""

from jobd.cli.main import main

if __name__ == "__main__":
    main()
