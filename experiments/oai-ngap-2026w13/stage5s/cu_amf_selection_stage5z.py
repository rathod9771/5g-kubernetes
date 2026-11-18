#!/usr/bin/env python3
"""Bounded, category-only CU AMF-selection observer for disposable RFsim.

This matches a CU RRCSetupComplete to a nearby CU selection result by
log adjacency, not UE identity. Never exports UE IDs, RNTI, cell subscriber
identifiers, PLMN values, NAS messages or raw log lines. Association is a
hypothesis, *not* wire-delivery or per-subscriber proof.
"""
import re

RRC_SETUP = re.compile(r'\[UL\]\s*\(cellID\s+(\d+),\s*UE ID\s+\d+[^\n]*?\)\s*Received RRCSetupComplete', re.I)
NO_MATCH = re.compile(r'No matching AMF found for PLMN', re.I)
NO_ASSOC = re.compile(r'No AMF is associated to the gNB', re.I)
SELECTED = re.compile(r"\bSelected AMF\s+'", re.I)
WINDOW_LINES = 5
ALLOWED_CELLS = {3804001, 3804002}


class CUSelectionObserver:
    """Stateful and memory-bounded filter. Returns categories and cell labels."""
    def __init__(self):
        self.pending_cell = None
        self.remaining = 0

    def process(self, line):
        line = line[:4096]
        result = []
        # The no-association line occurs after a no-match warning. Count it
        # globally, without claiming that it is attributable to a specific UE.
        if NO_ASSOC.search(line):
            result.append(("cu_amf_no_association", {}))
        match = RRC_SETUP.search(line)
        if match:
            cell = int(match.group(1))
            self.pending_cell = cell if cell in ALLOWED_CELLS else None
            self.remaining = WINDOW_LINES if self.pending_cell is not None else 0
            return result
        if NO_MATCH.search(line):
            meta = {"cell_id":self.pending_cell,"correlation":"adjacent_cu_log_only"} if self.pending_cell is not None and self.remaining else {}
            result.append(("cu_amf_selection_no_match",meta))
            self.pending_cell = None
            self.remaining = 0
            return result
        if SELECTED.search(line):
            meta = {"cell_id":self.pending_cell,"correlation":"adjacent_cu_log_only"} if self.pending_cell is not None and self.remaining else {}
            result.append(("cu_amf_selection_selected",meta))
            self.pending_cell = None
            self.remaining = 0
            return result
        if self.remaining:
            self.remaining -= 1
            if self.remaining == 0:
                self.pending_cell = None
        return result
