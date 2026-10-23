"""Offline calibration and evaluation tools for the recommendation subagent (phase P7, Step 10).

Nothing in this package is imported by the service, the gates or the terminal: it is an
external analysis entry point (a test checks that no runtime module imports it). It reads the
live PostgreSQL / Neo4j data READ-ONLY (the repositories open READ ONLY transactions / READ
sessions), runs the service only with the ScriptedController and TEMPORARY state files, and never
writes recommendation.yaml or data/.runtime.

Modules:
- ``stats``: pure statistics (quantiles, duplicates, outliers, Gini, coverage, tie groups, bin
  balance, browsing curves, convergence) used by the analyses and unit-tested on synthetic input.
- ``scenarios``: representative hard-criteria scenarios and soft requirement sets.
- ``live``: read-only data access and pool construction through the production pool builder.
- ``calibrate``: real-data calibration analyses -> one JSON report (CALIBRATION.md cites it).
- ``simulate``: rule-based simulated users and the multi-round cache / cold-start evaluation ->
  one JSON report (PERFORMANCE.md cites it). Simulated behaviour is always labelled as such and
  never reported as user satisfaction.
"""
