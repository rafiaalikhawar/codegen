import pytest

from parser.xml_parser import DomainParseError, parse_domain_model, parse_domain_string
from parser.xml_writer import model_to_xml
from parser.ir import model_hash
from validator.model_validator import validate
from validator.rule_registry import PATTERNS
from tests.conftest import ALL_DOMAINS, DOMAINS

BASE = """<domain name="Shop"><entities>{entities}</entities>{rest}</domain>"""
ITEM = '<entity name="Item"><attribute name="id" type="integer" primaryKey="true"/>{attrs}</entity>'


def model(attrs="", rest="", entities=None):
    xml = BASE.format(entities=entities or ITEM.format(attrs=attrs), rest=rest)
    return parse_domain_string(xml)


def errors(m):
    return validate(m)["errors"]


def test_parse_original_library_example():
    m = parse_domain_model(DOMAINS / "library.xml")
    assert m["name"] == "LibraryManagement" and m["ir_version"] == "1.0"
    assert [e["name"] for e in m["entities"]] == ["Book", "Member"]
    assert m["relationships"][0]["type"] == "many-to-many"
    assert m["constraints"][0]["description"].startswith("A book can only")


@pytest.mark.parametrize("name", ALL_DOMAINS)
def test_all_example_models_valid(name):
    assert errors(parse_domain_model(DOMAINS / f"{name}.xml")) == []


def test_invalid_xml_is_reported():
    with pytest.raises(DomainParseError, match="Invalid XML"):
        parse_domain_string("<domain><entities>")
    with pytest.raises(DomainParseError, match="Root element"):
        parse_domain_string("<site/>")
    with pytest.raises(DomainParseError, match="DOCTYPE"):
        parse_domain_string('<!DOCTYPE x [<!ENTITY a "b">]><domain name="x"/>')


def test_missing_file():
    with pytest.raises(DomainParseError):
        parse_domain_model(DOMAINS / "nope.xml")


def test_duplicate_entity_and_attribute():
    m = model(attrs='<attribute name="a" type="string"/><attribute name="a" type="string"/>',
              entities=ITEM.format(attrs="") + ITEM.format(attrs=""))
    text = " ".join(errors(m))
    assert "Duplicate entity: Item" in text and "Duplicate attribute" not in text or "Duplicate" in text
    assert any("Duplicate attribute" in e for e in errors(model(attrs='<attribute name="a" type="string"/>'
                                                                   '<attribute name="a" type="string"/>')))


def test_unsupported_type_and_default():
    e = errors(model('<attribute name="x" type="blob"/>'))
    assert any("Unsupported type 'blob'" in x for x in e)
    e = errors(model('<attribute name="n" type="integer" default="abc"/>'))
    assert any("Invalid default" in x for x in e)
    e = errors(model('<attribute name="b" type="boolean" default="maybe"/>'))
    assert any("Invalid default" in x for x in e)
    e = errors(model('<attribute name="c" type="string" choices="a,b" default="z"/>'))
    assert any("choices" in x for x in e)


def test_missing_primary_key():
    m = parse_domain_string('<domain name="S"><entities><entity name="A"><attribute name="x" type="string"/>'
                            '</entity></entities></domain>')
    assert any("exactly one primary key" in e for e in errors(m))


def test_non_integer_primary_key_rejected():
    m = parse_domain_string('<domain name="S"><entities><entity name="A"><attribute name="x" type="string" '
                            'primaryKey="true"/></entity></entities></domain>')
    assert any("must be of type integer" in e for e in errors(m))


@pytest.mark.parametrize("bad", ["class", "1abc", "Has Space", "metadata", "Upper"])
def test_invalid_attribute_identifiers(bad):
    assert any("Invalid attribute name" in e for e in errors(model(f'<attribute name="{bad}" type="string"/>')))


def test_invalid_entity_identifier():
    m = parse_domain_string('<domain name="S"><entities><entity name="my-entity"><attribute name="id" '
                            'type="integer" primaryKey="true"/></entity></entities></domain>')
    assert any("Invalid entity name" in e for e in errors(m))


def test_relationship_problems():
    rest = ('<relationships><relationship from="Item" to="Ghost" type="many-to-many" name="r"/>'
            '<relationship from="Item" to="Item" type="friends" name="s"/>'
            '<relationship from="Item" to="Item" type="one-to-many"/></relationships>')
    text = " ".join(errors(model(rest=rest)))
    assert "Unknown target entity 'Ghost'" in text
    assert "Unsupported relationship type 'friends'" in text
    assert "needs a name" in text


def test_unknown_xml_features_are_rejected_not_ignored():
    m = parse_domain_string('<domain name="S" theme="x"><entities><entity name="A" colour="red">'
                            '<attribute name="id" type="integer" primaryKey="true" index="yes"/>'
                            '<method name="m"/></entity></entities></domain>')
    text = " ".join(errors(m))
    assert "unsupported XML attribute 'theme'" in text
    assert "unsupported XML attribute 'colour'" in text
    assert "unsupported XML attribute 'index'" in text
    assert "unsupported XML element <method>" in text


def test_bad_boolean_flag():
    m = model('<attribute name="x" type="string" required="yes"/>')
    assert any("must be 'true' or 'false'" in e for e in errors(m))


def test_business_rule_reference_errors():
    rest = ('<constraints><constraint entity="Nope" type="business-rule">x</constraint>'
            '<constraint entity="Item" type="invariant">x</constraint>'
            '<constraint id="a" entity="Item" type="business-rule" pattern="numeric_range" attribute="ghost" min="1"/>'
            '<constraint id="b" entity="Item" type="business-rule" pattern="telepathy"/>'
            '<constraint id="c" entity="Item" type="business-rule" pattern="numeric_range" attribute="n" min="9" max="2"/>'
            '</constraints>')
    m = model('<attribute name="n" type="integer"/>', rest)
    text = " ".join(errors(m))
    assert "Unknown constraint entity: Nope" in text
    assert "unsupported constraint type 'invariant'" in text
    assert "'ghost' is not an attribute of Item" in text
    assert "unknown rule pattern 'telepathy'" in text
    assert "min is greater than max" in text


def test_text_only_rule_is_a_warning_not_implemented():
    rep = validate(parse_domain_model(DOMAINS / "library.xml"))
    assert rep["errors"] == [] and "NOT implemented" in rep["warnings"][0]


def test_every_pattern_has_runtime_implementation():
    engine = (DOMAINS.parent / "templates" / "app" / "engine.py").read_text()
    for pattern in PATTERNS:
        assert f'"{pattern}"' in engine, pattern


def test_xml_round_trip_preserves_model():
    original = parse_domain_model(DOMAINS / "hospital.xml")
    again = parse_domain_string(model_to_xml(original))
    assert model_hash(original) == model_hash(again)
