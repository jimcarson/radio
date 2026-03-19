"""
reconcile_adif.py
=================
Compares a LoTW ADIF export against a QRZ ADIF export for the same callsign
and identifies (and optionally corrects) field-level discrepancies.

Only LoTW-confirmed QSOs are compared (APP_LOTW_2XQSL=Y or QSL_RCVD=Y).
The LoTW export should be filtered to confirmed QSOs before running this
script (use LoTW's "Queried QSOs" filter with QSL Rcvd = Yes).

Fields compared (configurable per field via <CALLSIGN>.cfg):
    GRIDSQUARE, COUNTRY, DXCC, CQZ, ITUZ, MODE,
    STATE, CNTY (US contacts only, non-numeric values only),
    MY_COUNTRY, MY_CQ_ZONE, MY_ITU_ZONE, MY_DXCC, MY_STATE, MY_CNTY,
    APP_LOTW_RXQSL

Rules per field (set in config file):
    lotw_wins  : Apply LoTW value to QRZ record
    fill_blank : Only apply if QRZ field is blank
    flag_only  : Report difference but do not correct
    skip       : Ignore this field entirely

Usage
-----
    # Compare only — produce report and corrected ADIF for manual import
    python reconcile_adif.py \\
        --lotw  lotw_export.adi \\
        --qrz   qrz_export.adi \\
        --call  WT8P \\
        [--config WT8P.cfg]

    # Compare and push corrections directly to QRZ via API
    python reconcile_adif.py \\
        --lotw  lotw_export.adi \\
        --qrz   qrz_export.adi \\
        --call  WT8P \\
        --update-qrz \\
        [--dry-run]

Requirements
------------
    pip install requests

See qrz_common.py for shared logic.
Config file format: see load_field_rules() in qrz_common.py.

2026-03-19 Jim Carson (WT8P)
"""

import argparse
import csv
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import qrz_common as qrz

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fields to compare (ordered for report readability)
# ---------------------------------------------------------------------------
COMPARE_FIELDS = [
    "GRIDSQUARE",
    "COUNTRY",
    "DXCC",
    "CQZ",
    "ITUZ",
    "MODE",
    "STATE",
    "CNTY",
    "MY_COUNTRY",
    "MY_CQ_ZONE",
    "MY_ITU_ZONE",
    "MY_DXCC",
    "MY_STATE",
    "MY_CNTY",
    "APP_LOTW_RXQSL",
]

# LoTW field names that map to different names in QRZ
LOTW_FIELD_MAP = {
    "APP_LOTW_RXQSL": "LOTW_QSL_RCVD",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FieldChange:
    field:      str
    lotw_value: str
    qrz_value:  str
    rule:       str    # lotw_wins | fill_blank | flag_only | skip
    action:     str    # corrected | flagged | skipped | no_change


@dataclass
class RecordResult:
    call:      str
    qso_date:  str
    time_on:   str
    band:      str
    mode:      str
    logid:     str
    changes:   list[FieldChange] = field(default_factory=list)
    status:    str = "ok"        # ok | no_match | updated | dry_run | error
    error_msg: str = ""

    @property
    def has_corrections(self) -> bool:
        return any(c.action == "corrected" for c in self.changes)

    @property
    def has_flags(self) -> bool:
        return any(c.action == "flagged" for c in self.changes)


# ---------------------------------------------------------------------------
# LoTW confirmation check
# ---------------------------------------------------------------------------

def is_lotw_confirmed(rec: dict) -> bool:
    """Return True if this record represents a confirmed LoTW QSO."""
    return (
        rec.get("APP_LOTW_2XQSL", "").upper() == "Y"
        or rec.get("QSL_RCVD", "").upper() == "Y"
    )


# ---------------------------------------------------------------------------
# Field-level comparison
# ---------------------------------------------------------------------------

def compare_field(field_name: str, lotw_rec: dict, qrz_rec: dict,
                  rule: str) -> Optional[FieldChange]:
    """
    Compare one field between LoTW and QRZ records.
    Returns a FieldChange if there is anything to report, else None.
    """
    if rule == "skip":
        return None

    # Map LoTW field name to the QRZ equivalent where they differ
    qrz_field = LOTW_FIELD_MAP.get(field_name, field_name)

    lotw_val = lotw_rec.get(field_name, "").strip()
    qrz_val  = qrz_rec.get(qrz_field, "").strip()

    # STATE/CNTY: only compare for US contacts, skip foreign numeric codes
    if field_name in qrz.US_ONLY_FIELDS:
        if lotw_rec.get("DXCC", "").strip() != qrz.US_DXCC:
            return None
        if qrz.is_numeric_only(lotw_val):
            return None

    # Nothing to offer if LoTW has no value
    if not lotw_val:
        return None

    # Gridsquare: compare only first 4 characters (QRZ may have more precision)
    if field_name in ("GRIDSQUARE", "MY_GRIDSQUARE"):
        match = (lotw_val.upper()[:4] == qrz_val.upper()[:4])
    elif field_name == "MY_COUNTRY":
        match = (qrz.normalise_my_country(lotw_val).upper() == qrz_val.upper())
    else:
        match = qrz.fields_match(lotw_val, qrz_val, field_name)

    if match:
        return None  # values agree

    # Determine action
    if rule == "flag_only":
        action = "flagged"
    elif rule == "fill_blank":
        action = "corrected" if not qrz_val else "flagged"
    else:  # lotw_wins
        action = "corrected"

    return FieldChange(
        field=field_name,
        lotw_value=lotw_val,
        qrz_value=qrz_val,
        rule=rule,
        action=action,
    )


# ---------------------------------------------------------------------------
# Core reconciliation
# ---------------------------------------------------------------------------

def reconcile(
    lotw_records: list[dict],
    qrz_records:  list[dict],
    callsign:     str,
    field_rules:  dict[str, str],
) -> list[RecordResult]:
    """
    Match LoTW confirmed records against QRZ records and compare fields.
    Returns one RecordResult per LoTW confirmed record for this callsign.
    """
    qrz_index = qrz.build_index(qrz_records)

    lotw_filtered = [
        r for r in lotw_records
        if r.get("STATION_CALLSIGN", "").upper().strip() == callsign.upper().strip()
        and is_lotw_confirmed(r)
    ]
    log.info("LoTW: %d confirmed records for %s", len(lotw_filtered), callsign.upper())

    results:   list[RecordResult] = []
    unmatched: int = 0

    for lotw_rec in lotw_filtered:
        key = qrz.make_key(lotw_rec)
        if key is None:
            continue

        qrz_rec = qrz_index.get(key)
        if qrz_rec is None:
            unmatched += 1
            results.append(RecordResult(
                call=key[0], qso_date=key[1], time_on=key[2],
                band=lotw_rec.get("BAND", ""),
                mode=lotw_rec.get("MODE", ""),
                logid="", status="no_match",
            ))
            continue

        logid = qrz_rec.get("APP_QRZLOG_LOGID", "")
        if logid:
            try:
                logid = str(int(float(logid)))
            except ValueError:
                pass

        rr = RecordResult(
            call=key[0], qso_date=key[1], time_on=key[2],
            band=lotw_rec.get("BAND", ""),
            mode=lotw_rec.get("MODE", ""),
            logid=logid,
        )

        for fname in COMPARE_FIELDS:
            rule = field_rules.get(fname, "skip")
            fc   = compare_field(fname, lotw_rec, qrz_rec, rule)
            if fc:
                rr.changes.append(fc)

        results.append(rr)

    corrections = sum(1 for r in results if r.has_corrections)
    flags       = sum(1 for r in results if r.has_flags and not r.has_corrections)
    log.info(
        "Results: %d matched | %d with corrections | %d flagged only | %d unmatched",
        len(results) - unmatched, corrections, flags, unmatched,
    )
    return results


# ---------------------------------------------------------------------------
# Apply corrections to QRZ via API
# ---------------------------------------------------------------------------

def apply_corrections(
    results:   list[RecordResult],
    qrz_index: dict[tuple, dict],
    api_key:   str,
    callsign:  str,
    dry_run:   bool,
) -> None:
    """Push corrected records to QRZ via ACTION=INSERT OPTION=REPLACE."""
    to_update = [r for r in results if r.has_corrections and r.logid]
    log.info("Applying %d record corrections to QRZ%s…",
             len(to_update), " (DRY RUN)" if dry_run else "")

    for rr in to_update:
        key     = (rr.call, rr.qso_date, rr.time_on)
        qrz_rec = qrz_index.get(key)
        if qrz_rec is None:
            log.warning("QRZ record not found for update: %s", key)
            rr.status    = "error"
            rr.error_msg = "QRZ record not found for update"
            continue

        updated = dict(qrz_rec)
        for fc in rr.changes:
            if fc.action == "corrected":
                qrz_field = LOTW_FIELD_MAP.get(fc.field, fc.field)
                updated[qrz_field] = fc.lotw_value

        if dry_run:
            for fc in rr.changes:
                if fc.action == "corrected":
                    log.info("[DRY-RUN] logid=%-8s  %-12s  %s: %r -> %r",
                             rr.logid, rr.call, fc.field,
                             fc.qrz_value, fc.lotw_value)
            rr.status = "dry_run"
            continue

        try:
            result = qrz.qrz_replace(api_key, callsign, updated,
                                      user_agent="QRZReconcile/1.0")
            if result.get("RESULT") == "REPLACE":
                for fc in rr.changes:
                    if fc.action == "corrected":
                        log.info("Replaced logid=%-8s  %-12s  %s: %r -> %r",
                                 rr.logid, rr.call, fc.field,
                                 fc.qrz_value, fc.lotw_value)
                rr.status = "updated"
            else:
                log.error("Unexpected result logid=%s: %s", rr.logid, result)
                rr.status    = "error"
                rr.error_msg = f"Unexpected RESULT: {result.get('RESULT')}"
        except RuntimeError as exc:
            log.error("Failed logid=%s: %s", rr.logid, exc)
            rr.status    = "error"
            rr.error_msg = str(exc)

        time.sleep(qrz.API_PAUSE_SEC)


# ---------------------------------------------------------------------------
# ADIF output (corrected records for manual import)
# ---------------------------------------------------------------------------

def build_corrected_adif(
    results:   list[RecordResult],
    qrz_index: dict[tuple, dict],
) -> list[dict]:
    """Return corrected QRZ records as dicts, ready for write_adif_file()."""
    corrected = []
    for rr in results:
        if not rr.has_corrections:
            continue
        key     = (rr.call, rr.qso_date, rr.time_on)
        qrz_rec = qrz_index.get(key)
        if qrz_rec is None:
            continue
        updated = dict(qrz_rec)
        for fc in rr.changes:
            if fc.action == "corrected":
                qrz_field = LOTW_FIELD_MAP.get(fc.field, fc.field)
                updated[qrz_field] = fc.lotw_value
        corrected.append(updated)
    return corrected


# ---------------------------------------------------------------------------
# CSV report
# ---------------------------------------------------------------------------

CSV_HEADERS = [
    "call", "qso_date", "time_on", "band", "mode", "logid",
    "field", "lotw_value", "qrz_value", "rule", "action",
    "record_status", "error_msg",
]


def write_csv_report(results: list[RecordResult], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for rr in results:
            # Always write no_match and error records even with no field changes
            if not rr.changes and rr.status == "ok":
                continue
            if rr.changes:
                for fc in rr.changes:
                    writer.writerow({
                        "call":          rr.call,
                        "qso_date":      rr.qso_date,
                        "time_on":       rr.time_on,
                        "band":          rr.band,
                        "mode":          rr.mode,
                        "logid":         rr.logid,
                        "field":         fc.field,
                        "lotw_value":    fc.lotw_value,
                        "qrz_value":     fc.qrz_value,
                        "rule":          fc.rule,
                        "action":        fc.action,
                        "record_status": rr.status,
                        "error_msg":     rr.error_msg,
                    })
            else:
                writer.writerow({
                    "call":          rr.call,
                    "qso_date":      rr.qso_date,
                    "time_on":       rr.time_on,
                    "band":          rr.band,
                    "mode":          rr.mode,
                    "logid":         rr.logid,
                    "field":         "",
                    "lotw_value":    "",
                    "qrz_value":     "",
                    "rule":          "",
                    "action":        "",
                    "record_status": rr.status,
                    "error_msg":     rr.error_msg,
                })

    # Summary
    actions:  dict[str, int] = {}
    statuses: dict[str, int] = {}
    for rr in results:
        statuses[rr.status] = statuses.get(rr.status, 0) + 1
        for fc in rr.changes:
            actions[fc.action] = actions.get(fc.action, 0) + 1

    log.info("Report written to %s", path)
    for action, n in sorted(actions.items()):
        log.info("  %-20s : %d field changes", action, n)
    for status, n in sorted(statuses.items()):
        if status != "ok":
            log.info("  %-20s : %d records", status, n)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Reconcile LoTW and QRZ ADIF exports, with optional QRZ update."
    )
    p.add_argument("--lotw", required=True,
        help="LoTW ADIF export. Should contain only confirmed QSOs "
             "(export from LoTW with QSL Rcvd = Yes filter).")
    p.add_argument("--qrz", required=True,
        help="QRZ ADIF export for this callsign (must contain APP_QRZLOG_LOGID).")
    p.add_argument("--call", required=True,
        help="Callsign to process — filters LoTW export by STATION_CALLSIGN "
             "(e.g. WT8P or TF/WT8P).")
    p.add_argument("--config", default=None,
        help="Path to field rules config file. Defaults to <CALLSIGN>.cfg "
             "in current directory (/ replaced by _ e.g. TF_WT8P.cfg).")
    p.add_argument("--update-qrz", action="store_true",
        help="Push corrections to QRZ via API (requires --key or <CALLSIGN>.key).")
    p.add_argument("--key", default=None,
        help="QRZ API key. Optional if <CALLSIGN>.key file exists "
             "(use _ for / e.g. TF_WT8P.key).")
    p.add_argument("--dry-run", action="store_true",
        help="Preview corrections without writing to QRZ (implies --update-qrz).")
    p.add_argument("--output-adif", default="corrected_qrz.adi",
        help="Corrected ADIF for manual re-import (default: corrected_qrz.adi).")
    p.add_argument("--output-csv", default="reconciliation_report.csv",
        help="CSV report (default: reconciliation_report.csv).")
    return p


def main() -> None:
    args      = build_parser().parse_args()
    lotw_path = Path(args.lotw)
    qrz_path  = Path(args.qrz)
    out_adif  = Path(args.output_adif)
    out_csv   = Path(args.output_csv)
    cfg_path  = Path(args.config) if args.config else None

    for p in (lotw_path, qrz_path):
        if not p.exists():
            log.error("File not found: %s", p)
            sys.exit(1)

    log.info("=== LoTW / QRZ Reconciliation ===")
    log.info("LoTW     : %s", lotw_path)
    log.info("QRZ      : %s", qrz_path)
    log.info("Callsign : %s", args.call.upper())
    log.info("Update   : %s", args.update_qrz or args.dry_run)
    log.info("Dry run  : %s", args.dry_run)

    field_rules  = qrz.load_field_rules(args.call, cfg_path)
    lotw_records = qrz.parse_adif_file(lotw_path)
    qrz_records  = qrz.parse_adif_file(qrz_path)
    qrz_index    = qrz.build_index(qrz_records)
    log.info("QRZ index built with %d entries.", len(qrz_index))

    results = reconcile(lotw_records, qrz_records, args.call, field_rules)

    if args.update_qrz or args.dry_run:
        api_key = qrz.load_api_key(args.key, args.call)
        apply_corrections(results, qrz_index, api_key,
                          args.call.upper(), args.dry_run)

    corrected = build_corrected_adif(results, qrz_index)
    if corrected:
        qrz.write_adif_file(corrected, out_adif)
        log.info("Re-import %s via QRZ Settings -> ADIF Import.", out_adif)
    else:
        log.info("No corrections to write — %s not created.", out_adif)

    write_csv_report(results, out_csv)
    log.info("=== Done. ===")


if __name__ == "__main__":
    main()
