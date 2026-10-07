"""Create a consistent, private SQLite snapshot without overwriting any file.

Usage: python tools/backup_sqlite.py SOURCE.sqlite3 NEW_BACKUP.sqlite3
Uses the standard library only; the source is opened read-only, including its WAL.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time


def backup(source, destination, timeout=120):
    source = Path(source).expanduser().resolve(strict=True)
    destination = Path(destination).expanduser().absolute()
    if not source.is_file():
        raise ValueError('Source must be an existing SQLite file.')
    if source == destination.resolve():
        raise ValueError('Source and destination must differ.')
    # O_EXCL also refuses existing symlinks. Parent creation is deliberately explicit.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    started = time.monotonic()

    def progress(status, remaining, total):
        if time.monotonic() - started > timeout:
            raise TimeoutError('Backup timed out; retry when database writes are quieter.')

    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=10)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst, pages=256, progress=progress, sleep=0.1)
                if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('Snapshot failed SQLite integrity_check.')
        digest = hashlib.sha256()
        with destination.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(block)
        return {'source': str(source), 'backup': str(destination),
                'bytes': destination.stat().st_size, 'sha256': digest.hexdigest(),
                'integrity_check': 'ok'}
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination', help='New file in an existing private backup directory.')
    args = parser.parse_args()
    try:
        result = backup(args.source, args.destination)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f'Backup failed: {exc}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
