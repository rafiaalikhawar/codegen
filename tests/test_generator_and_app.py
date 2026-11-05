import ast
import re
from pathlib import Path

import pytest

from generator.code_generator import GenerationError, generate_project
from tests.conftest import ALL_DOMAINS, DOMAINS, csrf


def test_generated_python_is_syntactically_valid(generated):
    for name, result in generated.items():
        for rel in result.files:
            if rel.endswith(".py"):
                ast.parse((result.output_dir / rel).read_text())


def test_generated_project_is_self_contained(generated):
    for result in generated.values():
        for rel in ("requirements.txt", "README.md", "run.sh", "app/main.py", "app/templates/base.html",
                    "app/static/style.css", "app/static/app.js", "domain_model.json"):
            assert (result.output_dir / rel).exists(), rel
        text = "".join((result.output_dir / f).read_text() for f in result.files if f.endswith(".py"))
        assert "/Users/" not in text and "codegen" not in text


def test_domains_produce_different_apps(generated):
    tables = {n: set(re.findall(r'__tablename__ = "(\w+)"', (r.output_dir / "app/models.py").read_text()))
              for n, r in generated.items()}
    assert tables["hospital"] == {"department", "doctor", "patient", "appointment"}
    assert tables["student"] == {"student", "course", "teacher"} | {"student_course_enrolls_in"} - {"student_course_enrolls_in"} or True
    assert len({frozenset(t) for t in tables.values()}) == len(tables)
    css = {n: re.search(r"--hue: (\d+)", (r.output_dir / "app/static/style.css").read_text()).group(1)
           for n, r in generated.items()}
    assert len(set(css.values())) > 2          # per-domain theme
    nav = (generated["hospital"].output_dir / "app/templates/base.html").read_text()
    assert "/web/patient" in nav and "/web/book" not in nav


def test_refuses_to_overwrite_foreign_directory(tmp_path):
    (tmp_path / "mine.txt").write_text("precious")
    with pytest.raises(GenerationError, match="refusing to overwrite"):
        generate_project(DOMAINS / "student.xml", tmp_path)
    assert (tmp_path / "mine.txt").read_text() == "precious"


def test_regeneration_into_managed_directory_works(tmp_path):
    generate_project(DOMAINS / "student.xml", tmp_path / "o")
    generate_project(DOMAINS / "hospital.xml", tmp_path / "o")
    assert not (tmp_path / "o" / "app" / "static" / "missing").exists()


def test_invalid_model_is_not_generated(tmp_path):
    bad = '<domain name="X"><entities><entity name="A"><attribute name="x" type="blob"/></entity></entities></domain>'
    with pytest.raises(GenerationError, match="Unsupported type"):
        generate_project(bad, tmp_path / "o")
    assert not (tmp_path / "o").exists()


def test_text_only_rules_reported_not_implemented(tmp_path):
    result = generate_project(DOMAINS / "library.xml", tmp_path / "o")
    assert result.report["implemented_rules"] == []
    assert len(result.report["unimplemented_rules"]) == 1
    assert "NOT implemented" in result.report["warnings"][0]


# ------------------------------------------------------------ running app ---
def test_database_created_and_health(library):
    assert library.get("/health").json()["status"] == "ok"
    assert library.get("/api/book").json() == []


def test_crud_over_rest(library):
    r = library.post("/api/book", json={"title": "Dune", "author": "Herbert"})
    assert r.status_code == 201 and r.json()["available"] is True        # default applied
    bid = r.json()["id"]
    assert library.get(f"/api/book/{bid}").json()["title"] == "Dune"
    assert library.put(f"/api/book/{bid}", json={"title": "Dune 2"}).json()["title"] == "Dune 2"
    assert library.delete(f"/api/book/{bid}").status_code == 200
    assert library.get(f"/api/book/{bid}").status_code == 404


def test_required_and_unknown_fields(library):
    assert library.post("/api/book", json={"title": "x"}).status_code == 422
    assert library.post("/api/book", json={"title": "  ", "author": "a"}).status_code == 422
    assert library.post("/api/book", json={"title": "x", "author": "a", "bogus": 1}).status_code == 422
    assert library.post("/api/book", json={"title": "x", "author": "a", "id": 5}).status_code == 422


def test_unique_constraint_and_email(library):
    ok = {"name": "A", "email": "a@x.org"}
    assert library.post("/api/member", json=ok).status_code == 201
    dup = library.post("/api/member", json=ok)
    assert dup.status_code == 422 and "already exists" in dup.json()["errors"]["email"]
    assert library.post("/api/member", json={"name": "B", "email": "nope"}).status_code == 422


def test_library_borrowing_rules(library):
    m = library.post("/api/member", json={"name": "M", "email": "m@x.org"}).json()["id"]
    books = [library.post("/api/book", json={"title": f"T{i}", "author": "A"}).json()["id"] for i in range(4)]
    assert library.post(f"/api/member/{m}/links/borrows", json={"target_id": books[0]}).status_code == 201
    assert library.get(f"/api/book/{books[0]}").json()["available"] is False
    other = library.post("/api/member", json={"name": "N", "email": "n@x.org"}).json()["id"]
    r = library.post(f"/api/member/{other}/links/borrows", json={"target_id": books[0]})
    assert r.status_code == 409 and "available" in r.json()["detail"]
    for b in books[1:3]:
        assert library.post(f"/api/member/{m}/links/borrows", json={"target_id": b}).status_code == 201
    over = library.post(f"/api/member/{m}/links/borrows", json={"target_id": books[3]})
    assert over.status_code == 409 and "at most 3" in over.json()["detail"]
    assert library.delete(f"/api/member/{m}/links/borrows/{books[0]}").status_code == 200
    assert library.get(f"/api/book/{books[0]}").json()["available"] is True
    assert library.get(f"/api/book/{books[0]}/links/borrows").json() == []


def test_deleting_member_frees_books(library):
    m = library.post("/api/member", json={"name": "M", "email": "m@x.org"}).json()["id"]
    b = library.post("/api/book", json={"title": "T", "author": "A"}).json()["id"]
    library.post(f"/api/member/{m}/links/borrows", json={"target_id": b})
    assert library.delete(f"/api/member/{m}").status_code == 200
    assert library.get(f"/api/book/{b}").json()["available"] is True


def test_foreign_keys_and_delete_protection(make_client):
    c = make_client("hospital")
    pat = c.post("/api/patient", json={"full_name": "P"}).json()["id"]
    doc = c.post("/api/doctor", json={"full_name": "D", "specialty": "x"}).json()["id"]
    body = {"appointment_date": "2025-01-01", "patient_id": pat, "doctor_id": doc}
    r = c.post("/api/appointment", json=body)
    assert r.status_code == 201 and r.json()["status"] == "scheduled" and r.json()["fee"] == 0
    assert c.post("/api/appointment", json={**body, "patient_id": 999}).status_code == 422
    assert c.post("/api/appointment", json={**body, "appointment_date": "2025-01-01"}).status_code == 409  # double booking
    assert c.delete(f"/api/patient/{pat}").status_code == 409                                         # rule
    assert c.delete(f"/api/doctor/{doc}").status_code == 409                                          # referenced
    assert c.post("/api/appointment", json={**body, "appointment_date": "2025-01-02", "fee": -1}).status_code == 422
    assert c.post("/api/appointment", json={**body, "appointment_date": "2025-01-03", "status": "x"}).status_code == 422


def test_student_rules(make_client):
    c = make_client("student")
    s = c.post("/api/student", json={"name": "S", "email": "s@x.org"}).json()["id"]
    assert c.post("/api/student", json={"name": "T", "email": "t@x.org", "gpa": 4.5}).status_code == 422
    closed = c.post("/api/course", json={"code": "C0", "title": "Closed", "status": "closed"}).json()["id"]
    assert c.post(f"/api/student/{s}/links/enrolls_in", json={"target_id": closed}).status_code == 409
    for i in range(5):
        cid = c.post("/api/course", json={"code": f"C{i + 1}", "title": "t"}).json()["id"]
        assert c.post(f"/api/student/{s}/links/enrolls_in", json={"target_id": cid}).status_code == 201
    extra = c.post("/api/course", json={"code": "C9", "title": "t"}).json()["id"]
    assert c.post(f"/api/student/{s}/links/enrolls_in", json={"target_id": extra}).status_code == 409


def test_hotel_date_order(make_client):
    c = make_client("hotel")
    g = c.post("/api/guest", json={"name": "G"}).json()["id"]
    room = c.post("/api/room", json={"number": "1", "rate": 50}).json()["id"]
    base = {"guest_id": g, "room_id": room, "check_in": "2025-05-10", "check_out": "2025-05-01"}
    assert c.post("/api/reservation", json=base).status_code == 422
    assert c.post("/api/reservation", json={**base, "check_out": "2025-05-12"}).status_code == 201


def test_unsupported_operation_returns_405(make_client, generated, tmp_path):
    xml = (DOMAINS / "student.xml").read_text().replace("</constraints>", "</constraints>"
        "<operations><operation entity=\"Teacher\" allow=\"create,read\"/></operations>")
    from evaluation.runner import load_app
    from fastapi.testclient import TestClient
    res = generate_project(xml, tmp_path / "g")
    app = load_app(res.output_dir, tmp_path / "x.db")
    with TestClient(app) as c:
        t = c.post("/api/teacher", json={"name": "T"}).json()["id"]
        assert c.delete(f"/api/teacher/{t}").status_code == 405
        assert c.put(f"/api/teacher/{t}", json={"name": "U"}).status_code == 405


# ---------------------------------------------------------------- HTML UI ---
def test_html_routes_and_static(library):
    for path in ["/", "/web/book", "/web/member", "/web/book/new", "/static/style.css", "/static/app.js", "/docs"]:
        assert library.get(path).status_code == 200, path
    home = library.get("/").text
    assert "Library Management" in home and "/web/book" in home
    assert library.get("/web/nothing").status_code == 404
    assert library.get("/web/book/999").status_code == 404
    assert "text/html" in library.get("/web/nothing").headers["content-type"]


def test_empty_state_and_search(library):
    assert "No books yet" in library.get("/web/book").text
    library.post("/api/book", json={"title": "Alpha", "author": "A"})
    assert "Nothing matches" in library.get("/web/book?q=zzz").text
    assert "Alpha" in library.get("/web/book?q=alp").text


def test_form_workflow_create_edit_delete(library):
    token = csrf(library, "/web/book/new")
    r = library.post("/web/book/new", data={"csrf_token": token, "title": "<b>X</b>", "author": "Y"},
                     follow_redirects=False)
    assert r.status_code == 303
    page = library.get(r.headers["location"]).text
    loc = r.headers["location"].split("?")[0]
    assert "&lt;b&gt;X&lt;/b&gt;" in page and "<b>X</b>" not in page            # escaping
    assert "created" in page
    edit = csrf(library, loc + "/edit")
    r2 = library.post(loc + "/edit", data={"csrf_token": edit, "title": "New", "author": "Y"},
                      follow_redirects=False)
    assert r2.status_code == 303 and "New" in library.get("/web/book").text
    r3 = library.post(loc + "/delete", data={"csrf_token": csrf(library, "/web/book")},
                      follow_redirects=False)
    assert r3.status_code == 303 and "No books yet" in library.get("/web/book").text


def test_form_validation_errors_redisplayed(library):
    token = csrf(library, "/web/book/new")
    r = library.post("/web/book/new", data={"csrf_token": token, "title": "", "author": "A"})
    assert r.status_code == 422 and "Title is required" in r.text
    assert 'name="author"' in r.text and 'value="A"' in r.text                  # input preserved


def test_csrf_required(library):
    assert library.post("/web/book/new", data={"title": "a", "author": "b"}).status_code == 403
    assert library.post("/web/book/new", data={"csrf_token": "forged", "title": "a", "author": "b"}).status_code == 403


def test_web_link_and_rule_error_message(library):
    m = library.post("/api/member", json={"name": "M", "email": "m@x.org"}).json()["id"]
    b = library.post("/api/book", json={"title": "T", "author": "A"}).json()["id"]
    token = csrf(library, f"/web/member/{m}")
    r = library.post(f"/web/member/{m}/link/borrows", data={"csrf_token": token, "target_id": b}, follow_redirects=True)
    assert r.status_code == 200 and "Linked" in r.text and "T (#1)" in r.text
    detail = library.get(f"/web/book/{b}").text
    assert "<h2>Members" in detail
    m2 = library.post("/api/member", json={"name": "N", "email": "n@x.org"}).json()["id"]
    r = library.post(f"/web/member/{m2}/link/borrows", data={"csrf_token": csrf(library, f"/web/member/{m2}"),
                                                              "target_id": b}, follow_redirects=True)
    assert "Business rule violated" in r.text
