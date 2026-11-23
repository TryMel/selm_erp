from django.conf import settings
from django.db import models


class BusinessAuditLog(models.Model):
    """Journal d'audit métier immuable (Module 01 F05)."""

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT, null=True, blank=True)
    action = models.CharField(max_length=64, db_index=True)
    module = models.CharField(max_length=32, db_index=True)
    entity_type = models.CharField(max_length=48)
    entity_id = models.CharField(max_length=64, blank=True)
    document_number = models.CharField(max_length=48, blank=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='audit_actions'
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='audit_approvals'
    )
    summary = models.TextField()
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']


class TechnicalLog(models.Model):
    """Journal technique système (Point 4C : erreurs, imports/exports, backups, synchro)."""

    LEVEL_CHOICES = [('INFO', 'Info'), ('WARNING', 'Avertissement'), ('ERROR', 'Erreur')]
    CATEGORY_CHOICES = [
        ('SYSTEM', 'Système'),
        ('AUTH', 'Authentification'),
        ('IMPORT_EXPORT', 'Import / Export'),
        ('BACKUP', 'Sauvegarde / Restauration'),
        ('SYNC', 'Synchronisation Hors-Ligne'),
    ]

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT, null=True, blank=True)
    level = models.CharField(max_length=12, choices=LEVEL_CHOICES, default='INFO')
    category = models.CharField(max_length=24, choices=CATEGORY_CHOICES, default='SYSTEM', db_index=True)
    message = models.TextField()
    payload = models.JSONField(default=dict, blank=True)
    actor_name = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
