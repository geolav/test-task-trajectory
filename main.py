import logging
from typing import Dict, List, Optional, Set, Tuple, Union

from abstract_customer import (
    bulk_upsert_customers,
    bulk_upsert_leads,
    delete_empty_abstract_customers,
    delete_stale_children,
    delete_untouched_abstract_customers,
    fetch_crm_map,
    fetch_login_map,
    get_customer_json,
    get_lead_json,
    insert_new_abstract_customers,
    move_children,
)
from amocrm import COMPANIES, CONTACTS, Customer, Lead, amocrm_get
from models import AmoCustomer, AmoLead, db

logger = logging.getLogger(__name__)

INCREMENTAL_CUSTOMER_STATUS_SKIP = 111349
INCREMENTAL_LEAD_STATUS_SKIP = 143
Target = Union[int, Tuple[str, str, int]]


def _read_customers(is_full: bool):
    payloads: List[dict] = []
    crm_ids: Set[int] = set()
    logins: Set[str] = set()
    contact_ids: Set[int] = set()
    company_ids: Set[int] = set()

    for customer in amocrm_get(Customer, _with=f"{COMPANIES},{CONTACTS}"):
        if not is_full and customer.status_id == INCREMENTAL_CUSTOMER_STATUS_SKIP:
            continue
        login = customer.get_field("Логин")
        if not login:
            continue
        payloads.append(get_customer_json(customer))
        crm_ids.add(customer.id)
        logins.add(login)
        contact_ids.update(c["id"] for c in customer.contacts)
        company_ids.update(c["id"] for c in customer.companies)

    logger.info("Collected customers: %s", len(payloads))
    return payloads, crm_ids, logins, contact_ids, company_ids


def _read_leads(is_full: bool, contact_ids: Set[int]):
    payloads: List[dict] = []
    crm_ids: Set[int] = set()
    logins: Set[str] = set()
    for lead in amocrm_get(Lead, _with=CONTACTS):
        if not is_full and lead.status_id == INCREMENTAL_LEAD_STATUS_SKIP:
            continue
        payloads.append(get_lead_json(lead))
        crm_ids.add(lead.id)
        login = lead.get_field("Логин")
        if login:
            logins.add(login)
        contact_ids.update(c["id"] for c in lead.contacts)
    logger.info("Collected leads: %s", len(payloads))
    return payloads, crm_ids, logins


def _resolve_target(target: Target, aliases: Dict[Target, Target]) -> Target:
    path = []
    while target in aliases:
        next_target = aliases[target]
        if next_target == target:
            break
        path.append(target)
        target = next_target
    for item in path:
        aliases[item] = target
    return target


def _merge_target(victim: Target, root: Target, aliases: Dict[Target, Target]):
    victim = _resolve_target(victim, aliases)
    root = _resolve_target(root, aliases)
    if victim != root:
        aliases[victim] = root


def collect_leads_customers(is_full=True):
    """
     Берем всех покупателей, если его нет в бд - делаем новую запись AbstractCutomer, в него кладем.
     Есть есть - находим его запись по id и перезаписываем

     Вот так у нас коллектятся все кастомеры

     Далее берутся все лиды. Ищутся их записи в бд по id, если не находится и есть логин
     - находим его запись по кастомеру с тем же логином, кладем к нему.
     Если нет логина/нет кастомера - пропускаем. Если нашелся - перезаписываем соответственно
         abstract_id_customer : {
        customers: [...],
        leads: [...],
        contacts: [...],
        companies: [...]
    }
    """
    customers, customer_ids, customer_logins, contact_ids, company_ids = _read_customers(is_full)
    leads, lead_ids, lead_logins = _read_leads(is_full, contact_ids)
    try:
        customer_crm_map = fetch_crm_map(AmoCustomer, customer_ids)
        lead_crm_map = fetch_crm_map(AmoLead, lead_ids)
        lead_login_map = fetch_login_map(AmoLead, customer_logins)
        customer_login_map = fetch_login_map(AmoCustomer, lead_logins)
        in_run_customer_login: Dict[str, List[Target]] = {}
        customer_targets: List[Target] = []

        for payload in customers:
            crm_id = payload["general"]["id"]
            login = payload["general"]["login"]
            target = customer_crm_map.get(crm_id)
            if target is None:
                matches = lead_login_map.get(login, set())
                if len(matches) == 1:
                    target = next(iter(matches))
            if target is None:
                target = ("new", "customer", crm_id)

            customer_crm_map[crm_id] = target
            in_run_customer_login.setdefault(login, []).append(target)
            customer_targets.append(target)
        customer_targets_by_login: Dict[str, Optional[Target]] = {}
        for login in set(customer_login_map) | set(in_run_customer_login):
            matches = set(customer_login_map.get(login, set()))
            matches.update(in_run_customer_login.get(login, []))
            customer_targets_by_login[login] = next(iter(matches)) if len(matches) == 1 else None
        aliases: Dict[Target, Target] = {}
        lead_targets: List[Target] = []

        for payload in leads:
            crm_id = payload["general"]["id"]
            login = payload["general"]["login"]

            by_id = lead_crm_map.get(crm_id)
            if by_id is not None:
                by_id = _resolve_target(by_id, aliases)
            by_login = customer_targets_by_login.get(login) if login else None
            if by_login is not None:
                by_login = _resolve_target(by_login, aliases)
            if by_id is not None and by_login is not None and by_id != by_login:
                _merge_target(by_login, by_id, aliases)
            target = by_id if by_id is not None else by_login
            if target is None:
                target = ("new", "lead", crm_id)
            target = _resolve_target(target, aliases)
            lead_crm_map[crm_id] = target
            lead_targets.append(target)
        customer_targets = [_resolve_target(t, aliases) for t in customer_targets]
        lead_targets = [_resolve_target(t, aliases) for t in lead_targets]

        new_keys = []
        seen_new_keys = set()
        for target in customer_targets + lead_targets:
            if isinstance(target, tuple) and target not in seen_new_keys:
                seen_new_keys.add(target)
                new_keys.append(target)

        placeholder_abc = dict(zip(new_keys, insert_new_abstract_customers(len(new_keys))))

        def final_abc(target: Target) -> int:
            target = _resolve_target(target, aliases)
            return placeholder_abc[target] if isinstance(target, tuple) else target

        merge_pairs = []
        for source in aliases:
            if isinstance(source, int):
                root = _resolve_target(source, aliases)
                if root != source:
                    merge_pairs.append((source, final_abc(root)))
        merge_pairs.sort()

        touched: Set[int] = set()
        customer_rows = []
        for target, payload in zip(customer_targets, customers):
            abc_id = final_abc(target)
            touched.add(abc_id)
            customer_rows.append({
                "abc_id": abc_id,
                "crm_id": payload["general"]["id"],
                "login": payload["general"]["login"],
                "name": payload["general"]["name"],
                "data": payload,
            })
        sync_ok = bulk_upsert_customers(customer_rows)

        lead_rows = []
        for target, payload in zip(lead_targets, leads):
            abc_id = final_abc(target)
            touched.add(abc_id)
            lead_rows.append({
                "abc_id": abc_id,
                "crm_id": payload["general"]["id"],
                "login": payload["general"]["login"],
                "name": payload["general"]["name"],
                "data": payload,
            })
        sync_ok = bulk_upsert_leads(lead_rows) and sync_ok

        if sync_ok and merge_pairs:
            try:
                move_children(merge_pairs)
                db.session.commit()
            except Exception:
                db.session.rollback()
                sync_ok = False
                logger.exception("Merge failed")
    except Exception:
        db.session.rollback()
        logger.exception("Collection failed; cleanup skipped")
        return

    if not is_full or not sync_ok:
        if is_full and not sync_ok:
            logger.warning("Sync has errors; cleanup skipped")
        return
    _cleanup(touched, customer_ids, lead_ids, contact_ids, company_ids)


def _cleanup(touched: Set[int], customer_ids: Set[int], lead_ids: Set[int],
             contact_ids: Set[int], company_ids: Set[int]):
    try:
        touched_ids = sorted(touched)
        delete_untouched_abstract_customers(touched_ids)
        delete_stale_children("amo_customer", touched_ids, customer_ids)
        delete_stale_children("amo_lead", touched_ids, lead_ids)
        delete_stale_children("amo_contact", touched_ids, contact_ids)
        delete_stale_children("amo_company", touched_ids, company_ids)
        delete_empty_abstract_customers(touched_ids)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Cleanup failed")
