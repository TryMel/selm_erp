from django.core.exceptions import ValidationError
from django.db import models
from apps.core.models import TimeStampedModel


class UnitFamily(models.Model):
    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=80)

    def __str__(self):
        return self.name


class Unit(models.Model):
    family = models.ForeignKey(UnitFamily, on_delete=models.PROTECT, related_name='units')
    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=80)
    symbol = models.CharField(max_length=12)
    allow_fractional = models.BooleanField(default=False)
    precision = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['family__name', 'name']

    def __str__(self):
        return f'{self.name} ({self.symbol})'


class Category(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    parent = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='children')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Brand(models.Model):
    name = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Product(TimeStampedModel):
    """Module 02 : Référentiel article (Audit 1A : porte UNIQUEMENT prix standard de référence, coût d'achat, TVA)."""

    sku = models.CharField(max_length=48, unique=True)
    barcode = models.CharField(max_length=64, unique=True, null=True, blank=True)
    name = models.CharField(max_length=180, db_index=True)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products')
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, null=True, blank=True, related_name='products')
    base_unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name='base_products')
    tax_rule = models.ForeignKey('taxation.TaxRule', on_delete=models.PROTECT, related_name='products')
    default_supplier = models.ForeignKey('suppliers.Supplier', on_delete=models.PROTECT, null=True, blank=True)
    default_location = models.ForeignKey('organization.Location', on_delete=models.SET_NULL, null=True, blank=True)
    purchase_cost = models.DecimalField("Coût d'achat (CMUP)", max_digits=14, decimal_places=2, default=0)
    standard_price = models.DecimalField('Prix standard de référence TTC', max_digits=14, decimal_places=0, default=0)
    min_stock = models.DecimalField(max_digits=12, decimal_places=2, default=5)
    max_stock = models.DecimalField(max_digits=12, decimal_places=2, default=100)
    is_favorite = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_favorite', 'name']

    def __str__(self):
        return f'[{self.sku}] {self.name}'


class ProductPackaging(models.Model):
    """Module 03 : Conditionnements (ex: Boîte de 100, Carton de 1000) avec contrôle strict de famille d'unités."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='packagings')
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT)
    label = models.CharField(max_length=80)
    conversion_factor = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    barcode = models.CharField(max_length=64, unique=True, null=True, blank=True)
    standard_price = models.DecimalField(max_digits=14, decimal_places=0, null=True, blank=True)
    is_purchase_default = models.BooleanField(default=False)
    is_sale_default = models.BooleanField(default=False)

    class Meta:
        unique_together = ('product', 'label')
        ordering = ['conversion_factor']

    def clean(self):
        if self.conversion_factor <= 0:
            raise ValidationError('Le facteur de conversion doit être strictement positif.')
        if self.unit_id and self.product_id and self.unit.family_id != self.product.base_unit.family_id:
            raise ValidationError(
                f'Unités incompatibles : "{self.unit.name}" ({self.unit.family.name}) '
                f'vs "{self.product.base_unit.name}" ({self.product.base_unit.family.name}).'
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
