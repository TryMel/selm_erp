from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class Expense(TimeStampedModel):
    ACCOUNT_TYPES = [('CAISSE', 'Caisse Espèces'), ('MOBILE_MONEY', 'Mobile Money'), ('BANQUE', 'Banque')]
    STATUS_CHOICES = [('PENDING', 'En attente validation'), ('APPROVED', 'Validée & Débitée'), ('REJECTED', 'Rejetée')]

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    number = models.CharField(max_length=40, unique=True)
    category = models.CharField(max_length=80)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=14, decimal_places=0)
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPES, default='CAISSE')
    payment_method = models.CharField(max_length=24, default='CASH')
    cash_session = models.ForeignKey('cash.CashSession', on_delete=models.PROTECT, null=True, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='PENDING')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='expenses_created')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='expenses_approved'
    )

    class Meta:
        ordering = ['-created_at']
