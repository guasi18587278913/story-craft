#!/usr/bin/env python3
"""一次核字数、原话数字和逐句差异；结果留在任务目录，未变的输入复用。"""
import sys
sys.dont_write_bytecode=True
import argparse,hashlib,json
from pathlib import Path
from _text import normalize
from check_quotes_numbers import check
from mark_source_sentences import compare
from length_and_lint import lint
ROOT=Path(__file__).resolve().parents[1]

def sha(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()
def persistent(path):
    p=path.expanduser().resolve()
    bad=['/tmp','/private/tmp','/var/tmp','/private/var/tmp','/var/folders','/private/var/folders']
    if any(str(p)==x or str(p).startswith(x+'/') for x in bad):raise ValueError('输出请放任务目录，不用系统临时目录。')
    try:p.relative_to(ROOT)
    except ValueError:return p
    raise ValueError('核对产物不写进技能目录。')

def audit(source,draft,out,known=(),rewrites=()):
    out=persistent(out);out.mkdir(parents=True,exist_ok=True)
    code={p.name:sha(p.read_text(encoding='utf-8')) for p in [Path(__file__),*(ROOT/'scripts'/n for n in ['_text.py','check_quotes_numbers.py','mark_source_sentences.py','length_and_lint.py'])]}
    fingerprint=sha(json.dumps({'source':sha(source),'draft':sha(draft),'known':list(known),'rewrites':rewrites,'code':code},ensure_ascii=False,sort_keys=True))
    state=out/'audit.json'
    if state.exists():
        old=json.loads(state.read_text(encoding='utf-8'))
        if old.get('fingerprint')==fingerprint and all((out/f).is_file() and sha((out/f).read_text(encoding='utf-8'))==h for f,h in old.get('artifacts',{}).items()):
            return {**old['summary'],'reused':True,'directory':str(out)}
    numbers=check(source,draft);similarity=compare(source,draft);length=lint(draft,known=known)
    corrections={}
    for item in rewrites:
        sentence=item['sentence'];original=item['source_excerpt'];reason=item['reason']
        if not reason.strip() or not normalize(original) or normalize(original) not in normalize(source):raise ValueError('改写标记需要可在原文找到的原句和修改理由。')
        if sentence not in [r['sentence'] for r in similarity['sentences']]:raise ValueError('改写标记未对应当前稿的一整句：'+sentence)
        if sentence in corrections:raise ValueError('改写标记重复。')
        corrections[sentence]=item
    lines=['逐句对照','自动相似度只比较文字，不是事实等级；人工解释改写另记，不改自动分数。']
    for row in similarity['sentences']:
        correction=corrections.get(row['sentence']);label='改写' if correction else row['label']
        lines+=['',label+'：'+row['sentence'],'对应原文：'+(correction['source_excerpt'] if correction else row['source_text'])]
        if correction:lines+=['改写理由：'+correction['reason']+'；自动标签原为“'+row['label']+'”。']
    content={'quotes-numbers.json':json.dumps(numbers,ensure_ascii=False,indent=2),
             'source-sentences.json':json.dumps(similarity,ensure_ascii=False,indent=2),
             'length-lint.json':json.dumps(length,ensure_ascii=False,indent=2),
             'sentence-audit.md':'\n'.join(lines)+'\n'}
    for name,text in content.items():(out/name).write_text(text,encoding='utf-8')
    summary={'characters':length['characters'],'estimated_seconds':length['estimated_seconds'],
             'sentence_count':similarity['sentence_count'],'original_ratio':similarity['original_ratio'],
             'original_or_near_ratio':similarity['original_or_near_ratio'],'quote_number_not_found':numbers['not_found_count'],
             'unit_review_count':numbers['unit_review_count'],'precision_review_count':numbers['precision_review_count'],
             'unparsed_review_count':numbers['unparsed_review_count'],'lint_issue_count':length['issue_count'],'editor_rewrites':len(corrections)}
    state.write_text(json.dumps({'fingerprint':fingerprint,'source_sha256':sha(source),'draft_sha256':sha(draft),'summary':summary,'artifacts':{f:sha(t) for f,t in content.items()},'editor_rewrites':rewrites},ensure_ascii=False,indent=2),encoding='utf-8')
    return {**summary,'reused':False,'directory':str(out)}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--draft',type=Path,required=True)
    p.add_argument('--out-dir',type=Path,required=True,help='任务目录内持久位置')
    p.add_argument('--known-term',action='append',default=[])
    p.add_argument('--rewrites-json',type=Path,help='可选人工解释改写列表，每项含 sentence、source_excerpt、reason；不改变相似度分数')
    a=p.parse_args()
    try:
        rewrites=json.loads(a.rewrites_json.read_text(encoding='utf-8')) if a.rewrites_json else []
        if not isinstance(rewrites,list):raise ValueError('rewrites-json 须为列表')
        r=audit(a.source.read_text(encoding='utf-8'),a.draft.read_text(encoding='utf-8'),a.out_dir,a.known_term,rewrites)
    except (OSError,ValueError,KeyError,TypeError) as e:p.error(str(e))
    print(json.dumps(r,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
