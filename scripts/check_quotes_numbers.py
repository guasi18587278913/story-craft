#!/usr/bin/env python3
"""把引号和数字逐条回原文找。命中只表示文本能对上，仍需人工核说话人、单位与口径。"""
import sys
sys.dont_write_bytecode = True
import argparse
from decimal import Decimal
import json
from pathlib import Path
import re
import unicodedata
from _text import context

PAIRS = {'“':'”', '‘':'’', '「':'」', '『':'』', '"':'"', "'":"'"}
DIGITS = dict(zip('零〇一二三四五六七八九两', [0,0,1,2,3,4,5,6,7,8,9,2]))
CN = '零〇一二三四五六七八九十百千万亿两'
NUMBER = re.compile(r'(?<![\d.])[+−-]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?(?:[万亿])?|['+CN+r']+(?:点[零〇一二三四五六七八九两]+(?:[万亿])?(?!['+CN+r']))?')
UNITS = re.compile(r'\s*(美元|人民币|港元|欧元|元|块|%|％|‰|‱|人天|分钟|小时|秒|天|周|个月|月|年|个|人|页|次|倍|岁|单|户|台|版)')
NON_NUMERIC = ('一样','一直','一起','一般','一旦','一些','一边','一切','一共','一会','统一','进一步','另一方面','万一','千万别','千万要')

def flat(text):
    return ''.join(unicodedata.normalize('NFKC',text).split())

def quotes(text):
    stack, found = [], []
    for i,c in enumerate(text):
        if c in ['"',"'"] and i and text[i-1]=='\\':
            continue
        if c=="'" and 0<i<len(text)-1 and text[i-1].isalnum() and text[i+1].isalnum():
            continue
        if stack and c == PAIRS[stack[-1][0]]:
            opener,start=stack.pop()
            if i>start+1:
                found.append({'text':text[start+1:i], 'start':start, 'end':i+1})
        elif c in PAIRS:
            stack.append((c,i))
    return sorted(found,key=lambda x:x['start'])

def value(raw):
    s=unicodedata.normalize('NFKC',raw).replace(',','').replace('−','-')
    if '点' in s and re.match(r'[+-]?\d',s):
        left,right=s.split('点',1)
        s=left+'.'+''.join(str(DIGITS[c]) if c in DIGITS else c for c in right)
    if re.match(r'[+-]?\d',s):
        scale={'万':10000,'亿':100000000}.get(s[-1],1)
        return Decimal(s[:-1] if scale!=1 else s)*scale
    if '点' in s:
        left,right=s.split('点',1)
        scale={'万':10000,'亿':100000000}.get(right[-1],1)
        if scale!=1:right=right[:-1]
        if not right or any(c not in DIGITS and not c.isdigit() for c in right):
            raise ValueError('不是可解析的中文小数：'+raw)
        return (value(left)+Decimal('0.'+''.join(str(DIGITS[c]) if c in DIGITS else c for c in right)))*scale
    if not any(c in '十百千万亿' for c in s):
        return Decimal(''.join(str(DIGITS[c]) for c in s))
    total,part,num=0,0,0
    for c in s:
        if c in DIGITS: num=DIGITS[c]
        elif c in '十百千':
            part+=(num or 1)*{'十':10,'百':100,'千':1000}[c];num=0
        else:
            part+=num;num=0
            if c=='万': part=(part or 1)*10000
            else: total=(total+part or 1)*100000000;part=0
    return Decimal(total+part+num)

def approximate_form(raw):
    return bool(re.fullmatch(r'[一二两三四五六七八九]{2}(?:[十百千万亿]+)?',raw) or re.search(r'[百千万亿][一二两三四五六七八九]$',raw))

NUMERAL=r'(?:[+−-]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:[.点][0-9零〇一二三四五六七八九两]+)?|['+CN+r']+(?:点[0-9零〇一二三四五六七八九两]+)?)'
PERCENT=re.compile(r'(百分之|千分之|万分之)('+NUMERAL+r')')
MEASURE=r'(?:分钟|小时|个月|月|年|天|周|岁|个)'
HALF=re.compile(r'(?P<n>'+NUMERAL+r')(?:(?:个)?半(?P<after>'+MEASURE+r')|(?P<before>'+MEASURE+r')半)|半(?P<only>'+MEASURE+r')')
UNPARSED=re.compile(r'几[十百千万亿]|[十百千万亿]+几|数[十百千万亿]|上[百千万亿]|好几|几(?=个|人|年|月|天|本|次|元|户|台|岁)|若干|一半|大半|半数')

def unit_name(unit):return {'月':'个月','％':'%'}.get(unit,unit)

def unparsed_numbers(text):
    found=[{'text':m.group(),'start':m.start(),'end':m.end(),'line':text.count('\n',0,m.start())+1,
            'context':context(text,m.start(),m.end())} for m in UNPARSED.finditer(text)]
    for n in numbers(text):
        if n['approximate_form']:found.append({k:n[k] for k in ['text','start','end','line','context']})
    return found

def numbers(text):
    text=unicodedata.normalize('NFKC',text)
    found=[];occupied=[m.span() for m in UNPARSED.finditer(text)]
    def overlaps(start,end):return any(start<b and end>a for a,b in occupied)
    def add(raw,start,end,amount,unit,approx=False):
        unit=unit_name(unit)
        nearby=text[max(0,start-4):start]+text[end:end+4]
        qualified=approx or bool(re.search(r'大约|大概|差不多|约|左右|来|多|余|近',nearby))
        found.append({'text':raw,'value':None if approx else str(amount),'approximate_form':approx,
                      'qualified_approximate':qualified,'unit':unit,'start':start,'end':end,
                      'line':text.count('\n',0,start)+1,'context':context(text,start,end)})
        occupied.append((start,end))
    for m in PERCENT.finditer(text):
        if overlaps(*m.span()):continue
        raw=m.group(2);approx=approximate_form(raw)
        add(m.group(),*m.span(),None if approx else value(raw),{'百分之':'%','千分之':'‰','万分之':'‱'}[m.group(1)],approx)
    for m in HALF.finditer(text):
        if overlaps(*m.span()):continue
        raw=m.group('n');approx=bool(raw and approximate_form(raw))
        amount=None if approx else (value(raw) if raw else Decimal(0))+Decimal('0.5')
        add(m.group(),*m.span(),amount,m.group('after') or m.group('before') or m.group('only'),approx)
    for m in NUMBER.finditer(text):
        start,end=m.span()
        if overlaps(start,end):continue
        if any(text.startswith(word,start) or (start>0 and text.startswith(word,start-1)) for word in NON_NUMERIC):continue
        unit=UNITS.match(text,end)
        approx=approximate_form(m.group())
        add(m.group(),start,end,None if approx else value(m.group()),unit.group(1) if unit else '',approx)
    return sorted(found,key=lambda n:n['start'])

def check(source,draft):
    source_flat=flat(source)
    qs=[]
    for q in quotes(draft):
        q.update(line=draft.count('\n',0,q['start'])+1,
                 found=flat(q['text']) in source_flat,
                 context=context(draft,q['start'],q['end']))
        qs.append(q)
    original=numbers(source)
    nums=[]
    for n in numbers(draft):
        hits=[x for x in original if (n['approximate_form'] and x['text']==n['text']) or (not n['approximate_form'] and not x['approximate_form'] and Decimal(x['value'])==Decimal(n['value']))]
        n['found']=bool(hits)
        n['precision_needs_review']=bool(hits and not n['qualified_approximate'] and all(x['qualified_approximate'] for x in hits))
        n['source_matches']=[{'text':x['text'],'unit':x['unit'],'line':x['line'],'context':x['context']} for x in hits]
        n['unit_needs_review']=bool(hits and n['unit'] and not any(x['unit']==n['unit'] for x in hits))
        nums.append(n)
    missing=[{'kind':'quote',**q} for q in qs if not q['found']]+[{'kind':'number',**n} for n in nums if not n['found']]
    unresolved=[{'side':side,**item} for side,text in [('source',source),('draft',draft)] for item in unparsed_numbers(text)]
    return {'unparsed':unresolved,'unparsed_review_count':len(unresolved),'quotes':qs,'numbers':nums,'not_found':missing,'not_found_count':len(missing),
            'unit_review_count':sum(n['unit_needs_review'] for n in nums),
            'precision_review_count':sum(n['precision_needs_review'] for n in nums),
            'limit':'引号按文本比对，数字允许三万与30000等写法；同一个数出现过不证明这句话成立。汉字数词的词义、说话人、单位、报价与到账仍需人工核。'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,type=Path,help='原文 UTF-8 文件')
    p.add_argument('--draft',required=True,type=Path,help='只含待核正文的 UTF-8 文件')
    p.add_argument('--json',action='store_true',help='输出逐条机器结果')
    p.add_argument('--strict',action='store_true',help='有未命中、单位、约数疑点或未解析写法时退出 1；默认只提示')
    a=p.parse_args()
    try:r=check(a.source.read_text(encoding='utf-8'),a.draft.read_text(encoding='utf-8'))
    except (OSError,ValueError) as e:p.error(str(e))
    if a.json: print(json.dumps(r,ensure_ascii=False,indent=2))
    else:
        print('引号 '+str(len(r['quotes']))+' 处，数字 '+str(len(r['numbers']))+' 处，未在原文找到 '+str(r['not_found_count'])+' 处。')
        for x in r['not_found']:print('待核：'+x['text']+'；稿中位置：'+x['context'])
        for x in r['numbers']:
            if x['unit_needs_review']:print('单位待核：'+x['context'])
            if x['precision_needs_review']:print('约数是否变精确待核：'+x['context'])
        for x in r['unparsed']:print('写法未解析，人工核：'+x['text']+'；'+x['side']+'：'+x['context'])
        print(r['limit'])
    return int(a.strict and (r['not_found_count']>0 or r['unit_review_count']>0 or r['precision_review_count']>0 or r['unparsed_review_count']>0))
if __name__=='__main__': raise SystemExit(main())
