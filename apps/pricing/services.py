from decimal import Decimal
from django.db.models import Q
from django.utils import timezone
from apps.core.money import round_fcfa, round_qty
from .models import PricingRule


class PricingEngine:
    """Moteur déterministe de résolution tarifaire (Module 04 — échelle conditionnements corrigée)."""

    @staticmethod
    def resolve(company, product, quantity, packaging=None, client=None) -> dict:
        qty = round_qty(quantity or 1)
        factor = Decimal(str(packaging.conversion_factor)) if packaging else Decimal('1')
        qty_base = round_qty(qty * factor)
        unit_label = packaging.label if packaging else product.base_unit.name
        base_price = (
            Decimal(str(packaging.standard_price))
            if (packaging and packaging.standard_price is not None)
            else round_fcfa(Decimal(str(product.standard_price)) * factor)
        )

        now = timezone.now()
        qs = PricingRule.objects.filter(
            company=company,
            is_active=True,
            min_quantity__lte=qty_base,
        ).filter(
            Q(product=product) | Q(category=product.category) | (Q(product__isnull=True) & Q(category__isnull=True))
        )

        matching = []
        for rule in qs.order_by('priority', '-min_quantity'):
            if rule.start_date and rule.start_date > now:
                continue
            if rule.end_date and rule.end_date < now:
                continue
            if rule.client_id and (not client or rule.client_id != client.id):
                continue
            if rule.client_segment and (not client or rule.client_segment != client.segment):
                continue
            matching.append(rule)

        if not matching:
            return {
                'unit_label': unit_label,
                'conversion_factor': factor,
                'base_price': base_price,
                'resolved_price': base_price,
                'applied_rule': '',
            }

        resolved = base_price
        applied = []
        for rule in matching:
            if rule.price_mode == 'FIXED_PRICE':
                # Toujours multiplier un prix fixe unitaire par le facteur du conditionnement
                resolved = round_fcfa(Decimal(str(rule.value)) * factor)
            else:
                resolved = round_fcfa(resolved * (Decimal('1') - Decimal(str(rule.value)) / Decimal('100')))
            applied.append(rule.name)
            if not rule.is_cumulative:
                break

        return {
            'unit_label': unit_label,
            'conversion_factor': factor,
            'base_price': base_price,
            'resolved_price': max(Decimal('0'), resolved),
            'applied_rule': ' + '.join(applied),
        }
