"""
The Autonomous Investigation Engine (Phase 5).

The first component in this project permitted to call an LLM. What the agent
may do -- gather evidence, explain findings, summarize, recommend next steps,
draft a report -- and what it may never do -- calculate a risk score or a
confidence value, perform entity resolution, create risk events or alerts,
modify evidence, or decide a compliance outcome -- is the boundary this
package exists to hold.

Module map:
    schemas.py    the context that goes in, the report that must come out
    context.py    grounded context assembly (reads the DB; invents nothing)
    prompts.py    versioned prompt construction + untrusted-data quarantine
    grounding.py  deterministic post-validation: citations, vocabulary, injection
    agent.py      the single call site of an LLM in this codebase
"""
