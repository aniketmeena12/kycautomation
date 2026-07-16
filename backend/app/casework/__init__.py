"""
Case management (Phase 6): the compliance workspace built ON TOP of Phases 0-5.

    state_machine.py  validated case lifecycle (pure functions, no I/O)
    timeline.py       chronology GENERATED from stored rows, never hand-written
    sar.py            Draft SAR: deterministic sections + LLM narrative only
    schemas.py        timeline / metrics / case contracts

Nothing here re-derives a risk score, re-runs entity resolution, or writes
evidence. It aggregates, sequences, and records human decisions about what the
earlier phases already produced.
"""
