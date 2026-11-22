"""L4 monitoring.

Person A: expected behaviour and change detection
    monitoring.baseline        seasonal baseline, prediction range (backtested)
    monitoring.detect          change scores, anomalies, episodes; python -m monitoring.detect
Person B: what to watch, data quality, planned events
    monitoring.priorities      metric priority scores and the watchlist
    monitoring.data_quality    freshness and completeness checks
    monitoring.event_calendar  planned/unplanned events: scope, timing, stated effects
    monitoring.triage          report / suppress_planned / insufficient_data per anomaly

Import from the submodules (``from monitoring.detect import Detector``). This
file stays free of imports so both halves can add modules without touching it.
"""
