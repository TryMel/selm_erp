from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class Client(TimeStampedModel):
    """Module 05 (Audit 1B) : Propriétaire UNIQUE de l'encours et du plafond de crédit client."""

    SEGMENT_CHOICES = [
        ('PARTICULIER', 'Particulier'),
        ('PROFESSIONNEL', 'Artisan / Professionnel'),
        ('ENTREPRISE', 'Entreprise / Chantier BTP'),
        ('GROSSISTE', 'Grossiste'),
    ]
    STATUS_CHOICES = [
        ('ACTIVE', 'Actif'),
        ('BLOCKED', 'Bloqué (crédit suspendu)'),
        ('INACTIVE', 'Inactif'),
    ]

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=160, db_index=True)
    company_name = models.CharField(max_length=160, blank=True)
    segment = models.CharField(max_length=24, choices=SEGMENT_CHOICES, default='PARTICULIER')
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    ncc = models.CharField('NCC Client', max_length=64, blank=True)
    # Fiscalité client (Audit 2A)
    tax_exempt = models.BooleanField('Client exonéré de TVA', default=False)
    exemption_certificate = models.CharField("N° Attestation d'exonération", max_length=80, blank=True)
    # Crédit centralisé (Audit 1B)
    payment_term_days = models.PositiveSmallIntegerField(default=0)
    credit_limit = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    current_balance = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='ACTIVE')
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-current_balance', 'name']

    @property
    def credit_usage_pct(self):
        if self.credit_limit <= 0:
            return 0
        return min(100, int((self.current_balance / self.credit_limit) * 100))

    def __str__(self):
        return f'[{self.code}] {self.name}'


class CreditLedgerEntry(models.Model):
    """Grand-livre immuable du crédit client — seule trace d'évolution de l'encours."""

    ENTRY_TYPES = [
        ('CHARGE', 'Imputation vente à crédit'),
        ('PAYMENT', 'Encaissement / Règlement créance'),
        ('CREDIT_NOTE', 'Avoir / Retour client'),
    ]

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='credit_ledger')
    entry_type = models.CharField(max_length=20, choices=ENTRY_TYPES)
    amount = models.DecimalField(max_digits=14, decimal_places=0)
    balance_after = models.DecimalField(max_digits=14, decimal_places=0)
    payment_method = models.CharField(max_length=24, blank=True)
    reference = models.CharField(max_length=64, blank=True)
    note = models.CharField(max_length=255, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='credit_approvals'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
