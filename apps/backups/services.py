import hashlib
import shutil
import sqlite3
import uuid
from pathlib import Path
from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.utils import timezone
from apps.audit.services import log_business, log_technical
from apps.core.exceptions import BusinessRuleError
from .models import BackupRecord


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def create_backup(*, company=None, actor=None, backup_type: str = 'JSON_DUMP', notes: str = '') -> BackupRecord:
    ts = timezone.now().strftime('%Y%m%d-%H%M%S')
    uniq = uuid.uuid4().hex[:6]
    settings.BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    if backup_type == 'SQLITE_COPY' and settings.DATABASES['default']['ENGINE'].endswith('sqlite3'):
        db_path = Path(settings.DATABASES['default']['NAME'])
        filename = f'quincapro-db-{ts}-{uniq}.sqlite3'
        dest = settings.BACKUP_ROOT / filename
        # Sauvegarde en ligne atomique SQLite (sûre même sous écritures concurrentes)
        src_conn = sqlite3.connect(str(db_path))
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
    else:
        backup_type = 'JSON_DUMP'
        filename = f'quincapro-dump-{ts}-{uniq}.json'
        dest = settings.BACKUP_ROOT / filename
        with dest.open('w', encoding='utf-8') as out:
            call_command('dumpdata', exclude=['sessions', 'contenttypes', 'admin.logentry'], indent=2, stdout=out)

    digest = _sha256_file(dest)
    rec = BackupRecord.objects.create(
        company=company,
        filename=filename,
        backup_type=backup_type,
        file_size=dest.stat().st_size,
        sha256=digest,
        created_by=actor,
        notes=notes,
    )
    log_technical(
        company=company,
        category='BACKUP',
        message=f'Sauvegarde {filename} créée ({rec.file_size} octets, SHA-256: {digest[:12]})',
        actor_name=actor.display_name if actor else 'CRON',
    )
    return rec


def restore_backup(*, record: BackupRecord, actor):
    """Restauration sécurisée avec vérification SHA-256, Path.is_relative_to() et pré-snapshot garanti unique."""
    root = settings.BACKUP_ROOT.resolve()
    target = (root / Path(record.filename).name).resolve()
    if not target.is_relative_to(root) or not target.exists():
        raise BusinessRuleError('Fichier de sauvegarde introuvable ou chemin invalide.', 'INVALID_BACKUP')

    if _sha256_file(target) != record.sha256:
        raise BusinessRuleError('Intégrité compromise : empreinte SHA-256 non conforme !', 'CORRUPTED_BACKUP')

    create_backup(
        company=record.company, actor=actor, backup_type=record.backup_type,
        notes=f'Pré-restauration avant {record.filename}',
    )

    if record.backup_type == 'SQLITE_COPY' and settings.DATABASES['default']['ENGINE'].endswith('sqlite3'):
        connections.close_all()
        db_path = Path(settings.DATABASES['default']['NAME'])
        shutil.copy2(target, db_path)
    else:
        call_command('loaddata', str(target))

    log_business(
        company=record.company,
        action='BACKUP_RESTORED',
        module='BACKUPS',
        entity_type='BackupRecord',
        entity_id=record.id,
        actor=actor,
        summary=f'Restauration vérifiée de la sauvegarde {record.filename}',
    )
