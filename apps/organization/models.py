from django.db import models
from apps.core.models import TimeStampedModel


class Company(TimeStampedModel):
    """Société juridique (Point 2D Multi-entreprises & 3A/3B/3C règles globales)."""

    NEG_STOCK_CHOICES = [
        ('FORBIDDEN', 'Interdit (blocage strict)'),
        ('ALLOWED_WITH_PERMISSION', 'Autorisé uniquement avec permission ou PIN'),
        ('ALWAYS_ALLOWED', 'Toujours autorisé (déconseillé)'),
    ]
    VALUATION_CHOICES = [
        ('CMUP', 'CMUP — Coût Moyen Unitaire Pondéré (Défaut)'),
        ('FIFO', 'FIFO — Premier Entré, Premier Sorti'),
    ]

    code = models.CharField(max_length=20, unique=True)
    legal_name = models.CharField(max_length=160)
    trade_name = models.CharField(max_length=160, blank=True)
    ncc = models.CharField('NCC (N° Compte Contribuable)', max_length=64, blank=True)
    rccm = models.CharField('RCCM', max_length=64, blank=True)
    tax_regime = models.CharField(max_length=64, default='Réel Normal (DGI Côte d\'Ivoire)')
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=80, default='Abidjan')
    country = models.CharField(max_length=80, default="Côte d'Ivoire")
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    currency = models.CharField(max_length=12, default='FCFA')
    # Règles métier explicites (Audit Points 3A, 3B, 3C)
    negative_stock_policy = models.CharField(max_length=30, choices=NEG_STOCK_CHOICES, default='FORBIDDEN')
    valuation_method = models.CharField(max_length=12, choices=VALUATION_CHOICES, default='CMUP')
    cash_variance_threshold = models.DecimalField(max_digits=12, decimal_places=0, default=2000)
    fne_enabled = models.BooleanField('Facturation Normalisée FNE (DGI CI)', default=True)
    footer_note = models.CharField(max_length=255, default='Marchandise vérifiée et livrée — Merci pour votre confiance.')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['legal_name']

    def __str__(self):
        return f'{self.legal_name} ({self.code})'


class Store(TimeStampedModel):
    """Magasin rattaché à une Société (Hiérarchie officielle : Société → Magasin → Dépôt → Zone → Emplacement)."""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name='stores')
    code = models.CharField(max_length=24)
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('company', 'code')
        ordering = ['-is_default', 'name']

    def __str__(self):
        return f'{self.name} ({self.company.code})'


class Warehouse(TimeStampedModel):
    """Dépôt physique rattaché à un Magasin."""

    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='warehouses')
    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=255, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_default', 'name']

    @property
    def company(self):
        return self.store.company

    def __str__(self):
        return f'{self.name} [{self.code}]'


class Zone(models.Model):
    """Zone logique dans un Dépôt (ex: Gros Œuvre, Outillage, Quarantaine)."""

    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='zones')
    code = models.CharField(max_length=24)
    name = models.CharField(max_length=100)
    is_quarantine = models.BooleanField(default=False)

    class Meta:
        unique_together = ('warehouse', 'code')
        ordering = ['code']

    def __str__(self):
        return f'{self.warehouse.code} / {self.name}'


class Location(models.Model):
    """Emplacement exact dans une Zone (ex: Rayon A1-E2)."""

    zone = models.ForeignKey(Zone, on_delete=models.PROTECT, related_name='locations')
    code = models.CharField(max_length=32)
    label = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('zone', 'code')
        ordering = ['code']

    def __str__(self):
        return f'{self.zone.warehouse.code}-{self.code}'
