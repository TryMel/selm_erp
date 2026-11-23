from decimal import Decimal
from django.db import transaction
from apps.accounting.services import AccountingBridge
from apps.audit.services import log_business
from apps.cash.services import CashService
from apps.clients.services import CreditService
from apps.core.exceptions import BusinessRuleError
from apps.core.models import next_doc_number
from apps.core.money import round_fcfa, round_qty
from apps.inventory.services import StockService
from apps.suppliers.services import SupplierDebtService
from .models import ReturnOrder


class ReturnService:
    @staticmethod
    @transaction.atomic
    def process_return(*, company, return_type: str, warehouse, product, quantity, unit_price, condition: str, resolution: str, reason: str, actor, client=None, supplier=None):
        if return_type not in dict(ReturnOrder.TYPES):
            raise BusinessRuleError('Type de retour invalide.')
        if condition not in dict(ReturnOrder.CONDITIONS):
            raise BusinessRuleError('Condition produit invalide.')
        if resolution not in dict(ReturnOrder.RESOLUTIONS):
            raise BusinessRuleError('Mode de résolution invalide.')
        qty = round_qty(quantity)
        pu = round_fcfa(unit_price)
        if qty <= 0 or pu < 0:
            raise BusinessRuleError('Quantité et prix unitaire de retour invalides.')

        total = round_fcfa(qty * pu)
        number = next_doc_number(company, 'RETURN')
        tax_rate = Decimal(str(product.tax_rule.rate)) if product.tax_rule else Decimal('0')
        ht = round_fcfa(total / (Decimal('1') + tax_rate / Decimal('100'))) if tax_rate > 0 else total
        tva = round_fcfa(total - ht)

        ret = ReturnOrder.objects.create(
            company=company,
            number=number,
            return_type=return_type,
            client=client,
            supplier=supplier,
            warehouse=warehouse,
            product=product,
            quantity=qty,
            unit_price=pu,
            total_amount=total,
            condition=condition,
            resolution=resolution,
            reason=reason,
            actor=actor,
        )

        if return_type == 'CLIENT_RETURN':
            if condition == 'GOOD_RESTOCK':
                StockService.receive(
                    product=product,
                    warehouse=warehouse,
                    qty_base=qty,
                    unit_cost_base=product.purchase_cost,
                    movement_type='RETURN_CLIENT',
                    unit_label=product.base_unit.name,
                    conversion_factor=1,
                    document_type='RETURN',
                    document_number=number,
                    reason=f'Retour client bon état : {reason}',
                    actor=actor,
                    recalculate_cmup=False,
                )
            else:
                StockService.quarantine(
                    product=product,
                    warehouse=warehouse,
                    qty_base=qty,
                    reason=f'Retour client défectueux : {reason}',
                    actor=actor,
                    from_physical=False,
                    doc_number=number,
                )

            if resolution == 'CREDIT_NOTE' and client:
                CreditService.settle_credit(
                    client_id=client.id,
                    amount=total,
                    payment_method='CREDIT_NOTE',
                    reference=number,
                    note=f'Avoir retour {number}',
                    actor=actor,
                    entry_type='CREDIT_NOTE',
                    company=company,
                )
            elif resolution == 'REFUND' and total > 0:
                ses = CashService.get_open_session(company)
                if ses:
                    CashService.record_manual_movement(
                        session=ses,
                        movement_type='REFUND_OUT',
                        amount=total,
                        reason=f'Remboursement retour client {number}',
                        reference=number,
                        actor=actor,
                    )

            if total > 0 and resolution in ('CREDIT_NOTE', 'REFUND'):
                credit_acc = '571000' if resolution == 'REFUND' else '411000'
                acc_lines = [
                    {'account_code': '701000', 'label': f'Avoir / Retour ventes HT {number}', 'debit': ht},
                    {'account_code': credit_acc, 'label': f'Remboursement / Avoir client {number}', 'credit': total},
                ]
                if tva > 0:
                    acc_lines.insert(1, {'account_code': '443100', 'label': f'TVA avoir {number}', 'debit': tva})
                AccountingBridge.post_entry(
                    company=company, journal_code='VTE', description=f'Retour client {number}',
                    source_doc_type='RETURN', source_doc_number=number, actor_name=actor.display_name, lines=acc_lines,
                )
        else:
            StockService.deduct(
                product=product,
                warehouse=warehouse,
                qty_base=qty,
                movement_type='RETURN_SUPPLIER',
                unit_label=product.base_unit.name,
                conversion_factor=1,
                document_type='RETURN',
                document_number=number,
                reason=f'Retour fournisseur : {reason}',
                actor=actor,
            )
            if supplier and total > 0:
                SupplierDebtService.decrease_debt(supplier.id, total, company=company)
                acc_lines = [
                    {'account_code': '401000', 'label': f'Avoir fournisseur {supplier.name} ({number})', 'debit': total},
                    {'account_code': '601000', 'label': f'Retour achats HT {number}', 'credit': ht},
                ]
                if tva > 0:
                    acc_lines.append({'account_code': '445200', 'label': f'Régul TVA achat {number}', 'credit': tva})
                AccountingBridge.post_entry(
                    company=company, journal_code='ACH', description=f'Retour fournisseur {number}',
                    source_doc_type='RETURN', source_doc_number=number, actor_name=actor.display_name, lines=acc_lines,
                )

        log_business(
            company=company,
            action=return_type,
            module='RETURNS',
            entity_type='ReturnOrder',
            entity_id=ret.id,
            document_number=number,
            actor=actor,
            summary=f'{ret.get_return_type_display()} {number} ({product.name} × {qty}) — {int(total)} FCFA',
        )
        return ret
