from decimal import Decimal
from django.db import transaction
from apps.audit.services import log_business
from apps.core.exceptions import BusinessRuleError
from apps.core.money import round_fcfa
from .models import Supplier


class SupplierDebtService:
    @staticmethod
    @transaction.atomic
    def increase_debt(supplier_id: int, amount, company=None):
        qs = Supplier.objects.select_for_update()
        if company is not None:
            qs = qs.filter(company=company)
        sup = qs.filter(pk=supplier_id).first()
        if not sup:
            raise BusinessRuleError('Fournisseur introuvable pour cette société.')
        sup.current_debt = round_fcfa(sup.current_debt + round_fcfa(amount))
        sup.save(update_fields=['current_debt', 'updated_at'])
        return sup

    @staticmethod
    @transaction.atomic
    def decrease_debt(supplier_id: int, amount, company=None):
        qs = Supplier.objects.select_for_update()
        if company is not None:
            qs = qs.filter(company=company)
        sup = qs.filter(pk=supplier_id).first()
        if not sup:
            raise BusinessRuleError('Fournisseur introuvable pour cette société.')
        sup.current_debt = max(Decimal('0'), round_fcfa(sup.current_debt - round_fcfa(amount)))
        sup.save(update_fields=['current_debt', 'updated_at'])
        return sup

    @staticmethod
    @transaction.atomic
    def pay_supplier(*, supplier_id: int, amount, payment_method: str, reference: str, actor, company=None):
        amt = round_fcfa(amount)
        if amt <= 0:
            raise BusinessRuleError('Montant de règlement invalide.')
        qs = Supplier.objects.select_for_update()
        if company is not None:
            qs = qs.filter(company=company)
        sup = qs.filter(pk=supplier_id).first()
        if not sup:
            raise BusinessRuleError('Fournisseur introuvable pour cette société.')
        if amt > sup.current_debt:
            raise BusinessRuleError(f'Montant ({int(amt)} FCFA) supérieur à la dette ({int(sup.current_debt)} FCFA).')
        sup.current_debt = max(Decimal('0'), round_fcfa(sup.current_debt - amt))
        sup.save(update_fields=['current_debt', 'updated_at'])

        from apps.accounting.services import AccountingBridge, treasury_account_for
        AccountingBridge.post_entry(
            company=sup.company,
            journal_code='CAI' if payment_method == 'CASH' else 'BNQ',
            description=f'Règlement fournisseur {sup.name}',
            source_doc_type='SUPPLIER_PAYMENT',
            source_doc_number=reference or sup.code,
            actor_name=actor.display_name,
            lines=[
                {'account_code': '401000', 'label': f'Règlement dette {sup.name}', 'debit': amt},
                {'account_code': treasury_account_for(payment_method), 'label': f'Sortie {payment_method}', 'credit': amt},
            ],
        )
        log_business(
            company=sup.company,
            action='SUPPLIER_PAID',
            module='SUPPLIERS',
            entity_type='Supplier',
            entity_id=sup.id,
            document_number=reference or sup.code,
            actor=actor,
            summary=f'Paiement fournisseur {int(amt)} FCFA ({payment_method}) à {sup.name}',
        )
        return sup
