from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class PurchaseOrder(TimeStampedModel):
    STATUS_CHOICES = [
        ('ORDERED', 'Commandée'),
        ('PARTIALLY_RECEIVED', 'Partiellement reçue'),
        ('RECEIVED', 'Réceptionnée'),
        ('PAID', 'Soldée'),
        ('CANCELLED', 'Annulée'),
    ]

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    number = models.CharField(max_length=40, unique=True)
    supplier = models.ForeignKey('suppliers.Supplier', on_delete=models.PROTECT, related_name='purchase_orders')
    warehouse = models.ForeignKey('organization.Warehouse', on_delete=models.PROTECT)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='ORDERED')
    expected_date = models.DateField(null=True, blank=True)
    supplier_invoice_ref = models.CharField(max_length=80, blank=True)
    total_ttc = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']


class PurchaseLine(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT)
    product_name = models.CharField(max_length=180)
    unit_label = models.CharField(max_length=80, default='Unité')
    conversion_factor = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    ordered_qty = models.DecimalField(max_digits=14, decimal_places=4)
    received_qty = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=0)
    line_total_ttc = models.DecimalField(max_digits=14, decimal_places=0)

    @property
    def remaining_qty(self):
        return max(0, self.ordered_qty - self.received_qty)
