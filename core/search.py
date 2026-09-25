"""Deterministic reconstruction search for VERDICT (CLAUDE.md §5, §20 task 4).

Assemble a physical block path from a carved PNG anchor by consulting the PNG
validator after each extension:

    VALID      -> stop this branch, return success
    INCOMPLETE -> extend with the next ranked candidate block
    INVALID    -> backtrack to the deepest untried decision point that lies
                  at or after the last verified-chunk boundary

Baseline ranker uses physical locality only. No ML. No ground-source data.
The ranker is pluggable so a later ML ranker can replace it without touching
the search algorithm.
"""
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional, Tuple

from core.carve import Anchor
from core.config import BLOCK_SIZE, BUDGET, TOP_K, WINDOW
from core.ingest import Image
from core.validate_png import (
    PNG_SIGNATURE,
    PNGValidationResult,
    Status,
    validate_png,
)


class ReconStatus(str, Enum):
    VERIFIED = "VERIFIED"                          # validator returned VALID
    REJECTED_ANCHOR = "REJECTED_ANCHOR"            # anchor prefix invalid
    EXHAUSTED_BUDGET = "EXHAUSTED_BUDGET"          # ran out of validator calls
    EXHAUSTED_CANDIDATES = "EXHAUSTED_CANDIDATES"  # backtracking hit the bottom


@dataclass
class SearchEvent:
    """One structured event for the Proof Panel to render later."""
    step: int
    event: str
    depth: int = 0
    block: Optional[int] = None
    path: List[int] = field(default_factory=list)
    validation_count: int = 0
    validator_status: Optional[str] = None
    validator_reason: Optional[str] = None
    verified_prefix: Optional[int] = None
    verified_positions: Optional[int] = None
    remaining_candidates: int = 0
    ranked: List[int] = field(default_factory=list)


@dataclass
class ReconstructionResult:
    """Structured outcome of one reconstruct_png() call."""
    status: ReconStatus
    reason: str
    anchor_byte_offset: int
    anchor_block: int
    path: List[int]
    reconstructed_bytes: Optional[bytes] = None
    reconstructed_length: int = 0
    reconstructed_sha256: Optional[str] = None
    validation_count: int = 0
    max_depth: int = 0
    backtrack_count: int = 0
    branches_tried: int = 0
    final_validator: Optional[PNGValidationResult] = None
    events: List[SearchEvent] = field(default_factory=list)


# ---------------------------------------------------- baseline candidate ranker

def rank_by_locality(image: Image, path: List[int], candidates: List[int]) -> List[int]:
    """Physical-locality baseline: nearest to path[-1] first, block index as tiebreak.

    Deterministic and transparent — this is the 'locality baseline', not a
    probability. Signature intentionally accepts (image, path, candidates) so a
    later MLRanker can compute features from the path tail without changing
    search.py.
    """
    current = path[-1]
    return sorted(candidates, key=lambda b: (abs(b - current), b))


# --------------------------------------------------------- byte-level helpers

def _bytes_of_path(image: Image, path: List[int], anchor_offset: int) -> bytes:
    """Anchor prefix (from anchor_offset to end of block) then full subsequent blocks."""
    bs = image.block_size
    anchor_block = path[0]
    parts = [image.raw[anchor_offset:(anchor_block + 1) * bs]]
    for block in path[1:]:
        parts.append(image.raw[block * bs:block * bs + image.blocks[block].length])
    return b"".join(parts)


def _cumulative_lengths(image: Image, path: List[int], anchor_offset: int) -> List[int]:
    """Byte total contributed after each path position (inclusive)."""
    bs = image.block_size
    lens = [(path[0] + 1) * bs - anchor_offset]
    for block in path[1:]:
        lens.append(lens[-1] + image.blocks[block].length)
    return lens


def _verified_prefix_bytes(result: PNGValidationResult) -> int:
    """Byte offset up to which the current attempt has cleared PNG format checks."""
    if result.status is Status.INVALID and result.reason_code in (
            "signature_mismatch", "signature_incomplete"):
        return 0
    verified = len(PNG_SIGNATURE)             # signature is fixed and matched
    for check in result.chunk_checks:
        if check.ok:
            verified = check.offset + 8 + check.length + 4   # end of this chunk's CRC
    return verified


def _verified_positions(cum_lens: List[int], verified_bytes: int) -> int:
    """How many leading path positions are ENTIRELY inside the verified byte range."""
    covered = 0
    for i, cumulative in enumerate(cum_lens):
        if cumulative <= verified_bytes:
            covered = i + 1
        else:
            break
    return covered


def _candidates(image: Image, used: set, current: int, window: int) -> List[int]:
    """Unused physical blocks within +/-window of `current`; whole disk as fallback."""
    lo = max(0, current - window)
    hi = min(image.block_count - 1, current + window)
    local = [b for b in range(lo, hi + 1) if b not in used]
    if not local:
        local = [b for b in range(image.block_count) if b not in used]
    return local


def _dedup_by_sha(image: Image, blocks: List[int]) -> List[int]:
    """Keep the first-listed representative per unique SHA-256 (CLAUDE.md §5)."""
    seen: set = set()
    kept: List[int] = []
    for b in blocks:
        sha = image.blocks[b].sha256
        if sha in seen:
            continue
        seen.add(sha)
        kept.append(b)
    return kept


# ------------------------------------------------------------- backtracking

def _pop_alternative(stack: List[Tuple[int, List[int]]],
                     path: List[int],
                     used: set,
                     verified_upto: int) -> Optional[int]:
    """Pop the topmost decision point that still has a candidate and is not frozen."""
    while stack:
        position, remaining = stack[-1]
        if position < verified_upto or not remaining:
            stack.pop()
            continue
        next_block = remaining.pop(0)
        for b in path[position:]:
            used.discard(b)
        del path[position:]
        path.append(next_block)
        used.add(next_block)
        if not remaining:
            stack.pop()
        return next_block
    return None


# ------------------------------------------------------------- main search

def reconstruct_png(image: Image,
                    anchor: Anchor,
                    *,
                    budget: int = BUDGET,
                    top_k: int = TOP_K,
                    window: int = WINDOW,
                    ranker: Callable[[Image, List[int], List[int]], List[int]] = rank_by_locality
                    ) -> ReconstructionResult:
    """Deterministic validator-guided reconstruction from a PNG anchor."""
    anchor_block = anchor.block_index
    anchor_offset = anchor.byte_offset

    path: List[int] = [anchor_block]
    used: set = {anchor_block}
    stack: List[Tuple[int, List[int]]] = []
    verified_upto = 0
    validation_count = 0
    backtrack_count = 0
    branches_tried = 1
    max_depth = 1
    events: List[SearchEvent] = []
    step = 0

    events.append(SearchEvent(step=step, event="SEARCH_START",
                              depth=1, block=anchor_block, path=list(path)))

    def finish(status: ReconStatus, reason: str,
               final_val: Optional[PNGValidationResult] = None) -> ReconstructionResult:
        recon_bytes = None
        recon_len = 0
        recon_sha = None
        if status is ReconStatus.VERIFIED and final_val is not None:
            full = _bytes_of_path(image, path, anchor_offset)
            recon_bytes = full[:final_val.consumed_length]     # ignore post-IEND slack
            recon_len = len(recon_bytes)
            recon_sha = hashlib.sha256(recon_bytes).hexdigest()
        return ReconstructionResult(
            status=status, reason=reason,
            anchor_byte_offset=anchor_offset, anchor_block=anchor_block,
            path=list(path),
            reconstructed_bytes=recon_bytes,
            reconstructed_length=recon_len,
            reconstructed_sha256=recon_sha,
            validation_count=validation_count,
            max_depth=max_depth,
            backtrack_count=backtrack_count,
            branches_tried=branches_tried,
            final_validator=final_val,
            events=events,
        )

    while True:
        step += 1
        if validation_count >= budget:
            events.append(SearchEvent(step=step, event="SEARCH_EXHAUSTED",
                                      depth=len(path), path=list(path),
                                      validation_count=validation_count,
                                      validator_reason="budget_exhausted"))
            return finish(ReconStatus.EXHAUSTED_BUDGET,
                          f"validation budget of {budget} exhausted")

        candidate_bytes = _bytes_of_path(image, path, anchor_offset)
        result = validate_png(candidate_bytes)
        validation_count += 1

        verified_bytes = _verified_prefix_bytes(result)
        cum_lens = _cumulative_lengths(image, path, anchor_offset)
        cur_verified_positions = _verified_positions(cum_lens, verified_bytes)

        events.append(SearchEvent(step=step, event="VALIDATION_RESULT",
                                  depth=len(path), path=list(path),
                                  validation_count=validation_count,
                                  validator_status=result.status.value,
                                  validator_reason=result.reason_code,
                                  verified_prefix=verified_bytes,
                                  verified_positions=cur_verified_positions))

        # A path position is frozen once all of its contribution has been CRC-verified.
        if cur_verified_positions > verified_upto:
            verified_upto = cur_verified_positions
            stack = [(p, r) for (p, r) in stack if p >= verified_upto]

        if result.status is Status.VALID:
            events.append(SearchEvent(step=step, event="SEARCH_SUCCESS",
                                      depth=len(path), path=list(path),
                                      validation_count=validation_count))
            return finish(ReconStatus.VERIFIED, "validator returned VALID",
                          final_val=result)

        if result.status is Status.INVALID:
            picked = _pop_alternative(stack, path, used, verified_upto)
            if picked is None:
                if len(path) == 1:
                    events.append(SearchEvent(step=step, event="SEARCH_EXHAUSTED",
                                              depth=1, path=list(path),
                                              validation_count=validation_count,
                                              validator_reason=result.reason_code))
                    return finish(ReconStatus.REJECTED_ANCHOR,
                                  f"anchor prefix invalid: {result.reason_code}",
                                  final_val=result)
                events.append(SearchEvent(step=step, event="SEARCH_EXHAUSTED",
                                          depth=len(path), path=list(path),
                                          validation_count=validation_count,
                                          validator_reason=result.reason_code))
                return finish(ReconStatus.EXHAUSTED_CANDIDATES,
                              "all backtracking alternatives exhausted",
                              final_val=result)
            backtrack_count += 1
            branches_tried += 1
            max_depth = max(max_depth, len(path))
            events.append(SearchEvent(step=step, event="BACKTRACK",
                                      depth=len(path), block=picked, path=list(path),
                                      validation_count=validation_count,
                                      verified_prefix=verified_bytes))
            continue

        # INCOMPLETE: pick the next candidate and extend the path
        cands = _candidates(image, used, path[-1], window)
        ranked_full = ranker(image, path, cands)
        ranked = _dedup_by_sha(image, ranked_full)[:top_k]

        if not ranked:
            picked = _pop_alternative(stack, path, used, verified_upto)
            if picked is None:
                events.append(SearchEvent(step=step, event="SEARCH_EXHAUSTED",
                                          depth=len(path), path=list(path),
                                          validation_count=validation_count,
                                          validator_reason="no_candidates"))
                return finish(ReconStatus.EXHAUSTED_CANDIDATES,
                              "no candidates available and no backtracking possible",
                              final_val=result)
            backtrack_count += 1
            branches_tried += 1
            max_depth = max(max_depth, len(path))
            events.append(SearchEvent(step=step, event="BACKTRACK",
                                      depth=len(path), block=picked, path=list(path),
                                      validation_count=validation_count))
            continue

        chosen = ranked[0]
        remaining = list(ranked[1:])
        if remaining:
            stack.append((len(path), remaining))
        path.append(chosen)
        used.add(chosen)
        branches_tried += 1
        max_depth = max(max_depth, len(path))
        events.append(SearchEvent(step=step, event="CANDIDATE_TRIED",
                                  depth=len(path), block=chosen, path=list(path),
                                  validation_count=validation_count,
                                  ranked=list(ranked),
                                  remaining_candidates=len(remaining)))
