import math


class HighlightPolicyError(ValueError):
    pass


SUPPORTED_METRICS = (
    "CLICK_COUNT", "LOSE_COUNT", "LIKE_COUNT", "COMMENT_COUNT", "SHARE_COUNT", "NEW_FOLLOWER_COUNT",
)
_MODE_ALIASES = {"global_maximum": "global_top_values"}
_SUPPORTED_MODES = {"local_peaks", "global_top_values"}


def _number(value, label):
    if isinstance(value, bool):
        raise HighlightPolicyError("%s must be a finite number" % label)
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise HighlightPolicyError("%s must be a finite number" % label) from exc
    if not math.isfinite(value) or value < 0:
        raise HighlightPolicyError("%s must be a finite non-negative number" % label)
    return value


def normalize_highlight_policy(raw):
    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        raise HighlightPolicyError("highlight policy schemaVersion must be 1")
    include_zero = raw.get("includeZeroSecond", True)
    if type(include_zero) is not bool:
        raise HighlightPolicyError("includeZeroSecond must be a boolean")
    policies = raw.get("policies")
    if not isinstance(policies, list):
        raise HighlightPolicyError("highlight policy policies must be a list")
    if not policies and not include_zero:
        raise HighlightPolicyError("highlight policy requires a policy when includeZeroSecond is false")
    normalized = []
    seen = set()
    for item in policies:
        if not isinstance(item, dict):
            raise HighlightPolicyError("highlight policy entry must be an object")
        metric = str(item.get("metric") or "").strip()
        mode = str(item.get("mode") or "").strip()
        mode = _MODE_ALIASES.get(mode, mode)
        max_frames = item.get("maxFrames")
        if metric not in SUPPORTED_METRICS:
            raise HighlightPolicyError("unsupported interaction metric: %s" % metric)
        if metric in seen:
            raise HighlightPolicyError("duplicate interaction metric policy: %s" % metric)
        if mode not in _SUPPORTED_MODES:
            raise HighlightPolicyError("unsupported highlight selection mode")
        if type(max_frames) is not int or max_frames <= 0:
            raise HighlightPolicyError("maxFrames is invalid for highlight selection mode")
        seen.add(metric)
        normalized.append({"metric": metric, "mode": mode, "maxFrames": max_frames})
    return {"schemaVersion": 1, "includeZeroSecond": include_zero, "policies": normalized}


def _series(records, metric):
    values = {}
    for record in records:
        if not isinstance(record, dict) or record.get("recordType") != "interaction_series" or record.get("interactionMetric") != metric:
            continue
        second = _number(record.get("durationSec"), "durationSec")
        value = _number(record.get("metricValue"), "metricValue")
        if second in values and values[second] != value:
            raise HighlightPolicyError("conflicting %s values at second %s" % (metric, second))
        values[second] = value
    return sorted(values.items())


def _local_peaks(points):
    plateaus = []
    for second, value in points:
        if plateaus and plateaus[-1][1] == value:
            continue
        plateaus.append((second, value))
    return [
        point for index, point in enumerate(plateaus)
        if 0 < index < len(plateaus) - 1 and point[1] > plateaus[index - 1][1] and point[1] > plateaus[index + 1][1]
    ]


def select_highlight_frames(records, raw_policy):
    policy = normalize_highlight_policy(raw_policy)
    by_second = {}
    unavailable = []
    if policy["includeZeroSecond"]:
        by_second[0.0] = {"durationSec": 0.0, "selectionEvidence": [{"metric": "ZERO_SECOND", "mode": "zero_second", "policyRank": 0, "durationSec": 0.0, "metricValue": None}]}
    for policy_rank, item in enumerate(policy["policies"], start=1):
        points = _series(records, item["metric"])
        if not points:
            unavailable.append({"metric": item["metric"], "reason": "missing_interaction_series"})
            continue
        if item["mode"] == "local_peaks":
            selected = sorted(_local_peaks(points), key=lambda point: (-point[1], point[0]))[:item["maxFrames"]]
        else:
            selected = sorted(points, key=lambda point: (-point[1], point[0]))[:item["maxFrames"]]
        for index, (second, value) in enumerate(selected, start=1):
            frame = by_second.setdefault(second, {"durationSec": second, "selectionEvidence": []})
            frame["selectionEvidence"].append({"metric": item["metric"], "mode": item["mode"], "policyRank": policy_rank, "selectionRank": index, "durationSec": second, "metricValue": value})
    frames = [by_second[second] for second in sorted(by_second)]
    if not frames:
        raise HighlightPolicyError("no highlight frames were selected")
    return {"policy": policy, "frames": frames, "unavailableMetrics": unavailable}
