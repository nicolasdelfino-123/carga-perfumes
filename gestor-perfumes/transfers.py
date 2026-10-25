"""Adaptador del esquema inspeccionado de Catálogo Web. Sin DDL en clientes."""
import hashlib
import json
import os
import re
import subprocess
import uuid
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, event, select, or_
from sqlalchemy.engine import make_url


class TransferError(ValueError):
    pass


def connections():
    path = os.getenv("PERFUME_CONNECTIONS") or str(Path(__file__).parent / 'instance' / 'connections.json')
    if not Path(path).is_file():
        if os.getenv('PERFUME_CONNECTIONS'):
            raise TransferError('No existe el archivo de conexiones configurado.')
        return {}
    config = json.loads(Path(path).read_text())
    for alias, item in config.items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", alias):
            raise TransferError("Alias de conexión inválido.")
        if not (item.get("url_env") or item.get('url_file')) or not set(item.get("roles", [])) <= {"source", "target"}:
            raise TransferError("Configuración de conexiones inválida.")
    return config


def authorized(alias, role):
    item = connections().get(alias)
    if not item or role not in item.get("roles", []):
        raise TransferError("Base inexistente o no autorizada para esta operación.")
    raw = os.getenv(item['url_env']) if item.get('url_env') else None
    if not raw and item.get('url_file'):
        file = Path(item['url_file'])
        if file.stat().st_mode & 0o077:
            raise TransferError('El archivo de credenciales debe tener permisos privados (600).')
        raw = file.read_text().strip()
    if not raw:
        raise TransferError("Falta la variable de conexión configurada.")
    url = make_url(raw)
    if url.get_backend_name() != "postgresql":
        raise TransferError("Los clientes deben utilizar PostgreSQL.")
    if not url.database or url.database != item.get("database"):
        raise TransferError("El nombre de la base no coincide con la autorización.")
    return item, url.set(drivername="postgresql+psycopg")


@contextmanager
def connect(alias, role, write=False):
    if write:
        raise TransferError("ABORTADO: está prohibido abrir conexiones de escritura a clientes.")
    item, url = authorized(alias, role)
    engine = create_engine(url, connect_args={
        "connect_timeout": 10, "options": "-c default_transaction_read_only=on"})

    @event.listens_for(engine, "before_cursor_execute")
    def enforce_read_only(conn, cursor, statement, parameters, context, executemany):
        sql = " ".join(statement.strip().upper().split())
        allowed_settings = {"SET TRANSACTION READ ONLY", "SET LOCAL STATEMENT_TIMEOUT = '30S'",
                            "SET LOCAL LOCK_TIMEOUT = '10S'"}
        if not (sql.startswith("SELECT ") or sql.startswith("SHOW ") or sql in allowed_settings):
            raise TransferError("ABORTADO: se bloqueó una operación SQL que no es una consulta de lectura.")
    try:
        with engine.connect() as conn:
            with conn.begin():
                if not write:
                    conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
                conn.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
                yield conn, item
    finally:
        engine.dispose()


def schema(conn):
    meta = MetaData()
    tables = {name: Table(name, meta, schema="public", autoload_with=conn, resolve_fks=False)
              for name in ("product", "category", "product_images")}
    required = {
        "product": {"id", "name", "brand", "description", "short_description", "price", "stock",
                    "category_id", "image_url", "is_active", "created_at", "volume_ml", "volume_options",
                    "flavors", "flavor_enabled", "flavor_catalog", "flavor_stock_mode", "price_wholesale",
                    "puffs", "nicotine_mg"},
        "category": {"id", "name", "description"},
        "product_images": {"id", "product_id", "mime_type", "bytes", "width", "height", "digest", "created_at"},
    }
    for name, table in tables.items():
        if not required[name] <= set(table.c.keys()):
            raise TransferError(f"Esquema incompatible: faltan campos en {name}.")
        for col in table.c:
            if col.name not in required[name] and not col.nullable and col.server_default is None:
                raise TransferError(f"Campo obligatorio adicional sin valor por defecto: {name}.{col.name}.")
        if not table.c.id.server_default and not table.c.id.identity:
            raise TransferError(f"La tabla {name} no genera IDs automáticamente.")
    return tables


def category_key(name):
    value = unicodedata.normalize('NFKD',name or '')
    value = ''.join(c for c in value if not unicodedata.combining(c)).casefold()
    return ' '.join(re.findall(r'\w+',value))


NON_PERFUME_WORDS = {'reloj','relojes','accesorio','accesorios','cartera','carteras','maquillaje',
                     'zapatillas','ropa','vape','vapes','vapeadores','cigarrillos'}


def is_perfume_category(name, explicit_names=()):
    key = category_key(name)
    words = set(key.split())
    if words & NON_PERFUME_WORDS:
        return False
    allowed = {'arabe','arabes','arabigo','arabigos','disenador','disenadores','disenadora','designer','nicho','niche'}
    return bool(words & allowed) or key in {category_key(x) for x in explicit_names}


def product_category_ids(row):
    ids = [row['category_id']]
    for meta in row.get('flavor_catalog') or []:
        if isinstance(meta,dict) and meta.get('__type')=='multi_category_meta':
            for value in meta.get('extra_category_ids') or []:
                try:
                    cid = int(value)
                except (ValueError,TypeError):
                    continue
                if cid not in ids:
                    ids.append(cid)
    return ids


def allowed_category_ids(category_names, item):
    explicit = item.get('perfume_category_names',[])
    if not isinstance(explicit,list) or any(not isinstance(x,str) for x in explicit):
        raise TransferError('La lista de categorías de perfumes autorizadas no es válida.')
    return {cid for cid,name in category_names.items() if is_perfume_category(name,explicit)}


def source_products(alias, query=""):
    with connect(alias, "source") as (conn, item):
        t = schema(conn)
        p, c = t["product"], t["category"]
        category_names = dict(conn.execute(select(c.c.id,c.c.name)).all())
        allowed = allowed_category_ids(category_names,item)
        stmt = select(p.c.id, p.c.name, p.c.brand, p.c.category_id,p.c.flavor_catalog,c.c.name.label("category")).join(
            c, p.c.category_id == c.c.id)
        if query:
            stmt = stmt.where(p.c.name.icontains(query, autoescape=True) |
                              p.c.brand.icontains(query, autoescape=True))
        result = []
        for row in conn.execute(stmt.order_by(p.c.name,p.c.id).execution_options(yield_per=1000)).mappings():
            ids = product_category_ids(row)
            if not set(ids) & allowed:
                continue
            if any(set(category_key(category_names.get(cid,'')).split()) & NON_PERFUME_WORDS for cid in ids):
                continue
            result.append({key:row[key] for key in ('id','name','brand','category')})
            if len(result)>=1000:
                break
        return result


def extract(alias, ids):
    """Congela contenido seleccionado; nunca guarda precios ni stock de origen."""
    with connect(alias, "source") as (conn, item):
        t = schema(conn)
        p, c, image = t["product"], t["category"], t["product_images"]
        category_names = dict(conn.execute(select(c.c.id, c.c.name)).all())
        products = list(conn.execute(select(p, c.c.name.label("category_name")).join(
            c, p.c.category_id == c.c.id).where(p.c.id.in_(ids)).order_by(p.c.id)).mappings())
        if len(products) != len(set(ids)):
            raise TransferError('Algún producto seleccionado ya no existe en el origen.')
        allowed = allowed_category_ids(category_names,item)
        for row in products:
            categories = product_category_ids(row)
            forbidden = any(set(category_key(category_names.get(cid,'')).split()) & NON_PERFUME_WORDS for cid in categories)
            if forbidden or not set(categories) & allowed:
                raise TransferError(f"ABORTADO: {row['name']} no pertenece a categorías de perfumes autorizadas (árabes, diseñador o nicho).")
        main_ids = {int(match[1]) for row in products
                    if (match := re.fullmatch(r'/public/img/(\d+)', row['image_url'] or ''))}
        image_rows = list(conn.execute(select(image).where(or_(
            image.c.product_id.in_(ids), image.c.id.in_(main_ids))).order_by(image.c.id)).mappings())
        by_id = {row['id']:dict(row) for row in image_rows}
        by_product = {}
        for pic in image_rows:
            by_product.setdefault(pic['product_id'],[]).append(dict(pic))
        result = []
        for row in products:
            pictures = list(by_product.get(row['id'],[]))
            warnings = []
            main = row["image_url"] or ""
            match = re.fullmatch(r"/public/img/(\d+)", main)
            if match:
                image_id = int(match[1])
                primary = by_id.get(image_id)
                if not primary:
                    warnings.append('La foto principal indicada en el origen no existe. Se recuperan las otras fotos disponibles.')
                else:
                    pictures = [dict(primary)] + [x for x in pictures if x["id"] != image_id]
            elif main:
                # Rutas explícitas del servidor montadas localmente. No descarga URLs arbitrarias.
                root = item.get("image_root")
                prefix = "/public/uploads/"
                try:
                    if not root or not main.startswith(prefix):
                        raise TransferError('La imagen principal es externa o su carpeta local no está configurada.')
                    import mimetypes
                    base = Path(root).resolve()
                    file = (base / main[len(prefix):]).resolve()
                    if not file.is_relative_to(base) or not file.is_file():
                        raise TransferError('Archivo de imagen ausente o fuera de la carpeta autorizada.')
                    if file.stat().st_size > 20 * 1024 * 1024:
                        raise TransferError('Imagen demasiado grande (máximo 20 MB).')
                    mime = mimetypes.guess_type(file.name)[0] or ''
                    if mime not in {'image/png','image/jpeg','image/webp','image/gif','image/avif'}:
                        raise TransferError('Formato de imagen local no admitido.')
                    pictures.insert(0, {'bytes':file.read_bytes(),'mime_type':mime,'width':None,'height':None})
                except (TransferError,OSError) as error:
                    warnings.append(str(error) if isinstance(error,TransferError) else 'No se pudo leer el archivo de imagen local.')
            cleaned = []
            for pic in pictures:
                blob = bytes(pic["bytes"] or b'')
                if not blob or len(blob) > 20 * 1024 * 1024:
                    warnings.append('Se omitió una imagen vacía o demasiado grande; las demás fotos se conservan.')
                    continue
                digest = hashlib.sha256(blob).hexdigest()
                if digest not in {x["digest"] for x in cleaned}:
                    cleaned.append({"bytes": blob, "mime_type": pic["mime_type"],
                                    "width": pic["width"], "height": pic["height"], "digest": digest})
            labels = [row['category_name']]
            for meta in row['flavor_catalog'] or []:
                if isinstance(meta, dict) and meta.get('__type') == 'multi_category_meta':
                    for raw_id in meta.get('extra_category_ids') or []:
                        try:
                            label = category_names.get(int(raw_id))
                        except (ValueError, TypeError):
                            continue
                        if label and label not in labels:
                            labels.append(label)
            result.append({"name": row["name"], "brand": row["brand"] or "",
                           "category": row["category_name"], "description": row["description"] or "",
                           "category_labels": labels,
                           "short_description": row["short_description"] or "", "pictures": cleaned,
                           'image_warnings':warnings,
                           "origin": {"source": alias, "product_id": row["id"]}})
        if len(result) != len(set(ids)):
            raise TransferError("Algún producto seleccionado ya no existe en el origen.")
        return result


def duplicate_status(row, existing, normalize):
    key = normalize(row["name"])
    brand = normalize(row.get("brand") or "")
    for other in existing:
        if key == normalize(other["name"]):
            other_brand = normalize(other.get("brand") or "")
            if brand == other_brand or not brand or not other_brand:
                return True
    return False


def category_features(name, normalize):
    words = set(normalize(name).split())
    families, genders = set(), set()
    aliases = {
        'disenador': {'disenador', 'disenadores', 'designer', 'disenadora'},
        'nicho': {'nicho', 'niche'},
        'arabe': {'arabe', 'arabes', 'arabigo', 'arabigos', 'arabia'},
        'masculino': {'masculino', 'masculinos', 'masc', 'hombre', 'hombres', 'men', 'hombres'},
        'femenino': {'femenino', 'femeninos', 'femenina', 'femeninas', 'fem', 'mujer', 'mujeres', 'women'},
        'unisex': {'unisex'},
    }
    for key, variants in aliases.items():
        if words & variants:
            (genders if key in {'masculino','femenino','unisex'} else families).add(key)
    return families, genders


def suggest_category(source, names, normalize):
    """No cruza familias ni convierte unisex en masculino/femenino por su cuenta."""
    exact = [name for name in names if normalize(name) == normalize(source)]
    if len(exact) == 1:
        return exact[0], 'Coincidencia por nombre'
    family, gender = category_features(source, normalize)
    scored = []
    for name in names:
        f, g = category_features(name, normalize)
        if not family and not gender:
            continue
        if family != f:
            continue
        if gender and g and gender != g:
            continue
        score = 10 + (5 if gender == g else 0) + (1 if not g else 0)
        scored.append((score, name))
    if scored:
        top = max(score for score, _ in scored)
        best = [name for score, name in scored if score == top]
        if len(best) == 1:
            return best[0], 'Sugerencia por palabras clave; revisá el género'
        return '', 'Hay varias categorías posibles: elegí una'
    return '', 'Sin coincidencia segura: elegí categoría o conservá el nombre de origen'


def inspect_target(conn, rows, normalize):
    t = schema(conn)
    p = t["product"]
    existing = list(conn.execute(select(p.c.name, p.c.brand)).mappings())
    pending, omitted = [], []
    for row in rows:
        if duplicate_status(row, existing, normalize):
            omitted.append(row["name"])
        else:
            pending.append(row)
            existing.append(row)
    return t, pending, omitted


def backup(alias, folder):
    """pg_dump completo, sin contraseña en argumentos; valida el archivo con pg_restore."""
    _, url = authorized(alias, "target")
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / f"{alias}-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex}.dump"
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("PG"):
            env.pop(key)
    env.update(PGHOST=url.host or "localhost", PGPORT=str(url.port or 5432),
               PGDATABASE=url.database, PGUSER=url.username or "", PGPASSWORD=url.password or "",
               PGCONNECT_TIMEOUT="10")
    for key, value in url.query.items():
        if key not in {"sslmode", "sslrootcert", "sslcert", "sslkey"}:
            raise TransferError("Parámetro de conexión no soportado por el backup.")
        env["PG" + key.upper()] = str(value)
    try:
        with path.open("xb") as output:
            os.chmod(path, 0o600)
            subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--no-acl"],
                           stdout=output, stderr=subprocess.PIPE, env=env, check=True, timeout=600)
        subprocess.run(["pg_restore", "--list", str(path)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise TransferError("Falló el backup o su validación. No se importó nada; revisá pg_dump y pg_restore.") from None
    return str(path)


def execute(alias, rows, normalize, folder):
    # Bloqueo deliberado: ninguna variable de entorno puede habilitar escrituras.
    raise TransferError("ABORTADO: las escrituras en bases de clientes están bloqueadas. Solo se permite recuperar datos hacia la biblioteca local.")
