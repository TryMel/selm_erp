from django.conf import settings
from django.db import models


class ImportJob(models.Model):
    """Audit 2B : Reprise de données Excel/CSV avec Simulation (Dry-Run) et Journal d'erreurs."""

    TARGETS = [
        ('PRODUCTS', 'Articles & Catalogue'),
        ('CLIENTS', 'Clients & Plafonds crédit'),
        ('SUPPLIERS', 'Fournisseurs'),
        ('INITIAL_STOCK', 'Stock Initial par Dépôt'),
    ]
    STATUSES = [
        ('SIMULATED', 'Simulé (Dry-Run)'),
        ('COMMITTED', 'Importé en base'),
        ('FAILED', 'Échec'),
    ]

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    target = models.CharField(max_length=24, choices=TARGETS)
    filename = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=STATUSES, default='SIMULATED')
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    error_rows = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
