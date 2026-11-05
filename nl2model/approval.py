"""Human-in-the-loop gate: a proposed model must be approved before any generation."""
import time

from parser.ir import model_hash
from parser.xml_parser import DomainParseError, parse_domain_string
from validator.model_validator import validate


class ApprovalError(PermissionError):
    pass


def _check(xml):
    try:
        model = parse_domain_string(xml)
    except DomainParseError as exc:
        return None, [str(exc)]
    report = validate(model)
    return model, report["errors"]


class Proposal:
    """State machine: draft -> approved; any edit to the XML returns it to draft."""

    def __init__(self, xml, requirements="", origin="nl"):
        self.xml, self.requirements, self.origin = xml, requirements, origin
        self.status, self.approved_hash, self.approved_at = "draft", None, None
        self.human_edits = 0

    def edit(self, xml):
        if xml != self.xml:
            self.xml, self.human_edits = xml, self.human_edits + 1
        self.status, self.approved_hash, self.approved_at = "draft", None, None

    def approve(self):
        model, errors = _check(self.xml)
        if errors:
            raise ApprovalError("Cannot approve an invalid model: " + "; ".join(errors))
        self.status, self.approved_hash, self.approved_at = "approved", model_hash(model), time.time()
        return self.approved_hash

    def require_approved(self):
        model, errors = _check(self.xml)
        if self.status != "approved" or not self.approved_hash:
            raise ApprovalError("The domain model has not been approved by a human reviewer.")
        if errors or model_hash(model) != self.approved_hash:
            raise ApprovalError("The domain model changed after approval; approve it again.")
        return model

    def to_dict(self):
        return {"xml": self.xml, "requirements": self.requirements, "origin": self.origin, "status": self.status,
                "approved_hash": self.approved_hash, "approved_at": self.approved_at, "human_edits": self.human_edits}

    @classmethod
    def from_dict(cls, d):
        p = cls(d["xml"], d.get("requirements", ""), d.get("origin", "nl"))
        p.status, p.approved_hash = d.get("status", "draft"), d.get("approved_hash")
        p.approved_at, p.human_edits = d.get("approved_at"), d.get("human_edits", 0)
        return p
