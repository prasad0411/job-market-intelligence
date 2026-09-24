"""python3 -m outreach"""

# Process resilience must be installed before any network call. Two
# multi-hour hangs this week were sockets with no timeout; this sets a
# ceiling for every library that does not set its own, honours SIGTERM,
# reaps orphaned children, and dumps stacks when progress stops.
try:
    from aggregator import resilience as _resilience
    _resilience.install()
except Exception as _e:          # never let this block startup
    import logging as _l
    _l.warning("resilience not installed: %s", _e)

from outreach.run_outreach import main
if __name__ == "__main__":
    main()
