from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class StockCount(TimeStampedModel):
    """Module 12 : Inventaire physique (Simple ou Double comptage indépendant)."""

    MODE_CHOICES = [('DOUBLE', 'Double comptage indépendant'), ('SINGLE', 'Comptage simple')]
    STATUS_CHOICES = [
        ('COUNTING_1', 'Comptage 1 en cours'),
        ('COUNTING_2', 'Comptage 2 en cours'),
        ('REVIEW', 'Contrôle des écarts'),
        ('VALIDATED', 'Validé & Stock ajusté'),
        ('CANCELLED', 'Annulé'),
    ]

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    number = models.CharField(max_length=40, unique=True)
    warehouse = models.ForeignKey('organization.Warehouse', on_delete=models.PROTECT)
    zone = models.ForeignKey('organization.Zone', on_delete=models.SET_NULL, null=True, blank=True)
    mode = models.CharField(max_length=12, choices=MODE_CHOICES, default='DOUBLE')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='COUNTING_1')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='stock_counts_created')
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='stock_counts_validated'
    )
    validated_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']


class StockCountLine(models.Model):
    stock_count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT)
    theoretical_qty = models.DecimalField(max_digits=14, decimal_places=4)
    count1_qty = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    count1_by = models.CharField(max_length=120, blank=True)
    count2_qty = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    count2_by = models.CharField(max_length=120, blank=True)
    final_qty = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    variance_qty = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    variance_value = models.DecimalField(max_digits=14, decimal_places=0, default=0)

    class Meta:
        unique_together = ('stock_count', 'product')
        ordering = ['product__name']
