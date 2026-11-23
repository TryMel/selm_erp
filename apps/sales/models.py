from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class SalesDocument(TimeStampedModel):
    DOC_TYPES = [
        ('QUOTE', 'Devis'),
        ('ORDER', 'Commande client'),
        ('DELIVERY_NOTE', 'Bon de livraison'),
        ('INVOICE', 'Facture'),
        ('POS_SALE', 'Vente Comptoir (POS)'),
        ('CREDIT_NOTE', 'Avoir'),
    ]
    STATUS_CHOICES = [
        ('DRAFT', 'Brouillon'),
        ('CONFIRMED', 'Confirmé / Stock réservé'),
        ('DELIVERED', 'Livré'),
        ('INVOICED', 'Facturé'),
        ('PARTIALLY_PAID', 'Partiellement payé'),
        ('PAID', 'Payé'),
        ('CANCELLED', 'Annulé'),
    ]

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    number = models.CharField(max_length=40, unique=True)
    doc_type = models.CharField(max_length=20, choices=DOC_TYPES, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    source_doc = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='derived_docs')
    client = models.ForeignKey('clients.Client', on_delete=models.PROTECT, null=True, blank=True, related_name='sales_docs')
    warehouse = models.ForeignKey('organization.Warehouse', on_delete=models.PROTECT)
    cash_session = models.ForeignKey('cash.CashSession', on_delete=models.PROTECT, null=True, blank=True)
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='sales_created')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='sales_approved'
    )
    subtotal_ht = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    discount_total = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    tax_total = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    total_ttc = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    balance_due = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    # Facturation normalisée Côte d'Ivoire (Audit 2A)
    fne_reference = models.CharField(max_length=64, blank=True)
    fne_qr_payload = models.CharField(max_length=255, blank=True)
    due_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)
    is_locked = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']


class SalesLine(models.Model):
    document = models.ForeignKey(SalesDocument, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT)
    product_name = models.CharField(max_length=180)
    unit_label = models.CharField(max_length=80, default='Unité')
    conversion_factor = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    quantity = models.DecimalField(max_digits=14, decimal_places=4)
    quantity_base = models.DecimalField(max_digits=14, decimal_places=4)
    base_price = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    unit_price = models.DecimalField(max_digits=14, decimal_places=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=18)
    tax_label = models.CharField(max_length=80, default='TVA 18%')
    line_total_ht = models.DecimalField(max_digits=14, decimal_places=0)
    line_tax = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    line_total_ttc = models.DecimalField(max_digits=14, decimal_places=0)
    cost_price_base = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    pricing_rule_applied = models.CharField(max_length=160, blank=True)


class SalesPayment(models.Model):
    METHODS = [
        ('CASH', 'Espèces (FCFA)'),
        ('WAVE', 'Wave Mobile Money'),
        ('ORANGE_MONEY', 'Orange Money'),
        ('MTN_MOMO', 'MTN MoMo'),
        ('CARD', 'Carte / TPE'),
        ('BANK_TRANSFER', 'Virement Bancaire'),
        ('CREDIT', 'Crédit Client (Compte)'),
    ]

    document = models.ForeignKey(SalesDocument, on_delete=models.PROTECT, related_name='payments')
    client = models.ForeignKey('clients.Client', on_delete=models.PROTECT, null=True, blank=True)
    cash_session = models.ForeignKey('cash.CashSession', on_delete=models.PROTECT, null=True, blank=True)
    payment_method = models.CharField(max_length=24, choices=METHODS)
    amount = models.DecimalField(max_digits=14, decimal_places=0)
    reference = models.CharField(max_length=80, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class DeliveryOrder(TimeStampedModel):
    """Module 13 : Livraison chantier liée au document commercial."""

    STATUS_CHOICES = [
        ('PREPARING', 'En préparation'),
        ('LOADED', 'Chargée camion'),
        ('IN_TRANSIT', 'En route chantier'),
        ('DELIVERED', 'Livrée & Confirmée'),
        ('FAILED', 'Échec livraison'),
        ('RESCHEDULED', 'Reprogrammée'),
    ]

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    number = models.CharField(max_length=40, unique=True)
    sales_document = models.ForeignKey(SalesDocument, on_delete=models.PROTECT, related_name='deliveries')
    client = models.ForeignKey('clients.Client', on_delete=models.PROTECT, null=True, blank=True)
    warehouse = models.ForeignKey('organization.Warehouse', on_delete=models.PROTECT)
    driver_name = models.CharField(max_length=120, blank=True)
    vehicle_plate = models.CharField(max_length=40, blank=True)
    delivery_address = models.CharField(max_length=255)
    transport_fee = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PREPARING')
    scheduled_date = models.DateField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    recipient_name = models.CharField(max_length=120, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-created_at']
