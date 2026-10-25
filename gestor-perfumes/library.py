"""Deduplicación de biblioteca y backups exclusivamente locales."""
import difflib
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from datetime import datetime


def identity(name, brand, normalize):
    name = re.sub(r'\b\d+(?:[.,]\d+)?\s*ml\b', ' ', name or '', flags=re.I)
    name, brand = normalize(name), normalize(brand)
    if brand and name.startswith(brand + ' '):
        name = name[len(brand):].strip()
    return name, brand


def classify(rows, existing, normalize):
    known = [dict(p, _kind='master') for p in existing]
    result = []
    for row in rows:
        name, brand = identity(row['name'], row.get('brand'), normalize)
        exact, similar = None, []
        for other in known:
            other_name, other_brand = identity(other['name'],other.get('brand'),normalize)
            if (name,brand) == (other_name,other_brand):
                exact = other
                break
            # Mismo nombre con marca distinta/ausente requiere revisión.
            if name == other_name:
                similar.append(other)
                continue
            if brand and other_brand and brand != other_brand:
                continue
            # Una variante con palabras adicionales es distinta, nunca se fusiona.
            left, right = set(name.split()), set(other_name.split())
            if left != right and (left < right or right < left):
                continue
            if difflib.SequenceMatcher(None,name,other_name).ratio() >= .82:
                similar.append(other)
        status = 'duplicate' if exact else 'review' if similar else 'new'
        result.append({'row':dict(row), 'status':status, 'matches':[exact] if exact else similar[:5]})
        if not exact:
            known.append(dict(row, _kind='source'))
    return result


def comparison_groups(analysis, existing, normalize):
    """Unión de maestra + origen, con coincidencias en renglones consecutivos.

    Los IDs de dos bases nunca se confunden: cada clave incluye su procedencia.
    """
    nodes, parent = {}, {}

    def add(key, value):
        nodes[key] = value
        parent.setdefault(key,key)

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for p in existing:
        add(('master',p['id']), {'kind':'master','row':p,'status':'kept','matches':[]})
    for item in analysis:
        add(('source',item['row']['id']),dict(item,kind='source'))
    for item in analysis:
        key = ('source',item['row']['id'])
        for match in item['matches']:
            other = (match.get('_kind','master'),match['id'])
            if other in nodes:
                parent[root(key)] = root(other)
    groups = {}
    for key,value in nodes.items():
        groups.setdefault(root(key),[]).append(value)
    result = []
    for values in groups.values():
        values.sort(key=lambda x:(x['kind'] != 'master',normalize(x['row']['name']),x['row']['id']))
        statuses = {x['status'] for x in values}
        status = next((x for x in ('review','duplicate','new') if x in statuses),'kept')
        result.append({'rows':values,'status':status})
    return sorted(result,key=lambda x:(x['status'] != 'review',normalize(x['rows'][0]['row']['name'])))


def backup_library(engine):
    """Snapshot consistente con la API de SQLite; nunca conecta a clientes."""
    database = engine.url.database
    if not database or database == ':memory:':
        return None
    path = Path(database).resolve()
    root = path.parent / 'library-backups'
    root.mkdir(exist_ok=True,mode=0o700)
    fd, target = tempfile.mkstemp(prefix=f'biblioteca-{datetime.now():%Y%m%d-%H%M%S}-',suffix='.sqlite',dir=root)
    os.close(fd)
    try:
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as source:
            with sqlite3.connect(target) as destination:
                source.backup(destination)
                if destination.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError('No se pudo verificar el backup local.')
    except Exception:
        Path(target).unlink(missing_ok=True)
        raise
    return target
