from django.contrib import messages
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from apps.core.exceptions import BusinessRuleError
from apps.core.permissions import require_perm, user_has_perm
from .models import DocumentAttachment, store_attachment


@require_perm('documents.view')
def documents_view(request):
    if request.method == 'POST':
        if not user_has_perm(request.user, 'documents.manage'):
            return render(request, 'errors/403.html', {'required_perms': ['documents.manage']}, status=403)
        try:
            att = store_attachment(
                company=request.company,
                uploaded_file=request.FILES.get('file'),
                title=request.POST['title'].strip(),
                entity_type=request.POST.get('entity_type', 'GENERAL'),
                entity_ref=request.POST.get('entity_ref', '').strip(),
                actor=request.user,
            )
            messages.success(request, f'Pièce jointe "{att.title}" archivée (v{att.version}, conservation jusqu’au {att.retention_until}).')
        except BusinessRuleError as exc:
            messages.error(request, exc.message)
        return redirect('/documents/')

    return render(request, 'documents/documents.html', {
        'docs': DocumentAttachment.objects.filter(company=request.company).select_related('uploaded_by'),
    })


@require_perm('documents.view')
def download_document_view(request, pk: int):
    att = get_object_or_404(DocumentAttachment, pk=pk, company=request.company)
    return FileResponse(att.file.open('rb'), as_attachment=True, filename=att.original_filename)
