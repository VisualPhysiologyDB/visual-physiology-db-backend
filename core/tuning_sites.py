"""Site browsing uses verified sequence/profile-specific coordinates, never bare site numbers.

The offline index contains public source proteins only. Public requests read it and
fall back to explicitly named source numbering when an index is absent or stale.
"""
import hashlib
import json
import tempfile
from functools import lru_cache
from pathlib import Path
from django.conf import settings
from .tuning_mapping import read_alignment, coordinate_maps, MappingError

VERSION = 'site-index-1'
REFERENCES = ('bovine', 'squid', 'spider', 'human-lws')


def sequence_hash(sequence):
    return hashlib.sha256(sequence.encode()).hexdigest()


def profile_data():
    path = Path(settings.BASE_DIR) / 'data/tuning/reference-profile.fasta'
    return read_alignment(path.read_text()), hashlib.sha256(path.read_bytes()).hexdigest()


def index_path():
    return Path(settings.VPOD_TUNING_SITE_INDEX_PATH)


@lru_cache(maxsize=4)
def _read_index(path, stamp):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) and data.get('version') == VERSION else {}
    except (OSError, ValueError):
        return {}


def read_index():
    path = index_path()
    try: return _read_index(str(path), path.stat().st_mtime_ns)
    except OSError: return {}


def coordinates(alignments, query):
    maps = [coordinate_maps(a) for a in alignments]
    result = {}
    length = len(alignments[0][query].replace('-', ''))
    for ref in REFERENCES:
        result[ref] = []
        for pos in range(1, length+1):
            hits = [m[ref][1].get(m[query][0][pos]) for m in maps]
            result[ref].append(hits[0][0] if hits[0] is not None and hits[0] == hits[1] else None)
    return result


def write_index(proteins, cache, output=None):
    profile, fingerprint = profile_data()
    path = Path(output) if output else index_path()
    previous = read_index() if path == index_path() else {}
    reusable = previous.get('sequences', {}) if previous.get('profile_sha256') == fingerprint and previous.get('algorithm') == cache.identity else {}
    result = {'version': VERSION, 'profile_sha256': fingerprint, 'algorithm': cache.identity, 'sequences': {}, 'unresolved': []}
    for protein in proteins:
        sha = sequence_hash(protein.sequence)
        if sha in result['sequences']: continue
        if sha in reusable:
            result['sequences'][sha] = reusable[sha]; continue
        try:
            anchor = next((k for k, seq in profile.items() if seq.replace('-', '') == protein.sequence), None)
            alignments = [profile, profile] if anchor else cache.align(protein.sequence)
            result['sequences'][sha] = coordinates(alignments, anchor or 'query')
        except (MappingError, OSError, ValueError) as exc:
            result['unresolved'].append({'protein_key': protein.key, 'reason': str(exc)})
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False, suffix='.tmp') as handle:
        json.dump(result, handle, separators=(',', ':')); handle.write('\n'); temporary = Path(handle.name)
    temporary.chmod(0o644)  # Public catalogue coordinates must be readable by the web worker.
    temporary.replace(path)
    return {'indexed_sequences': len(result['sequences']), 'unresolved': result['unresolved'], 'output': str(path)}


def site_groups(entries, proteins):
    """Group by family AND stable reference coordinate; preserve every observation."""
    profile, fingerprint = profile_data()
    stored = read_index()
    indexed = stored.get('sequences', {}) if stored.get('profile_sha256') == fingerprint else {}
    anchors = {p.key: p for p in proteins if p.key in REFERENCES and p.key in profile
               and p.sequence == profile[p.key].replace('-', '')}
    source_maps = {}
    for entry in entries:
        protein = entry.protein
        sha = sequence_hash(protein.sequence)
        if sha not in source_maps:
            anchor = next((k for k, seq in profile.items() if seq.replace('-', '') == protein.sequence), None)
            value = coordinates([profile, profile], anchor) if anchor else indexed.get(sha, {})
            source_maps[sha] = value if isinstance(value, dict) else {}
    result = {}
    for ref in REFERENCES:
        groups = {}
        for entry in entries:
            mapping = source_maps[sequence_hash(entry.protein.sequence)].get(ref, [])
            for change in entry.changes:
                pos = change['position']
                mapped = mapping[pos-1] if pos <= len(mapping) else None
                resolved = ref in anchors and type(mapped) is int and 1 <= mapped <= len(anchors[ref].sequence)
                protein, coordinate = (anchors[ref], mapped) if resolved else (entry.protein, pos)
                key = f'{ref}|{entry.family}|{protein.key}|{coordinate}'
                group = groups.setdefault(key, {'key': key, 'protein_key': protein.key, 'position': coordinate,
                    'residue': protein.sequence[coordinate-1], 'family': entry.family,
                    'label': f'{protein.name} · {coordinate}', 'numbering': protein.name,
                    'reference_mapped': bool(resolved), 'members': []})
                group['members'].append({'evidence_key': entry.key, 'source_position': pos,
                    'from': change.get('from'), 'to': change.get('to'), 'combination_size': len(entry.changes)})
        result[ref] = sorted(groups.values(), key=lambda g: (not g['reference_mapped'], g['position'], g['family'], g['key']))
    return result
