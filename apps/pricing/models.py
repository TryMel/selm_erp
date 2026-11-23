from django.db import models
from apps.core.models import TimeStampedModel


class PricingRule(TimeStampedModel):
    """Module 04 (Audit 1A) : Tarification 100% indépendante du catalogue."""

    RULE_TYPES = [
        ('CLIENT_SPECIFIC', '1. Prix spécifique client / contrat'),
        ('PROMOTION', '2. Promotion datée'),
        ('SEGMENT', '3. Tarif segment client (Pro / Grossiste)'),
        ('QUANTITY_TIER', '4. Palier par quantité'),
    ]
    PRICE_MODES = [
        ('FIXED_PRICE', 'Prix fixe (FCFA)'),
        ('PERCENT_DISCOUNT', 'Remise (%)'),
    ]

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    name = models.CharField(max_length=140)
    rule_type = models.CharField(max_length=24, choices=RULE_TYPES, db_index=True)
    priority = models.PositiveSmallIntegerField(default=50)
    product = models.ForeignKey('catalog.Product', on_delete=models.CASCADE, null=True, blank=True)
    category = models.ForeignKey('catalog.Category', on_delete=models.CASCADE, null=True, blank=True)
    client = models.ForeignKey('clients.Client', on_delete=models.CASCADE, null=True, blank=True)
    client_segment = models.CharField(max_length=24, blank=True)
    min_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    price_mode = models.CharField(max_length=24, choices=PRICE_MODES, default='FIXED_PRICE')
    value = models.DecimalField(max_digits=14, decimal_places=2)
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    is_cumulative = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['priority', '-min_quantity', 'name']

    def __str__(self):
        return f'[P{self.priority}] {self.name}'
