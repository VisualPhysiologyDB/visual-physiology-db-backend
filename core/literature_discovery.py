"""Bounded, deterministic discovery. Provider text is data, never executable instructions."""
import json
import re
from datetime import timedelta
from urllib.parse import quote, urlencode

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .bibliography import classify_identifier, compact, crossref_metadata, plain_text, safe_url
from .metadata_recovery import digest
from .models import (Reference, ReferenceMetadataAudit, DiscoveryRun, DiscoveryCheckpoint,
                     LiteratureCandidate, DiscoveryIdentifier, DiscoveryEvidence, DiscoveryReviewEvent)

PROVIDERS = {'europepmc', 'crossref'}
LIMITS = {'interval_days': (1, 365), 'initial_lookback_days': (1, 3650), 'overlap_days': (1, 365),
          'window_days': (1, 31), 'max_new_candidates': (1, 500), 'max_unreviewed': (1, 10000),
          'max_requests': (1, 1000), 'max_runtime_seconds': (10, 3600), 'page_size': (1, 100)}


def read_config(path):
    config = json.loads(path.read_text())
    allowed = set(LIMITS) | {'enabled', 'providers', 'queries', 'request_interval_seconds'}
    if set(config) - allowed:
        raise ValueError('Unknown configuration keys: ' + ', '.join(sorted(set(config) - allowed)))
    if type(config.get('enabled')) is not bool:
        raise ValueError('enabled must be true or false')
    for name, (minimum, maximum) in LIMITS.items():
        value = config.get(name)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f'{name} must be an integer from {minimum} to {maximum}')
    interval = config.get('request_interval_seconds')
    if type(interval) not in (int, float) or not 1 <= interval <= 60:
        raise ValueError('request_interval_seconds must be between 1 and 60')
    providers = config.get('providers')
    if not isinstance(providers, list) or not providers or any(p not in PROVIDERS for p in providers):
        raise ValueError('providers must contain europepmc and/or crossref')
    if len(set(providers)) != len(providers):
        raise ValueError('Duplicate providers')
    queries = config.get('queries')
    if not isinstance(queries, list) or not 1 <= len(queries) <= 30:
        raise ValueError('Provide between 1 and 30 queries')
    names = set()
    for q in queries:
        if set(q) - {'crossref_query'} != {'name', 'category', 'any_terms', 'required_any', 'exclude_terms'}:
            raise ValueError('Each query needs name, category, any_terms, required_any and exclude_terms')
        if 'crossref_query' in q and (not isinstance(q['crossref_query'], str) or not 1 <= len(q['crossref_query'].strip()) <= 300 or any(c in q['crossref_query'] for c in '\r\n')):
            raise ValueError('crossref_query must be a short bibliographic search string')
        if not isinstance(q['name'], str) or not re.fullmatch(r'[a-z0-9-]{1,100}', q['name']) or q['name'] in names:
            raise ValueError('Query names must be unique lowercase names with optional hyphens')
        names.add(q['name'])
        if not isinstance(q['category'], str) or not 1 <= len(q['category']) <= 100:
            raise ValueError('Provide a short category label')
        for field in ('any_terms', 'required_any', 'exclude_terms'):
            terms = q[field]
            if not isinstance(terms, list) or len(terms) > 30 or any(not isinstance(t, str) or not t.strip() or len(t) > 100 or any(c in t for c in '\n\r"\\') for t in terms):
                raise ValueError(f'{q["name"]}: {field} must be short plain search phrases without quotes or backslashes')
        if not q['any_terms']:
            raise ValueError('any_terms must not be empty')
    return config


def normalize_text(text):
    return re.sub(r'[\s‐‑–—-]+', ' ', (plain_text(text or '') or '').casefold()).strip()


def relevance(metadata, query):
    text = normalize_text((metadata.get('title') or '') + ' ' + (metadata.get('abstract') or ''))
    def matches(terms):
        return [t for t in terms if re.search(r'(?<!\w)' + re.escape(normalize_text(t)) + r'(?!\w)', text)]
    main, required, excluded = (matches(query[k]) for k in ('any_terms', 'required_any', 'exclude_terms'))
    if excluded:
        return False, 'Excluded phrase: ' + ', '.join(excluded)
    if not main or (query['required_any'] and not required):
        return False, 'Insufficient title/abstract evidence for the configured phrase groups'
    return True, 'Matched phrases: ' + ', '.join(main + required) + '. Search relevance only; measurements unverified.'


def search_url(provider, query, start, end, position, size):
    if provider == 'europepmc':
        groups = ['(' + ' OR '.join(f'TITLE_ABS:"{t}"' for t in query[k]) + ')' for k in ('any_terms', 'required_any') if query[k]]
        text = ' AND '.join(groups) + f' AND FIRST_IDATE:[{start} TO {end}]'
        params = {'query': text, 'format': 'json', 'resultType': 'core', 'pageSize': size, 'cursorMark': position.get('cursor', '*')}
        return 'https://www.ebi.ac.uk/europepmc/webservices/rest/search?' + urlencode(params), text
    # Small bounded date windows and offsets are restartable without expiring server cursors.
    offset = position.get('offset', 0)
    if offset > 10000:
        raise ValueError('Crossref offset exceeds 10,000. Narrow the query or window_days; checkpoint retained.')
    # Crossref is ranked word search, not the Europe PMC Boolean phrase query.
    # A short explicit anchor avoids an OR-like search across every generic word.
    text = query.get('crossref_query') or query['any_terms'][0]
    params = {'query.bibliographic': text, 'filter': f'from-index-date:{start},until-index-date:{end}',
              'rows': size, 'offset': offset, 'sort': 'indexed', 'order': 'asc'}
    return 'https://api.crossref.org/works?' + urlencode(params), text


def parse_page(provider, data, position, size):
    if provider == 'europepmc':
        if not isinstance(data.get('resultList'), dict) or 'hitCount' not in data:
            raise ValueError('Unexpected Europe PMC response schema')
        items = data['resultList'].get('result', [])
        cursor = data.get('nextCursorMark')
        more = bool(items and len(items) == size and cursor and cursor != position.get('cursor'))
        return items, {'cursor': cursor} if more else None
    message = data.get('message', {})
    if not isinstance(message, dict) or 'items' not in message or 'total-results' not in message:
        raise ValueError('Unexpected Crossref response schema')
    items = message['items']
    offset = position.get('offset', 0) + len(items)
    return items, {'offset': offset} if items and offset < message['total-results'] else None


def metadata_for(provider, item):
    if provider == 'crossref':
        m = crossref_metadata(item)
        m.update(provider_id=item.get('DOI', ''), authors=', '.join(' '.join(filter(None, [a.get('given'), a.get('family')])) for a in item.get('author', [])),
                 journal=plain_text((item.get('container-title') or [''])[0]), abstract=plain_text(item.get('abstract') or ''),
                 publication_type=item.get('type', ''), relations=item.get('relation', {}))
    else:
        # Europe PMC may pad incomplete firstPublicationDate values. pubYear alone is supported precision here.
        year = str(item.get('pubYear', ''))
        year = int(year) if re.fullmatch(r'[1-9]\d{3}', year) else None
        source, record = item.get('source', ''), item.get('id', '')
        m = {'doi': classify_identifier(item.get('doi'))['doi'], 'title': plain_text(item.get('title') or ''),
             'year_of_publication': year, 'publication_date': str(year) if year else None,
             'provider_id': f'{source}:{record}' if source and record else '', 'authors': item.get('authorString', ''),
             'journal': item.get('journalInfo', {}).get('journal', {}).get('title', ''),
             'abstract': plain_text(item.get('abstractText') or ''), 'publication_type': item.get('pubTypeList', {}),
             'relations': item.get('commentCorrectionList', {}), 'raw_first_publication_date': item.get('firstPublicationDate'),
             'source_url': f'https://europepmc.org/article/{quote(source, safe="")}/{quote(record, safe="")}' if source and record else ''}
    m['doi'] = m.get('doi') or ''
    m['source_url'] = safe_url(m.get('source_url')) or ('https://doi.org/' + quote(m['doi'], safe='/():;') if m['doi'] else '')
    if not m.get('title') or not m.get('provider_id'):
        raise ValueError('Missing title or provider record ID; row retained in the error report')
    return m


def identity_values(provider, metadata):
    values = [f'{provider}:{metadata["provider_id"]}']
    if metadata.get('doi'):
        values.append('doi:' + metadata['doi'])
    return values


def reference_matches(metadata):
    doi, url = metadata.get('doi'), metadata.get('source_url')
    matches = []
    for ref in Reference.objects.all():
        other = classify_identifier(ref.doi)
        if (doi and doi == other['doi']) or (url and url in {other['source_url'], ref.source_url}):
            matches.append(ref)
    return sorted(matches, key=lambda r: ({'APPROVED': 0, 'PENDING': 1, 'REJECTED': 2}.get(r.status, 3), r.pk))


def inspect_candidate(provider, metadata):
    keys = [digest(v) for v in identity_values(provider, metadata)]
    found = list(LiteratureCandidate.objects.filter(identifiers__key__in=keys).distinct())
    if len(found) > 1 or (found and metadata['doi'] and found[0].doi and found[0].doi != metadata['doi']):
        raise ValueError('Conflicting provider/DOI identities; human reconciliation required')
    if found:
        return 'rediscovered', found[0], []
    references = reference_matches(metadata)
    if references:
        return 'known_reference', None, [{'refid': r.pk, 'status': r.status} for r in references]
    # Title alone never confirms identity, especially for preprint/journal versions.
    title = compact(metadata['title'])
    possible = [{'refid': r.pk, 'status': r.status} for r in Reference.objects.exclude(title__isnull=True) if len(title) > 20 and compact(r.title) == title]
    possible += [{'candidate_id': c.pk, 'decision': c.decision} for c in LiteratureCandidate.objects.all() if len(title) > 20 and compact(c.title) == title]
    return 'new', None, possible


@transaction.atomic
def ingest(provider, metadata, query, url, retrieved_at, rationale, run):
    outcome, candidate, matches = inspect_candidate(provider, metadata)
    if outcome == 'known_reference':
        return outcome, None, matches
    if candidate is None:
        candidate = LiteratureCandidate.objects.create(title=metadata['title'], doi=metadata['doi'],
            source_url=metadata['source_url'], metadata={**metadata, 'possible_duplicates': matches}, categories=[query['category']])
    else:
        candidate = LiteratureCandidate.objects.select_for_update().get(pk=candidate.pk)
        # Discovery never changes decisions, reference fields or existing bibliographic metadata.
        candidate.categories = sorted(set(candidate.categories + [query['category']]))
        candidate.save(update_fields=['categories', 'last_seen'])
    for value in identity_values(provider, metadata):
        identifier, _ = DiscoveryIdentifier.objects.get_or_create(key=digest(value), defaults={'value': value, 'candidate': candidate})
        if identifier.candidate_id != candidate.pk:
            raise ValueError('Concurrent identity conflict; transaction rolled back')
    DiscoveryEvidence.objects.get_or_create(fingerprint=digest([str(run.pk), provider, metadata['provider_id'], query]), defaults={
        'candidate': candidate, 'run': run, 'provider': provider, 'provider_id': metadata['provider_id'], 'query': json.dumps(query, ensure_ascii=False),
        'evidence_url': url, 'retrieved_at': retrieved_at, 'rationale': rationale, 'metadata': metadata})
    return outcome, candidate, matches


def pending_reference(candidate, user):
    doi = classify_identifier(candidate.doi)['doi']
    source_url = safe_url(candidate.source_url) or ('https://doi.org/' + quote(doi, safe='/():;') if doi else None)
    return Reference(doi=doi, title=candidate.title, source_url=source_url,
        identifier_kind='doi' if doi else 'url', status='PENDING', submitted_by=user,
        source_data={'discovery_candidate_id': candidate.pk},
        **{f: candidate.metadata.get(f) for f in ('publication_date', 'online_date', 'print_date', 'year_of_publication')})


def validate_review(candidate, decision, data_status, note, user):
    if not user.is_active or not user.is_staff or not user.has_perm('core.change_literaturecandidate'):
        raise PermissionDenied('Literature inbox change permission required')
    if decision not in dict(LiteratureCandidate.DECISIONS) or data_status not in dict(LiteratureCandidate.DATA_STATES):
        raise ValidationError('Invalid review state')
    if decision in {'REJECTED', 'DEFERRED'} or (candidate.decision == 'REJECTED' and decision != 'REJECTED'):
        if not note.strip() or (candidate.decision == 'REJECTED' and decision != 'REJECTED' and note.strip() == candidate.review_note.strip()):
            raise ValidationError('Write a reason for this decision; reconsideration needs a new explanation.')
    if candidate.decision == 'ACCEPTED' and decision != 'ACCEPTED':
        raise ValidationError('This item already has an accepted reference. Review its publication status in References.')
    if decision != 'ACCEPTED' and data_status != 'NEEDS_DATA':
        raise ValidationError('Accept the paper before setting its data-entry outcome.')
    if decision == 'ACCEPTED' and candidate.decision != 'ACCEPTED':
        if not user.has_perm('core.add_reference'):
            raise PermissionDenied('Accepting literature requires add-reference permission')
        matches = reference_matches({'doi': candidate.doi, 'source_url': candidate.source_url})
        if matches and matches[0].status == 'REJECTED':
            raise ValidationError('This reference is rejected. Reconsider it explicitly in References first.')
        if not matches:
            pending_reference(candidate, user).full_clean()


@transaction.atomic
def review_candidate(pk, decision, data_status, note, user):
    candidate = LiteratureCandidate.objects.select_for_update().get(pk=pk)
    validate_review(candidate, decision, data_status, note, user)
    before = {'decision': candidate.decision, 'data_status': candidate.data_status, 'reference_id': candidate.reference_id}
    if decision == 'ACCEPTED' and not candidate.reference_id:
        matches = reference_matches({'doi': candidate.doi, 'source_url': candidate.source_url})
        if matches:
            candidate.reference = matches[0]
        else:
            ref = pending_reference(candidate, user)
            ref.full_clean()
            ref.save()
            candidate.reference = ref
            evidence = candidate.evidence.order_by('pk').first()
            for field in ('doi', 'title', 'source_url', 'publication_date', 'online_date', 'print_date', 'year_of_publication'):
                value = getattr(ref, field)
                if value is not None:
                    ReferenceMetadataAudit.objects.create(reference=ref, field=field, original_value=None, recovered_value=value,
                        provider=evidence.provider if evidence else 'literature inbox', evidence_url=evidence.evidence_url if evidence else candidate.source_url,
                        retrieved_at=evidence.retrieved_at if evidence else timezone.now(), decision='curator', applied=True,
                        reason=f'Curator {user.pk} accepted a discovery suggestion; not automatically verified or published.',
                        run_id=str(evidence.run_id) if evidence else '', fingerprint=digest(['discovery', candidate.pk, ref.pk, field, value]))
    candidate.decision, candidate.data_status, candidate.review_note = decision, data_status, note
    candidate.reviewed_by, candidate.reviewed_at = user, timezone.now()
    candidate.save(update_fields=['decision', 'data_status', 'review_note', 'reviewed_by', 'reviewed_at', 'reference'])
    DiscoveryReviewEvent.objects.create(candidate=candidate, reviewer=user, before=before,
        after={'decision': decision, 'data_status': data_status, 'reference_id': candidate.reference_id}, note=note)
    return candidate


def due(config, today):
    last = DiscoveryRun.objects.filter(status='COMPLETE', config_hash=digest(config)).first()
    return config['enabled'] and (last is None or (today - last.finished_at.date()).days >= config['interval_days'])


def initial_checkpoint(provider, query, config, end):
    key = digest([provider, query, config['window_days'], config['initial_lookback_days']])
    saved = DiscoveryCheckpoint.objects.filter(key=key).first()
    checkpoint = saved or DiscoveryCheckpoint(key=key, provider=provider, query_name=query['name'])
    if not checkpoint.window_start:
        start = checkpoint.completed_through - timedelta(days=config['overlap_days'] - 1) if checkpoint.completed_through else end - timedelta(days=config['initial_lookback_days'] - 1)
        checkpoint.window_start = start
        checkpoint.window_end = min(end, start + timedelta(days=config['window_days'] - 1))
        checkpoint.position = {}
    return checkpoint
