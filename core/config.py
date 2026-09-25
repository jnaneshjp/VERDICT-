"""Shared constants for VERDICT (CLAUDE.md §4)."""

BLOCK_SIZE      = 4096
IDAT_CHUNK_SIZE = 2048   # generator writes PNG image data in chunks of this size
TOP_K           = 8      # ranked candidates kept per position
WINDOW          = 256    # locality window in blocks around the last placed block
BUDGET          = 60     # max candidate placements per artifact

# Triage weights (CLAUDE.md §9). Fixed in P0; sliders come in P1.
W_STATE    = 0.5
W_COMPLETE = 0.3
W_CATEGORY = 0.2

STATE_VALUE = {"PROVEN": 1.0, "PLAUSIBLE": 0.6, "PARTIAL": 0.4, "REJECTED": 0.0}

# How much an investigator usually cares about each category (a tunable guess, not a measurement).
CATEGORY_VALUE = {"document": 1.0, "log": 0.8, "csv": 0.7, "image": 0.5,
                  "archive": 0.5, "text": 0.4}
