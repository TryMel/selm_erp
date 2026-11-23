from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.audit.services import log_business
from apps.core.exceptions import BusinessRuleError
from apps.core.models import next_doc_number
from apps.core.money import round_fcfa, round_qty
from apps.inventory.models import StockLevel
from apps.inventory.services import StockService
from .models import StockCount, StockCountLine


class StockCountService:
    @staticmethod
    @transaction.atomic
    def create_count(*, company, warehouse, mode: str, actor, zone=None, notes=''):
        levels = StockLevel.objects.select_related('product').filter(warehouse=warehouse, product__is_active=True)
        if zone:
            levels = levels.filter(location__zone=zone)
        if not levels.exists():
            raise BusinessRuleError('Aucun article actif trouvé dans ce dépôt / zone.')

        number = next_doc_number(company, 'STOCK_COUNT')
        sc = StockCount.objects.create(
            company=company,
            number=number,
            warehouse=warehouse,
            zone=zone,
            mode=mode,
            status='COUNTING_1',
            created_by=actor,
            notes=notes,
        )
        StockCountLine.objects.bulk_create([
            StockCountLine(
                stock_count=sc,
                product=lvl.product,
                theoretical_qty=lvl.physical_qty,
                unit_cost=lvl.cmup,
            )
            for lvl in levels
        ])
        log_business(
            company=company,
            action='STOCK_COUNT_OPENED',
            module='STOCK_COUNTS',
            entity_type='StockCount',
            entity_id=sc.id,
            document_number=sc.number,
            actor=actor,
            summary=f'Ouverture inventaire {sc.number} ({mode}) sur {warehouse.name}',
        )
        return sc

    @staticmethod
    @transaction.atomic
    def record_pass(*, stock_count: StockCount, pass_number: int, counts_by_line_id: dict, actor):
        if stock_count.status in ('VALIDATED', 'CANCELLED'):
            raise BusinessRuleError('Inventaire déjà clôturé.')
        for line in stock_count.lines.select_for_update():
            if line.id not in counts_by_line_id:
                continue
            val = round_qty(counts_by_line_id[line.id])
            if val < 0:
                raise BusinessRuleError('Une quantité comptée ne peut pas être négative.')
            if pass_number == 1:
                line.count1_qty = val
                line.count1_by = actor.display_name
                if stock_count.mode == 'SINGLE':
                    line.final_qty = val
            else:
                line.count2_qty = val
                line.count2_by = actor.display_name
                line.final_qty = val
            if line.final_qty is not None:
                line.variance_qty = round_qty(line.final_qty - line.theoretical_qty)
                line.variance_value = round_fcfa(line.variance_qty * line.unit_cost)
            line.save()

        stock_count.status = 'COUNTING_2' if (pass_number == 1 and stock_count.mode == 'DOUBLE') else 'REVIEW'
        stock_count.save(update_fields=['status', 'updated_at'])
        return stock_count

    @staticmethod
    @transaction.atomic
    def validate_count(*, stock_count: StockCount, actor):
        sc = StockCount.objects.select_for_update().get(pk=stock_count.pk)
        if sc.status in ('VALIDATED', 'CANCELLED'):
            raise BusinessRuleError('Inventaire déjà validé ou annulé.')

        for line in sc.lines.select_related('product', 'product__base_unit'):
            # Attention au zéro falsy : toujours utiliser "is not None" (Architect H3)
            if line.final_qty is not None:
                final = line.final_qty
            elif line.count2_qty is not None:
                final = line.count2_qty
            elif line.count1_qty is not None:
                final = line.count1_qty
            else:
                final = line.theoretical_qty

            # Calcule le delta sur le stock physique RÉEL verrouillé au moment de la validation
            level = StockLevel.objects.select_for_update().filter(product=line.product, warehouse=sc.warehouse).first()
            current_phys = level.physical_qty if level else Decimal('0')
            diff = round_qty(final - current_phys)

            line.final_qty = final
            line.variance_qty = round_qty(final - line.theoretical_qty)
            line.variance_value = round_fcfa(line.variance_qty * line.unit_cost)
            line.save()

            if diff > Decimal('0'):
                StockService.receive(
                    product=line.product,
                    warehouse=sc.warehouse,
                    qty_base=diff,
                    unit_cost_base=line.unit_cost,
                    movement_type='ADJUSTMENT_IN',
                    unit_label=line.product.base_unit.name,
                    conversion_factor=1,
                    document_type='STOCK_COUNT',
                    document_number=sc.number,
                    reason=f'Régularisation inventaire {sc.number}',
                    actor=actor,
                    recalculate_cmup=False,
                )
            elif diff < Decimal('0'):
                StockService.deduct(
                    product=line.product,
                    warehouse=sc.warehouse,
                    qty_base=abs(diff),
                    movement_type='ADJUSTMENT_OUT',
                    unit_label=line.product.base_unit.name,
                    conversion_factor=1,
                    document_type='STOCK_COUNT',
                    document_number=sc.number,
                    reason=f'Régularisation inventaire {sc.number}',
                    actor=actor,
                    approver=actor,
                )

        sc.status = 'VALIDATED'
        sc.validated_by = actor
        sc.validated_at = timezone.now()
        sc.save(update_fields=['status', 'validated_by', 'validated_at', 'updated_at'])
        log_business(
            company=sc.company,
            action='STOCK_COUNT_VALIDATED',
            module='STOCK_COUNTS',
            entity_type='StockCount',
            entity_id=sc.id,
            document_number=sc.number,
            actor=actor,
            approver=actor,
            summary=f'Validation inventaire {sc.number} ({sc.warehouse.name}) — stocks ajustés',
        )
        return sc
