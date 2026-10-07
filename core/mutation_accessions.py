"""Accession labels only. These helpers never infer or modify a sequence."""
import re

SITE = r'(?:[ACDEFGHIKLMNPQRSTVWY][1-9][0-9]*(?:[ACDEFGHIKLMNPQRSTVWY]|del)|ins[1-9][0-9]*[ACDEFGHIKLMNPQRSTVWY])'
SUFFIX = re.compile(r'_(?P<label>' + SITE + r'(?:[,;_ ]+' + SITE + r')*)$')


def normalized_mutations(value):
    parts = [p.strip() for p in re.split(r'[,;]', value or '') if p.strip()]
    return ','.join(parts) if parts and all(re.fullmatch(SITE, p) for p in parts) else None


def split_accession(value):
    value = (value or '').strip()
    match = SUFFIX.search(value)
    return (value[:match.start()], ','.join(re.split(r'[,;_ ]+', match['label']))) if match else (value, None)


def tagged_accession(accession, mutations):
    label = normalized_mutations(mutations)
    base, previous = split_accession(accession)
    if not base or not label:
        return None
    # Accept the legacy underscore separator as well as commas; do not churn valid labels.
    return accession.strip() if previous == label else base + '_' + label
