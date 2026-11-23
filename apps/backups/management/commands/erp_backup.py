from django.core.management.base import BaseCommand
from apps.backups.services import create_backup


class Command(BaseCommand):
    help = 'Crée une sauvegarde vérifiée (SHA-256) de la base QuincaPro ERP (pour Cron Hostinger).'

    def add_arguments(self, parser):
        parser.add_argument('--type', default='JSON_DUMP', choices=['JSON_DUMP', 'SQLITE_COPY'])

    def handle(self, *args, **options):
        rec = create_backup(backup_type=options['type'], notes='Sauvegarde planifiée (cron)')
        self.stdout.write(self.style.SUCCESS(f'Backup OK : {rec.filename} ({rec.file_size} octets, SHA-256 {rec.sha256[:12]})'))
