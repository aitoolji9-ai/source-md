"""Join explicitly continued, compatible unmerged PDF tables with row provenance."""
import re
import unicodedata

from .raster_layout import table_markdown
from .model import md_url


def combine_continued_tables(result,assets):
    marker='### [원본 표의 셀 구조 — 선 검출]'
    end='### [원본 텍스트층 — 대조용]'
    norm=lambda s:''.join(unicodedata.normalize('NFC',s or '').split())
    units=result.units
    i=0
    while i<len(units):
        first=units[i];original=first.metadata.get('tables',[])
        if len(original)!=1 or any(v is None for row in original[0]['cells'] for v in row):i+=1;continue
        values=[list(row) for row in original[0]['cells']]
        origins=[{'source_locator':first.source_locator,'row':r+1,'table_data':original[0]['asset']} for r in range(len(values))]
        joined=[];j=i+1
        while j<len(units):
            current=units[j];tables=current.metadata.get('tables',[])
            if len(tables)!=1 or not re.search(r'\bcontinu(?:ed|ation)\b|\bcont\.|계속|이어짐|연속',current.metadata.get('producer_order_text',current.text),re.I):break
            cells=tables[0]['cells']
            if not cells or len(cells[0])!=len(values[0]) or [norm(x) for x in cells[0]]!=[norm(x) for x in values[0]] or any(v is None for row in cells for v in row):break
            values.extend([list(row) for row in cells[1:]])
            origins.extend({'source_locator':current.source_locator,'row':r+1,'table_data':tables[0]['asset']} for r in range(1,len(cells)))
            joined.append(current);j+=1
        if not joined:i+=1;continue
        data=assets.json('continued-table.json',{'values':values,'row_origins':origins,'basis':'explicit continuation label and identical header','verified':False},first.source_locator)
        table={'row_count':len(values),'column_count':len(values[0]),'cells':[{'row':r,'column':c,'rowspan':1,'colspan':1,'text':value} for r,row in enumerate(values) for c,value in enumerate(row)]}
        before,after=first.markdown.split(marker,1)
        _,tail=after.split(end,1)
        first.markdown=before+marker+'\n\n**[연속 표 — 페이지별 출처 보존]**\n\n'+table_markdown(table)+f'\n\n[행별 원본 위치](<{md_url(data)}>)\n\n'+end+tail
        first.metadata['continued_table_data']=data
        for unit in joined:
            before,after=unit.markdown.split(marker,1);_,tail=after.split(end,1)
            unit.markdown=before+f'이 페이지의 표는 앞쪽 연속 표로 연결했습니다. [행별 원본 위치](<{md_url(data)}>)\n\n'+end+tail
            unit.metadata['continued_table_data']=data
        result.warn('pdf_table_continuation','계속 표식과 같은 머리글을 확인한 인접 표를 연결했습니다. 페이지별 셀·행 출처와 원본은 보존했습니다.',first.source_locator)
        i=j
