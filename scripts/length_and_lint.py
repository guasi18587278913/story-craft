#!/usr/bin/env python3
"""估口播时长并提示难念处；只做文本检查，不代表已经试听。"""
import sys
sys.dont_write_bytecode=True
import argparse
import json
from pathlib import Path
import re
from _text import visible_count, sentences, context

def lint(text,rate=288,long_sentence=60,known=()):
    issues=[]
    def add(kind,m):
        issues.append({'kind':kind,'text':m.group(),'line':text.count('\n',0,m.start())+1,
                       'context':context(text,m.start(),m.end())})
    for s in sentences(text):
        if visible_count(s['text'])>long_sentence:
            issues.append({'kind':'长句','text':s['text'],'line':s['line'],'characters':visible_count(s['text'])})
    for kind,pattern in [('括号补充',r'（[^）\n]*）|\([^\)\n]*\)'),('破折号',r'—+|–+|--+'),
                         ('不是而是对仗',r'(?<!是)不是[^，,。！？；;\n]{1,100}?(?:[，,]\s*(?:而是|是)|而是)')]:
        for m in re.finditer(pattern,text):add(kind,m)
    seen=set()
    for m in re.finditer(r'(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_+.-]*',text):
        term=m.group().rstrip('.')
        if term.lower() in seen or term in known:continue
        seen.add(term.lower())
        before=text[max(0,m.start()-25):m.start()]
        after=text[m.end():m.end()+35]
        explained=bool(re.search(r'[\u3400-\u9fff]{2,}[（(]$',before) or
                       re.match(r'[（(][\u3400-\u9fff]{2,}[^)）]*[)）]',after) or
                       re.match(r'[，,:： ]*(?:是指|指的是|意思是|也就是|就是|是)[\u3400-\u9fff]{2,}',after))
        if not explained:add('英文或缩写待确认解释',m)
    count=visible_count(text)
    return {'characters':count,'characters_without_whitespace':len(''.join(text.split())),
            'rate_chars_per_minute':rate,'estimated_seconds':round(count/rate*60,1),
            'sentence_count':len(sentences(text)),'issues':issues,'issue_count':len(issues),
            'limit':'字数按汉字、字母、数字计，不含标点空白；每分钟288字仅估算。英文解释识别是提示，需按目标观众人工确认。'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('draft',type=Path,help='只含口播正文的 UTF-8 文件')
    p.add_argument('--rate',type=float,default=288)
    p.add_argument('--long-sentence',type=int,default=60,help='超过该字数提示，默认60')
    p.add_argument('--known-term',action='append',default=[],help='本次观众已知词，可重复传入')
    p.add_argument('--json',action='store_true')
    a=p.parse_args()
    if a.rate<=0 or a.long_sentence<1:p.error('速度和长句阈值必须为正数')
    try:r=lint(a.draft.read_text(encoding='utf-8'),a.rate,a.long_sentence,a.known_term)
    except OSError as e:p.error(str(e))
    if a.json:print(json.dumps(r,ensure_ascii=False,indent=2))
    else:
        print('正文 '+str(r['characters'])+' 字，约 '+str(r['estimated_seconds'])+' 秒。')
        for x in r['issues']:print(x['kind']+'：'+x['text'])
        print(r['limit'])
    return 0
if __name__=='__main__':raise SystemExit(main())
