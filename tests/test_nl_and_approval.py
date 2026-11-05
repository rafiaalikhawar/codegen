import pytest

from generator.code_generator import GenerationError
from nl2model.approval import ApprovalError, Proposal
from nl2model.extractor import extract_domain_model, proposal_to_ir
from parser.xml_parser import parse_domain_string
from slm.providers import FakeProvider
from validator.model_validator import validate

HOSPITAL = {
    "name": "Hospital",
    "entities": [
        {"name": "patient", "attributes": [{"name": "Full Name", "type": "varchar", "required": True},
                                           {"name": "age", "type": "int"}]},
        {"name": "doctor", "attributes": [{"name": "name", "type": "string", "required": True}]},
        {"name": "Appointment", "attributes": [{"name": "day", "type": "date", "required": True},
                                               {"name": "status", "type": "string", "choices": ["new", "done"], "default": "new"}]}],
    "relationships": [{"from": "Appointment", "to": "Patient", "type": "many_to_one", "name": "for_patient"},
                      {"from": "Appointment", "to": "Doctor", "type": "many-to-one"}],
    "rules": [{"entity": "Appointment", "description": "Appointments cannot overlap for a doctor."}],
}


def test_extraction_normalises_and_validates():
    res = extract_domain_model("Hospital with patients, doctors, appointments", FakeProvider([HOSPITAL]))
    assert res["errors"] == [] and res["repair_rounds"] == 0
    names = [e["name"] for e in res["ir"]["entities"]]
    assert names == ["Patient", "Doctor", "Appointment"]
    assert res["ir"]["entities"][0]["attributes"][0]["primary_key"]       # id added deterministically
    assert any(a["name"] == "full_name" and a["type"] == "string" for a in res["ir"]["entities"][0]["attributes"])
    assert res["usage"]["calls"] == 1 and "<domain" in res["xml"]
    assert any("NOT implemented" in w for w in res["warnings"])           # rule stays text-only
    assert validate(parse_domain_string(res["xml"]))["errors"] == []


def test_invalid_proposal_triggers_one_repair():
    bad = {"name": "X", "entities": [{"name": "A", "attributes": [{"name": "x", "type": "blob"}]}]}
    good = {"name": "X", "entities": [{"name": "A", "attributes": [{"name": "x", "type": "string"}]}]}
    fake = FakeProvider([bad, good])
    res = extract_domain_model("anything", fake)
    assert res["errors"] == [] and res["repair_rounds"] == 1
    assert "Unsupported type" in fake.prompts[1]


def test_unrepairable_proposal_returns_errors_for_human():
    bad = {"name": "X", "entities": [{"name": "A", "attributes": [{"name": "x", "type": "blob"}]}]}
    res = extract_domain_model("anything", FakeProvider([bad, bad]))
    assert res["errors"] and res["xml"]


def test_empty_and_oversized_requirements():
    with pytest.raises(ValueError):
        extract_domain_model("   ", FakeProvider([]))
    with pytest.raises(ValueError):
        extract_domain_model("x" * 7000, FakeProvider([]))


def test_garbage_model_output():
    res = extract_domain_model("hello", FakeProvider(["nonsense", "still nonsense"]))
    assert res["ir"] is None and res["errors"]


def test_approval_state_machine():
    xml = extract_domain_model("hospital", FakeProvider([HOSPITAL]))["xml"]
    p = Proposal(xml, "req")
    with pytest.raises(ApprovalError, match="not been approved"):
        p.require_approved()
    p.approve()
    assert p.require_approved()["name"] == "Hospital"
    p.edit(xml.replace("Hospital", "Clinic"))
    assert p.status == "draft" and p.human_edits == 1
    with pytest.raises(ApprovalError):
        p.require_approved()
    p.approve()
    p.xml = xml                                     # tampering after approval
    with pytest.raises(ApprovalError, match="changed after approval"):
        p.require_approved()


def test_cannot_approve_invalid_model():
    with pytest.raises(ApprovalError, match="invalid"):
        Proposal("<domain name='X'><entities/></domain>").approve()
    with pytest.raises(ApprovalError):
        Proposal("<broken").approve()


def test_proposal_round_trip_to_phase1(tmp_path):
    from generator.code_generator import generate_project
    xml = extract_domain_model("hospital", FakeProvider([HOSPITAL]))["xml"]
    p = Proposal(xml)
    p.approve()
    result = generate_project(p.xml, tmp_path / "h")
    assert (tmp_path / "h/app/models.py").exists() and len(result.report["unimplemented_rules"]) == 1
