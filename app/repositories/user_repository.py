from sqlalchemy.orm import Session

from app.models.user import User, Role


class UserRepository:

    def __init__(
        self,
        db: Session
    ):
        self.db = db

    def get_by_email(
        self,
        email: str
    ):

        return (
            self.db.query(User)
            .filter(User.email == email)
            .first()
        )

    def get_all_for_customer(
        self,
        customer_id: int
    ):
        """
        role=USER accounts under one tenant — e.g. for a company's own
        user-management screen. Deliberately excludes the tenant's single
        role=CUSTOMER admin row (that account is managed via
        /customers/{id}, not this list).
        """
        return (
            self.db.query(User)
            .filter(
                User.customer_id == customer_id,
                User.role == Role.USER
            )
            .all()
        )

    def get_customer_admin(
        self,
        customer_id: int
    ):
        """
        The single role=CUSTOMER row for this tenant — the login account
        auto-created alongside the Customer (see
        CustomerService.create_customer), whose full_name is seeded from
        the Customer's company_name at creation time. Used to keep the
        two in sync whenever the company is renamed later.
        """
        return (
            self.db.query(User)
            .filter(
                User.customer_id == customer_id,
                User.role == Role.CUSTOMER
            )
            .first()
        )

    def get_by_id_for_customer(
        self,
        user_id: int,
        customer_id: int
    ):
        """
        Scoped lookup used before any update/delete of a company's User —
        so a company admin (or a Super Admin acting on a customer they
        own) can never touch a user belonging to a different tenant just
        by guessing a user_id, and can never touch the tenant's own
        role=CUSTOMER admin row through this path.
        """
        return (
            self.db.query(User)
            .filter(
                User.id == user_id,
                User.customer_id == customer_id,
                User.role == Role.USER
            )
            .first()
        )

    def create(
        self,
        user: User
    ):

        self.db.add(user)

        self.db.commit()

        self.db.refresh(user)

        return user

    def update(
        self,
        user: User,
        **fields
    ):
        previous_is_active = user.is_active

        for key, value in fields.items():
            if value is not None:
                setattr(user, key, value)

        self.db.commit()
        self.db.refresh(user)

        # Cascade: a Super Admin's own account is the one User row with no
        # customer_id, so this is the one User-update path that can't be
        # handled by set_active_for_customer below. If/when something
        # flips is_active on a role=SUPER_ADMIN row, mirror that same
        # transition onto every Customer they created (created_by), and
        # in turn every User under each of those Customers — same rule as
        # CustomerService.update, one level up. Only fires on an actual
        # transition, never on an unrelated field edit, and role=USER /
        # role=CUSTOMER updates (the existing Team-panel Activate/
        # Deactivate flow) never match this branch at all.
        if (
            user.role == Role.SUPER_ADMIN
            and fields.get("is_active") is not None
            and fields["is_active"] != previous_is_active
        ):
            from app.repositories.customer_repository import CustomerRepository

            customer_ids = CustomerRepository(self.db).set_active_for_creator(
                user.id, user.is_active
            )
            for customer_id in customer_ids:
                self.set_active_for_customer(customer_id, user.is_active)

        return user

    def set_active_for_customer(
        self,
        customer_id: int,
        is_active: bool
    ):
        """
        Bulk-sets is_active on every User row under one tenant — both the
        tenant's role=CUSTOMER admin row and all of its role=USER
        accounts. Used to cascade a Customer-level Active/Inactive toggle
        (see CustomerService.update) down to everyone under that company,
        in both directions (deactivate and reactivate).
        """
        (
            self.db.query(User)
            .filter(User.customer_id == customer_id)
            .update({"is_active": is_active}, synchronize_session=False)
        )
        self.db.commit()

    def delete(
        self,
        user: User,
        
    ):
        """
        Soft delete only — flips is_active to False instead of removing
        the row, so the account/login history survives. Routed through
        update() so this still correctly cascades if it's ever called on
        a role=SUPER_ADMIN row (same as any other deactivation) — for
        the existing role=USER delete routes it's just a plain
        is_active=False flip.
        """
        self.update(user, is_active=False)

        self.db.delete(user)
        self.db.commit()
    
        