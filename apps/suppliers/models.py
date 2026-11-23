from django.db import models
from apps.core.models import TimeStampedModel


class Supplier(TimeStampedModel):
    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=160)
    contact_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    ncc = models.CharField(max_length=64, blank=True)
    payment_term_days = models.PositiveSmallIntegerField(default=30)
    avg_lead_days = models.PositiveSmallIntegerField(default=3)
    conformity_rate = models.DecimalField(max_digits=5, decimal_places=2, default=98)
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=4.5)
    current_debt = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-is_active', 'name']

    def __str__(self):
        return f'[{self.code}] {self.name}'
