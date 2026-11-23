from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class CashRegister(models.Model):
    store = models.ForeignKey('organization.Store', on_delete=models.PROTECT, related_name='registers')
    warehouse = models.ForeignKey('organization.Warehouse', on_delete=models.PROTECT, related_name='registers')
    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    @property
    def company(self):
        return self.store.company

    def __str__(self):
        return f'{self.name} ({self.code})'


class CashSession(TimeStampedModel):
    STATUS_CHOICES = [
        ('OPEN', 'Ouverte'),
        ('PENDING_VALIDATION', 'Écart > seuil : Validation Resp. requise'),
        ('CLOSED', 'Clôturée'),
    ]

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    number = models.CharField(max_length=40, unique=True)
    register = models.ForeignKey(CashRegister, on_delete=models.PROTECT, related_name='sessions')
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cash_opened')
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='cash_closed'
    )
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='cash_validated'
    )
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='OPEN')
    opening_float = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    expected_cash = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    counted_cash = models.DecimalField(max_digits=14, decimal_places=0, null=True, blank=True)
    expected_mobile = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    counted_mobile = models.DecimalField(max_digits=14, decimal_places=0, null=True, blank=True)
    expected_card = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    counted_card = models.DecimalField(max_digits=14, decimal_places=0, null=True, blank=True)
    cash_variance = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    variance_notes = models.TextField(blank=True)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-opened_at']


class CashMovement(models.Model):
    MVT_TYPES = [
        ('SALE_IN', 'Encaissement Vente / Créance'),
        ('CASH_IN', 'Entrée Espèces (Apport)'),
        ('CASH_OUT', 'Sortie Espèces (Versement)'),
        ('EXPENSE_OUT', 'Règlement Dépense'),
        ('REFUND_OUT', 'Remboursement Client'),
    ]

    session = models.ForeignKey(CashSession, on_delete=models.PROTECT, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=MVT_TYPES)
    payment_method = models.CharField(max_length=24, default='CASH')
    amount = models.DecimalField(max_digits=14, decimal_places=0)
    reason = models.CharField(max_length=255)
    reference = models.CharField(max_length=64, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
