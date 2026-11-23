from django.conf import settings
from django.db import models


class BackupRecord(models.Model):
    """Audit 2C : Sauvegarde & Restauration vérifiées (checksum SHA-256 + historique)."""

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT, null=True, blank=True)
    filename = models.CharField(max_length=160, unique=True)
    backup_type = models.CharField(max_length=16, default='SQLITE_COPY')  # SQLITE_COPY | JSON_DUMP
    file_size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
