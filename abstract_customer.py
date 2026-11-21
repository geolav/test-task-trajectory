import logging
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from amocrm import Customer, Lead
from models import AbstractCustomer, AmoCustomer, AmoLead, db

logger = logging.getLogger(__name__)

INSERT_CHUNK = 2000
SELECT_CHUNK = 5000


def chunked(seq: Sequence, size: int):
    for start in range(0, len(seq), size):
        yield seq[start:start + size]


def get_customer_json(customer: Customer) -> dict:
    return {
        'general': {
            'id': customer.id,
            'login': customer.custom_fields_values.get('Логин', {}).get('value'),
            'billing_type': customer.custom_fields_values.get('Вид оплаты', {}).get('value'),
            'is_robot': bool(customer.custom_fields_values.get('Робот')),
            'is_nds': bool(customer.custom_fields_values.get('Оплата с НДС')),
            'system': customer.custom_fields_values.get('Система', {}).get('value'),
            'host': customer.custom_fields_values.get('Хост', {}).get('value'),
            'name': customer.name,
        },
        'api_value': customer.as_dict()
    }


def get_lead_json(lead: Lead) -> dict:
    return {
        'general': {
            'id': lead.id,
            'login': lead.custom_fields_values.get('Логин', {}).get('value'),
            'name': lead.name,
        },
        'api_value': lead.as_dict()
    }


def fetch_crm_map(model, crm_ids: Iterable[int]) -> Dict[int, object]:
    result: Dict[int, object] = {}
    unique_ids = sorted(set(crm_ids))
    for chunk in chunked(unique_ids, SELECT_CHUNK):
        result.update(db.session.execute(
            select(model.crm_id, model.abc_id).where(model.crm_id.in_(chunk))).all())
    return result


def fetch_login_map(model, logins: Iterable[str]) -> Dict[str, Set[int]]:
    result: Dict[str, Set[int]] = {}
    unique_logins = sorted({login for login in logins if login})
    for chunk in chunked(unique_logins, SELECT_CHUNK):
        rows = db.session.execute(
            select(model.login, model.abc_id).where(model.login.in_(chunk)))
        for login, abc_id in rows:
            result.setdefault(login, set()).add(abc_id)
    return result


def insert_new_abstract_customers(count: int) -> List[int]:
    if count <= 0:
        return []
    reserved = db.session.execute(text(
        "SELECT nextval(pg_get_serial_sequence('abstract_customer', 'id')) "
        "FROM generate_series(1, :count)"), {"count": count})
    ids = [row[0] for row in reserved]
    for chunk in chunked(ids, INSERT_CHUNK):
        db.session.execute(pg_insert(AbstractCustomer).values([{"id": i} for i in chunk]))
    db.session.commit()
    return ids


def _bulk_upsert(model, rows: List[dict], conflict_cols: Sequence[str],
                 update_cols: Sequence[str]) -> bool:
    """Пакетный upsert. False означает ошибку хотя бы одной пачки."""
    unique: Dict[Tuple, dict] = {}
    for row in rows:
        unique[tuple(row[col] for col in conflict_cols)] = row
    ok = True
    for chunk in chunked(list(unique.values()), INSERT_CHUNK):
        stmt = pg_insert(model).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=list(conflict_cols),
            set_={col: getattr(stmt.excluded, col) for col in update_cols}
                | {"updated_at": func.now()},
        )
        try:
            db.session.execute(stmt)
            db.session.commit()
        except Exception:
            db.session.rollback()
            ok = False
            logger.exception("Пачковый upsert %s не удался (chunk отклонён)",
                             model.__tablename__)
    return ok


def bulk_upsert_customers(rows: List[dict]) -> bool:
    return _bulk_upsert(AmoCustomer, rows, ["crm_id"],
                        ["abc_id", "login", "name", "data"])


def bulk_upsert_leads(rows: List[dict]) -> bool:
    return _bulk_upsert(AmoLead, rows, ["crm_id"],
                        ["abc_id", "login", "name", "data"])


def move_children(victim_root_pairs: List[Tuple[int, int]]):
    """Переносит дочерние записи на агрегаты-приёмники и удаляет источники."""
    if not victim_root_pairs:
        return
    victims = [v for v, _ in victim_root_pairs]
    roots = [r for _, r in victim_root_pairs]
    for table in ("amo_customer", "amo_lead"):
        db.session.execute(text(
            f"UPDATE {table} c SET abc_id = p.root, updated_at = now() "
            f"FROM unnest(CAST(:victims AS bigint[]), CAST(:roots AS bigint[])) AS p(victim, root) "
            f"WHERE c.abc_id = p.victim"),
            {"victims": victims, "roots": roots})
    for table in ("amo_contact", "amo_company"):
        db.session.execute(text(
            f"INSERT INTO {table} (abc_id, crm_id, name, data, created_at) "
            f"SELECT merged.root, merged.crm_id, merged.name, merged.data, now() "
            f"FROM ("
            f"  SELECT DISTINCT ON (p.root, c.crm_id) "
            f"         p.root, c.crm_id, c.name, c.data "
            f"  FROM {table} c "
            f"  JOIN unnest(CAST(:victims AS bigint[]), CAST(:roots AS bigint[])) "
            f"       AS p(victim, root) ON c.abc_id = p.victim "
            f"  ORDER BY p.root, c.crm_id, p.victim DESC"
            f") merged "
            f"ON CONFLICT (abc_id, crm_id) DO UPDATE SET name = EXCLUDED.name, "
            f"data = EXCLUDED.data, updated_at = now()"),
            {"victims": victims, "roots": roots})
        db.session.execute(text(
            f"DELETE FROM {table} WHERE abc_id = ANY(CAST(:victims AS bigint[]))"),
            {"victims": victims})
    db.session.execute(text(
        "DELETE FROM abstract_customer WHERE id = ANY(CAST(:victims AS bigint[]))"),
        {"victims": victims})


def delete_untouched_abstract_customers(touched: Sequence[int]):
    db.session.execute(text(
        "DELETE FROM abstract_customer a "
        "WHERE a.id NOT IN (SELECT unnest(CAST(:touched AS bigint[])))"),
        {"touched": list(touched)})


def delete_stale_children(table: str, touched: Sequence[int], seen_ids: Sequence[int]):
    db.session.execute(text(
        f"DELETE FROM {table} t "
        f"USING unnest(CAST(:touched AS bigint[])) AS u(abc_id) "
        f"WHERE t.abc_id = u.abc_id "
        f"AND NOT (t.crm_id = ANY(CAST(:seen AS bigint[])))"),
        {"touched": list(touched), "seen": list(seen_ids)})
    db.session.execute(text(
        f"DELETE FROM {table} t "
        f"USING unnest(CAST(:touched AS bigint[])) AS u(abc_id) "
        f"WHERE t.abc_id = u.abc_id "
        f"AND lower(coalesce(t.name, '')) LIKE '%удалить%'"),
        {"touched": list(touched)})


def delete_empty_abstract_customers(touched: Sequence[int]):
    db.session.execute(text(
        "DELETE FROM abstract_customer a "
        "WHERE a.id IN (SELECT unnest(CAST(:touched AS bigint[]))) "
        "AND NOT EXISTS (SELECT 1 FROM amo_customer c WHERE c.abc_id = a.id) "
        "AND NOT EXISTS (SELECT 1 FROM amo_lead l WHERE l.abc_id = a.id)"),
        {"touched": list(touched)})
