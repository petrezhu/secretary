"""ColdSkill sedimentation pipeline — zero-LLM candidate mining.

This package hosts the pure-function mining stage of the ColdSkill
pipeline: aggregating the inbound-query trace (``queries.jsonl``, written
by :mod:`secretary.gateway.inbound`) into a candidate rule list
(``rule_candidates.json``) consumed by the later adoption stage.

No LLM calls, no network access, and no dependence on the daemon
classes — every input (paths, thresholds, windows, "now") is a
parameter so the functions can be reasoned about and tested in
isolation.
"""