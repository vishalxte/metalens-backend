from app.models.customer import Customer
from app.models.user import User, Role
from app.core.security import hash_password
from app.core.exceptions import CustomerAlreadyExistsException
from app.repositories.document_repository import DocumentRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.semantic_cache_repository import SemanticCacheRepository
from app.repositories.exact_cache_repository import ExactCacheRepository
from app.models.audit_log import AuditAction
from app.models.user_session import SessionEndReason
from app.services.audit_service import audit_service
from app.services.session_service import session_service
from app.core.request_context import get_current_user_id


class CustomerService:
    """
    Implements the PDF's "Customer creation" flow:

        Form (company_name, address, mobile, email, password)
            -> Customer row created
            -> generated customer_id
            -> a default User is automatically created with
               role=CUSTOMER, customer_id=<new id>, email/password from
               the same form.

    Only a Super Admin can call this (enforced at the API layer via the
    get_super_admin dependency), so this is the *only* way new tenants and
    their first user get into the system — there is no public signup.
    """

    def __init__(self, customer_repository, user_repository):
        self.customer_repository = customer_repository
        self.user_repository = user_repository

    def create_customer(self, company_name, address, mobile, email, password, created_by_user_id):

        if self.customer_repository.get_by_email(email):
            raise CustomerAlreadyExistsException()

        if self.user_repository.get_by_email(email):
            raise CustomerAlreadyExistsException(
                detail="A user with this email already exists"
            )

        # Company name must also be unique across ALL tenants (not just
        # per-creator), otherwise two different Super Admins (or the same
        # one, twice) can create two separate Customer rows that both
        # display as e.g. "xtensible" — different customer_id's sharing a
        # name, so uploads/logins silently split across the two.
        if self.customer_repository.get_by_company_name(company_name):
            raise CustomerAlreadyExistsException(
                detail=f"A customer named '{company_name}' already exists"
            )

        customer = Customer(
            company_name=company_name,
            address=address,
            mobile=mobile,
            email=email,
            created_by=created_by_user_id
        )

        saved_customer = self.customer_repository.create(customer)

        # Automatically create the default Customer user, tied to the
        # newly generated customer_id — mirrors the PDF's
        # "Generated customer_id=5 -> Automatically Users ..." flow.
        default_user = User(
            full_name=company_name,
            email=email,
            hashed_password=hash_password(password),
            role=Role.CUSTOMER,
            customer_id=saved_customer.id
        )

        saved_user = self.user_repository.create(default_user)

        # Two distinct events: the tenant record, and the login account
        # auto-created alongside it. Recorded separately because they are
        # separate resources — a reader of the trail should not have to
        # know that one implies the other.
        # target_type=CUSTOMER, target_id=<the new customer>. The ACTOR
        # (the Super Admin doing this) comes from the request context.
        audit_service.log_customer_event(
            action=AuditAction.CUSTOMER_CREATED,
            target_customer_id=saved_customer.id,
            company_name=company_name,
            details={"contact_email": email}
        )

        audit_service.log_user_event(
            action=AuditAction.USER_CREATED,
            target_user_id=saved_user.id,
            target_email=saved_user.email,
            details={
                "assigned_role": Role.CUSTOMER,
                "assigned_customer_id": saved_customer.id,
                "created_via": "customer_onboarding"
            }
        )

        return saved_customer, saved_user

    def get_all_for_creator(self, created_by):
        return self.customer_repository.get_all_for_creator(created_by)

    def get_by_id_for_creator(self, customer_id, created_by):
        return self.customer_repository.get_by_id_for_creator(customer_id, created_by)

    def update(self, customer, **fields):
        previous_is_active = customer.is_active
        previous_company_name = customer.company_name
        updated_customer = self.customer_repository.update(customer, **fields)

        # Cascade: deactivating a Customer also deactivates every User
        # under that tenant (the role=CUSTOMER admin row included), and
        # reactivating the Customer reactivates them all again. Only
        # fires on an actual Active<->Inactive transition — saving
        # unrelated fields (company name, address, mobile) with the
        # status left untouched never re-applies is_active onto users
        # who were individually toggled while the Customer was active.
        new_is_active = fields.get("is_active")
        if new_is_active is not None and new_is_active != previous_is_active:
            self.user_repository.set_active_for_customer(updated_customer.id, new_is_active)

            # FORCE LOGOUT. set_active_for_customer() above flips
            # is_active=False on every user in this tenant, but a JWT
            # already in a browser stays valid until `exp` — so without
            # this the company would look disabled in the console while
            # its entire staff carried on working for up to an hour.
            #
            # Bulk, one UPDATE, and never raises: a failure here is
            # logged and the deactivation still completes.
            if not new_is_active:
                session_service.end_all_for_customer(
                    updated_customer.id,
                    reason=SessionEndReason.ACCOUNT_DISABLED,
                    revoked_by_user_id=get_current_user_id()
                )

            # A tenant-level Active/Inactive toggle cascades to every user
            # under it, so this is a high-impact administrative action and
            # is audited as its own event rather than folded into the
            # generic "customer updated" record below.
            audit_service.log_customer_event(
                action=(
                    AuditAction.CUSTOMER_ACTIVATED
                    if new_is_active
                    else AuditAction.CUSTOMER_DEACTIVATED
                ),
                target_customer_id=updated_customer.id,
                company_name=updated_customer.company_name,
                details={
                    "previous_is_active": previous_is_active,
                    "new_is_active": new_is_active,
                    "cascaded_to_all_users": True
                }
            )

        # Keep the tenant's role=CUSTOMER login row's full_name in sync
        # with company_name — it was seeded from company_name at
        # creation (see create_customer below), so renaming the company
        # without also updating that row left the two silently diverging
        # (Customer.company_name changed, but the matching User row's
        # full_name still showed the old name).
        new_company_name = fields.get("company_name")
        if new_company_name is not None and new_company_name != previous_company_name:
            customer_admin = self.user_repository.get_customer_admin(updated_customer.id)
            if customer_admin:
                self.user_repository.update(customer_admin, full_name=new_company_name)

        # Field NAMES only — no values, same rule as the company-user
        # update path.
        audit_service.log_customer_event(
            action=AuditAction.CUSTOMER_UPDATED,
            target_customer_id=updated_customer.id,
            details={"updated_fields": sorted(fields.keys())}
        )

        return updated_customer

    def delete(self, customer):
        """
        "Deleting" a Customer is a soft delete, not a row removal — the
        Customer itself and every User under it (the role=CUSTOMER admin
        row included) just get deactivated, so the account records stay
        around for audit/history purposes and nothing else in the app
        has to treat "deleted" specially, it's just another Inactive
        tenant.

        What DOES get hard-deleted is everything that tenant actually
        produced, since none of it has a soft-delete concept of its own:
        their documents (each one's chunks/embeddings + physical file
        cascade via DocumentRepository.delete), their chat conversations
        (messages cascade at the DB level), and both caches.
        """
        db = self.customer_repository.db
        customer_id = customer.id
        company_name = customer.company_name

        document_repo = DocumentRepository(db)
        documents = document_repo.get_all_for_customer(customer_id)
        deleted_document_count = len(documents)

        for document in documents:
            document_repo.delete(document.id)

        ConversationRepository(db).delete_all_for_customer(customer_id)
        SemanticCacheRepository(db).delete_by_customer(customer_id)
        ExactCacheRepository(db).delete_for_customer(customer_id)

        # Audited BEFORE the soft-delete cascade below, so the record
        # exists even if the cascade itself were to fail partway. Counts
        # only — nothing about the content of what was destroyed.
        audit_service.log_customer_event(
            action=AuditAction.CUSTOMER_DELETED,
            target_customer_id=customer_id,
            company_name=company_name,
            details={
                "delete_type": "soft (customer + users deactivated)",
                "hard_deleted_documents": deleted_document_count,
                "hard_deleted_conversations": True,
                "caches_cleared": True
            }
        )

        # Soft-delete the Customer + cascade-deactivate every User under
        # it — reuses the exact same Active<->Inactive cascade a
        # Settings-page toggle already triggers (see update() above).
        self.update(customer, is_active=False)
