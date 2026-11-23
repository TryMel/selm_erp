from django.db import models


class Account(models.Model):
    TYPES = [
        ('ASSET', 'Actif'),
        ('LIABILITY', 'Passif'),
        ('EQUITY', 'Capitaux propres'),
        ('REVENUE', 'Produits'),
        ('EXPENSE', 'Charges'),
    ]

    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=140)
    account_class = models.PositiveSmallIntegerField()
    account_type = models.CharField(max_length=16, choices=TYPES)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} — {self.name}'


class JournalEntry(models.Model):
    JOURNALS = [
        ('VTE', 'Journal des Ventes'),
        ('ACH', 'Journal des Achats'),
        ('CAI', 'Journal de Caisse'),
        ('BNQ', 'Journal Banque & Mobile Money'),
        ('OD', 'Opérations Diverses'),
    ]

    company = models.ForeignKey('organization.Company', on_delete=models.PROTECT)
    number = models.CharField(max_length=40, unique=True)
    journal_code = models.CharField(max_length=8, choices=JOURNALS, db_index=True)
    entry_date = models.DateTimeField(auto_now_add=True, db_index=True)
    description = models.CharField(max_length=255)
    source_doc_type = models.CharField(max_length=32, blank=True)
    source_doc_number = models.CharField(max_length=48, blank=True)
    actor_name = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['-entry_date']


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name='lines')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='journal_lines')
    label = models.CharField(max_length=200)
    debit = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    credit = models.DecimalField(max_digits=14, decimal_places=0, default=0)
