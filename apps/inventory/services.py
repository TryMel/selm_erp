from decimal import Decimal
from django.db import transaction
from apps.audit.services import log_business
from apps.core.exceptions import BusinessRuleError, ManagerOverrideRequiredError
from apps.core.models import next_doc_number
from apps.core.money import round_cost, round_qty
from apps.core.permissions import user_has_perm
from .models import StockLevel, StockMovement


class StockService:
    """
    Service MAÎTRE unique des stocks :
    - Verrouillage pessimiste select_for_update() (ordonné sur transferts anti-deadlock)
    - Protection anti-vol de réservations d'autres commandes
    - CMUP précis à 2 décimales (HT SYSCOHADA) & recalcul CMUP destination sur transfert
    """

    @staticmethod
    def _get_locked_level(product, warehouse) -> StockLevel:
        level, _ = StockLevel.objects.select_for_update().get_or_create(
            product=product,
            warehouse=warehouse,
            defaults={'physical_qty': Decimal('0'), 'cmup': product.purchase_cost},
        )
        return level

    @staticmethod
    def _check_negative_policy(company, level: StockLevel, qty_out: Decimal, actor, approver=None, release_reserved=False):
        other_reserved = max(Decimal('0'), level.reserved_qty - qty_out) if release_reserved else level.reserved_qty
        avail = round_qty(level.physical_qty - other_reserved)
        if qty_out <= avail:
            return
        policy = company.negative_stock_policy
        shortage_msg = (
            f'Stock insuffisant pour "{level.product.name}" dans {level.warehouse.name} '
            f'(demandé : {qty_out}, disponible : {avail}).'
        )
        if policy == 'ALWAYS_ALLOWED':
            return
        if policy == 'ALLOWED_WITH_PERMISSION':
            if user_has_perm(actor, 'inventory.allow_negative') or approver is not None:
                return
            raise ManagerOverrideRequiredError([f'Stock négatif interdit sans autorisation : {shortage_msg}'])
        raise BusinessRuleError(shortage_msg, 'INSUFFICIENT_STOCK')

    @staticmethod
    @transaction.atomic
    def reserve(*, product, warehouse, qty_base, actor, approver=None):
        qty = round_qty(qty_base)
        if qty <= 0:
            raise BusinessRuleError('Quantité de réservation invalide.')
        level = StockService._get_locked_level(product, warehouse)
        StockService._check_negative_policy(warehouse.company, level, qty, actor, approver=approver)
        level.reserved_qty = round_qty(level.reserved_qty + qty)
        level.save(update_fields=['reserved_qty', 'updated_at'])
        return level

    @staticmethod
    @transaction.atomic
    def release_reservation(*, product, warehouse, qty_base):
        qty = round_qty(qty_base)
        level = StockService._get_locked_level(product, warehouse)
        level.reserved_qty = max(Decimal('0'), round_qty(level.reserved_qty - qty))
        level.save(update_fields=['reserved_qty', 'updated_at'])
        return level

    @staticmethod
    @transaction.atomic
    def deduct(
        *,
        product,
        warehouse,
        qty_base,
        movement_type: str,
        unit_label: str,
        conversion_factor,
        document_type: str,
        document_number: str,
        reason: str,
        actor,
        approver=None,
        release_reserved: bool = False,
    ) -> StockMovement:
        qty = round_qty(qty_base)
        if qty <= 0:
            raise BusinessRuleError('Quantité de sortie invalide.')
        level = StockService._get_locked_level(product, warehouse)
        StockService._check_negative_policy(
            warehouse.company, level, qty, actor, approver=approver, release_reserved=release_reserved
        )
        level.physical_qty = round_qty(level.physical_qty - qty)
        if release_reserved:
            level.reserved_qty = max(Decimal('0'), round_qty(level.reserved_qty - qty))
        level.save(update_fields=['physical_qty', 'reserved_qty', 'updated_at'])

        ref = next_doc_number(warehouse.company, 'STOCK_MOVEMENT')
        return StockMovement.objects.create(
            company=warehouse.company,
            reference=ref,
            product=product,
            warehouse=warehouse,
            movement_type=movement_type,
            qty_base=-qty,
            unit_label=unit_label,
            conversion_factor=conversion_factor,
            unit_cost=level.cmup,
            reason=reason,
            document_type=document_type,
            document_number=document_number,
            actor=actor,
        )

    @staticmethod
    @transaction.atomic
    def receive(
        *,
        product,
        warehouse,
        qty_base,
        unit_cost_base,
        movement_type: str,
        unit_label: str,
        conversion_factor,
        document_type: str,
        document_number: str,
        reason: str,
        actor,
        recalculate_cmup: bool = True,
    ) -> StockMovement:
        qty = round_qty(qty_base)
        cost = round_cost(unit_cost_base or 0)
        if qty <= 0:
            raise BusinessRuleError("Quantité d'entrée invalide.")
        level = StockService._get_locked_level(product, warehouse)
        old_qty = max(Decimal('0'), level.physical_qty)
        new_qty = round_qty(level.physical_qty + qty)

        if recalculate_cmup and cost > 0 and (old_qty + qty) > 0:
            new_cmup = round_cost(((old_qty * level.cmup) + (qty * cost)) / (old_qty + qty))
            level.cmup = new_cmup
            product.purchase_cost = new_cmup
            product.save(update_fields=['purchase_cost', 'updated_at'])

        level.physical_qty = new_qty
        level.save(update_fields=['physical_qty', 'cmup', 'updated_at'])

        ref = next_doc_number(warehouse.company, 'STOCK_MOVEMENT')
        return StockMovement.objects.create(
            company=warehouse.company,
            reference=ref,
            product=product,
            warehouse=warehouse,
            movement_type=movement_type,
            qty_base=qty,
            unit_label=unit_label,
            conversion_factor=conversion_factor,
            unit_cost=cost if cost > 0 else level.cmup,
            reason=reason,
            document_type=document_type,
            document_number=document_number,
            actor=actor,
        )

    @staticmethod
    @transaction.atomic
    def quarantine(*, product, warehouse, qty_base, reason: str, actor, from_physical: bool = True, doc_number=''):
        qty = round_qty(qty_base)
        if qty <= 0:
            raise BusinessRuleError('Quantité quarantaine invalide.')
        level = StockService._get_locked_level(product, warehouse)
        if from_physical:
            StockService._check_negative_policy(warehouse.company, level, qty, actor)
            level.physical_qty = round_qty(level.physical_qty - qty)
        level.blocked_qty = round_qty(level.blocked_qty + qty)
        level.save(update_fields=['physical_qty', 'blocked_qty', 'updated_at'])

        ref = next_doc_number(warehouse.company, 'STOCK_MOVEMENT')
        mvt = StockMovement.objects.create(
            company=warehouse.company,
            reference=ref,
            product=product,
            warehouse=warehouse,
            movement_type='QUARANTINE',
            qty_base=-qty if from_physical else qty,
            unit_label=product.base_unit.name,
            conversion_factor=1,
            unit_cost=level.cmup,
            reason=reason,
            document_number=doc_number,
            actor=actor,
        )
        log_business(
            company=warehouse.company,
            action='STOCK_QUARANTINE',
            module='INVENTORY',
            entity_type='StockLevel',
            entity_id=level.id,
            document_number=ref,
            actor=actor,
            summary=f'Mise en quarantaine de {qty} {product.name} ({warehouse.name}) — {reason}',
        )
        return mvt

    @staticmethod
    @transaction.atomic
    def transfer(*, product, from_warehouse, to_warehouse, qty_base, reason: str, actor):
        if from_warehouse.id == to_warehouse.id:
            raise BusinessRuleError('Les dépôts source et destination doivent être différents.')
        qty = round_qty(qty_base)
        if qty <= 0:
            raise BusinessRuleError('Quantité de transfert invalide.')

        ordered_ids = sorted([from_warehouse.id, to_warehouse.id])
        levels = {
            sl.warehouse_id: sl
            for sl in StockLevel.objects.select_for_update().filter(product=product, warehouse_id__in=ordered_ids)
        }
        src = levels.get(from_warehouse.id) or StockService._get_locked_level(product, from_warehouse)
        dst = levels.get(to_warehouse.id) or StockService._get_locked_level(product, to_warehouse)

        StockService._check_negative_policy(from_warehouse.company, src, qty, actor)
        dst_old_qty = max(Decimal('0'), dst.physical_qty)
        src.physical_qty = round_qty(src.physical_qty - qty)
        dst.physical_qty = round_qty(dst.physical_qty + qty)
        if (dst_old_qty + qty) > 0:
            dst.cmup = round_cost(((dst_old_qty * dst.cmup) + (qty * src.cmup)) / (dst_old_qty + qty))
        src.save(update_fields=['physical_qty', 'updated_at'])
        dst.save(update_fields=['physical_qty', 'cmup', 'updated_at'])

        ref = next_doc_number(from_warehouse.company, 'STOCK_MOVEMENT')
        StockMovement.objects.create(
            company=from_warehouse.company,
            reference=ref,
            product=product,
            warehouse=from_warehouse,
            target_warehouse=to_warehouse,
            movement_type='TRANSFER_OUT',
            qty_base=-qty,
            unit_label=product.base_unit.name,
            conversion_factor=1,
            unit_cost=src.cmup,
            reason=reason or f'Transfert vers {to_warehouse.name}',
            actor=actor,
        )
        StockMovement.objects.create(
            company=to_warehouse.company,
            reference=ref,
            product=product,
            warehouse=to_warehouse,
            target_warehouse=from_warehouse,
            movement_type='TRANSFER_IN',
            qty_base=qty,
            unit_label=product.base_unit.name,
            conversion_factor=1,
            unit_cost=src.cmup,
            reason=reason or f'Transfert depuis {from_warehouse.name}',
            actor=actor,
        )
        log_business(
            company=from_warehouse.company,
            action='STOCK_TRANSFER',
            module='INVENTORY',
            entity_type='StockMovement',
            document_number=ref,
            actor=actor,
            summary=f'Transfert {qty} {product.name} : {from_warehouse.name} → {to_warehouse.name}',
        )
        return ref
