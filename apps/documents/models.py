import hashlib
import os
from datetime import timedelta
from django.conf import settings
from django.db import models
from django.utils import timezone
from apps.core.exceptions import BusinessRuleError


def attachment_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    safe_ref = ''.join(ch for ch in (instance.entity_ref or 'doc') if ch.isalnum() or ch in ('-', '_'))[:32]
    return f'attachments/{instance.company.code}/{instance.entity_type}/{safe_ref}_v{instance.version}{ext}'


class DocumentAttachment(models.Model):
    """Module 22 & Audit 4B : Pièces jointes sécurisées (taille max 5 Mo, whitelist, SHA-256, versionning, conservation 10 ans OHADA)."""

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    entity_type = models.CharField(max_length=32, default='GENERAL')
    entity_ref = models.CharField(max_length=64, blank=True)
    title = models.CharField(max_length=160)
    original_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to=attachment_upload_path)
    file_size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=80, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    version = models.PositiveSmallIntegerField(default=1)
    retention_until = models.DateField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


ALLOWED_ENTITY_TYPES = {'INVOICE', 'PURCHASE', 'EXPENSE', 'CLIENT', 'GENERAL'}


def store_attachment(*, company, uploaded_file, title: str, entity_type: str, entity_ref: str, actor) -> DocumentAttachment:
    if not uploaded_file:
        raise BusinessRuleError('Aucun fichier sélectionné.')
    if uploaded_file.size > settings.MAX_ATTACHMENT_SIZE_BYTES:
        raise BusinessRuleError('Fichier trop volumineux (limite stricte : 5 Mo).', 'FILE_TOO_LARGE')

    clean_entity_type = (entity_type or 'GENERAL').strip().upper()
    if clean_entity_type not in ALLOWED_ENTITY_TYPES:
        raise BusinessRuleError('Catégorie de document non autorisée.', 'INVALID_ENTITY_TYPE')

    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in settings.ALLOWED_ATTACHMENT_EXTENSIONS:
        raise BusinessRuleError(
            f'Type de fichier interdit ({ext}). Autorisés : {", ".join(sorted(settings.ALLOWED_ATTACHMENT_EXTENSIONS))}.',
            'INVALID_FILE_TYPE',
        )

    raw_bytes = uploaded_file.read()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    uploaded_file.seek(0)

    prev_version = (
        DocumentAttachment.objects.filter(company=company, entity_type=clean_entity_type, entity_ref=entity_ref, title=title)
        .aggregate(m=models.Max('version'))['m']
        or 0
    )

    return DocumentAttachment.objects.create(
        company=company,
        entity_type=clean_entity_type,
        entity_ref=entity_ref,
        title=title,
        original_filename=os.path.basename(uploaded_file.name),
        file=uploaded_file,
        file_size=uploaded_file.size,
        content_type=getattr(uploaded_file, 'content_type', '') or '',
        sha256=digest,
        version=prev_version + 1,
        retention_until=(timezone.now() + timedelta(days=3650)).date(),  # 10 ans OHADA
        uploaded_by=actor,
    )
