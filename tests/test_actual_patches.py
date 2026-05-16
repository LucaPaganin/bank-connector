"""actualpy compatibility shims (best-effort; intentionally fail-soft)."""
import json
import types

from bank_connector.actual_patches import (
    fix_rule_note_casing,
    patch_actualpy,
    patch_payee_name_rules,
)


def test_patch_actualpy_is_idempotent_and_safe():
    # Called eagerly on package import; calling again must not raise.
    patch_actualpy()
    patch_actualpy()


def _rule(actions=None, conditions=None):
    return types.SimpleNamespace(
        actions=json.dumps(actions) if actions is not None else None,
        conditions=json.dumps(conditions) if conditions is not None else None,
    )


def test_payee_name_rules_field_remap(monkeypatch):
    rule = _rule(
        actions=[{"field": "payee_name", "op": "set", "value": "X"}],
        conditions=[{"field": "imported_payee", "op": "is", "value": "Y"}],
    )
    monkeypatch.setattr("actual.queries.get_rules", lambda _s: [rule])
    patch_payee_name_rules(session=None)
    assert json.loads(rule.actions)[0]["field"] == "description"
    assert json.loads(rule.conditions)[0]["field"] == "imported_description"


def test_fix_rule_note_casing_restores_original(monkeypatch):
    rule = _rule(actions=[{"field": "notes", "op": "set", "value": "Café Loyalty"}])
    monkeypatch.setattr("actual.queries.get_rules", lambda _s: [rule])
    import unicodedata

    lowered = unicodedata.normalize("NFD", "Café Loyalty".lower())
    txn = types.SimpleNamespace(notes=lowered)
    fix_rule_note_casing(session=None, transactions=[txn])
    assert txn.notes == "Café Loyalty"


def test_fix_rule_note_casing_noop_without_note_rules(monkeypatch):
    rule = _rule(actions=[{"field": "category", "op": "set", "value": "Food"}])
    monkeypatch.setattr("actual.queries.get_rules", lambda _s: [rule])
    txn = types.SimpleNamespace(notes="unchanged")
    fix_rule_note_casing(session=None, transactions=[txn])
    assert txn.notes == "unchanged"
