"""Deterministic Office math and bounded display formatting; no recalculation."""
from __future__ import annotations

import re
import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def omml_text(node):
    tag=node.tag.rsplit('}',1)[-1]
    children=list(node)
    def named(name):
        found=next((c for c in children if c.tag.rsplit('}',1)[-1]==name),None)
        return omml_text(found) if found is not None else ''
    def prop(container,name,default=''):
        group=next((c for c in children if c.tag.rsplit('}',1)[-1]==container),None)
        target=next((c for c in group if c.tag.rsplit('}',1)[-1]==name),None) if group is not None else None
        return next((v for k,v in target.attrib.items() if k.rsplit('}',1)[-1]=='val'),default) if target is not None else default
    if tag.endswith('Pr'):return ''
    if tag=='t':return node.text or ''
    if tag=='f':
        top,bottom=named('num'),named('den')
        return top+'/'+bottom if top.isdecimal() and bottom.isdecimal() else r'\frac{'+top+'}{'+bottom+'}'
    if tag=='sSup':return named('e')+'^{'+named('sup')+'}'
    if tag=='sSub':return named('e')+'_{'+named('sub')+'}'
    if tag=='sSubSup':return named('e')+'_{'+named('sub')+'}^{'+named('sup')+'}'
    if tag=='rad':
        degree=named('deg')
        return r'\sqrt'+('['+degree+']' if degree else '')+'{'+named('e')+'}'
    if tag=='m':
        rows=[]
        for row in children:
            if row.tag.rsplit('}',1)[-1]=='mr':rows.append(' & '.join(omml_text(c) for c in row if c.tag.rsplit('}',1)[-1]=='e'))
        return r'\begin{matrix}'+r' \\ '.join(rows)+r'\end{matrix}'
    if tag=='nary':
        symbol=prop('naryPr','chr','∫')
        operator={'∑':r'\sum','∏':r'\prod','∫':r'\int','∬':r'\iint','∭':r'\iiint','∮':r'\oint','⋃':r'\bigcup','⋂':r'\bigcap'}.get(symbol,symbol)
        return operator+('_{'+named('sub')+'}' if named('sub') else '')+('^{'+named('sup')+'}' if named('sup') else '')+' '+named('e')
    if tag in ('limLow','limUpp'):
        return named('e')+('_{' if tag=='limLow' else '^{')+named('lim')+'}'
    if tag=='func':return named('fName')+' '+named('e')
    if tag=='d':
        start=prop('dPr','begChr','(');end=prop('dPr','endChr',')');separator=prop('dPr','sepChr','|')
        start={'{':r'\{','}':r'\}'}.get(start,start);end={'{':r'\{','}':r'\}'}.get(end,end)
        return r'\left'+(start or '.')+separator.join(omml_text(c) for c in children if c.tag.rsplit('}',1)[-1]=='e')+r'\right'+(end or '.')
    if tag=='acc':
        accent=prop('accPr','chr','̂')
        command={'̂':'hat','̄':'bar','⃗':'vec','̇':'dot','̈':'ddot','̃':'tilde'}.get(accent)
        return ('\\'+command+'{'+named('e')+'}') if command else accent+'{'+named('e')+'}'
    if tag=='bar':return ('\\underline' if prop('barPr','pos','top')=='bot' else '\\overline')+'{'+named('e')+'}'
    if tag=='groupChr':
        symbol=prop('groupChrPr','chr','⏟')
        command={'⏟':'underbrace','⏞':'overbrace'}.get(symbol)
        if command:return '\\'+command+'{'+named('e')+'}'
        position=prop('groupChrPr','pos','bot')
        return ('\\underset' if position=='bot' else '\\overset')+'{'+symbol+'}{'+named('e')+'}'
    if tag=='eqArr':return r'\begin{aligned}'+r' \\ '.join(omml_text(c) for c in children if c.tag.rsplit('}',1)[-1]=='e')+r'\end{aligned}'
    return ''.join(omml_text(c) for c in children)


def excel_display(value, number_format):
    """Return (display, supported). Preserve unsupported formats as raw values."""
    if value is None:return '',True
    if isinstance(value,bool):return ('TRUE' if value else 'FALSE'),True
    if isinstance(value,(datetime.datetime,datetime.date,datetime.time)):
        return str(value),False
    if not isinstance(value,(int,float,Decimal)):return str(value),True
    if not number_format or number_format=='General':return str(value),True
    sections=number_format.split(';')
    index=1 if value < 0 and len(sections)>1 else 2 if value==0 and len(sections)>2 else 0
    code=sections[index]
    if '[' in code or '\\' in code or '*' in code or '_' in code:return str(value),False
    # Quoted strings are literal affixes. Date, scientific and fraction formats
    # require their own formatter and are deliberately not guessed here.
    literals={}
    def protect(m):
        key=chr(0xE000+len(literals));literals[key]=m.group(1);return key
    protected=re.sub(r'"([^"]*)"',protect,code)
    match=re.fullmatch(r'([^0#?]*)([#,]*0+)(?:\.(0+))?(%?)([^0#?]*)',protected)
    if not match:return str(value),False
    prefix,integer,decimals,percent,suffix=match.groups()
    if any(c.isalpha() for c in prefix+suffix if c not in literals):return str(value),False
    prefix=''.join(literals.get(c,c) for c in prefix)
    suffix=''.join(literals.get(c,c) for c in suffix)
    try:
        amount=Decimal(str(value))
        if index==1:amount=abs(amount)
        if percent:amount*=100
        places=len(decimals or '')
        amount=amount.quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_UP)
        rendered=format(amount,(',' if ',' in integer else '')+'.'+str(places)+'f')
        if integer.count('0')>1 and not ',' in integer:
            integer_part,_,fraction=rendered.partition('.')
            negative=integer_part.startswith('-');integer_part=integer_part.lstrip('-').zfill(integer.count('0'))
            rendered=('-' if negative else '')+integer_part+('.'+fraction if places else '')
        return prefix+rendered+percent+suffix,True
    except (InvalidOperation,ValueError):return str(value),False
