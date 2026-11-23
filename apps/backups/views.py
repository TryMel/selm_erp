from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from apps.core.exceptions import BusinessRuleError
from apps.core.permissions import require_perm
from .models import BackupRecord
from .services import create_backup, restore_backup


@require_perm('backups.manage')
def backups_view(request):
    qs = BackupRecord.objects.select_related('created_by')
    if not request.user.is_superuser:
        qs = qs.filter(company=request.company)

    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'create':
                btype = request.POST.get('backup_type', 'JSON_DUMP')
                rec = create_backup(
                    company=request.company, actor=request.user, backup_type=btype,
                    notes=request.POST.get('notes', '').strip(),
                )
                messages.success(request, f'Sauvegarde {rec.filename} créée (SHA-256 vérifié).')
            elif action == 'restore':
                if not request.user.is_superuser:
                    raise BusinessRuleError('Seul un Super-Administrateur peut restaurer la base complète.')
                rec = get_object_or_404(qs, pk=request.POST['backup_id'])
                restore_backup(record=rec, actor=request.user)
                messages.success(request, f'Restauration de {rec.filename} effectuée (snapshot pré-restauration créé).')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
        return redirect('/backups/')

    return render(request, 'backups/backups.html', {'backups': qs[:30]})


@require_perm('backups.manage')
def download_backup_view(request, pk: int):
    qs = BackupRecord.objects.all() if request.user.is_superuser else BackupRecord.objects.filter(company=request.company)
    rec = get_object_or_404(qs, pk=pk)
    root = settings.BACKUP_ROOT.resolve()
    path = (root / Path(rec.filename).name).resolve()
    if not path.is_relative_to(root) or not path.exists():
        messages.error(request, 'Fichier de sauvegarde introuvable.')
        return redirect('/backups/')
    return FileResponse(path.open('rb'), as_attachment=True, filename=rec.filename)
