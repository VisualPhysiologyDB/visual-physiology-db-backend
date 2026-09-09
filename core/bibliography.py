"""Conservative identifier, method and date handling. No network at import time."""
import re
import unicodedata
from urllib.parse import quote, unquote, urlsplit
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from .validators import partial_date

DOI_RE = re.compile(r'10\.\d{4,9}/[^\s"<>]+', re.I)
EMPTY = {'', 'missing', 'n/a', 'na', 'none', 'no doi', 'unknown'}
NONPUBLICATION = ('genbank search', 'various citations', 'directed search')


def safe_url(value):
    value = (value or '').strip()
    if any(ord(c) < 33 for c in value):
        return None
    try:
        u = urlsplit(value)
        if u.scheme.lower() in {'http', 'https'} and u.hostname and not u.username and not u.password:
            URLValidator(schemes=['http', 'https'])(value)
            return value
    except (ValueError, ValidationError):
        pass
    return None


def classify_identifier(value):
    raw = (value or '').strip()
    if raw.lower() in EMPTY:
        return {'kind': 'placeholder', 'doi': None, 'source_url': None, 'candidate_doi': None}
    cleaned = re.sub(r'^doi:\s*', '', raw, flags=re.I)
    if cleaned.startswith('10.'):
        cleaned = unquote(cleaned)
    try:
        u = urlsplit(cleaned)
        if u.hostname and u.hostname.lower() in {'doi.org', 'dx.doi.org'}:
            cleaned = unquote(u.path.lstrip('/'))
    except ValueError:
        pass
    if DOI_RE.fullmatch(cleaned):
        return {'kind': 'doi', 'doi': cleaned.lower(), 'source_url': None, 'candidate_doi': None}
    url = safe_url(raw)
    matches = DOI_RE.findall(raw)
    candidate = matches[0].rstrip('.,;') if len(matches) == 1 else None
    if candidate and candidate.count(')') > candidate.count('('):
        candidate = candidate.rstrip(')')
    kind = 'url' if url else 'citation'
    if any(x in raw.lower() for x in NONPUBLICATION):
        kind = 'nonpublication'
    elif candidate and not url:
        prefix = raw[:raw.lower().find(candidate)].strip()
        if re.fullmatch(r':?\s*(?:https?|ttps)(?:://)?(?:dx\.|doi[-.]org/)?', prefix, flags=re.I):
            kind = 'malformed_doi'
    return {'kind': kind, 'doi': None, 'source_url': url, 'candidate_doi': candidate.lower() if candidate else None}


def reference_link(ref):
    classified = classify_identifier(ref.doi)
    return ('https://doi.org/' + quote(classified['doi'], safe='/():;')) if classified['doi'] else safe_url(ref.source_url) or classified['source_url']


METHODS = {
    'heterologous': ('Heterologous expression', 'experimental'),
    'heterolgous': ('Heterologous expression', 'experimental'),
    'hetereologous': ('Heterologous expression', 'experimental'),
    'msp': ('Microspectrophotometry (MSP)', 'experimental'),
    'scp': ('Single-cell photometry (SCP)', 'experimental'),
    'erg': ('Electroretinography (ERG)', 'experimental'),
    'ish': ('In situ hybridization (ISH)', 'experimental_other'),
    'ish (fish)': ('Fluorescence in situ hybridization (FISH)', 'experimental_other'),
    'kinetics': ('Kinetics', 'experimental_other'),
    'kintetics': ('Kinetics', 'experimental_other'),
    'mutagenesis': ('Mutagenesis', 'experimental_other'),
    'immunostaining': ('Immunostaining', 'experimental_other'),
    'biochemistry': ('Biochemistry (unspecified)', 'unclassified'),
    'computational inference': ('Computational inference', 'computational'),
    'mnm': ('MNM computational inference', 'computational'),
    'optics': ('OPTICS computational inference', 'computational'),
}


def normalize_methods(raw):
    if not raw or raw.strip().lower() in EMPTY:
        return None
    result = []
    for token in re.split(r',|//|;', raw):
        token = token.strip()
        if not token:
            continue
        uncertain = '?' in token
        key = token.replace('?', '').strip().lower()
        name, kind = METHODS.get(key, (token.replace('?', '').strip(), 'unclassified'))
        entry = {'name': name, 'kind': kind, 'uncertain': uncertain, 'raw': token}
        if not any(all(x[k] == entry[k] for k in ('name', 'kind', 'uncertain')) for x in result):
            result.append(entry)
    return result or None


def date_parts(value):
    parts = value.get('date-parts', [[]])[0] if isinstance(value, dict) else []
    if not parts or len(parts) > 3 or any(not isinstance(x, int) for x in parts):
        return None
    text = '-'.join([f'{parts[0]:04d}'] + [f'{x:02d}' for x in parts[1:]])
    try:
        partial_date(text)
        return text
    except ValidationError:
        return None


def plain_text(value):
    # Bibliographic deposits may include JATS/HTML inline markup.
    from html import unescape
    return unescape(re.sub(r'<[^>]*>', '', value or '')).strip() or None


def crossref_metadata(message):
    online = date_parts(message.get('published-online'))
    printed = date_parts(message.get('published-print'))
    selected = printed or online or date_parts(message.get('published')) or date_parts(message.get('issued'))
    return {'doi': classify_identifier(message.get('DOI'))['doi'],
            'title': plain_text((message.get('title') or [None])[0]),
            'online_date': online, 'print_date': printed, 'publication_date': selected,
            'year_of_publication': int(selected[:4]) if selected else None}


def datacite_metadata(attributes):
    issued = next((d.get('date') for d in attributes.get('dates', []) if d.get('dateType') == 'Issued'), None)
    try:
        partial_date(issued)
    except (ValidationError, TypeError):
        issued = None
    year = attributes.get('publicationYear')
    if not issued and year and re.fullmatch(r'[1-9]\d{3}', str(year)):
        issued = str(year)
    return {'doi': classify_identifier(attributes.get('doi'))['doi'],
            'title': plain_text((attributes.get('titles') or [{}])[0].get('title')),
            'publication_date': issued, 'year_of_publication': int(issued[:4]) if issued else None}


def compact(value):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', value or '').encode('ascii', 'ignore').decode().lower())


def citation_identity(citation, message):
    """Require near-exact title containment AND independent bibliographic agreement.

    Fuzzy similarity is never used as proof. All available signals are reported.
    A year may match either online or print. Journal abbreviations usually require
    manual review unless volume+first page provide independent corroboration.
    """
    normalized = compact(citation)
    title = compact((message.get('title') or [''])[0])
    author = next(iter(message.get('author') or []), {}).get('family', '')
    years = {d[:4] for k in ('published-online', 'published-print', 'published', 'issued') if (d := date_parts(message.get(k)))}
    year_match = any(re.search(r'\b' + y + r'\b', citation) for y in years)
    journal = compact((message.get('container-title') or [''])[0])
    volume = message.get('volume', '')
    first_page = re.split(r'[-–]', message.get('page', ''))[0]
    evidence = {
        'title': len(title) >= 20 and title in normalized,
        'first_author': len(compact(author)) >= 3 and compact(author) in normalized,
        'year': bool(year_match),
        'journal': len(journal) >= 8 and journal in normalized,
        'volume': bool(volume and re.search(r'(?<!\d)' + re.escape(volume) + r'(?!\d)', citation)),
        'first_page': bool(first_page and re.search(r'(?<!\d)' + re.escape(first_page) + r'(?!\d)', citation)),
    }
    confirmed = evidence['title'] and evidence['first_author'] and evidence['year'] and (evidence['journal'] or (evidence['volume'] and evidence['first_page']))
    return confirmed, evidence
