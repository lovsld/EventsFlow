"""Narrative layer (Step 2) for the ACP Story Simulator.

Read-only downstream of the emergence layer: this package parses finished
runs' products (world_log.json et al.) and never imports v02 decision code,
never writes into runs_v02/, and never feeds anything back to agents.

Design: docs/NARRATIVE_LAYER_DESIGN.md (approved by Hu, N0 gate, 2026-11-21).
"""

OBSERVER_VERSION = "n1-observer-1.0"
