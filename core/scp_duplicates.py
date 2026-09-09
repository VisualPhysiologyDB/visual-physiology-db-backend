"""MSP/SCP identity: taxon, measured wavelength, and publication; never source file."""
from .bibliography import classify_identifier


def publication_key(reference):
    if reference is None:
        return ('missing', None)
    doi = classify_identifier(reference.doi)['doi']
    return ('doi', doi) if doi else ('reference', reference.pk)


def observation_key(observation):
    # Null and the legacy zero sentinel do not establish a measured wavelength.
    if observation.lambda_max is None or observation.lambda_max <= 0:
        return None
    return ((observation.genus or '').strip().lower(),
            (observation.species or '').strip().lower(),
            observation.lambda_max, publication_key(observation.reference))


def duplicate_groups(queryset):
    groups = {}
    for row in queryset.select_related('reference').order_by('pk'):
        key = observation_key(row)
        if key is not None:
            groups.setdefault(key, []).append(row)
    rank = {'APPROVED': 0, 'PENDING': 1, 'REJECTED': 2}
    return [sorted(rows, key=lambda r: (rank[r.status], r.pk))
            for rows in groups.values() if len(rows) > 1]


def duplicate_of(observation):
    from django.db.models.functions import Lower, Trim, Coalesce
    from django.db.models import Value
    from .models import CuratedSCP
    key = observation_key(observation)
    if key is None:
        return None
    candidates = CuratedSCP.objects.filter(duplicate_of__isnull=True, lambda_max=observation.lambda_max).exclude(pk=observation.pk).annotate(
        taxon_genus=Lower(Trim(Coalesce('genus', Value('')))),
        taxon_species=Lower(Trim(Coalesce('species', Value('')))),
    ).filter(taxon_genus=key[0], taxon_species=key[1]).select_related('reference').order_by('pk')
    return next((r for r in candidates if publication_key(r.reference) == key[3]), None)
