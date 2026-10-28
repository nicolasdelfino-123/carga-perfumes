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


def target_inventory(alias):
    with connect(alias,'target') as (conn, _):
        p = schema(conn)['product']
        return [dict(row) for row in conn.execute(select(p.c.id,p.c.name,p.c.brand,p.c.price,p.c.is_active)).mappings()]


def possible_target_matches(row, existing, normalize):
    import difflib
    result = []
    brand = normalize(row.get('brand') or '')
    for other in existing:
        other_brand = normalize(other.get('brand') or '')
        if brand and other_brand and brand != other_brand:
            continue
        # Para sugerir, contempla marca en cualquier posición y tamaño explícito.
        brand_words = set((brand or other_brand).split())
        def words(name):
            name = re.sub(r'\b\d+(?:[.,]\d+)?\s*ml\b', ' ', name, flags=re.I)
            return set(normalize(name).split()) - brand_words - {'edp','edt','parfum','perfume'}
        left, right = words(row['name']), words(other['name'])
        if not left or not right:
            continue
        score = len(left & right) / len(left | right)
        ratio = difflib.SequenceMatcher(None,' '.join(sorted(left)), ' '.join(sorted(right))).ratio()
        if score >= .65 or ratio >= .84:
            result.append({'id':other.get('id'), 'name':other['name'], 'brand':other.get('brand') or '', 'price':other.get('price'), 'is_active':other.get('is_active',True)})
    return result


def match_signature(match):
    return [match.get('id'),match['name'],match.get('brand') or '']


def inspect_target(conn, rows, normalize):
    t = schema(conn)
    p = t["product"]
    existing = list(conn.execute(select(p.c.id, p.c.name, p.c.brand, p.c.price, p.c.is_active)).mappings())
    pending, omitted = [], []
    for row in rows:
        if duplicate_status(row, existing, normalize):
            omitted.append(row["name"])
        else:
            row = dict(row, target_matches=possible_target_matches(row,existing,normalize))
            pending.append(row)
            existing.append(row)
    return t, pending, omitted


def imported_product(alias, name, normalize):
    """Lee el producto actual del destino, sin filtros de origen ni escrituras."""
    with connect(alias, 'target') as (conn, _):
        tables = schema(conn)
        p, c, image = tables['product'], tables['category'], tables['product_images']
        matches = [row['id'] for row in conn.execute(select(p.c.id,p.c.name)).mappings()
                   if normalize(row['name']) == normalize(name)]
        if len(matches) != 1:
            raise TransferError('El producto ya no existe o hay varios con ese nombre en destino; revisalo en el catálogo.')
        row = dict(conn.execute(select(p,c.c.name.label('category')).join(c,p.c.category_id==c.c.id)
                               .where(p.c.id==matches[0])).mappings().one())
        pictures = [dict(pic) for pic in conn.execute(select(image).where(image.c.product_id==row['id'])
                                                     .order_by(image.c.id)).mappings()]
        main = re.fullmatch(r'/public/img/(\d+)',row.get('image_url') or '')
        if main:
            pictures.sort(key=lambda pic:pic['id'] != int(main[1]))
        row['pictures'] = [dict(pic, bytes=bytes(pic['bytes'])) for pic in pictures
                           if pic['bytes'] and len(pic['bytes']) <= 20 * 1024 * 1024]
        row['category_labels'] = [row['category']]
        return row


def existing_product_changes(conn, tables, rows, normalize, changes):
    """Incluye también existentes detectados al preparar una selección guardada."""
    changes = list(changes)
    p = tables['product']
    inventory = list(conn.execute(select(p.c.id,p.c.name,p.c.brand,p.c.price,p.c.is_active)).mappings())
    for row in rows:
        matches = [item for item in inventory if duplicate_status(row,[item],normalize)]
        if not matches:
            continue
        if len(matches) != 1:
            raise TransferError(f"{row['name']}: hay varias coincidencias existentes; revisalas antes de activar o cambiar precio.")
        item = matches[0]
        if any(change['id']==item['id'] for change in changes):
            continue
        price = row.get('price') if row.get('price_entered') else None
        if price is not None or not item['is_active']:
            changes.append({'id':item['id'],'name':item['name'],'old_price':str(item['price']),
                            'price':price,'activate':True})
    return changes


def backup_tools():
    # Evita que pg_wrapper elija versiones diferentes según conexión/cluster.
    root = Path('/usr/lib/postgresql')
    versions = sorted((p for p in root.iterdir() if p.name.isdigit()),
                      key=lambda p: int(p.name), reverse=True) if root.is_dir() else []
    for version in versions:
        pair = [version / 'bin' / name for name in ('pg_dump', 'pg_restore')]
        if all(path.is_file() and os.access(path, os.X_OK) for path in pair):
            return tuple(str(path) for path in pair)
    return 'pg_dump', 'pg_restore'


def backup(alias, folder):
    """pg_dump completo, sin contraseña en argumentos; valida el archivo con pg_restore."""
    _, url = authorized(alias, "target")
    dump_tool, restore_tool = backup_tools()
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
            subprocess.run([dump_tool, "--format=custom", "--no-owner", "--no-acl"],
                           stdout=output, stderr=subprocess.PIPE, env=env, check=True, timeout=600)
        subprocess.run([restore_tool, "--list", str(path)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise TransferError("Falló el backup o su validación. No se importó nada; revisá pg_dump y pg_restore.") from None
    return str(path)


def target_categories(conn, item):
    table = schema(conn)['category']
    actual = dict(conn.execute(select(table.c.id, table.c.name)).all())
    configured = item.get('category_ids', {})
    if not configured:
        raise TransferError('Configurá los IDs de categorías del destino antes de importar.')
    result = {}
    for gender, cid in configured.items():
        name = actual.get(int(cid))
        if not name or category_features(name, category_key)[1] != {gender}:
            raise TransferError(f'La categoría {cid} no coincide con {gender} en el destino.')
        result[name] = int(cid)
    return result


def suggest_gender(row, names):
    brief = category_features(re.sub('<[^>]+>', ' ', row.get('short_description', '')), category_key)[1]
    if len(brief) != 1:
        return '', 'No se pudo tomar un género claro de la descripción breve: elegí la categoría.'
    matches = [name for name in names if category_features(name, category_key)[1] == brief]
    if len(matches) != 1:
        return '', 'El género de la descripción breve no tiene una categoría única en destino: elegí la categoría.'
    return matches[0], 'Categoría tomada de la descripción breve. Revisala antes de continuar.'


def validate_import(rows):
    from decimal import Decimal
    for row in rows:
        if not re.sub('<[^>]+>', '', (row.get('description') or '')).strip() or not row.get('pictures'):
            raise TransferError(f"{row['name']}: se requiere descripción y foto.")
        price = Decimal(str(row.get('price') or 0))
        if not price.is_finite() or price < 0 or price > Decimal('999999999999.99'):
            raise TransferError('Precio inválido.')
        if not 1 <= int(row.get('volume_ml', 100)) <= 10000:
            raise TransferError('Mililitros inválidos.')
        for pic in row['pictures']:
            blob = bytes(pic.get('bytes') or b'')
            if not blob or len(blob) > 20 * 1024 * 1024 or pic['mime_type'] not in {'image/png','image/jpeg','image/webp','image/gif','image/avif'}:
                raise TransferError('Foto vacía o inválida.')


def protect_import_engine(engine, price_statements=None):
    """Defensa adicional: INSERT sin upsert y actualizaciones de precio/activación autorizadas."""
    @event.listens_for(engine, 'before_cursor_execute')
    def insert_only(conn, cursor, statement, parameters, context, executemany):
        compiled = context.compiled
        query = compiled.statement if compiled is not None else None
        if getattr(query, 'is_insert', False):
            table = query.table
            if (table.name in {'product', 'product_images'} and table.schema == 'public'
                    and getattr(query, '_post_values_clause', None) is None):
                return
        if price_statements is not None and any(query is approved for approved in price_statements):
            return
        sql = ' '.join(statement.strip().upper().split())
        allowed = {"SET LOCAL LOCK_TIMEOUT = '10S'", "SET LOCAL STATEMENT_TIMEOUT = '30S'",
                   'LOCK TABLE PUBLIC.PRODUCT IN SHARE ROW EXCLUSIVE MODE'}
        if sql.startswith('SELECT ') or sql.startswith('SHOW ') or sql in allowed:
            return
        raise TransferError('ABORTADO: operación fuera de productos nuevos, fotos o cambios de precio/activación expresamente autorizados.')


def execute(alias, rows, normalize, folder, price_updates=None):
    item, url = authorized(alias, 'target')
    if not item.get('import_products'):
        raise TransferError('ABORTADO: destino no habilitado para importar productos.')
    price_updates = price_updates or []
    if not rows and not price_updates:
        raise TransferError('No hay productos ni precios para enviar.')
    from pricing import retail_price
    for change in price_updates:
        if change.get('price') is not None and retail_price(change['price']) != change['price']:
            raise TransferError('Precio de actualización inválido.')
    validate_import(rows)
    backup_path = backup(alias, folder)
    engine = create_engine(url, connect_args={'connect_timeout': 10})
    approved_prices = []
    protect_import_engine(engine, approved_prices)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
            conn.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            # Serializa importaciones y altas concurrentes mientras se revisan duplicados.
            conn.exec_driver_sql('LOCK TABLE public.product IN SHARE ROW EXCLUSIVE MODE')
            tables, pending, omitted = inspect_target(conn, rows, normalize)
            categories = target_categories(conn, item)
            p, image = tables['product'], tables['product_images']
            for change in price_updates:
                # Solo precio explícito y activación; el resto permanece intacto.
                values = {}
                if change.get('price') is not None:
                    values['price'] = float(change['price'])
                if change.get('activate'):
                    values['is_active'] = True
                if not values:
                    continue
                statement = p.update().where(p.c.id==change['id'], p.c.name==change['name'],
                    p.c.price==float(change['old_price'])).values(**values)
                approved_prices.append(statement)
                if conn.execute(statement).rowcount != 1:
                    raise TransferError(f"Cambió o desapareció {change['name']} en destino. Volvé a preparar los precios.")
            for row in pending:
                reviewed = row.get('reviewed_matches',[])
                if any(match_signature(match) not in reviewed for match in row.get('target_matches',[])):
                    raise TransferError(f"Se encontró un posible duplicado de {row['name']}. Volvé a preparar el envío y revisá las coincidencias.")
                if row['category'] not in categories:
                    raise TransferError('Categoría no autorizada para este destino.')
                # Genera referencias nuevas sin tocar productos existentes ni secuencias manualmente.
                iid = conn.exec_driver_sql("SELECT nextval(pg_get_serial_sequence('public.product_images','id'))").scalar_one()
                pid = conn.execute(p.insert().values(
                    name=row['name'], brand=row.get('brand') or '', description=row['description'],
                    short_description=row.get('short_description') or '', price=float(row.get('price') or 0),
                    stock=12, category_id=categories[row['category']], is_active=True, created_at=datetime.now(),
                    image_url=f'/public/img/{iid}', volume_ml=int(row.get('volume_ml',100)),
                    volume_options=[], flavors=[], flavor_enabled=False, flavor_catalog=[],
                    flavor_stock_mode=False, price_wholesale=0, puffs=None, nicotine_mg=None
                ).returning(p.c.id)).scalar_one()
                for index, pic in enumerate(row['pictures']):
                    values = dict(product_id=pid, mime_type=pic['mime_type'], bytes=bytes(pic['bytes']),
                                  width=pic.get('width'), height=pic.get('height'),
                                  digest=hashlib.sha256(pic['bytes']).hexdigest(), created_at=datetime.now())
                    if index == 0:
                        values['id'] = iid
                    conn.execute(image.insert().values(**values))
        return [row["name"] for row in pending], omitted, backup_path
    finally:
        engine.dispose()
