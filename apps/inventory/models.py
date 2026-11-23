from django.conf import settings
from django.db import models
from apps.core.money import round_qty


class StockLevel(models.Model):
    """Module 11 : État du stock par Article × Dépôt (toujours en unité de base)."""

    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT, related_name='stock_levels')
    warehouse = models.ForeignKey('organization.Warehouse', on_delete=models.PROTECT, related_name='stock_levels')
    location = models.ForeignKey('organization.Location', on_delete=models.SET_NULL, null=True, blank=True)
    physical_qty = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    reserved_qty = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    blocked_qty = models.DecimalField('Quarantaine', max_digits=14, decimal_places=4, default=0)
    cmup = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('product', 'warehouse')
        ordering = ['product__name', 'warehouse__name']

    @property
    def available_qty(self):
        return round_qty(self.physical_qty - self.reserved_qty)

    @property
    def is_low_stock(self):
        return self.available_qty <= self.product.min_stock

    @property
    def valuation(self):
        return int(round(self.physical_qty * self.cmup))


class StockMovement(models.Model):
    """Historique immuable de tous les mouvements de stock (avec unité & facteur de conversion)."""

    MVT_TYPES = [
        ('IN_PURCHASE', 'Entrée Réception Achat'),
        ('OUT_POS', 'Sortie Vente Comptoir POS'),
        ('OUT_SALE', 'Sortie Vente / BL'),
        ('TRANSFER_OUT', 'Transfert Sortant'),
        ('TRANSFER_IN', 'Transfert Entrant'),
        ('ADJUSTMENT_IN', 'Régularisation (+)'),
        ('ADJUSTMENT_OUT', 'Régularisation (−)'),
        ('LOSS_DAMAGE', 'Perte / Casse'),
        ('QUARANTINE', 'Mise en Quarantaine'),
        ('RETURN_CLIENT', 'Retour Client'),
        ('RETURN_SUPPLIER', 'Retour Fournisseur'),
        ('IMPORT_INIT', 'Reprise Stock Initial'),
    ]

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    reference = models.CharField(max_length=40, db_index=True)
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT, related_name='movements')
    warehouse = models.ForeignKey('organization.Warehouse', on_delete=models.PROTECT, related_name='movements')
    target_warehouse = models.ForeignKey(
        'organization.Warehouse', on_delete=models.PROTECT, null=True, blank=True, related_name='incoming_transfers'
    )
    movement_type = models.CharField(max_length=24, choices=MVT_TYPES, db_index=True)
    qty_base = models.DecimalField(max_digits=14, decimal_places=4)
    unit_label = models.CharField(max_length=80, default='Unité')
    conversion_factor = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reason = models.CharField(max_length=255, blank=True)
    document_type = models.CharField(max_length=32, blank=True)
    document_number = models.CharField(max_length=48, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
