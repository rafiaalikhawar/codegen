"""Server-rendered HTML interface (generic, driven by META)."""
import hmac
import secrets
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import engine
from .database import get_session
from .engine import AppError
from .meta import META

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()


def qs(params, **changes):
    """Build a query string from ``params`` with ``changes`` applied (None removes a key)."""
    merged = {**params, **changes}
    pairs = {k: v for k, v in merged.items() if v not in (None, "")}
    return "?" + urlencode(pairs) if pairs else "?"


templates.env.globals["qs"] = qs
PER_PAGE = 25
INPUT_TYPES = {"integer": "number", "float": "number", "date": "date", "datetime": "datetime-local",
               "email": "email", "string": "text"}


def _token(request: Request):
    return request.cookies.get("csrf_token") or secrets.token_urlsafe(24)


def render(request: Request, name: str, context: dict, status: int = 200):
    token = _token(request)
    context.update(meta=META, csrf_token=token, entities=META["entities"],
                   message=request.query_params.get("msg", "")[:200],
                   error=request.query_params.get("error", "")[:300])
    response = templates.TemplateResponse(request, name, context, status_code=status)
    if request.cookies.get("csrf_token") != token:
        response.set_cookie("csrf_token", token, httponly=True, samesite="strict")
    return response


def render_error(request: Request, status: int, message: str):
    return render(request, "error.html", {"status": status, "detail": message}, status)


async def _form(request: Request):
    form = await request.form()
    cookie = request.cookies.get("csrf_token", "")
    sent = str(form.get("csrf_token", ""))
    if not cookie or not hmac.compare_digest(cookie, sent):
        raise AppError(403, "Invalid or missing form token. Reload the page and try again.")
    return form


def _redirect(url, msg=None, error=None):
    if msg:
        url += "?msg=" + quote(msg)
    if error:
        url += "?error=" + quote(error)
    return RedirectResponse(url, status_code=303)


def _entity(slug):
    return engine.entity_by_slug(slug)


def _form_data(entity, form):
    data = {}
    for f in META["entities"][entity]["fields"]:
        if f["pk"]:
            continue
        if f["type"] == "boolean":
            data[f["name"]] = "true" if f["name"] in form else "false"
        else:
            data[f["name"]] = form.get(f["name"], "")
    return data


def _form_context(session, entity, values, errors, title, action, cancel):
    meta = META["entities"][entity]
    fields = []
    for f in meta["fields"]:
        if f["pk"]:
            continue
        item = dict(f)
        item["input_type"] = INPUT_TYPES.get(f["type"], "text")
        item["options"] = engine.options(session, f["fk"]["target"]) if f["fk"] else None
        item["value"] = values.get(f["name"])
        item["error"] = errors.get(f["name"])
        fields.append(item)
    return {"entity": meta, "fields": fields, "title": title, "action": action, "cancel": cancel,
            "errors": errors}


@router.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_session)):
    latest = {name: [engine.card_view(session, name, r) for r in engine.latest(session, name, 4)]
              for name, e in META["entities"].items() if "read" in e["operations"]}
    latest.update({name: [] for name in META["entities"] if name not in latest})
    return render(request, "index.html", {"counts": engine.counts(session), "latest": latest})


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", session: Session = Depends(get_session)):
    q = q.strip()[:100]
    results = engine.search_all(session, q) if q else []
    return render(request, "search.html", {"q": q, "results": results})


@router.get("/web/{slug}", response_class=HTMLResponse)
def list_page(request: Request, slug: str, q: str = "", page: int = 1, sort: str = "", dir: str = "",
              session: Session = Depends(get_session)):
    entity = _entity(slug)
    page = max(page, 1)
    meta = META["entities"][entity]
    fields = {f["name"]: f for f in meta["fields"]}
    params = {k: v for k, v in request.query_params.items() if v != ""}
    filters = {k[2:]: v for k, v in params.items() if k.startswith("f_")}
    sort = sort if sort in meta["ui"]["sortable"] else ""
    rows, total = engine.list_records(session, entity, q=q or None, limit=PER_PAGE, offset=(page - 1) * PER_PAGE,
                                      filters=filters, sort=sort or None, desc=(dir != "asc") if sort else True)
    columns = [f for f in meta["fields"] if f["type"] not in ("text", "image")]
    data = []
    for r in rows:
        cells = [c for c in engine.row_cells(session, entity, r) if c["field"]["type"] not in ("text", "image")]
        data.append({"id": getattr(r, meta["pk"]), "cells": cells, "card": engine.card_view(session, entity, r)})
    filter_defs = []
    for name in meta["ui"]["filters"]:
        f = fields[name]
        options = [("true", "Yes"), ("false", "No")] if f["type"] == "boolean" else [(c, c) for c in f["choices"]]
        filter_defs.append({"label": f["label"], "param": f"f_{name}", "options": options,
                            "current": params.get(f"f_{name}")})
    return render(request, "list.html", {
        "entity": meta, "columns": columns, "rows": data, "q": q, "page": page, "total": total, "fields": fields,
        "pages": max((total + PER_PAGE - 1) // PER_PAGE, 1), "params": params, "filter_defs": filter_defs,
        "filtered": bool(filters)})


@router.get("/web/{slug}/new", response_class=HTMLResponse)
def new_page(request: Request, slug: str, session: Session = Depends(get_session)):
    entity = _entity(slug)
    engine.require_op(entity, "create")
    meta = META["entities"][entity]
    values = {f["name"]: f["default"] for f in meta["fields"] if f["default"] is not None}
    return render(request, "form.html", _form_context(
        session, entity, values, {}, f"New {meta['label']}", f"/web/{slug}/new", f"/web/{slug}"))


@router.post("/web/{slug}/new", response_class=HTMLResponse)
async def create_page(request: Request, slug: str, session: Session = Depends(get_session)):
    entity = _entity(slug)
    form = await _form(request)
    data = _form_data(entity, form)
    try:
        obj = engine.create(session, entity, data)
    except AppError as exc:
        if exc.status in (404, 405):
            raise
        errors = dict(exc.errors)
        errors["_form"] = exc.message
        meta = META["entities"][entity]
        return render(request, "form.html", _form_context(
            session, entity, data, errors, f"New {meta['label']}", f"/web/{slug}/new", f"/web/{slug}"),
            exc.status)
    return _redirect(f"/web/{slug}/{getattr(obj, META['entities'][entity]['pk'])}", f"{META['entities'][entity]['label']} created")


@router.get("/web/{slug}/{item_id}", response_class=HTMLResponse)
def detail_page(request: Request, slug: str, item_id: int, session: Session = Depends(get_session)):
    entity = _entity(slug)
    obj = engine.get(session, entity, item_id)
    meta = META["entities"][entity]
    sections = []
    for rel, side in engine.relations_of(entity):
        if rel["kind"] == "m2m":
            other = rel["to"] if side == "from" else rel["from"]
            current = [(o, engine.label_of(other, row), getattr(row, META["entities"][o]["pk"]))
                       for o, row in engine.linked(session, entity, item_id, rel["name"])]
            taken = {c[2] for c in current}
            candidates = [(i, l) for i, l in engine.options(session, other) if i not in taken]
            sections.append({"kind": "m2m", "rel": rel["name"], "label": engine.rel_label(rel, side) if side == "from" else META["entities"][other]["plural"],
                             "other": META["entities"][other], "linked": current, "candidates": candidates})
    for rel, holder, rows in engine.referencing(session, entity, item_id):
        sections.append({"kind": "fk", "rel": rel["name"], "label": f"{META['entities'][holder]['plural']} ({rel['name'].replace('_', ' ')})",
                         "other": META["entities"][holder],
                         "linked": [(holder, engine.label_of(holder, r), getattr(r, META['entities'][holder]['pk'])) for r in rows]})
    return render(request, "detail.html", {
        "entity": meta, "item_id": item_id, "cells": engine.row_cells(session, entity, obj),
        "title": engine.label_of(entity, obj), "view": engine.card_view(session, entity, obj), "sections": sections})


@router.get("/web/{slug}/{item_id}/edit", response_class=HTMLResponse)
def edit_page(request: Request, slug: str, item_id: int, session: Session = Depends(get_session)):
    entity = _entity(slug)
    engine.require_op(entity, "update")
    obj = engine.get(session, entity, item_id)
    meta = META["entities"][entity]
    values = {f["name"]: getattr(obj, f["name"]) for f in meta["fields"]}
    return render(request, "form.html", _form_context(
        session, entity, values, {}, f"Edit {engine.label_of(entity, obj)}",
        f"/web/{slug}/{item_id}/edit", f"/web/{slug}/{item_id}"))


@router.post("/web/{slug}/{item_id}/edit", response_class=HTMLResponse)
async def update_page(request: Request, slug: str, item_id: int, session: Session = Depends(get_session)):
    entity = _entity(slug)
    form = await _form(request)
    data = _form_data(entity, form)
    try:
        engine.update(session, entity, item_id, data)
    except AppError as exc:
        if exc.status in (404, 405):
            raise
        errors = dict(exc.errors)
        errors["_form"] = exc.message
        return render(request, "form.html", _form_context(
            session, entity, data, errors, f"Edit {META['entities'][entity]['label']} #{item_id}",
            f"/web/{slug}/{item_id}/edit", f"/web/{slug}/{item_id}"), exc.status)
    return _redirect(f"/web/{slug}/{item_id}", "Changes saved")


@router.post("/web/{slug}/{item_id}/delete")
async def delete_page(request: Request, slug: str, item_id: int, session: Session = Depends(get_session)):
    entity = _entity(slug)
    await _form(request)
    try:
        engine.delete(session, entity, item_id)
    except AppError as exc:
        if exc.status == 404:
            raise
        return _redirect(f"/web/{slug}", error=exc.message)
    return _redirect(f"/web/{slug}", "Record deleted")


@router.post("/web/{slug}/{item_id}/link/{relationship}")
async def link_page(request: Request, slug: str, item_id: int, relationship: str,
                    session: Session = Depends(get_session)):
    entity = _entity(slug)
    form = await _form(request)
    try:
        target = int(str(form.get("target_id", "")))
    except ValueError:
        return _redirect(f"/web/{slug}/{item_id}", error="Choose a record to link")
    try:
        engine.link(session, entity, item_id, relationship, target)
    except AppError as exc:
        if exc.status == 404 and "not found" not in exc.message:
            raise
        return _redirect(f"/web/{slug}/{item_id}", error=exc.message)
    return _redirect(f"/web/{slug}/{item_id}", "Linked")


@router.post("/web/{slug}/{item_id}/unlink/{relationship}/{target_id}")
async def unlink_page(request: Request, slug: str, item_id: int, relationship: str, target_id: int,
                      session: Session = Depends(get_session)):
    entity = _entity(slug)
    await _form(request)
    try:
        engine.unlink(session, entity, item_id, relationship, target_id)
    except AppError as exc:
        return _redirect(f"/web/{slug}/{item_id}", error=exc.message)
    return _redirect(f"/web/{slug}/{item_id}", "Unlinked")
