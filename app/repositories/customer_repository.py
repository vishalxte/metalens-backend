from sqlalchemy.orm import Session

from app.models.customer import Customer


class CustomerRepository:

    def __init__(self, db: Session):
        self.db = db

    def create(self, customer: Customer):
        self.db.add(customer)
        self.db.commit()
        self.db.refresh(customer)
        return customer

    def get_by_id(self, customer_id: int):
        return (
            self.db.query(Customer)
            .filter(Customer.id == customer_id)
            .first()
        )

    def get_by_id_for_creator(self, customer_id: int, created_by: int):
        """
        Scoped to the Super Admin that created this Customer. Per spec: "A
        Super Admin should NOT have access to Customers created by another
        Super Admin" — this is the query that enforces it.
        """
        return (
            self.db.query(Customer)
            .filter(
                Customer.id == customer_id,
                Customer.created_by == created_by
            )
            .first()
        )

    def get_by_email(self, email: str):
        return (
            self.db.query(Customer)
            .filter(Customer.email == email)
            .first()
        )

    def get_by_company_name(self, company_name: str):
        """
        Only used to block creating a second tenant with the exact same
        display name (see CustomerService.create_customer). Previously
        only `email` was checked, so nothing stopped two Customer rows
        both named e.g. "xtensible" with different ids/emails — which is
        exactly what caused Super-Admin-uploaded documents for one
        "xtensible" to not show up in that company's own login, since
        they were actually two different customer_id's sharing a label.
        """
        return (
            self.db.query(Customer)
            .filter(Customer.company_name == company_name)
            .first()
        )

    def get_all_for_creator(self, created_by: int):
        """
        A Super Admin only ever sees the Customers they personally
        created — never another Super Admin's Customers.
        """
        return (
            self.db.query(Customer)
            .filter(Customer.created_by == created_by)
            .order_by(Customer.id.desc())
            .all()
        )

    def update(self, customer: Customer, **fields):
        for key, value in fields.items():
            if value is not None:
                setattr(customer, key, value)

        self.db.commit()
        self.db.refresh(customer)
        return customer

    def set_active_for_creator(self, created_by: int, is_active: bool):
        """
        Bulk-sets is_active on every Customer a given Super Admin created
        — the Customer half of the "deactivate a Super Admin -> cascades
        to everything they own" rule (see UserRepository.update). Returns
        the affected customer ids so the caller can cascade further down
        to each Customer's own Users.
        """
        customers = (
            self.db.query(Customer)
            .filter(Customer.created_by == created_by)
            .all()
        )
        customer_ids = [c.id for c in customers]

        (
            self.db.query(Customer)
            .filter(Customer.created_by == created_by)
            .update({"is_active": is_active}, synchronize_session=False)
        )
        self.db.commit()

        return customer_ids

    def delete(self, customer: Customer):
        """
        Soft delete only — flips is_active to False instead of removing
        the row. CustomerService.delete() no longer calls this directly
        (it purges the tenant's real content first, then soft-deletes
        via update() so Users cascade too) — kept as a safety net so
        nothing that calls this method directly ever hard-deletes a
        Customer.
        """
        customer.is_active = False
        self.db.commit()
        self.db.refresh(customer)
