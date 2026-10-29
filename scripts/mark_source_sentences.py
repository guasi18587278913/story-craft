#!/usr/bin/env python3
"""逐句标原句、改写、新写，并保留最接近的原文供人核对；相似度不证明事实正确，也不作为稿件通过门槛。"""
import sys
sys.dont_write_bytecode = True
import argparse
from difflib import SequenceMatcher
import json
from pathlib import Path
from _text import normalize, sentences

def compare(source,draft,near=0.85,rewrite=0.60):
    originals=sentences(source)
    candidates=[]
    for i in range(len(originals)):
        # A draft sentence can combine up to three adjacent source sentences.
        for width in range(1,4):
            group=originals[i:i+width]
            if len(group)!=width:break
            raw=''.join(x['text'] for x in group)
            candidates.append((normalize(raw),raw,group[0]['line'],width))
    rows=[]
    for s in sentences(draft):
        n=normalize(s['text'])
        if not n:continue
        best=(0.0,'',None,0)
        fragment=None
        for original,raw,line,width in candidates:
            if not original:continue
            score=SequenceMatcher(None,n,original,autojunk=False).ratio()
            if score>best[0]:best=(score,raw,line,width)
            if len(n)>=8 and n in original and (fragment is None or len(original)<fragment[0]):
                fragment=(len(original),raw,line,width)
            if score==1 and width==1:break
        score,raw,line,width=best
        label='原句' if score==1 and width==1 else ('改写' if score>=rewrite or fragment else '新写')
        # Exact fragments remain rewrites; only small edits count toward near-original.
        close=score>=near
        rows.append({'sentence':s['text'],'line':s['line'],'label':label,'similarity':round(score,4),
                     'near_original':close,'source_text':raw,'source_line':line,
                     'source_sentence_count':width,'verbatim_fragment':fragment is not None})
    count=len(rows)
    exact=sum(x['label']=='原句' for x in rows)
    close=sum(x['near_original'] for x in rows)
    return {'sentence_count':count,'original_count':exact,'original_ratio':exact/count if count else 0,
            'original_or_near_count':close,'original_or_near_ratio':close/count if count else 0,
            'near_threshold':near,'rewrite_threshold':rewrite,'sentences':rows,
            'limit':'按句号、问号、感叹号、分号和换行分句；忽略空白及标点。原句须与一整句一致，小改默认相似度至少0.85。低相似度只叫文字新写，不等于新增事实；数字、人称、否定词仍需人工核。'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,type=Path)
    p.add_argument('--draft',required=True,type=Path)
    p.add_argument('--near-threshold',type=float,default=0.85,help='字面分类阈值，默认0.85；仅供参考，不是过稿门槛')
    p.add_argument('--rewrite-threshold',type=float,default=0.60)
    p.add_argument('--json',action='store_true')
    a=p.parse_args()
    if not 0<=a.rewrite_threshold<=a.near_threshold<=1:p.error('阈值应满足 0 <= 改写 <= 小改 <= 1')
    try:r=compare(a.source.read_text(encoding='utf-8'),a.draft.read_text(encoding='utf-8'),a.near_threshold,a.rewrite_threshold)
    except OSError as e:p.error(str(e))
    if a.json:print(json.dumps(r,ensure_ascii=False,indent=2))
    else:
        print('原句占比：{:.1%}；原句或小改占比：{:.1%}。'.format(r['original_ratio'],r['original_or_near_ratio']))
        for x in r['sentences']:
            print('\n'+x['label']+'：'+x['sentence'])
            print('最接近的原文：'+x['source_text'])
        print('\n'+r['limit'])
    return 0
if __name__=='__main__':raise SystemExit(main())
