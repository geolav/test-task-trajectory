import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCALE = {}


def set_scale(n_customers: int, n_leads: int):
    if n_customers < 1 or n_leads < 3:
        raise ValueError("Нужно минимум 1 покупателя и 3 сделки")

    merge = max(1, min(500, n_customers // 10, n_leads - 2))
    no_login = min(max(2, n_leads // 10), n_leads - merge)
    half_seeded = no_login // 2
    matched_len = n_leads - merge - no_login
    start = 100001

    SCALE.update(
        merge=merge,
        no_login=no_login,
        half_seeded=half_seeded,
        stale_abc=max(1, n_customers // 10),
        ranges={
            "merge": range(start, start + merge),
            "matched": range(start + merge, start + merge + matched_len),
            "seeded": range(start + merge + matched_len,
                            start + merge + matched_len + half_seeded),
            "new": range(start + merge + matched_len + half_seeded,
                         start + merge + matched_len + no_login),
        },
    )


def build_export(n_customers: int, n_leads: int):
    from tests.fakes import make_customer, make_lead

    def customers_iter():
        for i in range(1, n_customers + 1):
            contacts = [
                {"id": 2_000_000 + 2 * i, "name": f"Контакт {2 * i}"},
                {"id": 2_000_000 + 2 * i + 1, "name": f"Контакт {2 * i + 1}"},
            ]
            if i <= SCALE["merge"]:
                contacts.append({"id": 5_100_000 + i, "name": f"Контакт Удалить {i}"})

            customer = make_customer(
                i,
                login=f"user{i}@example.com",
                name=f"Покупатель {i}",
                contacts=contacts,
                companies=[{"id": 3_000_000 + i, "name": f"Компания {i}"}],
            )
            for key in (
                "Вид оплаты", "Система", "Хост", "Адрес", "Телефон", "Email",
                "Комментарий", "Источник", "Город", "Статус проверки",
            ):
                customer.custom_fields_values[key] = {
                    "id": 700 + len(customer.custom_fields_values),
                    "value": f"значение поля {key} для {i} " + "x" * 120,
                }
            yield customer

    def leads_iter():
        ranges = SCALE["ranges"]
        for lead_id in ranges["merge"]:
            index = lead_id - ranges["merge"].start
            yield make_lead(
                lead_id,
                login=f"merge-user{index + 1}@example.com",
                name=f"Сделка слияния {lead_id}",
            )

        for lead_id in ranges["matched"]:
            customer_id = (lead_id - ranges["matched"].start) % n_customers + 1
            yield make_lead(
                lead_id,
                login=f"user{customer_id}@example.com",
                name=f"Сделка {lead_id}",
            )

        for lead_id in ranges["seeded"]:
            yield make_lead(lead_id, login=None, name=f"Сделка без логина {lead_id}")

        for lead_id in ranges["new"]:
            yield make_lead(lead_id, login=None, name=f"Новая сделка {lead_id}")

    return customers_iter(), leads_iter()
