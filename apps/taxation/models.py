from django.db import models


class TaxRule(models.Model):
    """Fiscalité ivoirienne (Point 2A) : TVA 18%, 9%, 0%, Exonéré & retenues AIRSI."""

    TAX_KIND_CHOICES = [
        ('VAT_STANDARD', 'TVA Normale (18%)'),
        ('VAT_REDUCED', 'TVA Réduite (9%)'),
        ('VAT_ZERO', 'TVA 0% (Export)'),
        ('EXEMPT', 'Exonéré de TVA'),
    ]

    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=24, choices=TAX_KIND_CHOICES, default='VAT_STANDARD')
    rate = models.DecimalField(max_digits=5, decimal_places=2, default=18)
    withholding_rate = models.DecimalField('Taux retenue AIRSI (%)', max_digits=5, decimal_places=2, default=0)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_default', '-rate', 'name']

    def __str__(self):
        return f'{self.name} ({self.rate}%)'
