"""Shared constants for VERDICT (CLAUDE.md §4)."""

BLOCK_SIZE      = 4096
IDAT_CHUNK_SIZE = 2048   # generator writes PNG image data in chunks of this size
TOP_K           = 8      # ranked candidates kept per position
WINDOW          = 256    # locality window in blocks around the last placed block
BUDGET          = 60     # max candidate placements per artifact
