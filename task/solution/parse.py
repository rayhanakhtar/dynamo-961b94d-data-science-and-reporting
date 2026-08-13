"""Independent readers for the four operational source artifacts.

Each reader returns raw rows that carry the same logical event fields, plus a
source-provenance tag. The directory readers return canonical mapping
dictionaries. Nothing in this module depends on the starter implementation.
"""

import csv
import json


def read_scheduling_events(path):
    """Read the core reservation/revision feed (CSV).

    Returns a list of raw row dicts, each tagged with ``feed`` = "SCHEDULING".
    """
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [dict(row, feed="SCHEDULING") for row in rows]


def read_reservation_adjustments(path):
    """Read the late adjustment/correction/release feed (JSON Lines).

    Returns a list of raw row dicts, each tagged with ``feed`` = "ADJUSTMENT".
    """
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            rows.append(dict(obj, feed="ADJUSTMENT"))
    return rows


def read_clinic_directory(path):
    """Read canonical clinic aliases: {alias: canonical_clinic_id}."""
    mapping = {}
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            alias = row["clinic_alias"].strip().upper()
            canonical = row["canonical_clinic_id"].strip().upper()
            mapping[alias] = canonical
    return mapping


def read_room_directory(path):
    """Read clinic-scoped room aliases: {(clinic, alias): canonical_room_id}."""
    mapping = {}
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            clinic = row["clinic_id"].strip().upper()
            alias = row["room_alias"].strip().upper()
            canonical = row["canonical_room_id"].strip().upper()
            mapping[(clinic, alias)] = canonical
    return mapping
