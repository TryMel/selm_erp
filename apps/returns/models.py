from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class ReturnOrder(TimeStampedModel):
    TYPES = [('CLIENT_RETURN', 'Retour Client'), ('SUPPLIER_RETURN', 'Retour Fournisseur')]
    CONDITIONS = [('GOOD_RESTOCK', 'Bon état → Réintégrer stock'), ('DEFECTIVE_QUARANTINE', 'Défectueux → Quarantaine')]
    RESOLUTIONS = [('CREDIT_NOTE', 'Avoir en compte'), ('REFUND', 'Remboursement'), ('REPLACEMENT', 'Remplacement')]

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    number = models.CharField(max_length=40, unique=True)
    return_type = models.CharField(max_length=20, choices=TYPES)
    client = models.ForeignKey('clients.Client', on_delete=models.PROTECT, null=True, blank=True)
    supplier = models.ForeignKey('suppliers.Supplier', on_delete=models.PROTECT, null=True, blank=True)
    warehouse = models.ForeignKey('organization.Warehouse', on_delete=models.PROTECT)
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=4)
    unit_price = models.DecimalField(max_digits=14, decimal_places=0)
    total_amount = models.DecimalField(max_digits=14, decimal_places=0)
    condition = models.CharField(max_length=24, choices=CONDITIONS, default='GOOD_RESTOCK')
    resolution = models.CharField(max_length=20, choices=RESOLUTIONS, default='CREDIT_NOTE')
    reason = models.CharField(max_length=255)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ['-created_at']
