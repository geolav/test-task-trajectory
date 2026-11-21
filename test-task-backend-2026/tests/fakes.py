from typing import Optional, Sequence

from amocrm import Customer, Lead


class FakeCustomer(Customer):
    def as_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "status_id": self.status_id,
            "custom_fields_values": self.custom_fields_values,
        }


class FakeLead(Lead):
    def as_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "status_id": self.status_id,
            "custom_fields_values": self.custom_fields_values,
        }


def make_customer(cid: int, login: Optional[str], name: Optional[str] = None,
                  status_id: int = 1, contacts: Sequence = (), companies: Sequence = ()):
    fields = {}
    if login is not None:
        fields["Логин"] = {"id": 771, "value": login}
    return FakeCustomer(
        id=cid,
        name=name if name is not None else f"customer-{cid}",
        next_price=None,
        next_date=None,
        responsible_user_id=None,
        status_id=status_id,
        periodicity=None,
        created_by=None,
        updated_by=None,
        created_at=None,
        updated_at=None,
        closest_task_at=None,
        is_deleted=False,
        ltv=None,
        purchases_count=None,
        average_check=None,
        account_id=None,
        custom_fields_values=fields,
        _embedded={"companies": list(companies), "contacts": list(contacts)},
    )


def make_lead(lid: int, login: Optional[str] = None, name: Optional[str] = None,
              status_id: int = 1, contacts: Sequence = ()):
    fields = {}
    if login is not None:
        fields["Логин"] = {"id": 772, "value": login}
    return FakeLead(
        id=lid,
        name=name if name is not None else f"lead-{lid}",
        price=None,
        responsible_user_id=None,
        group_id=None,
        status_id=status_id,
        pipeline_id=None,
        source_id=None,
        created_by=None,
        updated_by=None,
        created_at=None,
        updated_at=None,
        closest_task_at=None,
        is_deleted=False,
        score=None,
        account_id=None,
        custom_fields_values=fields,
        _embedded={"contacts": list(contacts)},
    )
