"""Resumen por renglón del pedido y por perfume único seleccionado."""


def make_client_summary(lines, assignments, manual, unique_selected):
    covered={i for i in assignments if 0<=i<len(lines)}
    manual={i for i in manual if 0<=i<len(lines)}-covered
    outside=[{'index':i,'raw':raw,'name':name,'status':'manual' if i in manual else 'pending'}
             for i,(raw,name) in enumerate(lines) if i not in covered]
    return {'total_requested':len(lines),'matched_lines':len(covered),
            'unique_selected':unique_selected,'manual_count':len(manual),
            'pending_count':len(outside)-len(manual),'outside_count':len(outside),
            'repeated_lines':len(covered)-len({assignments[i] for i in covered}),
            'outside':outside}
