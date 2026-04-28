"""actualpy compatibility shims.

Three monkey-patches that all silently fail-soft if actualpy changes shape:

* `patch_actualpy()` rewrites `actual.database.apply_change` so the SQLite
  ON-CONFLICT SET clause uses plain string keys (Actual Budget >= 26.3.0).
* `patch_payee_name_rules(session)` remaps `payee_name` → `description` and
  `imported_payee` → `imported_description` in stored rule JSON, so actualpy's
  Pydantic validation accepts them before `run_rules`.
* `fix_rule_note_casing(session, transactions)` restores the original case of
  notes set by SET-action rules (actualpy lowercases them).
"""
import json
import logging
import unicodedata

log = logging.getLogger("connector")


def patch_actualpy() -> None:
    try:
        import actual as _actual_mod
        import actual.database as _adb
        from sqlalchemy import Column
        from sqlalchemy.dialects.sqlite import insert

        def _patched(session, table, table_id, values):
            set_dict = {
                (c.name if isinstance(c, Column) else c): v for c, v in values.items()
            }
            stmt = (
                insert(table)
                .values({"id": table_id, **values})
                .on_conflict_do_update(index_elements=["id"], set_=set_dict)
            )
            session.exec(stmt)

        _adb.apply_change = _patched
        if hasattr(_actual_mod, "apply_change"):
            _actual_mod.apply_change = _patched
    except Exception as e:
        log.warning("Failed to patch actualpy: %s", e)


def patch_payee_name_rules(session) -> None:
    from actual.queries import get_rules

    field_map = {"payee_name": "description", "imported_payee": "imported_description"}
    for rule in get_rules(session):
        for attr in ("conditions", "actions"):
            raw = getattr(rule, attr, None)
            if not raw:
                continue
            try:
                items = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            patched = False
            for item in items:
                if item.get("field") in field_map:
                    item["field"] = field_map[item["field"]]
                    patched = True
            if patched:
                setattr(rule, attr, json.dumps(items))


def fix_rule_note_casing(session, transactions) -> None:
    from actual.queries import get_rules

    note_rules = []
    for rule in get_rules(session):
        try:
            actions = json.loads(rule.actions)
        except (json.JSONDecodeError, TypeError):
            continue
        for action in actions:
            if (
                action.get("field") == "notes"
                and action.get("op") == "set"
                and action.get("value")
            ):
                original = action["value"]
                lowered = unicodedata.normalize("NFD", original.lower())
                note_rules.append((lowered, original))
    if not note_rules:
        return
    for txn in transactions:
        if not txn.notes:
            continue
        for lowered, original in note_rules:
            if txn.notes == lowered:
                txn.notes = original
                break
