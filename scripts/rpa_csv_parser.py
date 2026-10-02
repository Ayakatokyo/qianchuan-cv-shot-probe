"""Parse the unit-separated CSV returned by the Qianchuan RPA diagnosis."""

import ast
import csv
import io
import json
import math
from pathlib import Path


FIELD_DELIMITER = "\x01"
RECORD_PREFIX = "\x02"
STRUCTURED_FIELDS = ("Dimensions", "Metrics", "Fields")


class RpaCsvError(ValueError):
    pass


def _error(source, row_number, message):
    return RpaCsvError("RPA CSV %s, row %s: %s" % (source, row_number, message))


def _number(value, field_name, source, row_number):
    if value in (None, ""):
        return value
    if isinstance(value, bool):
        raise _error(source, row_number, "%s must be a finite number" % field_name)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _error(source, row_number, "%s must be a finite number" % field_name) from exc
    if not math.isfinite(number):
        raise _error(source, row_number, "%s must be a finite number" % field_name)
    return number


def _structured(value, field_name, source, row_number):
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError, TypeError) as exc:
            raise _error(source, row_number, "%s must be a structured object" % field_name) from exc
    if not isinstance(parsed, dict):
        raise _error(source, row_number, "%s must be a structured object" % field_name)
    return parsed


def _normalize_crowd_metric(record, source, row_number):
    if record.get("recordType") != "crowd_analysis" or record.get("displayMetric") != "SHOW_COUNT" or record.get("metricValue") not in (None, ""):
        return
    values = []
    for metric in record.get("Metrics", {}).values():
        if isinstance(metric, dict) and metric.get("Value") not in (None, ""):
            values.append(_number(metric["Value"], "Metrics.Value", source, row_number))
    if not values:
        raise _error(source, row_number, "SHOW_COUNT crowd record has no numeric Metrics.Value")
    if len(values) > 1:
        raise _error(source, row_number, "multiple numeric Metrics.Value entries")
    record["metricValue"] = values[0]


def parse_diagnosis_csv(path):
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise RpaCsvError("unable to read RPA CSV %s" % source) from exc
    if FIELD_DELIMITER not in text.partition("\n")[0]:
        raise RpaCsvError("RPA CSV %s must use the \\x01 field delimiter" % source)
    text = text.removeprefix(RECORD_PREFIX).replace("\n" + RECORD_PREFIX, "\n")
    reader = csv.DictReader(io.StringIO(text), delimiter=FIELD_DELIMITER)
    if not reader.fieldnames or any(not name for name in reader.fieldnames):
        raise RpaCsvError("RPA CSV %s has an invalid header" % source)
    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise RpaCsvError("RPA CSV %s has duplicate header names" % source)
    records = []
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            raise _error(source, row_number, "has more values than header columns")
        if not any(value not in (None, "") for value in row.values()):
            continue
        record = dict(row)
        for field in STRUCTURED_FIELDS:
            if field in record:
                record[field] = _structured(record[field], field, source, row_number)
        for field in ("durationSec", "metricValue"):
            if field in record:
                record[field] = _number(record[field], field, source, row_number)
        _normalize_crowd_metric(record, source, row_number)
        records.append(record)
    if not records:
        raise RpaCsvError("RPA CSV %s has no records" % source)
    return records
