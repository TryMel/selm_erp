from django.contrib import messages
from django.shortcuts import redirect, render
from apps.core.exceptions import BusinessRuleError
from apps.core.permissions import require_perm
from .models import ImportJob
from .services import run_import


@require_perm('data_import.manage')
def data_import_view(request):
    last_job = None
    if request.method == 'POST':
        try:
            uploaded = request.FILES.get('file')
            if not uploaded:
                raise BusinessRuleError('Veuillez sélectionner un fichier .csv ou .xlsx.')
            commit = request.POST.get('mode') == 'commit'
            last_job = run_import(
                company=request.company,
                target=request.POST['target'],
                uploaded_file=uploaded,
                commit=commit,
                actor=request.user,
            )
            if last_job.error_rows:
                messages.warning(request, f'Simulation / Import : {last_job.error_rows} erreur(s) détectée(s). Corrigez votre fichier.')
            elif commit:
                messages.success(request, f'Import terminé : {last_job.valid_rows} ligne(s) intégrée(s) en base.')
            else:
                messages.info(request, f'Simulation OK : {last_job.valid_rows} ligne(s) valides, 0 erreur. Prêt pour import définitif.')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
            return redirect('/data-import/')

    return render(request, 'data_import/import.html', {
        'jobs': ImportJob.objects.filter(company=request.company).select_related('actor')[:20],
        'last_job': last_job,
    })
