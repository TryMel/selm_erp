from decimal import Decimal
from django.db import transaction
from apps.accounting.services import AccountingBridge
from apps.audit.services import log_business
from apps.catalog.models import Product, ProductPackaging
from apps.core.exceptions import BusinessRuleError
from apps.core.models import next_doc_number
from apps.core.money import round_cost, round_fcfa, round_qty
from apps.inventory.services import StockService
from apps.suppliers.services import SupplierDebtService
from .models import PurchaseLine, PurchaseOrder


class PurchaseService:
    @staticmethod
    @transaction.atomic
    def create_order(*, company, supplier, warehouse, lines_input: list[dict], actor, notes='') -> PurchaseOrder:
        if not lines_input:
            raise BusinessRuleError('Ajoutez au moins un article à la commande fournisseur.')
        number = next_doc_number(company, 'PURCHASE_ORDER')
        po = PurchaseOrder.objects.create(
            company=company, number=number, supplier=supplier, warehouse=warehouse, created_by=actor, notes=notes
        )
        total = Decimal('0')
        for item in lines_input:
            product = Product.objects.select_related('base_unit').get(pk=item['product_id'])
            pkg = ProductPackaging.objects.filter(pk=item.get('packaging_id'), product=product).first() if item.get('packaging_id') else None
            qty = round_qty(item['ordered_qty'])
            cost = round_fcfa(item['unit_cost'])
            if qty <= 0 or cost < 0:
                raise BusinessRuleError('Quantité et coût unitaire invalides.')
            line_ttc = round_fcfa(qty * cost)
            total += line_ttc
            PurchaseLine.objects.create(
                purchase_order=po,
                product=product,
                product_name=product.name,
                unit_label=pkg.label if pkg else product.base_unit.name,
                conversion_factor=pkg.conversion_factor if pkg else Decimal('1'),
                ordered_qty=qty,
                unit_cost=cost,
                line_total_ttc=line_ttc,
            )
        po.total_ttc = total
        po.save(update_fields=['total_ttc'])
        log_business(
            company=company,
            action='PURCHASE_ORDER_CREATED',
            module='PURCHASES',
            entity_type='PurchaseOrder',
            entity_id=po.id,
            document_number=po.number,
            actor=actor,
            summary=f'Commande fournisseur {po.number} ({supplier.name}) — {int(total)} FCFA',
        )
        return po

    @staticmethod
    @transaction.atomic
    def receive_order(*, po_id: int, qty_by_line_id: dict, supplier_invoice_ref: str, actor, company=None) -> PurchaseOrder:
        qs = PurchaseOrder.objects.select_for_update().select_related('supplier', 'warehouse', 'company')
        if company is not None:
            qs = qs.filter(company=company)
        po = qs.filter(pk=po_id).first()
        if not po:
            raise BusinessRuleError('Commande fournisseur introuvable pour cette société.')
        if po.status in ('RECEIVED', 'PAID', 'CANCELLED'):
            raise BusinessRuleError('Cette commande ne peut plus recevoir de marchandise.')

        rec_number = next_doc_number(po.company, 'RECEPTION')
        total_ht = Decimal('0')
        total_tva = Decimal('0')
        received_value_ttc = Decimal('0')
        any_received = False

        lines = list(po.lines.select_related('product', 'product__tax_rule'))
        for line in lines:
            now_qty = round_qty(qty_by_line_id.get(line.id, 0))
            if now_qty <= 0:
                continue
            rem = round_qty(line.ordered_qty - line.received_qty)
            if now_qty > rem:
                raise BusinessRuleError(f'Quantité reçue ({now_qty}) supérieure au reliquat ({rem}) pour {line.product_name}.')

            any_received = True
            added_base = round_qty(now_qty * line.conversion_factor)
            line_ttc = round_fcfa(now_qty * line.unit_cost)
            tax_rate = Decimal(str(line.product.tax_rule.rate)) if line.product.tax_rule else Decimal('0')
            line_ht = round_fcfa(line_ttc / (Decimal('1') + tax_rate / Decimal('100'))) if tax_rate > 0 else line_ttc
            line_tva = round_fcfa(line_ttc - line_ht)

            # CMUP calculé STRICTEMENT sur le coût unitaire HT (norme SYSCOHADA)
            unit_cost_ht_base = round_cost(line_ht / added_base) if added_base > 0 else Decimal('0')

            received_value_ttc += line_ttc
            total_ht += line_ht
            total_tva += line_tva

            line.received_qty = round_qty(line.received_qty + now_qty)
            line.save(update_fields=['received_qty'])

            StockService.receive(
                product=line.product,
                warehouse=po.warehouse,
                qty_base=added_base,
                unit_cost_base=unit_cost_ht_base,
                movement_type='IN_PURCHASE',
                unit_label=line.unit_label,
                conversion_factor=line.conversion_factor,
                document_type='RECEPTION',
                document_number=rec_number,
                reason=f'Réception {rec_number} sur {po.number}',
                actor=actor,
                recalculate_cmup=True,
            )

        if not any_received:
            raise BusinessRuleError('Saisissez au moins une quantité reçue supérieure à 0.')

        all_done = all(l.received_qty >= l.ordered_qty for l in lines)
        po.status = 'RECEIVED' if all_done else 'PARTIALLY_RECEIVED'
        if supplier_invoice_ref:
            po.supplier_invoice_ref = supplier_invoice_ref.strip()
        po.save(update_fields=['status', 'supplier_invoice_ref', 'updated_at'])

        SupplierDebtService.increase_debt(po.supplier_id, received_value_ttc, company=po.company)
        acc_lines = [
            {'account_code': '601000', 'label': f'Achats marchandises HT {rec_number}', 'debit': total_ht},
            {'account_code': '401000', 'label': f'Dette fournisseur {po.supplier.name}', 'credit': received_value_ttc},
        ]
        if total_tva > 0:
            acc_lines.insert(1, {'account_code': '445200', 'label': f'TVA récupérable {rec_number}', 'debit': total_tva})
        AccountingBridge.post_entry(
            company=po.company,
            journal_code='ACH',
            description=f'Réception {rec_number} ({po.supplier.name})',
            source_doc_type='RECEPTION',
            source_doc_number=rec_number,
            actor_name=actor.display_name,
            lines=acc_lines,
        )
        log_business(
            company=po.company,
            action='PURCHASE_RECEIVED',
            module='PURCHASES',
            entity_type='PurchaseOrder',
            entity_id=po.id,
            document_number=rec_number,
            actor=actor,
            summary=f'Réception {rec_number} sur {po.number} ({int(received_value_ttc)} FCFA) — Statut : {po.status}',
        )
        return po
