from django.db import transaction
from apps.accounting.services import AccountingBridge, treasury_account_for
from apps.audit.services import log_business
from apps.cash.services import CashService
from apps.core.exceptions import BusinessRuleError
from apps.core.models import next_doc_number
from apps.core.money import round_fcfa
from .models import Expense


class ExpenseService:
    @staticmethod
    def create_expense(*, company, category: str, description: str, amount, account_type: str, actor, reference='', cash_session=None):
        amt = round_fcfa(amount)
        if amt <= 0:
            raise BusinessRuleError('Le montant de la dépense doit être positif.')
        pm = 'CASH' if account_type == 'CAISSE' else ('WAVE' if account_type == 'MOBILE_MONEY' else 'BANK_TRANSFER')
        exp = Expense.objects.create(
            company=company,
            number=next_doc_number(company, 'EXPENSE'),
            category=category,
            description=description,
            amount=amt,
            account_type=account_type,
            payment_method=pm,
            cash_session=cash_session,
            reference=reference,
            requested_by=actor,
        )
        log_business(
            company=company,
            action='EXPENSE_CREATED',
            module='EXPENSES',
            entity_type='Expense',
            entity_id=exp.id,
            document_number=exp.number,
            actor=actor,
            summary=f'Demande dépense {exp.number} ({category}) — {int(amt)} FCFA',
        )
        return exp

    @staticmethod
    @transaction.atomic
    def decide(*, expense_id: int, approve: bool, approver, company=None):
        qs = Expense.objects.select_for_update().select_related('cash_session', 'company')
        if company is not None:
            qs = qs.filter(company=company)
        exp = qs.filter(pk=expense_id).first()
        if not exp:
            raise BusinessRuleError('Dépense introuvable pour cette société.')
        if exp.status != 'PENDING':
            raise BusinessRuleError('Cette dépense a déjà été traitée.')
        exp.status = 'APPROVED' if approve else 'REJECTED'
        exp.approved_by = approver
        exp.save(update_fields=['status', 'approved_by', 'updated_at'])

        if approve:
            if exp.account_type == 'CAISSE' and exp.cash_session and exp.cash_session.status == 'OPEN':
                CashService.record_manual_movement(
                    session=exp.cash_session,
                    movement_type='EXPENSE_OUT',
                    amount=exp.amount,
                    reason=f'Dépense {exp.number} : {exp.description}',
                    reference=exp.number,
                    actor=approver,
                )
            AccountingBridge.post_entry(
                company=exp.company,
                journal_code='CAI' if exp.account_type == 'CAISSE' else 'BNQ',
                description=f'Dépense {exp.number} — {exp.category}',
                source_doc_type='EXPENSE',
                source_doc_number=exp.number,
                actor_name=approver.display_name,
                lines=[
                    {'account_code': '622000', 'label': f'{exp.category} : {exp.description}', 'debit': exp.amount},
                    {'account_code': treasury_account_for(exp.payment_method), 'label': f'Sortie {exp.number}', 'credit': exp.amount},
                ],
            )
        log_business(
            company=exp.company,
            action='EXPENSE_APPROVED' if approve else 'EXPENSE_REJECTED',
            module='EXPENSES',
            entity_type='Expense',
            entity_id=exp.id,
            document_number=exp.number,
            actor=approver,
            approver=approver,
            summary=f"{'Validation' if approve else 'Rejet'} dépense {exp.number} ({int(exp.amount)} FCFA)",
        )
        return exp
