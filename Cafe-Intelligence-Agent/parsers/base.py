"""
base.py — the one normalized schema every source lands in.

Every parser (csv / excel / email / json) must return a ParseResult.
Downstream code (cleaning, analysts) never touches raw files again —
they only ever see NormalizedRecord objects. This is what makes
"add a 7th source = add a node + config entry" true.
"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class NormalizedRecord:
    source: str                 # e.g. "pos_transactions"
    record_id: str              # stable id, unique within the source
    date: Optional[str]         # ISO "YYYY-MM-DD", None if unparsable
    time: Optional[str] = None  # "HH:MM:SS", None if not applicable
    attrs: dict = field(default_factory=dict)   # source-specific normalized fields
    raw: dict = field(default_factory=dict)     # original row, for audit / debugging


@dataclass
class ParseResult:
    source: str
    records: list[NormalizedRecord] = field(default_factory=list)
    rows_in: int = 0            # rows read from the raw file
    rows_out: int = 0           # records successfully normalized
    errors: list[str] = field(default_factory=list)   # row-level or file-level errors
    fatal_error: Optional[str] = None   # set if the whole source failed (e.g. corrupt file)

    @property
    def ok(self) -> bool:
        return self.fatal_error is None
