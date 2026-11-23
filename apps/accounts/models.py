from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True)
    max_discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    can_approve_override = models.BooleanField(default=False)
    permissions = models.JSONField(default=list, blank=True)
    is_system = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.code})'


class User(AbstractUser):
    full_name = models.CharField(max_length=140, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    role = models.ForeignKey(Role, on_delete=models.PROTECT, null=True, blank=True, related_name='users')
    pin_hash = models.CharField(max_length=255, blank=True)
    default_company = models.ForeignKey('organization.Company', on_delete=models.PROTECT, null=True, blank=True)
    default_warehouse = models.ForeignKey('organization.Warehouse', on_delete=models.PROTECT, null=True, blank=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    @property
    def display_name(self):
        return self.full_name or self.get_full_name() or self.username

    @property
    def max_discount_percent(self):
        if self.is_superuser:
            return 100
        return float(self.role.max_discount_percent) if self.role else 0.0

    @property
    def can_approve_override(self):
        if self.is_superuser:
            return True
        return bool(self.role and self.role.can_approve_override)
