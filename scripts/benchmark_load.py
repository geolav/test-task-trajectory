import argparse
import os
import sys
import time
import tracemalloc
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from sqlalchemy import event, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

import abstract_customer as ac
from scripts.export_generator import build_export, set_scale, SCALE
import main
from models import AbstractCustomer, AmoCompany, AmoContact, AmoCustomer, AmoLead, db

DB_URI = os.environ.get(
    "DATABASE_URI",
    "postgresql+psycopg2://postgres@/test_sync?host=/tmp&port=5433",
)


def seed_previous_state(n_customers: int, n_leads: int):
    session = db.session
    merge = SCALE["merge"]
    half = SCALE["half_seeded"]
    stale_n = SCALE["stale_abc"]
    rng = SCALE["ranges"]
    total_abc = n_customers + 2 * merge + half + stale_n
    abc_ids = [r[0] for r in session.execute(
        ac.text("SELECT nextval(pg_get_serial_sequence('abstract_customer','id')) "
                "FROM generate_series(1, :n)"), {"n": total_abc})]
    session.execute(pg_insert(AbstractCustomer).values([{"id": i} for i in abc_ids]))
    session.commit()

    cust_abc = abc_ids[:n_customers]
    merge_abc = abc_ids[n_customers:n_customers + merge]
    merge_lead_abc = abc_ids[n_customers + merge:n_customers + 2 * merge]
    no_login_abc = abc_ids[n_customers + 2 * merge:n_customers + 2 * merge + half]
    stale_abc = abc_ids[n_customers + 2 * merge + half:]

    customer_rows = [{
        "abc_id": cust_abc[i - 1], "crm_id": i, "login": f"user{i}@example.com",
        "name": f"Покупатель {i} (старое)",
        "data": {"general": {"id": i, "login": f"user{i}@example.com",
                             "name": f"Покупатель {i} (старое)"},
                 "api_value": {"seeded": True}},
    } for i in range(1, n_customers + 1)]
    customer_rows += [{
        "abc_id": merge_abc[j], "crm_id": 400_000 + j + 1,
        "login": f"merge-user{j + 1}@example.com", "name": f"Жертва слияния {j + 1}",
        "data": {"general": {"id": 400_000 + j + 1,
                             "login": f"merge-user{j + 1}@example.com",
                             "name": f"Жертва слияния {j + 1}"},
                 "api_value": {"seeded": True}},
    } for j in range(merge)]
    customer_rows += [{
        "abc_id": stale_abc[k], "crm_id": 900_000 + k + 1,
        "login": f"stale{k + 1}@example.com", "name": f"Stale покупатель {k + 1}",
        "data": {"general": {"id": 900_000 + k + 1}, "api_value": {}},
    } for k in range(stale_n)]
    ac._bulk_upsert(AmoCustomer, customer_rows, ["crm_id"],
                    ["abc_id", "login", "name", "data"])

    lead_rows = [{
        "abc_id": merge_lead_abc[lid - rng["merge"][0]], "crm_id": lid,
        "login": f"merge-user{lid - rng['merge'][0] + 1}@example.com",
        "name": f"Сделка {lid} (старое)",
        "data": {"general": {"id": lid}, "api_value": {}},
    } for lid in rng["merge"]]
    for lid in rng["matched"]:
        k = (lid - rng["matched"][0]) % n_customers + 1
        lead_rows.append({
            "abc_id": cust_abc[k - 1], "crm_id": lid,
            "login": f"user{k}@example.com", "name": f"Сделка {lid} (старое)",
            "data": {"general": {"id": lid}, "api_value": {}},
        })
    for lid in rng["seeded"]:
        lead_rows.append({
            "abc_id": no_login_abc[lid - rng["seeded"][0]], "crm_id": lid, "login": None,
            "name": f"Сделка без логина {lid} (старое)",
            "data": {"general": {"id": lid}, "api_value": {}},
        })
    ac._bulk_upsert(AmoLead, lead_rows, ["crm_id"], ["abc_id", "login", "name", "data"])

    # контакты трёх видов: живые (есть в новой выгрузке), stale помеченные «удалить»
    contact_rows = [{
        "abc_id": cust_abc[i - 1], "crm_id": 2_000_000 + 2 * i,
        "name": f"Живой контакт {i}", "data": {"general": {"id": 2_000_000 + 2 * i}},
    } for i in range(1, n_customers + 1)]
    contact_rows += [{
        "abc_id": cust_abc[i - 1], "crm_id": 5_000_000 + i,
        "name": f"Stale контакт {i}", "data": {"general": {"id": 5_000_000 + i}},
    } for i in range(1, n_customers + 1)]
    contact_rows += [{
        "abc_id": cust_abc[i - 1], "crm_id": 5_100_000 + i,
        "name": f"Контакт Удалить {i}", "data": {"general": {"id": 5_100_000 + i}},
    } for i in range(1, merge + 1)]
    contact_rows += [{
        "abc_id": merge_abc[j], "crm_id": 5_200_000 + j + 1,
        "name": f"Контакт жертвы {j + 1}", "data": {"general": {"id": 5_200_000 + j + 1}},
    } for j in range(merge)]
    session.execute(pg_insert(AmoContact).values(contact_rows))
    company_rows = [{
        "abc_id": cust_abc[i - 1], "crm_id": 6_000_000 + i,
        "name": f"Stale компания {i}", "data": {"general": {"id": 6_000_000 + i}},
    } for i in range(1, n_customers + 1)]
    session.execute(pg_insert(AmoCompany).values(company_rows))
    session.commit()


def run_benchmark(n_customers: int, n_leads: int, measure_memory: bool = True):
    set_scale(n_customers, n_leads)
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = DB_URI
    db.init_app(app)

    queries = Counter()
    commits = Counter()

    def count_sql(conn, cursor, statement, parameters, context, executemany):
        head = statement.lstrip()[:16].upper()
        for kind in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            if head.startswith(kind):
                queries[kind] += 1
                return
        queries["OTHER"] += 1

    def count_commit(conn):
        commits["COMMIT"] += 1

    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.commit()
        seed_start = time.perf_counter()
        seed_previous_state(n_customers, n_leads)
        seed_seconds = time.perf_counter() - seed_start

        event.listen(db.engine, "before_cursor_execute", count_sql)
        event.listen(db.engine, "commit", count_commit)
        py_peak = None
        if measure_memory:
            tracemalloc.start()
        try:
            customers, leads = build_export(n_customers, n_leads)

            def fake_amocrm_get(obj_cls, **kwargs):
                from amocrm import Customer
                yield from customers if obj_cls is Customer else leads

            main.amocrm_get = fake_amocrm_get

            start = time.perf_counter()
            main.collect_leads_customers(is_full=True)
            seconds = time.perf_counter() - start
            if measure_memory:
                _, py_peak = tracemalloc.get_traced_memory()
        finally:
            if measure_memory and tracemalloc.is_tracing():
                tracemalloc.stop()
            event.remove(db.engine, "before_cursor_execute", count_sql)
            event.remove(db.engine, "commit", count_commit)

        n_abc = db.session.execute(select(func.count(AbstractCustomer.id))).scalar()
        n_cust = db.session.execute(select(func.count(AmoCustomer.crm_id))).scalar()
        n_lead = db.session.execute(select(func.count(AmoLead.crm_id))).scalar()
        n_contact = db.session.execute(select(func.count(AmoContact.crm_id))).scalar()
        n_company = db.session.execute(select(func.count(AmoCompany.crm_id))).scalar()
        marked_left = db.session.execute(
            select(func.count()).select_from(AmoContact)
            .where(AmoContact.name.like("%Удалить%"))).scalar()
        stale_left = db.session.execute(
            select(func.count()).select_from(AmoCustomer)
            .where(AmoCustomer.crm_id > 900_000)).scalar()

        expected_abc = (n_customers
                        + SCALE["merge"]
                        + SCALE["half_seeded"]
                        + SCALE["no_login"] - SCALE["half_seeded"])
        print("=" * 68)
        print(f"Выгрузка: {n_customers} покупателей, {n_leads} сделок; "
              f"подготовка БД {seed_seconds:.1f} c")
        print(f"Синхронизация: {seconds:.1f} c")
        print(f"SQL-запросов: {sum(queries.values())} {dict(queries)}")
        print(f"Коммитов: {sum(commits.values())}")
        memory_text = f"{py_peak / (1024 ** 2):.0f} МиБ" if py_peak is not None else "не измерялся"
        print(f"Пик Python heap (tracemalloc): {memory_text}")
        print(f"Итог: abc={n_abc} (ожидалось {expected_abc}), покупателей={n_cust}, "
              f"сделок={n_lead}, контактов={n_contact}, компаний={n_company}")
        print(f"Помеченные «удалить» остались: {marked_left} (ожидалось 0); "
              f"stale-покупатели остались: {stale_left} (ожидалось 0)")
        print("=" * 68)
        assert n_abc == expected_abc, "не сходится число abc-записей"
        assert n_cust == n_customers and n_lead == n_leads
        assert marked_left == 0 and stale_left == 0
        assert n_contact == n_customers and n_company == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--customers", type=int, default=20_000)
    parser.add_argument("--leads", type=int, default=50_000)
    parser.add_argument("--skip-memory", action="store_true",
                        help="без tracemalloc — чистый замер времени")
    args = parser.parse_args()
    run_benchmark(args.customers, args.leads, measure_memory=not args.skip_memory)
