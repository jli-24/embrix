from .models import EDAArtifactReference, EDAValidationKind, EDAValidationReport


def validation_binds(
    report: EDAValidationReport, artifact: EDAArtifactReference, kind: EDAValidationKind
) -> bool:
    return report.artifact == artifact and report.validation_kind is kind


__all__ = ("validation_binds",)
