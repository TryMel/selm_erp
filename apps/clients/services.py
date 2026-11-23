from decimal import Decimal
from django.db import transaction
from apps.audit.services import log_business
from apps.core.exceptions import BusinessRuleError, ManagerOverrideRequiredError
from apps.core.money import round_fcfa
from .models import Client, CreditLedgerEntry


class CreditService:
    """
    Service MAÎTRE unique du crédit client (isolation multi-sociétés + allocation FIFO factures).
    """

    @staticmethod
    def assert_credit_allowed(client_id: int, new_credit_amount, approver=None, company=None) -> Client:
        amount = round_fcfa(new_credit_amount)
        qs = Client.objects.select_for_update()
        if company is not None:
            qs = qs.filter(company=company)
        client = qs.filter(pk=client_id).first()
        if not client:
            raise BusinessRuleError('Client introuvable pour cette société.', 'CLIENT_NOT_FOUND')
        if client.status != 'ACTIVE':
            raise BusinessRuleError(f'Client {client.name} bloqué ou inactif pour le crédit.', 'CLIENT_BLOCKED')
        projected = client.current_balance + amount
        if projected > client.credit_limit and approver is None:
            raise ManagerOverrideRequiredError([
                f'Dépassement plafond crédit {client.name} : encours projeté {int(projected):,} FCFA > limite {int(client.credit_limit):,} FCFA'.replace(',', ' ')
            ])
        return client

    @staticmethod
    @transaction.atomic
    def charge_credit(*, client_id: int, amount, reference: str, note: str, actor, approver=None, company=None) -> CreditLedgerEntry:
        amt = round_fcfa(amount)
        if amt <= 0:
            raise BusinessRuleError('Le montant crédit doit être positif.')
        client = CreditService.assert_credit_allowed(client_id, amt, approver=approver, company=company)
        client.current_balance = round_fcfa(client.current_balance + amt)
        client.save(update_fields=['current_balance', 'updated_at'])
        return CreditLedgerEntry.objects.create(
            client=client,
            entry_type='CHARGE',
            amount=amt,
            balance_after=client.current_balance,
            reference=reference,
            note=note,
            actor=actor,
            approver=approver,
        )

    @staticmethod
    @transaction.atomic
    def settle_credit(
        *,
        client_id: int,
        amount,
        payment_method: str,
        reference: str,
        note: str,
        actor,
        entry_type: str = 'PAYMENT',
        cash_session=None,
        company=None,
        allocate_fifo: bool = False,
    ) -> CreditLedgerEntry:
        amt = round_fcfa(amount)
        if amt <= 0:
            raise BusinessRuleError('Le montant du règlement doit être strictement positif.')
        qs = Client.objects.select_for_update()
        if company is not None:
            qs = qs.filter(company=company)
        client = qs.filter(pk=client_id).first()
        if not client:
            raise BusinessRuleError('Client introuvable pour cette société.', 'CLIENT_NOT_FOUND')
        if entry_type == 'PAYMENT' and amt > client.current_balance:
            raise BusinessRuleError(
                f"Montant ({int(amt)} FCFA) supérieur à l'encours actuel ({int(client.current_balance)} FCFA)."
            )
        client.current_balance = max(Decimal('0'), round_fcfa(client.current_balance - amt))
        client.save(update_fields=['current_balance', 'updated_at'])

        entry = CreditLedgerEntry.objects.create(
            client=client,
            entry_type=entry_type,
            amount=-amt,
            balance_after=client.current_balance,
            payment_method=payment_method,
            reference=reference,
            note=note,
            actor=actor,
        )

        # Synchronisation FIFO avec les factures impayées si encaissé depuis la fiche client
        if entry_type == 'PAYMENT' and allocate_fifo:
            from apps.sales.models import SalesDocument
            rem = amt
            unpaid = SalesDocument.objects.select_for_update().filter(
                company=client.company,
                client=client,
                doc_type__in=['INVOICE', 'POS_SALE'],
                balance_due__gt=0,
            ).exclude(status='CANCELLED').order_by('created_at')
            for doc in unpaid:
                if rem <= 0:
                    break
                take = min(rem, doc.balance_due)
                doc.paid_amount = round_fcfa(doc.paid_amount + take)
                doc.balance_due = max(Decimal('0'), round_fcfa(doc.total_ttc - doc.paid_amount))
                doc.status = 'PAID' if doc.balance_due == 0 else 'PARTIALLY_PAID'
                doc.save(update_fields=['paid_amount', 'balance_due', 'status', 'updated_at'])
                rem -= take

        if entry_type == 'PAYMENT':
            if cash_session and cash_session.company_id == client.company_id:
                from apps.cash.services import CashService
                CashService.record_sale_inflow(
                    session=cash_session,
                    payment_method=payment_method,
                    amount=amt,
                    reason=f'Règlement créance {client.name}',
                    reference=reference or client.code,
                    actor=actor,
                )
            from apps.accounting.services import AccountingBridge, treasury_account_for
            AccountingBridge.post_entry(
                company=client.company,
                journal_code='CAI' if payment_method == 'CASH' else 'BNQ',
                description=f'Règlement créance client {client.name}',
                source_doc_type='CLIENT_PAYMENT',
                source_doc_number=reference or client.code,
                actor_name=actor.display_name,
                lines=[
                    {'account_code': treasury_account_for(payment_method), 'label': f'Encaissement {payment_method}', 'debit': amt},
                    {'account_code': '411000', 'label': f'Apurement créance {client.name}', 'credit': amt},
                ],
            )
            log_business(
                company=client.company,
                action='CLIENT_PAYMENT_COLLECTED',
                module='CLIENTS',
                entity_type='Client',
                entity_id=client.id,
                document_number=reference or client.code,
                actor=actor,
                summary=f'Encaissement créance {int(amt)} FCFA ({payment_method}) pour {client.name}',
            )
        return entry
