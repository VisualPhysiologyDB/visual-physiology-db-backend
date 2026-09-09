"""Offline-reviewable bibliographic recovery. Network requests go only to fixed providers."""
import hashlib
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urlencode
import requests
from .bibliography import classify_identifier, citation_identity, crossref_metadata, datacite_metadata


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class MetadataClient:
    def __init__(self, cache, interval=1.05, timeout=20, retries=2, retry_failures=False, mailto=None, offline=False):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.interval, self.timeout, self.retries = max(interval, 1.0), timeout, retries
        self.retry_failures, self.mailto, self.offline = retry_failures, mailto, offline
        self.last_request = 0
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'VPOD-metadata-recovery/1.0' + (f' (mailto:{mailto})' if mailto else '')

    def get(self, url):
        file = self.cache / (digest(url) + '.json')
        if file.exists():
            cached = json.loads(file.read_text())
            if not (self.retry_failures and cached.get('error')):
                return cached
        result = {'url': url, 'retrieved_at': now(), 'error': 'not cached; offline'}
        if self.offline:
            return result
        for attempt in range(self.retries + 1):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.session.get(url, timeout=(min(5, self.timeout), self.timeout), allow_redirects=False)
                result = {'url': url, 'retrieved_at': now(), 'status': response.status_code,
                          'rate_headers': {k: v for k, v in response.headers.items() if 'rate-limit' in k.lower() or 'concurrency' in k.lower()}}
                if response.status_code == 200:
                    result['data'] = response.json()
                    break
                result['error'] = f'HTTP {response.status_code}'
                if response.status_code not in {429, 500, 502, 503, 504}:
                    break
                retry_after = response.headers.get('Retry-After', '0')
                try:
                    wait = float(retry_after)
                except ValueError:
                    try:
                        wait = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                    except (ValueError, TypeError):
                        wait = 0
                # Never retry earlier than the provider requests. Long deferrals remain resumable failures.
                if wait > 60:
                    result['error'] += f'; deferred Retry-After={retry_after}'
                    break
                if attempt < self.retries:
                    time.sleep(max(wait, 2 ** attempt))
            except (requests.RequestException, ValueError) as exc:
                result = {'url': url, 'retrieved_at': now(), 'error': type(exc).__name__ + ': ' + str(exc)}
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
        temporary = file.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False))
        temporary.replace(file)
        return result

    def crossref(self, doi):
        return self.get('https://api.crossref.org/works/' + quote(doi, safe='') + (('?' + urlencode({'mailto': self.mailto})) if self.mailto else ''))

    def datacite(self, doi):
        return self.get('https://api.datacite.org/dois/' + quote(doi, safe=''))

    def search(self, text):
        params = {'query.bibliographic': text[:1500], 'rows': 5}
        if self.mailto:
            params['mailto'] = self.mailto
        return self.get('https://api.crossref.org/works?' + urlencode(params))


def recover(reference, client):
    raw = reference.doi or reference.raw_citation or reference.source_url or reference.notes or ''
    identity = classify_identifier(raw)
    result = {'refid': reference.pk, 'identifier': raw, 'classification': identity['kind'], 'attempts': [], 'candidates': [], 'fields': {}, 'reason': ''}
    def evidence(response):
        result['attempts'].append({k: v for k, v in response.items() if k != 'data'})
    def accepted(metadata, response, provider, reason):
        result.update(fields=metadata, evidence_url=response['url'], provider=provider, retrieved_at=response['retrieved_at'], reason=reason)
    source_warning = ' '.join(str(v) for v in (reference.source_data or {}).values()) + ' ' + (reference.notes or '')
    if 'doi wrong' in source_warning.lower():
        result['classification'] = 'disputed'
        result['reason'] = 'Source explicitly flags DOI WRONG; registry match cannot confirm publication identity'
        return result
    doi = identity['doi'] or identity['candidate_doi']
    if doi:
        response = client.crossref(doi)
        evidence(response)
        message = response.get('data', {}).get('message', {})
        if isinstance(message, dict) and classify_identifier(message.get('DOI'))['doi'] == doi:
            # Citation-embedded DOIs need independent citation agreement too.
            confirmed, signals = citation_identity(raw, message)
            if identity['kind'] in {'doi', 'malformed_doi', 'url'} or confirmed:
                accepted(crossref_metadata(message), response, 'Crossref', 'Exact DOI registry match' if identity['kind'] == 'doi' else 'Recovered DOI; registry identity verified' if identity['kind'] != 'citation' else f'Citation corroborated: {signals}')
                return result
            result['candidates'].append({'doi': doi, 'title': (message.get('title') or [''])[0], 'signals': signals, 'reason': 'Embedded DOI conflicts with citation'})
        elif response.get('status') == 404:
            dc = client.datacite(doi)
            evidence(dc)
            attributes = dc.get('data', {}).get('data', {}).get('attributes', {})
            if classify_identifier(attributes.get('doi'))['doi'] == doi and identity['kind'] != 'citation':
                accepted(datacite_metadata(attributes), dc, 'DataCite', 'Exact DOI registry match')
                return result
    if identity['kind'] in {'citation', 'malformed_doi'} and len(raw) > 20:
        response = client.search(raw)
        evidence(response)
        matches = []
        for message in response.get('data', {}).get('message', {}).get('items', []):
            confirmed, signals = citation_identity(raw, message)
            metadata = crossref_metadata(message)
            result['candidates'].append({'doi': metadata['doi'], 'title': metadata['title'], 'date': metadata['publication_date'], 'authors': [a.get('family') for a in message.get('author', [])], 'journal': message.get('container-title'), 'volume': message.get('volume'), 'pages': message.get('page'), 'signals': signals, 'confirmed': confirmed})
            if confirmed and metadata['doi']:
                matches.append(metadata)
        unique = {m['doi']: m for m in matches}
        if len(unique) == 1:
            candidate = next(iter(unique.values()))
            # Search evidence alone is insufficient; resolve its DOI again.
            response2 = client.crossref(candidate['doi'])
            evidence(response2)
            message = response2.get('data', {}).get('message', {})
            confirmed, signals = citation_identity(raw, message)
            if confirmed and classify_identifier(message.get('DOI'))['doi'] == candidate['doi']:
                accepted(crossref_metadata(message), response2, 'Crossref', f'Citation title + author + year + journal or volume/pages, singleton resolved: {signals}')
                return result
        result['reason'] = 'Ambiguous candidates' if len(unique) > 1 else 'Insufficient independent bibliographic agreement; curator review needed'
    else:
        result['reason'] = 'No verified registry metadata; preserve raw source for curation'
    return result
