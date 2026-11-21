import os
from collections import Counter
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import event, select

from models import AbstractCustomer, AmoCompany, AmoContact, AmoCustomer, AmoLead, db

DB_URI = os.environ.get(
    "TEST_DATABASE_URI",
    "postgresql+psycopg2://postgres@/test_sync?host=/tmp&port=5433",
)


@pytest.fixture()
def app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = DB_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.commit()
        yield app
        db.session.remove()


@pytest.fixture()
def query_counter(app):
    counts = Counter()

    def before(conn, cursor, statement, parameters, context, executemany):
        head = statement.lstrip()[:16].upper()
        for kind in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            if head.startswith(kind):
                counts[kind] += 1
                return
        counts["OTHER"] += 1

    event.listen(db.engine, "before_cursor_execute", before)
    yield counts
    event.remove(db.engine, "before_cursor_execute", before)


@pytest.fixture()
def export(monkeypatch, app):
    """Подменяет amocrm_get в main на выдачу заранее заданных сущностей."""
    import main
    from amocrm import Customer, Lead

    state = {"customers": [], "leads": []}

    def fake_amocrm_get(obj_cls, **kwargs):
        entities = state["customers"] if obj_cls is Customer else state["leads"]
        yield from entities

    monkeypatch.setattr(main, "amocrm_get", fake_amocrm_get)

    def set_customers(entities):
        state["customers"] = list(entities)

    def set_leads(entities):
        state["leads"] = list(entities)

    return SimpleNamespace(set_customers=set_customers, set_leads=set_leads)


def new_abc() -> int:
    abc = AbstractCustomer()
    db.session.add(abc)
    db.session.flush()
    return abc.id


def seed_customer(crm_id: int, login: str, name: str = "seeded", abc_id=None):
    if abc_id is None:
        abc_id = new_abc()
    db.session.execute(
        AmoCustomer.__table__.insert().values(
            crm_id=crm_id, abc_id=abc_id, login=login, name=name,
            data={"general": {"id": crm_id, "login": login, "name": name},
                  "api_value": {"seeded": True}}))
    db.session.commit()
    return abc_id


def seed_lead(crm_id: int, login, name: str = "seeded-lead", abc_id=None):
    if abc_id is None:
        abc_id = new_abc()
    db.session.execute(
        AmoLead.__table__.insert().values(
            crm_id=crm_id, abc_id=abc_id, login=login, name=name,
            data={"general": {"id": crm_id, "login": login, "name": name},
                  "api_value": {"seeded": True}}))
    db.session.commit()
    return abc_id


def seed_contact(abc_id: int, crm_id: int, name: str = "seeded-contact"):
    db.session.execute(
        AmoContact.__table__.insert().values(
            abc_id=abc_id, crm_id=crm_id, name=name,
            data={"general": {"id": crm_id, "name": name}}))
    db.session.commit()


def seed_company(abc_id: int, crm_id: int, name: str = "seeded-company"):
    db.session.execute(
        AmoCompany.__table__.insert().values(
            abc_id=abc_id, crm_id=crm_id, name=name,
            data={"general": {"id": crm_id, "name": name}}))
    db.session.commit()
