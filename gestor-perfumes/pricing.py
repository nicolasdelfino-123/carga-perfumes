"""Precios minoristas de la selección, independientes de la biblioteca."""
from decimal import Decimal,InvalidOperation
import re


def retail_price(value):
    raw=str(value or '').strip().replace('$','').replace('ARS','').replace('ars','')
    raw=re.sub(r'\s+','',raw)
    if not raw:
        return '0.00'
    if not re.fullmatch(r'\d[\d.,]*',raw):
        raise ValueError('Ingresá un precio válido, por ejemplo 45000 o 45.000,50.')
    if '.' in raw and ',' in raw:
        raw=raw.replace('.','').replace(',','.') if raw.rfind(',')>raw.rfind('.') else raw.replace(',','')
    elif ',' in raw:
        raw=raw.replace(',','.')
    elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+',raw):
        raw=raw.replace('.','')
    try:
        price=Decimal(raw)
        if price<0 or price>Decimal('999999999999.99') or price.as_tuple().exponent < -2:
            raise ValueError('El precio debe ser positivo o cero y tener hasta dos decimales.')
        return format(price.quantize(Decimal('.01')),'f')
    except InvalidOperation:
        raise ValueError('El precio no es válido.') from None


def money_ar(value):
    try:
        return '$ '+format(Decimal(str(value or 0)),',.2f').translate(str.maketrans('.,',',.'))
    except InvalidOperation:
        return '$ 0,00'


def selected_line_values(assignments, form):
    """Precios y tamaños del renglón elegido, también para coincidencias del modal."""
    result={}
    for line,perfume_id in sorted(assignments.items()):
        if form.get('presentation_mode') == '1':
            try:
                values = presentation_values(form, f'line_option_{line}')
            except ValueError as error:
                raise ValueError(f'Renglón {line+1}: {error}') from None
            if perfume_id in result and result[perfume_id] != values:
                raise ValueError(f'Renglón {line+1}: el mismo perfume tiene presentaciones diferentes en otro renglón.')
            result[perfume_id] = values
            continue
        raw=form.get(f'line_price_{line}','').strip()
        try:
            price=retail_price(raw) if raw else None
            volume=int(form.get(f'line_volume_{line}','100'))
            if not 1<=volume<=10000:
                raise ValueError('Los mililitros deben estar entre 1 y 10000.')
        except ValueError as error:
            raise ValueError(f'Renglón {line+1}: {error}') from None
        old=result.get(perfume_id)
        if old and (old['volume_ml']!=volume or
                    (old['price'] is not None and price is not None and old['price']!=price)):
            raise ValueError(f'Los renglones {old["first_line"]+1} y {line+1} eligen el mismo perfume '
                             'con precios o tamaños diferentes. Igualá esos valores o corregí la coincidencia antes de continuar.')
        if not old:
            result[perfume_id]={'price':price,'volume_ml':volume,'first_line':line}
        elif price is not None:
            old['price']=price
    if form.get('presentation_mode') == '1':
        return result
    return {pid:{'price':values['price'] or '0.00','volume_ml':values['volume_ml']}
            for pid,values in result.items()}


PRESENTATION_SIZES = (5, 10, 100)


def presentation_values(form, prefix):
    """Solo los tamaños con precio explícito, sin inventar opciones gratuitas."""
    options = []
    for ml in PRESENTATION_SIZES:
        raw = form.get(f'{prefix}_{ml}', '').strip()
        if not raw:
            continue
        price = retail_price(raw)
        if Decimal(price) <= 0:
            raise ValueError(f'{ml} ml: ingresá un precio mayor a cero o dejá vacío para excluirlo.')
        options.append({'ml': ml, 'price': price, 'stock': 12})
    if not options:
        raise ValueError('Completá al menos un precio de 5, 10 o 100 ml para el perfume elegido, o quitá la elección.')
    base = next((option for option in options if option['ml'] == 100), options[0])
    return {'price': base['price'], 'volume_ml': base['ml'], 'volume_options': options}


def presentation_update(item, options):
    """Actualiza precios solicitados y conserva opciones y stock preexistentes."""
    from copy import deepcopy
    previous = item.get('volume_options') or []
    if not isinstance(previous, list) or any(not isinstance(o, dict) or 'ml' not in o for o in previous):
        raise ValueError('Las presentaciones existentes requieren revisión en el catálogo.')
    merged = deepcopy(previous)
    for option in options:
        matches = [old for old in merged if str(old['ml']) == str(option['ml'])]
        if len(matches) > 1:
            raise ValueError('Hay presentaciones repetidas en el catálogo; revisalas antes de actualizar.')
        if matches:
            matches[0]['price'] = float(option['price'])
        else:
            merged.append(dict(option, price=float(option['price'])))
    main_ml = item.get('volume_ml')
    main = next((o for o in options if o['ml'] == main_ml), None)
    return {'volume_options': merged, 'old_volume_options': deepcopy(item.get('volume_options')),
            'old_volume_ml': main_ml, 'price': main['price'] if main else None,
            'presentation_prices': options}
