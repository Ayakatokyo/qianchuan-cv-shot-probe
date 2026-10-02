import json
from pathlib import Path


class FieldRegistryError(ValueError):
    pass


class FieldRegistry:
    def __init__(self, payload):
        if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
            raise FieldRegistryError("field registry schemaVersion must be 1")
        fields = payload.get("fields")
        if not isinstance(fields, list) or not fields:
            raise FieldRegistryError("field registry fields must be a nonempty list")
        self.version = str(payload.get("registryVersion") or "").strip()
        self._entries = {}
        self._display_labels = {}
        for entry in fields:
            if not isinstance(entry, dict):
                raise FieldRegistryError("field registry entry must be an object")
            field_id = str(entry.get("id") or "").strip()
            if not field_id or field_id in self._entries:
                raise FieldRegistryError("field registry has an invalid or duplicate id")
            if entry.get("kind") not in {"dimension", "metric"}:
                raise FieldRegistryError("field registry has an invalid field kind")
            label = str(entry.get("label") or "").strip()
            if not label or label in self._display_labels:
                raise FieldRegistryError("field registry has an invalid or duplicate display label")
            operators = entry.get("operators")
            if not isinstance(operators, list) or not all(isinstance(item, str) for item in operators):
                raise FieldRegistryError("field registry operators must be a string list")
            self._entries[field_id] = entry
            self._display_labels[label] = field_id

    def require(self, field_id):
        field_id = str(field_id or "").strip()
        # Query inputs may use a verified Qianchuan export label; all downstream
        # contracts retain this canonical API field ID.
        if field_id not in self._entries:
            field_id = self._display_labels.get(field_id, field_id)
        if field_id not in self._entries:
            raise FieldRegistryError("unknown field: %s" % field_id)
        return self._entries[field_id]

    def validate_operator(self, field_id, operator):
        entry = self.require(field_id)
        if operator not in entry["operators"]:
            raise FieldRegistryError("field %s does not allow %s" % (field_id, operator))
        return entry

    def entries(self):
        return tuple(self._entries.values())


def load_field_registry(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FieldRegistryError("field registry is unreadable") from exc
    return FieldRegistry(payload)
