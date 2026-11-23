"""Exceptions métier structurées partagées par tous les modules ERP."""


class BusinessRuleError(Exception):
    def __init__(self, message: str, code: str = 'BUSINESS_ERROR', details=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class ManagerOverrideRequiredError(BusinessRuleError):
    """Levée lorsqu'une opération exige l'approbation PIN d'un responsable (Module 01 F04)."""

    def __init__(self, reasons: list[str]):
        super().__init__(
            ' | '.join(reasons),
            code='MANAGER_OVERRIDE_REQUIRED',
            details={'reasons': reasons},
        )
        self.reasons = reasons
