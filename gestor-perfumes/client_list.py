"""Resumen por renglón del pedido y por perfume único seleccionado."""


def make_client_summary(lines, assignments, manual, unique_selected, existing=()):
    existing={i for i in existing if 0<=i<len(lines)}
    covered={i for i in assignments if 0<=i<len(lines)}-existing
    manual={i for i in manual if 0<=i<len(lines)}-covered-existing
    outside=[{'index':i,'raw':raw,'name':name,'status':'manual' if i in manual else 'pending'}
             for i,(raw,name) in enumerate(lines) if i not in covered and i not in existing]
    return {'total_requested':len(lines),'existing_count':len(existing),
            'existing':[{'index':i,'raw':lines[i][0]} for i in sorted(existing)],'matched_lines':len(covered),
            'unique_selected':unique_selected,'manual_count':len(manual),
            'pending_count':len(outside)-len(manual),'outside_count':len(outside),
            'repeated_lines':len(covered)-len({assignments[i] for i in covered}),
            'outside':outside}


def mark_manual_exclusions(summary, excluded):
    """Actualiza la cobertura por renglón sin alterar el resumen guardado original."""
    if not summary:
        return summary
    from copy import deepcopy
    result = deepcopy(summary)
    outside = {item['index']:item for item in result.get('outside',[])}
    removed_lines = set()
    for row in excluded:
        lines = row.get('client_lines') or []
        for position,index in enumerate(row.get('client_line_indices') or []):
            removed_lines.add(index)
            outside[index] = {'index':index,'raw':lines[position] if position<len(lines) else row['name'],
                              'name':row['name'],'status':'manual'}
    result['outside'] = [outside[index] for index in sorted(outside)]
    result['outside_count'] = len(outside)
    result['manual_count'] = sum(item['status']=='manual' for item in outside.values())
    result['pending_count'] = result['outside_count'] - result['manual_count']
    result['matched_lines'] = max(0,result['matched_lines']-len(removed_lines))
    result['unique_selected'] = max(0,result['unique_selected']-len(excluded))
    result['repeated_lines'] = max(0,result['matched_lines']-result['unique_selected'])
    return result
