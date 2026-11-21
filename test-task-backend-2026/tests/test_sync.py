"""Тесты синхронизации: правила из TASK.md и отсутствие per-entity запросов.
Запуск: pytest tests/ -q (нужен PostgreSQL, см. TEST_DATABASE_URI в conftest).
"""
import pytest
from sqlalchemy import select

import main
from models import AbstractCustomer, AmoCompany, AmoContact, AmoCustomer, AmoLead, db
from tests.conftest import seed_company, seed_contact, seed_customer, seed_lead
from tests.fakes import make_customer, make_lead


def run_sync(is_full=True):
    main.collect_leads_customers(is_full=is_full)
    db.session.expire_all()


def abc_ids():
    return {row[0] for row in db.session.execute(select(AbstractCustomer.id))}


def crm_ids(model):
    return sorted(r.crm_id for r in db.session.execute(select(model.crm_id)))


def test_customer_without_login_skipped(export, app):
    export.set_customers([make_customer(1, login=None),
                          make_customer(2, login="user2")])
    run_sync()
    assert crm_ids(AmoCustomer) == [2]


def test_customer_who_lost_login_is_dropped_by_cleanup(export, app):
    seed_customer(1, login="user1")
    export.set_customers([make_customer(1, login=None)])
    run_sync()
    assert db.session.execute(select(AmoCustomer.crm_id)).all() == []
    assert db.session.execute(select(AbstractCustomer.id)).all() == []


def test_customer_found_by_crm_id_updates_same_abc(export, app):
    abc_id = seed_customer(1, login="user1", name="old-name")
    export.set_customers([make_customer(1, login="user1", name="new-name")])
    run_sync()
    row = db.session.execute(select(AmoCustomer)).scalars().one()
    assert row.abc_id == abc_id
    assert row.name == "new-name"
    assert row.data["general"]["name"] == "new-name"
    assert len(abc_ids()) == 1


def test_customer_found_by_login_among_saved_leads(export, app):
    abc_id = seed_lead(100, login="user1")
    export.set_customers([make_customer(9, login="user1")])
    run_sync()
    row = db.session.execute(select(AmoCustomer)).scalars().one()
    assert row.abc_id == abc_id
    assert len(abc_ids()) == 1


def test_customer_login_ambiguous_match_not_used(export, app):
    abc_a = seed_lead(100, login="user1")
    abc_b = seed_lead(101, login="user1")
    export.set_customers([make_customer(9, login="user1")])
    run_sync()
    row = db.session.execute(select(AmoCustomer)).scalars().one()
    assert row.abc_id not in {abc_a, abc_b}


def test_lead_login_ambiguous_match_not_used(export, app):
    abc_a = seed_customer(1, login="user1")
    abc_b = seed_customer(2, login="user1")
    export.set_leads([make_lead(500, login="user1")])
    run_sync()
    lead = db.session.execute(select(AmoLead)).scalars().one()
    assert lead.abc_id not in {abc_a, abc_b}


def test_new_customer_creates_new_abc(export, app):
    export.set_customers([make_customer(1, login="user1")])
    run_sync()
    assert len(abc_ids()) == 1
    rows = db.session.execute(select(AmoCustomer.crm_id, AmoCustomer.login)).all()
    assert rows == [(1, "user1")]


def test_duplicate_customer_id_in_export_deduped(export, app):
    export.set_customers([make_customer(1, login="user1", name="first"),
                          make_customer(1, login="user1", name="second")])
    run_sync()
    rows = db.session.execute(select(AmoCustomer.name)).all()
    assert rows == [("second",)]
    assert len(abc_ids()) == 1


def test_lead_found_by_crm_id_updates_same_abc(export, app):
    abc_id = seed_lead(500, login="user1", name="old")
    export.set_leads([make_lead(500, login="user1", name="new")])
    run_sync()
    row = db.session.execute(select(AmoLead)).scalars().one()
    assert row.abc_id == abc_id and row.name == "new"


def test_lead_attached_to_customer_by_login(export, app):
    abc_id = seed_customer(1, login="user1")
    export.set_customers([make_customer(1, login="user1")])
    export.set_leads([make_lead(500, login="user1")])
    run_sync()
    lead = db.session.execute(select(AmoLead)).scalars().one()
    cust = db.session.execute(select(AmoCustomer)).scalars().one()
    assert lead.abc_id == cust.abc_id == abc_id
    assert len(abc_ids()) == 1


def test_lead_without_login_creates_own_abc(export, app):
    export.set_leads([make_lead(600, login=None)])
    run_sync()
    assert len(abc_ids()) == 1
    row = db.session.execute(select(AmoLead)).scalars().one()
    assert row.crm_id == 600 and row.login is None


def test_lead_merge_two_records_into_by_id_one(export, app):
    abc_a = seed_lead(500, login="user1")
    abc_b = seed_customer(1, login="user1")
    seed_contact(abc_b, crm_id=11, name="contact-11")
    seed_company(abc_b, crm_id=21, name="company-21")
    export.set_customers([make_customer(1, login="user1")])
    export.set_leads([make_lead(500, login="user1", name="updated-lead",
                                contacts=[{"id": 11}])])
    run_sync()
    assert abc_ids() == {abc_a}
    lead = db.session.execute(select(AmoLead)).scalars().one()
    cust = db.session.execute(select(AmoCustomer)).scalars().one()
    assert lead.abc_id == cust.abc_id == abc_a
    assert lead.name == "updated-lead"
    contact = db.session.execute(select(AmoContact)).scalars().one()
    assert contact.abc_id == abc_a and contact.crm_id == 11
    # компанию 21 в выгрузку не положили — чистка удаляет её по правилу 4
    assert db.session.execute(select(AmoCompany)).scalars().all() == []


def test_lead_merge_contact_conflict_victim_data_wins(export, app):
    abc_a = seed_lead(500, login="user1")
    seed_contact(abc_a, crm_id=11, name="from-survivor")
    abc_b = seed_customer(1, login="user1")
    seed_contact(abc_b, crm_id=11, name="from-victim")
    export.set_leads([make_lead(500, login="user1", contacts=[{"id": 11}])])
    run_sync()
    contact = db.session.execute(select(AmoContact)).scalars().one()
    assert contact.abc_id == abc_a and contact.name == "from-victim"


def test_lead_new_with_login_but_no_customer(export, app):
    export.set_leads([make_lead(700, login="ghost")])
    run_sync()
    assert len(abc_ids()) == 1
    assert db.session.execute(select(AmoLead)).scalars().one().crm_id == 700


def test_lead_merge_chain(export, app):
    abc_a = seed_lead(500, login="user1")
    seed_lead(501, login="user1")
    abc_b = seed_customer(1, login="user1")
    export.set_leads([make_lead(500, login="user1"),
                      make_lead(501, login="user1")])
    run_sync()
    assert len(abc_ids()) == 1
    lead_abcs = {r.abc_id for r in db.session.execute(select(AmoLead)).scalars()}
    assert lead_abcs == abc_ids()
    # покупатель перенесён с жертвы, но в выгрузке его не было — чистка удаляет
    assert db.session.execute(select(AmoCustomer.crm_id)).all() == []


def test_cleanup_deletes_stale_and_marked(export, app):
    abc1 = seed_customer(1, login="user1")
    seed_lead(100, login="user1", abc_id=abc1)
    seed_contact(abc1, crm_id=11, name="seen-contact")
    seed_contact(abc1, crm_id=99, name="stale-contact")
    seed_contact(abc1, crm_id=12, name="контакт Удалить")
    seed_company(abc1, crm_id=21, name="seen-company")
    seed_company(abc1, crm_id=98, name="stale-company")
    stale_abc = seed_customer(2, login="user2")
    seed_contact(stale_abc, crm_id=77, name="orphan-contact")

    export.set_customers([
        make_customer(1, login="user1", contacts=[{"id": 11}, {"id": 12}],
                      companies=[{"id": 21}]),
    ])
    export.set_leads([make_lead(100, login="user1", contacts=[{"id": 11}])])
    run_sync()
    assert abc_ids() == {abc1}
    contacts = sorted(r.crm_id for r in db.session.execute(select(AmoContact.crm_id)))
    assert contacts == [11]
    companies = sorted(r.crm_id for r in db.session.execute(select(AmoCompany.crm_id)))
    assert companies == [21]

    export.set_customers([make_customer(1, login="user1"),
                          make_customer(3, login="user3", name="Вася удалить")])
    export.set_leads([])
    run_sync()
    assert crm_ids(AmoCustomer) == [1]
    assert abc_ids() == {abc1}


def test_cleanup_deletes_abc_with_only_stale_entities(export, app):
    abc1 = seed_customer(1, login="user1")
    seed_lead(100, login="user1", abc_id=abc1)
    seed_lead(101, login="user9")
    export.set_customers([make_customer(1, login="user1")])
    export.set_leads([make_lead(100, login="user1")])
    run_sync()
    assert abc_ids() == {abc1}
    assert crm_ids(AmoLead) == [100]


def test_interrupted_collection_skips_cleanup(export, app, monkeypatch):
    seed_customer(1, login="user1")
    stale_abc = seed_customer(2, login="user2")

    def broken(*args, **kwargs):
        raise RuntimeError("СRM упал")

    monkeypatch.setattr(main, "bulk_upsert_leads", broken)
    export.set_customers([make_customer(1, login="user1")])
    export.set_leads([make_lead(100, login="user1")])
    run_sync()
    assert stale_abc in abc_ids()
    assert 2 in crm_ids(AmoCustomer)


def test_interrupted_read_skips_cleanup_and_rolls_back(export, app, monkeypatch):
    seed_customer(2, login="user2")

    def broken_read(*args, **kwargs):
        raise RuntimeError("API CRM недоступен")

    monkeypatch.setattr(main, "_read_leads", broken_read)
    export.set_customers([make_customer(1, login="user1")])
    export.set_leads([make_lead(100, login="user1")])
    with pytest.raises(RuntimeError):
        run_sync()
    assert db.session.execute(
        select(AmoCustomer.crm_id).where(AmoCustomer.crm_id == 2)).scalar() == 2


def test_incremental_run_skips_statuses_and_cleanup(export, app):
    abc1 = seed_customer(1, login="user1")
    seed_lead(100, login="user1", abc_id=abc1)
    stale_abc = seed_customer(2, login="user2")
    export.set_customers([make_customer(3, login="user3"),
                          make_customer(1, login="user1", status_id=111349)])
    export.set_leads([make_lead(100, login="user1", status_id=143)])
    run_sync(is_full=False)
    # покупатель 1 пропущен по статусу, 2 остался (чистки не было), 3 записан
    assert crm_ids(AmoCustomer) == [1, 2, 3]
    assert stale_abc in abc_ids()
    assert db.session.execute(select(AmoLead.crm_id)).all() == [(100,)]


def test_full_empty_export_wipes_everything(export, app):
    seed_customer(1, login="user1")
    seed_lead(100, login="user1")
    export.set_customers([])
    export.set_leads([])
    run_sync()
    assert abc_ids() == set()
    assert db.session.execute(select(AmoCustomer.crm_id)).all() == []
    assert db.session.execute(select(AmoLead.crm_id)).all() == []


def test_query_count_stays_small(export, app, query_counter):
    """300 покупателей + 700 сделок: десятки запросов, а не тысячи."""
    customers = [make_customer(i, login=f"user{i}") for i in range(1, 301)]
    leads = [make_lead(i, login=f"user{(i % 300) + 1}") for i in range(501, 1201)]
    export.set_customers(customers)
    export.set_leads(leads)
    run_sync()
    total = sum(query_counter.values())
    assert total < 150, f"слишком много запросов: {dict(query_counter)}"
    assert len(abc_ids()) == 300


def test_payload_format_preserved(export, app):
    export.set_customers([make_customer(1, login="user1", name="Иван")])
    run_sync()
    row = db.session.execute(select(AmoCustomer)).scalars().one()
    assert set(row.data.keys()) == {"general", "api_value"}
    assert row.data["general"]["login"] == "user1"
    assert row.data["api_value"]["id"] == 1


def test_customer_login_matches_multiple_leads_in_same_abc(export, app):
    abc_id = seed_lead(100, login="user1")
    seed_lead(101, login="user1", abc_id=abc_id)
    export.set_customers([make_customer(9, login="user1")])
    run_sync()
    customer = db.session.execute(select(AmoCustomer)).scalars().one()
    assert customer.abc_id == abc_id


def test_lead_id_match_merges_customer_created_in_same_export(export, app):
    abc_id = seed_lead(500, login="user1")
    export.set_customers([make_customer(1, login="user1")])
    export.set_leads([make_lead(500, login="user1")])
    run_sync()
    customer = db.session.execute(select(AmoCustomer)).scalars().one()
    lead = db.session.execute(select(AmoLead)).scalars().one()
    assert customer.abc_id == lead.abc_id == abc_id
    assert abc_ids() == {abc_id}


def test_merge_chain_with_duplicate_contact_ids(export, app):
    abc_first = seed_lead(500, login="user1")
    abc_last = seed_lead(501, login="user1")
    abc_customer = seed_customer(1, login="user1")
    seed_contact(abc_first, crm_id=77, name="from-first")
    seed_contact(abc_customer, crm_id=77, name="from-customer")

    export.set_leads([
        make_lead(500, login="user1", contacts=[{"id": 77}]),
        make_lead(501, login="user1"),
    ])
    run_sync()

    assert abc_ids() == {abc_last}
    contact = db.session.execute(select(AmoContact)).scalars().one()
    assert contact.abc_id == abc_last
    assert contact.name == "from-customer"


def test_failed_upsert_skips_merge(export, app, monkeypatch):
    abc_lead = seed_lead(500, login="user1")
    abc_customer = seed_customer(1, login="user1")
    export.set_customers([make_customer(1, login="user1")])
    export.set_leads([make_lead(500, login="user1")])
    monkeypatch.setattr(main, "bulk_upsert_customers", lambda rows: False)

    run_sync()

    assert abc_ids() == {abc_lead, abc_customer}
    assert db.session.execute(select(AmoCustomer)).scalars().one().abc_id == abc_customer
    assert db.session.execute(select(AmoLead)).scalars().one().abc_id == abc_lead
