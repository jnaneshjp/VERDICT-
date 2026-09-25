"""Deterministic reconstruction search for VERDICT (CLAUDE.md §5, §20 task 4).

Assemble a physical block path from a carved PNG anchor by consulting the PNG
validator after each extension:

    VALID      -> stop this branch, return success
    INCOMPLETE -> extend with the next ranked candidate block; advance the
                  frozen-prefix mark based on chunks whose CRCs verified
    INVALID    -> backtrack to the deepest untried decision point at or after
                  the frozen prefix; DO NOT advance the frozen prefix from a
                  failed attempt (a failing attempt may include a CRC-ok
                  ChunkCheck for the very chunk whose semantic check just
                  failed — trusting it would freeze the whole path)

Baseline ranker uses physical locality only. No ML. No ground-source data.
The Ranker interface follows CLAUDE.md §10 so a later ML ranker can drop in
without touching this file.
"""
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

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
    """Structured outcome of one reconstruct_png() call.

    `path` follows CLAUDE.md §5: for VERIFIED the full assembled path, and for
    any other outcome only the frozen-prefix positions (`path[:verified_upto]`)
    so consumers never treat unverified blocks as evidence. The full attempted
    path at termination is kept in `search_path` for diagnostics/UI.
    """
    status: ReconStatus
    reason: str
    anchor_byte_offset: int
    anchor_block: int
    path: List[int]
    search_path: List[int] = field(default_factory=list)
    verified_upto: int = 0
    reconstructed_bytes: Optional[bytes] = None
    reconstructed_length: int = 0
    reconstructed_sha256: Optional[str] = None
    validation_count: int = 0
    max_depth: int = 0
    backtrack_count: int = 0
    branches_tried: int = 0
    final_validator: Optional[PNGValidationResult] = None
    events: List[SearchEvent] = field(default_factory=list)


# ---------------------------------------------------------------- rankers

class Ranker:
    """Candidate-order contract (CLAUDE.md §10). MLRanker will subclass this."""

    def order(self, path: List[int], candidates: List[int]) -> List[int]:
        raise NotImplementedError


class BaselineRanker(Ranker):
    """Deterministic physical-locality baseline (Task 4).

    Not a probability. Sorts by (absolute distance from path tail, block index)
    so ties break on the lower block number.
    """

    def __init__(self, image: Image):
        self.image = image

    def order(self, path: List[int], candidates: List[int]) -> List[int]:
        current = path[-1]
        return sorted(candidates, key=lambda b: (abs(b - current), b))


# ------------------------------------------------------------ byte helpers

def _bytes_of_path(image: Image, path: List[int], anchor_offset: int) -> bytes:
    """Anchor prefix (from anchor_offset to end of anchor block) + full subsequent blocks."""
    bs = image.block_size
    anchor_block = path[0]
    anchor_end = anchor_block * bs + image.blocks[anchor_block].length
    parts = [image.raw[anchor_offset:anchor_end]]
    for block in path[1:]:
        start = block * bs
        parts.append(image.raw[start:start + image.blocks[block].length])
    return b"".join(parts)


def _cumulative_lengths(image: Image, path: List[int], anchor_offset: int) -> List[int]:
    """Byte total contributed after each path position (inclusive)."""
    bs = image.block_size
    anchor_block = path[0]
    anchor_len = image.blocks[anchor_block].length
    offset_within = anchor_offset - anchor_block * bs
    lens = [anchor_len - offset_within]
    for block in path[1:]:
        lens.append(lens[-1] + image.blocks[block].length)
    return lens


def _verified_prefix_bytes(result: PNGValidationResult) -> int:
    """Byte offset up to which the attempt has cleared PNG format checks."""
    if result.status is Status.INVALID and result.reason_code in (
            "signature_mismatch", "signature_incomplete"):
        return 0
    verified = len(PNG_SIGNATURE)
    for check in result.chunk_checks:
        if check.ok:
            verified = check.offset + 8 + check.length + 4
    return verified


def _verified_positions(cum_lens: List[int], verified_bytes: int) -> int:
    """Number of leading path positions entirely inside the verified byte range."""
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


# ---------------------------------------------------------------- search

def reconstruct_png(image: Image,
                    anchor: Anchor,
                    *,
                    budget: int = BUDGET,
                    top_k: int = TOP_K,
                    window: int = WINDOW,
                    ranker: Optional[Ranker] = None) -> ReconstructionResult:
    """Deterministic validator-guided reconstruction from a PNG anchor."""
    if ranker is None:
        ranker = BaselineRanker(image)
    return _Search(image, anchor, budget, top_k, window, ranker).run()


class _Search:
    """Mutable search state for one reconstruct_png() call.

    Split into small named steps (see run()); this makes the state machine
    walkable by a human reader and matches CLAUDE.md §21's readability goal.
    """

    def __init__(self, image: Image, anchor: Anchor,
                 budget: int, top_k: int, window: int, ranker: Ranker):
        self.image = image
        self.anchor = anchor
        self.budget = budget
        self.top_k = top_k
        self.window = window
        self.ranker = ranker

        self.anchor_block = anchor.block_index
        self.anchor_offset = anchor.byte_offset
        self.path: List[int] = [self.anchor_block]
        self.used: set = {self.anchor_block}
        self.stack: List[Tuple[int, List[int]]] = []
        self.verified_upto = 0
        self.validation_count = 0
        self.backtrack_count = 0
        self.branches_tried = 1
        self.max_depth = 1
        self.events: List[SearchEvent] = []
        self.step = 0
        self._log("SEARCH_START", block=self.anchor_block)

    # ---- main loop --------------------------------------------------------

    def run(self) -> ReconstructionResult:
        while True:
            self.step += 1
            if self.validation_count >= self.budget:
                self._log("SEARCH_EXHAUSTED", reason="budget_exhausted")
                return self._finish(ReconStatus.EXHAUSTED_BUDGET,
                                    f"validation budget of {self.budget} exhausted")

            result = self._validate_current()
            self._log("VALIDATION_RESULT",
                      status=result.status.value,
                      reason=result.reason_code,
                      verified_prefix=_verified_prefix_bytes(result))

            if result.status is Status.VALID:
                # Whole reconstruction is verified evidence; the final block's
                # post-IEND bytes are slack (trimmed by consumed_length) but the
                # block itself is part of the recovered artifact.
                self.verified_upto = len(self.path)
                self._log("SEARCH_SUCCESS")
                return self._finish(ReconStatus.VERIFIED,
                                    "validator returned VALID",
                                    final_val=result)

            if result.status is Status.INVALID:
                if self._try_backtrack():
                    continue
                if len(self.path) == 1:
                    self._log("SEARCH_EXHAUSTED", reason=result.reason_code)
                    return self._finish(ReconStatus.REJECTED_ANCHOR,
                                        f"anchor prefix invalid: {result.reason_code}",
                                        final_val=result)
                self._log("SEARCH_EXHAUSTED", reason=result.reason_code)
                return self._finish(ReconStatus.EXHAUSTED_CANDIDATES,
                                    "all backtracking alternatives exhausted",
                                    final_val=result)

            # INCOMPLETE
            self._advance_frozen_prefix(result)
            if self._extend():
                continue
            if self._try_backtrack():
                continue
            self._log("SEARCH_EXHAUSTED", reason="no_candidates")
            return self._finish(ReconStatus.EXHAUSTED_CANDIDATES,
                                "no candidates available and no backtracking possible",
                                final_val=result)

    # ---- named steps ------------------------------------------------------

    def _validate_current(self) -> PNGValidationResult:
        candidate_bytes = _bytes_of_path(self.image, self.path, self.anchor_offset)
        result = validate_png(candidate_bytes)
        self.validation_count += 1
        return result

    def _advance_frozen_prefix(self, result: PNGValidationResult) -> None:
        """Freeze positions whose bytes lie inside a successful CRC chunk.

        Only called on INCOMPLETE outcomes: on INVALID the failing attempt may
        include a CRC-ok ChunkCheck for the chunk whose semantic check just
        failed (e.g. IEND with valid CRC but zlib inflate error), which would
        wrongly freeze the whole path and defeat backtracking.
        """
        verified_bytes = _verified_prefix_bytes(result)
        cum = _cumulative_lengths(self.image, self.path, self.anchor_offset)
        positions = _verified_positions(cum, verified_bytes)
        if positions > self.verified_upto:
            self.verified_upto = positions
            self.stack = [(p, r) for (p, r) in self.stack if p >= self.verified_upto]

    def _extend(self) -> bool:
        cands = _candidates(self.image, self.used, self.path[-1], self.window)
        ranked_full = self.ranker.order(self.path, cands)
        ranked = _dedup_by_sha(self.image, ranked_full)[:self.top_k]
        if not ranked:
            return False
        chosen = ranked[0]
        remaining = list(ranked[1:])
        if remaining:
            self.stack.append((len(self.path), remaining))
        self.path.append(chosen)
        self.used.add(chosen)
        self.branches_tried += 1
        self.max_depth = max(self.max_depth, len(self.path))
        self._log("CANDIDATE_TRIED", block=chosen, ranked=list(ranked),
                  remaining_candidates=len(remaining))
        return True

    def _try_backtrack(self) -> bool:
        picked = _pop_alternative(self.stack, self.path, self.used, self.verified_upto)
        if picked is None:
            return False
        self.backtrack_count += 1
        self.branches_tried += 1
        self.max_depth = max(self.max_depth, len(self.path))
        self._log("BACKTRACK", block=picked)
        return True

    # ---- housekeeping -----------------------------------------------------

    def _log(self, event: str, **kw) -> None:
        self.events.append(SearchEvent(
            step=self.step,
            event=event,
            depth=len(self.path),
            path=list(self.path),
            validation_count=self.validation_count,
            block=kw.get("block"),
            validator_status=kw.get("status"),
            validator_reason=kw.get("reason"),
            verified_prefix=kw.get("verified_prefix"),
            verified_positions=self.verified_upto,
            ranked=kw.get("ranked", []),
            remaining_candidates=kw.get("remaining_candidates", 0),
        ))

    def _finish(self, status: ReconStatus, reason: str,
                final_val: Optional[PNGValidationResult] = None) -> ReconstructionResult:
        recon_bytes = None
        recon_len = 0
        recon_sha = None
        if status is ReconStatus.VERIFIED and final_val is not None:
            full = _bytes_of_path(self.image, self.path, self.anchor_offset)
            recon_bytes = full[:final_val.consumed_length]
            recon_len = len(recon_bytes)
            recon_sha = hashlib.sha256(recon_bytes).hexdigest()
            reported_path = list(self.path)
        else:
            reported_path = list(self.path[:self.verified_upto])
        return ReconstructionResult(
            status=status,
            reason=reason,
            anchor_byte_offset=self.anchor_offset,
            anchor_block=self.anchor_block,
            path=reported_path,
            search_path=list(self.path),
            verified_upto=self.verified_upto,
            reconstructed_bytes=recon_bytes,
            reconstructed_length=recon_len,
            reconstructed_sha256=recon_sha,
            validation_count=self.validation_count,
            max_depth=self.max_depth,
            backtrack_count=self.backtrack_count,
            branches_tried=self.branches_tried,
            final_validator=final_val,
            events=self.events,
        )
