from django.db import models, transaction
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class DocumentSequence(models.Model):
    """Numérotation documentaire annuelle par Société (Point 4A : DEV-2026-00001)."""

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    doc_type = models.CharField(max_length=32)
    prefix = models.CharField(max_length=12)
    year = models.PositiveIntegerField()
    last_number = models.PositiveIntegerField(default=0)
    padding = models.PositiveSmallIntegerField(default=5)

    class Meta:
        unique_together = ('company', 'doc_type', 'year')

    def __str__(self):
        return f'{self.prefix}-{self.year}-{str(self.last_number).zfill(self.padding)}'


DEFAULT_PREFIXES = {
    'QUOTE': 'DEV',
    'ORDER': 'CMD',
    'DELIVERY_NOTE': 'BL',
    'INVOICE': 'FAC',
    'POS_SALE': 'VNT',
    'CREDIT_NOTE': 'AVO',
    'DELIVERY': 'LIV',
    'PURCHASE_ORDER': 'ACH',
    'RECEPTION': 'REC',
    'STOCK_COUNT': 'INV',
    'STOCK_MOVEMENT': 'MVT',
    'RETURN': 'RET',
    'EXPENSE': 'DEP',
    'CASH_SESSION': 'CAI',
    'JOURNAL_ENTRY': 'ECR',
}


def next_doc_number(company, doc_type: str) -> str:
    """Génère atomiquement un numéro unique multi-sociétés : <CODE_SOC>-<PREFIX>-<ANNEE>-<00001>."""
    year = timezone.now().year
    prefix = DEFAULT_PREFIXES.get(doc_type, doc_type[:3].upper())
    with transaction.atomic():
        seq, _ = DocumentSequence.objects.select_for_update().get_or_create(
            company=company,
            doc_type=doc_type,
            year=year,
            defaults={'prefix': prefix, 'last_number': 0, 'padding': 5},
        )
        seq.last_number += 1
        seq.save(update_fields=['last_number'])
        return f'{company.code}-{seq.prefix}-{seq.year}-{str(seq.last_number).zfill(seq.padding)}'
