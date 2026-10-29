#!/usr/bin/env python3
"""生成口播顺读时间线，或核对划走句确实改写；不生成音频、不判断故事好坏。"""
import sys
sys.dont_write_bytecode=True
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
from audit_draft import persistent
from _text import visible_count, sentences

RATE=288

def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def layout(text):
    paragraphs=[p.strip() for p in re.split(r'\n\s*\n',text.strip()) if p.strip()]
    if not paragraphs:raise ValueError('正文不能为空')
    elapsed=0.0;rows=[]
    for number,paragraph in enumerate(paragraphs,1):
        parts=sentences(paragraph)
        if not parts:raise ValueError('正文段落没有可读句子')
        count=visible_count(paragraph);duration=count/RATE*60
        rows.append({'paragraph':number,'characters':count,'start_seconds':round(elapsed,2),
                     'end_seconds':round(elapsed+duration,2),'text':paragraph,
                     'first_sentence':parts[0]['text'],'last_sentence':parts[-1]['text']})
        elapsed+=duration
    first=rows[0]['first_sentence']
    return {'draft_sha256':sha(text),'rate_chars_per_minute':RATE,'characters':sum(x['characters'] for x in rows),
            'estimated_seconds':round(elapsed,2),'first_sentence':first,
            'first_sentence_estimated_seconds':round(visible_count(first)/RATE*60,2),'paragraphs':rows,
            'notice':'这是文字顺读的估时地图，不证明实际发声或播放，也不自动评判钩子质量。'}

def task_dir(path):
    if not path.is_absolute():raise ValueError('任务目录须为已存在的绝对路径')
    task=persistent(path)
    if not task.is_dir():raise ValueError('任务目录不存在')
    folder=task/'readthrough'
    if folder.is_symlink():raise ValueError('顺读目录不能是软链')
    return task,folder

def read_at(path,task):
    path=path if path.is_absolute() else task/path
    return path.read_text(encoding='utf-8')

def write_new(path,text):
    if path.is_symlink():raise ValueError('记录文件不能是软链')
    if path.exists():
        if path.read_text(encoding='utf-8')!=text:raise ValueError('已有记录与本次内容不同；保留旧记录后另建版本')
        return
    with path.open('x',encoding='utf-8') as f:f.write(text)

def prepare(text,folder):
    data=layout(text);key=data['draft_sha256'];plan=folder/(key+'.json');frozen=folder/(key+'.md')
    if folder.exists() and not folder.is_dir():raise ValueError('顺读位置不是目录')
    if plan.is_symlink() or frozen.is_symlink():raise ValueError('记录文件不能是软链')
    if plan.exists():
        previous=json.loads(plan.read_text(encoding='utf-8'))
        if {k:v for k,v in previous.items() if k!='prepared_at'}!=data or frozen.read_text(encoding='utf-8')!=text:
            raise ValueError('既有顺读记录损坏')
        return previous
    if frozen.exists() and frozen.read_text(encoding='utf-8')!=text:raise ValueError('既有正文快照损坏')
    data['prepared_at']=now();folder.mkdir(exist_ok=True)
    write_new(frozen,text);write_new(plan,json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    return data

def nonempty(value,label):
    if not isinstance(value,str) or not value.strip():raise ValueError(label+'须填写非空文字')
    return value

def verify(before,after,review,folder):
    plan=layout(before);key=plan['draft_sha256'];saved=folder/(key+'.json');frozen=folder/(key+'.md')
    if saved.is_symlink() or frozen.is_symlink():raise ValueError('记录文件不能是软链')
    record=json.loads(saved.read_text(encoding='utf-8'))
    if {k:v for k,v in record.items() if k!='prepared_at'}!=plan or frozen.read_text(encoding='utf-8')!=before:
        raise ValueError('顺读快照与初稿不一致')
    if not isinstance(review,dict) or review.get('draft_sha256')!=key:raise ValueError('顺读记录没有绑定这份初稿')
    if review.get('reading_mode')!='text':raise ValueError('本脚本只核文字顺读，reading_mode填text，不声称音频实读')
    observations=review.get('observations')
    if not isinstance(observations,list) or len(observations)!=len(plan['paragraphs']):raise ValueError('需要每一段的顺读观察')
    seen=set()
    for item in observations:
        if not isinstance(item,dict):raise ValueError('每段观察应为对象')
        n=item.get('paragraph')
        if type(n) is not int or not 1<=n<=len(plan['paragraphs']) or n in seen:raise ValueError('段落编号缺失、重复或越界')
        seen.add(n);quote=nonempty(item.get('quote'),'段内摘录');nonempty(item.get('note'),'顺读感受')
        if quote not in plan['paragraphs'][n-1]['text']:raise ValueError('顺读摘录不在对应段落')
    risk=nonempty(review.get('risk_sentence'),'划走句');reason=nonempty(review.get('reason'),'原因')
    replacement=nonempty(review.get('replacement_sentence'),'修改句')
    if risk not in [x['text'] for x in sentences(before)]:raise ValueError('划走句须逐字引用初稿中的整句，含标点')
    if risk in after:raise ValueError('划走句仍原样存在于最终稿')
    if replacement not in [x['text'] for x in sentences(after)]:raise ValueError('修改句不在最终稿中')
    if sha(before)==sha(after):raise ValueError('初稿与最终稿相同')
    return {'changed_sentence_verified':True,'before_sha256':key,'after_sha256':sha(after),
            'review_sha256':sha(json.dumps(review,ensure_ascii=False,sort_keys=True)),
            'risk_sentence':risk,'reason':reason,'replacement_sentence':replacement,
            'prepared_at':record['prepared_at'],'verified_at':now(),
            'notice':'只核逐段记录与实际改句，不证明真实观众会划走或已经实际朗读。'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task-dir',type=Path,required=True)
    p.add_argument('--draft',type=Path,required=True,help='只含正文的初稿；相对路径从任务目录解析')
    p.add_argument('--final',type=Path,help='最终正文，与--review同时使用以核对改句')
    p.add_argument('--review',type=Path,help='人工顺读JSON：draft_sha256、reading_mode=text、observations数组（paragraph/quote/note）、risk_sentence、reason、replacement_sentence')
    a=p.parse_args()
    if bool(a.final)!=bool(a.review):p.error('--final与--review必须一起提供')
    try:
        task,folder=task_dir(a.task_dir);before=read_at(a.draft,task)
        if a.final:
            result=verify(before,read_at(a.final,task),json.loads(read_at(a.review,task)),folder)
            target=folder/('verified-'+result['before_sha256'][:12]+'-'+result['after_sha256'][:12]+'.json')
            if target.is_symlink():raise ValueError('记录文件不能是软链')
            # Verification is a fresh observation; preserve the original preparation snapshot.
            target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        else:result=prepare(before,folder)
    except(OSError,ValueError,KeyError,TypeError) as e:
        print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
