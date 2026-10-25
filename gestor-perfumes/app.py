"""Biblioteca privada y transferencia revisable de perfumes de Catálogo Web."""
import difflib
import os
import re
import secrets
import time
import logging
import unicodedata
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for, Response, jsonify
from sqlalchemy import JSON, String, Text, LargeBinary, UniqueConstraint, create_engine, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.engine import make_url
import transfers
from presentation import rich_description
from library import classify, backup_library, comparison_groups
from pricing import retail_price,money_ar,selected_line_values
from client_list import make_client_summary


def normalize(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.findall(r"\w+", value.casefold()))


def parse_lines(text):
    result = []
    for raw in text.splitlines():
        name = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw).strip()
        # Solo precios delimitados: conservar números de nombres como 9 PM o 212.
        name = re.sub(r"\s*(?:\$|ARS\s+)\s*[\d.,]+\s*$", "", name, flags=re.I)
        name = re.sub(r"\s+[-–—]\s+[\d.,]+\s*$", "", name).strip()
        if name:
            result.append((raw, name))
    return result


class Base(DeclarativeBase):
    pass


class Perfume(Base):
    __tablename__ = "master_perfume"
    __table_args__ = (UniqueConstraint("name_key", "brand_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(2000))
    brand: Mapped[str] = mapped_column(String(200), default="")
    name_key: Mapped[str] = mapped_column(String(2000))
    brand_key: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50), default="Perfumes")
    description: Mapped[str] = mapped_column(Text, default="")
    short_description: Mapped[str] = mapped_column(Text, default="")
    origins: Mapped[list] = mapped_column(JSON, default=list)


class Picture(Base):
    __tablename__ = "master_picture"
    id: Mapped[int] = mapped_column(primary_key=True)
    perfume_id: Mapped[int]
    mime_type: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    width: Mapped[int | None]
    height: Mapped[int | None]
    digest: Mapped[str] = mapped_column(String(64))


class CategoryLabel(Base):
    __tablename__ = 'master_category_label'
    id: Mapped[int] = mapped_column(primary_key=True)
    perfume_id: Mapped[int]
    name: Mapped[str] = mapped_column(String(50))


class ComparisonSkip(Base):
    __tablename__ = 'master_comparison_skip'
    __table_args__ = (UniqueConstraint('source','source_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(80))
    source_id: Mapped[int]
    name_key: Mapped[str] = mapped_column(String(2000))
    brand_key: Mapped[str] = mapped_column(String(200))


class ContentIssue(Base):
    __tablename__ = 'master_content_issue'
    id: Mapped[int] = mapped_column(primary_key=True)
    perfume_id: Mapped[int]
    message: Mapped[str] = mapped_column(Text)


class SavedSelection(Base):
    __tablename__ = 'master_saved_selection'
    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(80),unique=True)
    client: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text,default='')
    created_at: Mapped[str] = mapped_column(String(40))
    items: Mapped[list] = mapped_column(JSON)


def candidates(query, perfumes, limit=8):
    key = normalize(query)
    if not key:
        return []
    query_words=key.split()
    ranked = []
    for p in perfumes:
        name=normalize(p.name)
        full = normalize(f"{p.brand} {p.name}")
        exact = key in (p.name_key, full, normalize(f"{p.name} {p.brand}"))
        words=full.split()
        word_score=sum(max((difflib.SequenceMatcher(None,q,w).ratio() for w in words),default=0)
                       for q in query_words)/len(query_words)
        # Separar nombre y marca evita que un nombre largo o una marca repetida
        # escondan coincidencias como «Liquid Brun».
        compact_key=''.join(query_words)
        compact_name=''.join(name.split())
        score = 1.0 if exact else max(difflib.SequenceMatcher(None,key,name).ratio(),
            difflib.SequenceMatcher(None,key,full).ratio(),word_score*.94,
            .92 if compact_key in compact_name else 0)
        partial = bool(key) and (key in full or all(t in full.split() for t in key.split()))
        if exact or partial or score >= .55:
            ranked.append((p, "Exacta" if exact else "Revisar variante" if partial else "Posible", score))
    ranked=sorted(ranked,key=lambda row:(-row[2],len(normalize(row[0].name)),row[0].name))
    return ranked if limit is None else ranked[:limit]


def create_app(database_url=None):
    app = Flask(__name__, root_path=str(Path(__file__).resolve().parent))
    app.config.update(SECRET_KEY=secrets.token_hex(32), MAX_CONTENT_LENGTH=2 * 1024 * 1024,
                      SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_HTTPONLY=True)
    folder = Path(__file__).parent / "instance"
    folder.mkdir(exist_ok=True)
    url = database_url or os.getenv("MASTER_DATABASE_URL") or f"sqlite:///{folder / 'biblioteca.db'}"
    master_url = make_url(url)
    if master_url.get_backend_name() != 'sqlite':
        raise transfers.TransferError('ABORTADO: durante la recuperación la biblioteca debe ser SQLite local. No se permite crear tablas en una base PostgreSQL.')
    engine = create_engine(url)
    # Únicamente crea tablas de la biblioteca, nunca tablas de clientes.
    Base.metadata.create_all(engine)
    plans = {}
    app.jinja_env.filters['rich_description'] = rich_description
    app.jinja_env.filters['money_ar'] = money_ar

    def remember(kind, payload):
        for key, value in list(plans.items()):
            if time.monotonic() - value[0] > 1800:
                plans.pop(key, None)
        token = secrets.token_urlsafe(32)
        plans[token] = (time.monotonic(), session['csrf'], kind, payload)
        return token

    def recall(token, kind, consume=False):
        plan = plans.get(token)
        if not plan or plan[1] != session['csrf'] or plan[2] != kind or time.monotonic() - plan[0] > 1800:
            raise transfers.TransferError('La vista previa venció. Prepará otra selección.')
        if consume:
            plans.pop(token, None)
        return plan[3]

    def checked_ids():
        try:
            ids = {int(x) for x in request.form.getlist('perfume')}
        except ValueError:
            abort(400)
        if not ids or len(ids) > 500:
            raise transfers.TransferError('Elegí entre 1 y 500 perfumes.')
        return ids

    @app.errorhandler(transfers.TransferError)
    def transfer_error(error):
        return render_template('message.html', message=str(error)), 400

    @app.errorhandler(Exception)
    def unexpected(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error, HTTPException):
            return error
        # No imprimir DSN, parámetros SQL ni datos binarios en errores.
        logging.error('Operación fallida: %s', type(error).__name__)
        return render_template('message.html', message='La operación falló y sus cambios se revirtieron. Revisá la conexión y el esquema; no se muestran credenciales.'), 500

    @app.before_request
    def protect_local():
        if request.host.split(":")[0] not in {"127.0.0.1", "localhost"}:
            abort(403)
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(32)
        if request.method == "POST" and not secrets.compare_digest(
            session["csrf"], request.form.get("csrf", "")
        ):
            abort(400)

    @app.route("/", methods=["GET", "POST"])
    def index():
        query = request.args.get("q", "").strip()
        text = request.form.get("list", "")
        with Session(engine) as db:
            perfumes = list(db.scalars(select(Perfume).order_by(Perfume.name)))
            matches = [(raw, name, candidates(name, perfumes)) for raw, name in parse_lines(text)]
            visible = [p for p in perfumes if not query or normalize(query) in normalize(
                f"{p.name} {p.brand} {p.description} {p.short_description}"
            )]
            return render_template("index.html", perfumes=visible, matches=matches, text=text,
                                   query=query, total=len(perfumes),
                                   client_summary=make_client_summary(parse_lines(text),{},set(),0))

    @app.get('/api/master/search')
    def master_search():
        query=request.args.get('q','').strip()[:200]
        with Session(engine) as db:
            perfumes=list(db.scalars(select(Perfume).order_by(Perfume.name)))
            results=candidates(query,perfumes,limit=None) if query else [(p,'Biblioteca',0) for p in perfumes]
            visible=results[:50]
            ids=[p.id for p,_,_ in visible]
            photos=dict(db.execute(select(Picture.perfume_id,func.count(Picture.id)).where(
                Picture.perfume_id.in_(ids)).group_by(Picture.perfume_id)).all())
            return jsonify(total_master=len(perfumes),matching_count=len(results),items=[{
                'id':p.id,'name':p.name,'brand':p.brand,'category':p.category,'match':status,
                'detail_url':url_for('perfume_detail',perfume_id=p.id),
                'image_url':url_for('picture',perfume_id=p.id) if photos.get(p.id) else None,
                'photo_count':photos.get(p.id,0)} for p,status,_ in visible])

    @app.post("/add")
    def add():
        name = request.form.get("name", "").strip()
        brand = request.form.get("brand", "").strip()
        category = request.form.get("category", "Perfumes").strip() or "Perfumes"
        if not name or len(name) > 2000 or len(brand) > 200 or len(category) > 50:
            abort(400)
        with Session(engine) as db:
            existing = db.scalar(select(Perfume).where(
                Perfume.name_key == normalize(name), Perfume.brand_key == normalize(brand)))
            if existing:
                flash("Ese nombre y marca ya están en la biblioteca. No se agregó otro registro.")
            else:
                db.add(Perfume(name=name, brand=brand, category=category,
                    name_key=normalize(name), brand_key=normalize(brand),
                    description=request.form.get("description", "")[:40000]))
                db.commit()
                flash("Perfume agregado a la biblioteca local.")
        return redirect(url_for("index"))

    @app.post("/preview")
    def preview():
        try:
            ids = {int(value) for value in request.form.getlist("perfume")}
            ids.update(int(value) for key,value in request.form.items() if re.fullmatch(r'match_\d+',key) and value)
        except ValueError:
            abort(400)
        if not ids or len(ids)>500:
            raise transfers.TransferError('Elegí entre 1 y 500 perfumes para revisar.')
        client_list = request.form.get('client_list','')[:100000]
        parsed_list=parse_lines(client_list)
        assignments={}
        assignments_by_line={}
        indices_by_perfume={}
        for key,value in request.form.items():
            if re.fullmatch(r'match_\d+',key) and value:
                line=int(key.split('_')[1])
                if line<len(parsed_list):
                    assignments.setdefault(int(value),[]).append(parsed_list[line][0])
                    assignments_by_line[line]=int(value)
                    indices_by_perfume.setdefault(int(value),[]).append(line)
        manual={int(key.split('_')[1]) for key,value in request.form.items()
                if re.fullmatch(r'manual_\d+',key) and value=='1'}
        try:
            line_values=selected_line_values(assignments_by_line,request.form)
        except ValueError as error:
            raise transfers.TransferError(str(error)) from None
        client_summary=make_client_summary(parsed_list,assignments_by_line,manual,len(ids))
        with Session(engine) as db:
            selected = list(db.scalars(select(Perfume).where(Perfume.id.in_(ids))))
            if len(selected)!=len(ids):
                raise transfers.TransferError('Algún perfume elegido ya no existe en la maestra. Volvé a revisar la lista.')
            rows = []
            for p in selected:
                row = {k: getattr(p, k) for k in ('id', 'name', 'brand', 'category', 'description', 'short_description')}
                row['pictures'] = [{"master_picture_id":image.id,"bytes": image.data, "mime_type": image.mime_type,
                    "width": image.width, "height": image.height, "digest": image.digest}
                    for image in db.scalars(select(Picture).where(Picture.perfume_id == p.id).order_by(Picture.id))]
                row['category_labels'] = list(db.scalars(select(CategoryLabel.name).where(CategoryLabel.perfume_id == p.id))) or [p.category]
                row['image_warnings'] = list(db.scalars(select(ContentIssue.message).where(ContentIssue.perfume_id == p.id)))
                row.update(price='0.00',volume_ml=100,stock=11)
                row.update(line_values.get(p.id,{}))
                row['client_lines']=assignments.get(p.id,[])
                row['client_line_indices']=indices_by_perfume.get(p.id,[])
                rows.append(row)
            if not rows:
                raise transfers.TransferError('Seleccioná al menos un perfume.')
            rows[0]['client_list'] = client_list
            rows[0]['client_summary'] = client_summary
            token = remember('selection', rows)
            targets = {k: v for k, v in transfers.connections().items() if 'target' in v.get('roles', [])}
            return render_template("preview.html", perfumes=rows, token=token, targets=targets,original_list=client_list,
                                   client_summary=client_summary)

    def selection_rows(saved_items, db):
        ids = {pic['id'] for item in saved_items for pic in item['pictures']}
        images = {pic.id:pic for pic in db.scalars(select(Picture).where(Picture.id.in_(ids)))}
        rows = []
        for item in saved_items:
            row = dict(item)
            row['pictures'] = []
            for reference in item['pictures']:
                pic = images.get(reference['id'])
                if not pic or hashlib.sha256(pic.data).hexdigest() != reference['digest']:
                    raise transfers.TransferError('Una foto de la selección guardada falta o cambió. Se abortó la preparación; revisá esa selección.')
                row['pictures'].append({'master_picture_id':pic.id,'bytes':pic.data,'mime_type':reference['mime_type'],
                    'width':reference['width'],'height':reference['height'],'digest':reference['digest']})
            rows.append(row)
        return rows

    @app.post('/selections/save')
    def save_selection():
        token = request.form.get('token','')
        client = request.form.get('client','').strip()
        name = request.form.get('name','').strip() or f'Perfumes para {client}'
        notes = request.form.get('notes','').strip()
        if not client or len(client)>120 or len(name)>200 or len(notes)>2000:
            raise transfers.TransferError('Indicá un cliente (hasta 120 caracteres), un título (hasta 200) y notas de hasta 2000.')
        with Session(engine) as db:
            existing = db.scalar(select(SavedSelection).where(SavedSelection.request_key==token))
            if existing:
                return redirect(url_for('saved_selection',selection_id=existing.id))
        rows = recall(token,'selection')
        snapshots = []
        for row in rows:
            snapshot = {key:value for key,value in row.items() if key != 'pictures'}
            try:
                snapshot['price'] = retail_price(request.form.get(f"price_{row['id']}",row.get('price','0')))
                volume = int(request.form.get(f"volume_{row['id']}",row.get('volume_ml',100)))
                if not 1 <= volume <= 10000:
                    raise ValueError('Los mililitros deben ser un entero entre 1 y 10000.')
            except ValueError as error:
                raise transfers.TransferError(f"{row['name']}: {error}") from None
            snapshot['volume_ml'] = volume
            snapshot['stock'] = 11
            snapshot['pictures'] = [{'id':pic['master_picture_id'],'digest':hashlib.sha256(pic['bytes']).hexdigest(),
                'mime_type':pic['mime_type'],'width':pic['width'],'height':pic['height']} for pic in row['pictures']]
            snapshots.append(snapshot)
        backup_library(engine)
        with Session(engine) as db, db.begin():
            selection_rows(snapshots,db)
            saved = SavedSelection(request_key=token,client=client,name=name,notes=notes,items=snapshots,
                created_at=datetime.now(ZoneInfo('America/Argentina/Cordoba')).isoformat(timespec='seconds'))
            db.add(saved)
            db.flush()
            selection_id = saved.id
        try:
            backup_library(engine)
            flash('Selección guardada en tu computadora con copia de seguridad. Podés cerrar el programa y retomarla después. No fue enviada al VPS.')
        except Exception:
            flash('Selección guardada localmente. Falló el backup posterior; conservá el archivo de biblioteca.')
        return redirect(url_for('saved_selection',selection_id=selection_id))

    @app.get('/selections')
    def saved_selections():
        with Session(engine) as db:
            saved = list(db.scalars(select(SavedSelection).order_by(SavedSelection.id.desc())))
            return render_template('selections.html',selections=saved)

    @app.get('/selections/<int:selection_id>')
    def saved_selection(selection_id):
        with Session(engine) as db:
            saved = db.get(SavedSelection,selection_id)
            if not saved:
                abort(404)
            return render_template('saved_selection.html',saved=saved,
                                   client_summary=saved.items[0].get('client_summary') if saved.items else None)

    @app.get('/selections/<int:selection_id>/perfume/<int:index>')
    def saved_perfume_detail(selection_id,index):
        with Session(engine) as db:
            saved = db.get(SavedSelection,selection_id)
            if not saved or index>=len(saved.items):
                abort(404)
            row = selection_rows([saved.items[index]],db)[0]
            token = remember('detail',row)
            urls = [url_for('detail_picture',token=token,index=i) for i in range(len(row['pictures']))]
            return render_template('detail.html',perfume=row,pictures=urls,location=f'Selección guardada para {saved.client}',
                source=None,detail_return_url=url_for('saved_selection',selection_id=selection_id))

    @app.post('/selections/<int:selection_id>/prepare')
    def prepare_saved_selection(selection_id):
        with Session(engine) as db:
            saved = db.get(SavedSelection,selection_id)
            if not saved:
                abort(404)
            rows = selection_rows(saved.items,db)
            token = remember('selection',rows)
            targets = {k:v for k,v in transfers.connections().items() if 'target' in v.get('roles',[])}
            return render_template('preview.html',perfumes=rows,token=token,targets=targets,saved=saved,
                                   original_list=rows[0].get('client_list','') if rows else '',
                                   client_summary=rows[0].get('client_summary') if rows else None)

    @app.get('/picture/<int:perfume_id>', defaults={'index': 0})
    @app.get('/picture/<int:perfume_id>/<int:index>')
    def picture(perfume_id, index):
        with Session(engine) as db:
            image = db.scalar(select(Picture).where(Picture.perfume_id == perfume_id).order_by(Picture.id).offset(index).limit(1))
            if not image:
                abort(404)
            mime = image.mime_type if image.mime_type in {'image/png','image/jpeg','image/webp','image/gif','image/avif'} else 'application/octet-stream'
            return Response(image.data, content_type=mime, headers={'X-Content-Type-Options': 'nosniff'})

    @app.get('/perfume/<int:perfume_id>')
    def perfume_detail(perfume_id):
        with Session(engine) as db:
            p = db.get(Perfume, perfume_id)
            if not p:
                abort(404)
            row = {k:getattr(p,k) for k in ('name','brand','category','description','short_description')}
            row['category_labels'] = list(db.scalars(select(CategoryLabel.name).where(CategoryLabel.perfume_id == p.id))) or [p.category]
            row['image_warnings'] = list(db.scalars(select(ContentIssue.message).where(ContentIssue.perfume_id == p.id)))
            pictures = list(db.scalars(select(Picture.id).where(Picture.perfume_id == p.id).order_by(Picture.id)))
            urls = [url_for('picture',perfume_id=p.id,index=i) for i in range(len(pictures))]
            return render_template('detail.html', perfume=row, pictures=urls, location='Biblioteca local', source=None)

    @app.get('/source/<alias>/perfume/<int:product_id>')
    def source_detail(alias, product_id):
        rows = transfers.extract(alias, {product_id})
        row = rows[0]
        token = remember('detail', row)
        urls = [url_for('detail_picture',token=token,index=i) for i in range(len(row['pictures']))]
        return render_template('detail.html',perfume=row,pictures=urls,location=f'Origen: {alias} · consulta de solo lectura',source=alias)

    @app.get('/detail-picture/<token>/<int:index>')
    def detail_picture(token, index):
        row = recall(token, 'detail')
        if index >= len(row['pictures']):
            abort(404)
        image = row['pictures'][index]
        mime = image['mime_type'] if image['mime_type'] in {'image/png','image/jpeg','image/webp','image/gif','image/avif'} else 'application/octet-stream'
        return Response(image['bytes'],content_type=mime,headers={'X-Content-Type-Options':'nosniff'})

    @app.get('/recover/detail/<token>/<int:index>')
    def recover_detail(token, index):
        rows = recall(token, 'recover')
        if index >= len(rows):
            abort(404)
        row = rows[index]
        urls = [url_for('recovery_picture',token=token,row=index,picture=i) for i in range(len(row['pictures']))]
        return render_template('detail.html',perfume=row,pictures=urls,location='Selección pendiente de guardar',source=None)

    @app.get('/recovery-picture/<token>/<int:row>/<int:picture>')
    def recovery_picture(token, row, picture):
        rows = recall(token, 'recover')
        try:
            image = rows[row]['pictures'][picture]
        except IndexError:
            abort(404)
        mime = image['mime_type'] if image['mime_type'] in {'image/png','image/jpeg','image/webp','image/gif','image/avif'} else 'application/octet-stream'
        return Response(image['bytes'], content_type=mime, headers={'X-Content-Type-Options':'nosniff'})

    @app.get('/sources')
    def sources():
        alias = request.args.get('source', '')
        query = request.args.get('q', '')
        sources = {k:v for k,v in transfers.connections().items() if 'source' in v.get('roles', [])}
        products = transfers.source_products(alias, query) if alias else []
        return render_template('sources.html', sources=sources, source=alias, query=query, products=products)

    @app.post('/recover/analyze')
    def recover_analyze():
        alias = request.form.get('source','')
        rows = transfers.source_products(alias, request.form.get('q',''))
        return render_comparison(alias,rows)

    def render_comparison(alias,rows):
        with Session(engine) as db:
            existing = [{'id':p.id,'name':p.name,'brand':p.brand,'origins':p.origins} for p in db.scalars(select(Perfume))]
            skipped = {p.source_id:(p.name_key,p.brand_key) for p in db.scalars(select(ComparisonSkip).where(ComparisonSkip.source==alias))}
        analysis = classify(rows,existing,normalize)
        for item in analysis:
            row = item['row']
            if skipped.get(int(row['id'])) == (normalize(row['name']),normalize(row.get('brand'))):
                item['status'] = 'duplicate'
                item['manual_skip'] = True
        token = remember('analysis', {'alias':alias,'rows':analysis})
        counts = {key:sum(x['status'] == key for x in analysis) for key in ('new','review','duplicate')}
        groups = comparison_groups(analysis,existing,normalize)
        return render_template('analysis.html',token=token,analysis=analysis,counts=counts,source=alias,
                               groups=groups,master_count=len(existing),source_count=len(rows))

    @app.post('/recover/decision/reset')
    def reset_comparison_decision():
        plan = recall(request.form.get('token'),'analysis')
        try:
            source_id = int(request.form.get('source_id',''))
        except ValueError:
            abort(400)
        if not any(int(x['row']['id'])==source_id and x.get('manual_skip') for x in plan['rows']):
            raise transfers.TransferError('No hay una decisión guardada para ese perfume en esta comparación.')
        backup_library(engine)
        with Session(engine) as db, db.begin():
            decision = db.scalar(select(ComparisonSkip).where(ComparisonSkip.source==plan['alias'],ComparisonSkip.source_id==source_id))
            if decision:
                db.delete(decision)
        return render_comparison(plan['alias'],[x['row'] for x in plan['rows']])

    @app.post('/recover/analyzed')
    def recover_analyzed():
        plan = recall(request.form.get('token'),'analysis')
        try:
            chosen = {int(x) for x in request.form.getlist('perfume')}
        except ValueError:
            abort(400)
        skip = []
        for item in plan['rows']:
            if item['status'] != 'review':
                continue
            decision = request.form.get(f"decision_{item['row']['id']}",'pending')
            if decision not in {'pending','same','new'}:
                raise transfers.TransferError('Decisión de comparación inválida.')
            if decision == 'new':
                chosen.add(int(item['row']['id']))
            else:
                chosen.discard(int(item['row']['id']))
                if decision == 'same':
                    skip.append(item['row'])
        allowed = {int(x['row']['id']) for x in plan['rows'] if x['status'] != 'duplicate'}
        if not chosen <= allowed:
            raise transfers.TransferError('La selección contiene productos fuera del análisis o duplicados.')
        if not chosen and not skip:
            raise transfers.TransferError('No hay perfumes nuevos elegidos. Los que ya están en la maestra se conservan. Marcá nuevos o elegí «Es distinto: sumar» para alguna coincidencia.')
        if len(chosen)>500:
            raise transfers.TransferError('Elegí hasta 500 perfumes nuevos por lote.')
        if skip:
            backup_library(engine)
            with Session(engine) as db, db.begin():
                for row in skip:
                    decision = db.scalar(select(ComparisonSkip).where(ComparisonSkip.source==plan['alias'],ComparisonSkip.source_id==int(row['id'])))
                    if not decision:
                        decision = ComparisonSkip(source=plan['alias'],source_id=int(row['id']))
                        db.add(decision)
                    decision.name_key = normalize(row['name'])
                    decision.brand_key = normalize(row.get('brand'))
            flash(f'{len(skip)} decisiones de no sumar guardadas en la biblioteca local. Podés deshacerlas con «Revisar de nuevo».')
        if not chosen:
            return render_comparison(plan['alias'],[x['row'] for x in plan['rows']])
        rows = transfers.extract(plan['alias'], chosen)
        # El análisis solo trae nombres; aquí se recuperan fotos y textos de los elegidos.
        return render_recovery(rows,auto_select=True)

    def render_recovery(rows,auto_select=False):
        with Session(engine) as db:
            existing = [{'id':p.id,'name':p.name,'brand':p.brand} for p in db.scalars(select(Perfume))]
        analysis = classify(rows,existing,normalize)
        review = [(x['row'], x['matches'][0] if x['status']=='duplicate' else None,
                   [(p,'Revisar',0) for p in x['matches']] if x['status']=='review' else []) for x in analysis]
        token = remember('recover', rows)
        return render_template('recover.html',review=review,token=token,auto_select=auto_select)

    @app.post('/recover/preview')
    def recover_preview():
        rows = transfers.extract(request.form.get('source', ''), checked_ids())
        return render_recovery(rows)

    @app.post('/recover/confirm')
    def recover_confirm():
        token = request.form.get('token')
        rows = recall(token, 'recover')
        try:
            chosen = {int(x) for x in request.form.getlist('row')}
        except ValueError:
            abort(400)
        count = 0
        if not chosen or not chosen <= set(range(len(rows))):
            raise transfers.TransferError('Seleccioná al menos un perfume válido para guardar.')
        backup_library(engine)
        with Session(engine) as db, db.begin():
            known = [{'id':p.id,'name':p.name,'brand':p.brand} for p in db.scalars(select(Perfume))]
            selected = [row for i,row in enumerate(rows) if i in chosen]
            duplicates = {i for i,x in enumerate(classify(selected,known,normalize)) if x['status']=='duplicate'}
            selected_index = -1
            for i, row in enumerate(rows):
                if i not in chosen:
                    continue
                selected_index += 1
                if selected_index in duplicates:
                    continue
                p = db.scalar(select(Perfume).where(Perfume.name_key == normalize(row['name']),
                                                     Perfume.brand_key == normalize(row['brand'])))
                if p:
                    if row['origin'] not in p.origins:
                        p.origins = [*p.origins, row['origin']]
                    continue
                p = Perfume(**{k: row[k] for k in ('name','brand','category','description','short_description')},
                            name_key=normalize(row['name']), brand_key=normalize(row['brand']), origins=[row['origin']])
                db.add(p)
                db.flush()
                for pic in row['pictures']:
                    db.add(Picture(perfume_id=p.id, data=pic['bytes'], **{k:v for k,v in pic.items() if k != 'bytes'}))
                for label in row.get('category_labels', [row['category']]):
                    db.add(CategoryLabel(perfume_id=p.id, name=label))
                warnings = row.get('image_warnings',[])
                if not row['pictures']:
                    warnings = [*warnings,'Guardado sin fotos por selección explícita en la revisión.']
                for message in dict.fromkeys(warnings):
                    db.add(ContentIssue(perfume_id=p.id,message=message))
                count += 1
        recall(token, 'recover',consume=True)
        try:
            saved = backup_library(engine)
            message = ' Se verificó una copia de seguridad local.' if saved else ''
        except Exception:
            message = ' Los datos quedaron guardados, pero falló la copia posterior; conservá la biblioteca local.'
        flash(f'{count} perfumes guardados definitivamente en la biblioteca local; los duplicados exactos se omitieron.'+message)
        return redirect('/')

    @app.post('/target/preview')
    def target_preview():
        rows = recall(request.form.get('token'), 'selection')
        alias = request.form.get('target', '')
        with transfers.connect(alias, 'target') as (conn, item):
            tables, pending, omitted = transfers.inspect_target(conn, rows, normalize)
            names = list(conn.scalars(select(tables['category'].c.name).order_by(tables['category'].c.name)))
        review = [(row, *transfers.suggest_category(row['category'], names, normalize)) for row in pending]
        token = remember('mapping', {'alias':alias, 'rows':pending, 'names':names, 'omitted':omitted})
        return render_template('mapping.html', alias=alias, review=review, names=names, omitted=omitted, token=token)

    @app.post('/target/map')
    def target_map():
        plan = recall(request.form.get('token'), 'mapping')
        pending = []
        for i, source in enumerate(plan['rows']):
            choice = request.form.get(f'category_{i}', '')
            if choice == '__source__':
                name = source['category']
            elif choice in plan['names']:
                name = choice
            else:
                raise transfers.TransferError('Elegí una categoría para cada perfume.')
            extra = list(dict.fromkeys(request.form.getlist(f'extra_{i}')))
            if any(x not in plan['names'] for x in extra):
                raise transfers.TransferError('Categoría adicional no autorizada.')
            pending.append(dict(source, category=name, extra_categories=[x for x in extra if x != name]))
        alias = plan['alias']
        item, _ = transfers.authorized(alias, 'target')
        token = remember('target', {'alias':alias, 'rows':pending})
        return render_template('target.html', alias=alias, pending=pending, omitted=plan['omitted'],
                               token=token, writable=False)

    @app.post('/target/confirm')
    def target_confirm():
        raise transfers.TransferError('ABORTADO: el envío a bases de clientes está bloqueado. Solo se permite guardar perfumes en la biblioteca local.')

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5055, debug=False)
