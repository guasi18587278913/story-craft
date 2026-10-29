#!/usr/bin/env python3
"""读取故事卡、写法卡或按需方法，保存显示记录并核对使用声称；不联网。"""
import sys
sys.dont_write_bytecode=True
import argparse
import datetime
import fcntl
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/'references/story-methods/provenance.json'
CLAIM=re.compile(r'\[(method|card|template|case):([^\]\s]+)\]')

def digest(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()

def manifest():return json.loads(MANIFEST.read_text(encoding='utf-8'))

def get_section(item):
    path=(ROOT/item['file']).resolve()
    path.relative_to(ROOT.resolve())
    text=path.read_text(encoding='utf-8')
    start='<!-- source-section:'+item['key']+':begin -->\n'
    end='\n<!-- source-section:'+item['key']+':end -->'
    if text.count(start)!=1 or text.count(end)!=1:raise ValueError('来源标记缺失或重复：'+item['key'])
    body=text.split(start,1)[1].split(end,1)[0]
    if digest(body)!=item['section_sha256']:raise ValueError('正文指纹不一致：'+item['key'])
    return body

def task_path(value):
    if value is None or not value.is_absolute():raise ValueError('--task-dir 需要已存在的绝对任务目录')
    from audit_draft import persistent
    task=persistent(value)
    if not task.is_dir():raise ValueError('任务目录不存在')
    if task==ROOT or ROOT in task.parents:raise ValueError('任务记录不能写进技能目录')
    folder=task/'method-reads'
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):raise ValueError('method-reads 必须是任务内的实际目录')
    return task

def record_readings(items,task):
    folder=task/'method-reads';folder.mkdir(exist_ok=True)
    log=folder/'reads.jsonl'
    if log.is_symlink():raise ValueError('读取日志不能是软链')
    with log.open('a+',encoding='utf-8') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        f.seek(0)
        for line in f:
            if line.strip():json.loads(line)
        events=[]
        for item in items:
            encoded=json.dumps(item,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
            sha=digest(encoded);dest=folder/(sha+'.json')
            if dest.is_symlink():raise ValueError('读取证据不能是软链')
            if dest.exists():
                if dest.read_text(encoding='utf-8')!=encoded:raise ValueError('已有读取证据损坏')
            else:
                with dest.open('x',encoding='utf-8') as evidence:evidence.write(encoded)
            events.append({'read_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'request':item['request'],'mode':item['mode'],'body_sha256':digest(item['body']),
                'evidence':dest.name,'evidence_sha256':sha})
        f.seek(0,2)
        for event in events:f.write(json.dumps(event,ensure_ascii=False)+'\n')
        f.flush()

def live_items(path):
    # Imported here to keep get_section/manifest compatible with integrity checking.
    from check_integrity import pages,section_from_full
    items=[]
    for request,page in pages(json.loads(path.read_text(encoding='utf-8'))):
        slug=page['slug'];section=str(page.get('section') or '')
        if request:
            base,sep,asked=request.partition('#')
            if base!=slug or (section and section!=asked):raise ValueError('请求与返回页不一致')
            section=asked if sep else ''
        else:request=slug+('#'+section if section else '')
        body=page['compiled_truth']
        if section and not page.get('section'):body=section_from_full(body,section)
        if not body.strip():raise ValueError('知识库正文为空')
        if body.lstrip().startswith('没有找到「'):raise ValueError('知识库未返回所选小节，先核对页名或编号')
        items.append({'request':request,'mode':'get_page','body':body,'source_content_hash':page.get('content_hash')})
    if not items:raise ValueError('没有有效的 get_page 正文')
    return items

def card_items(card_ids):
    from check_templates import read_card
    items={}
    for value in card_ids:
        _,entry,body=read_card(value)
        request='card:'+entry['id']
        items[request]={'request':request,'mode':'card','body':body,'source_content_hash':None}
    return list(items.values())

def verify_claims(paths,task):
    from check_templates import canonical_id,is_card_id,load_catalog,read_card
    folder=task/'method-reads';events=[]
    for line in (folder/'reads.jsonl').read_text(encoding='utf-8').splitlines():
        if line.strip():events.append(json.loads(line))
    valid={};stale=[];current={};catalog=None
    for event in events:
        name=event['evidence']
        if Path(name).name!=name:raise ValueError('读取证据路径无效')
        p=folder/name
        if p.is_symlink():raise ValueError('读取证据不能是软链')
        raw=p.read_text(encoding='utf-8')
        if digest(raw)!=event['evidence_sha256']:raise ValueError('读取证据指纹不一致')
        item=json.loads(raw)
        if item['request']!=event['request'] or digest(item['body'])!=event['body_sha256']:raise ValueError('读取记录与原文不一致')
        request=event['request']
        if request.startswith(('card:','template:','case:')):
            # 旧模板库留下的 template:、case: 记录对不上现在的卡片库，一律算过期。
            if not request.startswith('card:'):stale.append(request);continue
            identifier=request.split(':',1)[1]
            if identifier not in current:
                if catalog is None:catalog=load_catalog()
                try:current[identifier]=read_card(identifier,data=catalog)[2]
                except(OSError,ValueError):current[identifier]=None
            if current[identifier] is None or digest(item['body'])!=digest(current[identifier]):
                stale.append(request);continue
        valid.setdefault(request,[]).append(event['read_at'])
    files=[];missing=[]
    for path in paths:
        path=path if path.is_absolute() else task/path
        found=CLAIM.findall(path.read_text(encoding='utf-8'))
        if any(kind=='case' for kind,_ in found):
            raise ValueError('[case:…] 已不再使用：案例都在卡里，用 --template 读整张卡，标记写 [card:编号]（'+path.name+'）')
        bad=list(dict.fromkeys('['+kind+':'+value+']' for kind,value in found if kind!='method' and not is_card_id(value)))
        if bad:raise ValueError('标记里的卡号认不出：'+'、'.join(bad)+'（'+path.name+'）；故事卡写文件名前的数字，写法卡写前四位；模板里的占位要换成真卡号，旧模板名（如 trial）已不再使用')
        claims=list(dict.fromkeys(value if kind=='method' else 'card:'+canonical_id(value) for kind,value in found))
        if not claims:raise ValueError('声称文件没有读卡或方法标记：'+path.name)
        absent=[x for x in claims if x not in valid];missing.extend(absent)
        files.append({'file':str(path),'claims':claims,'missing':absent,
                      'read_times':{x:valid[x] for x in claims if x in valid}})
    return {'verified':not missing,'files':files,'missing':sorted(set(missing)),
            'stale':sorted(set(stale)),
            'notice':'只核声称的卡或方法已显示并记录；读卡记录还须与当前卡片一致，不证明写得好。'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=['story','structure','opening','body','ending'])
    p.add_argument('--method',type=int,nargs='+',action='extend',default=[],help='知识库原编号，可多个')
    p.add_argument('--section',nargs='+',action='extend',default=[],help='通用禁忌、缺口、不照学、写完自查、照单全收；story用all')
    p.add_argument('--list',action='store_true',help='只列条目，不记为已读')
    p.add_argument('--requests-only',action='store_true',help='只打印 get_page 请求名，不记为已读')
    p.add_argument('--template',nargs='+',default=[],metavar='卡号',help='读整张卡：故事卡写数字（如 30），写法卡写前四位（如 LX07）；先核指纹，带 --task-dir 时记 card:卡号')
    p.add_argument('--case',nargs='*',default=None,help=argparse.SUPPRESS)
    p.add_argument('--list-templates',action='store_true',help='只显示路由目录，不记已读')
    p.add_argument('--task-dir',type=Path,help='已存在的绝对持久任务目录；拒绝系统临时目录；读正文时留原文、时间和指纹')
    p.add_argument('--record-get-page',type=Path,help='显示并记录刚取得的 get_page JSON；不直接访问知识库磁盘')
    p.add_argument('--verify-claims',type=Path,nargs='+',help='核工作表与交付说明中的 [card:卡号]（旧写法 [template:卡号] 照样认）和 [method:页名#节]')
    a=p.parse_args()
    if a.case is not None:p.error('案例都在卡里，用 --template 读整张卡')
    template_mode=bool(a.template or a.list_templates)
    special=bool(a.record_get_page)+bool(a.verify_claims)+template_mode
    if special:
        if special!=1 or a.stage or a.method or a.section or a.list or a.requests_only:p.error('记录实时页、核声称与快照读取分别使用')
        if (a.record_get_page or a.verify_claims) and not a.task_dir:p.error('记录实时页或核声称需要 --task-dir')
        if a.list_templates and a.template:p.error('目录与卡片正文分别读取')
    else:
        if not a.stage or not(a.method or a.section or a.list):p.error('选择 --stage 及 --method、--section 或 --list')
        if a.list and(a.method or a.section or a.requests_only):p.error('--list 不与其他读取方式混用')
    try:
        task=task_path(a.task_dir) if a.task_dir else None
        if a.verify_claims:
            result=verify_claims(a.verify_claims,task)
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['verified'] else 1
        if a.list_templates:
            from check_templates import read_index
            print(read_index());return 0
        if template_mode:items=card_items(a.template)
        elif a.record_get_page:items=live_items(a.record_get_page)
        else:
            entries=[x for x in manifest()['entries'] if x['stage']==a.stage]
            if a.list:
                print('\n'.join(x['section']+'：'+x['heading']+'；'+x['request'] for x in entries));return 0
            wanted=list(dict.fromkeys(a.section+[str(n) for n in a.method]));chosen=[]
            for key in wanted:
                hit=[x for x in entries if x['section']==key]
                if len(hit)!=1:raise ValueError('本包没有该条目：'+a.stage+'#'+key+'；用 --list 看已存范围。')
                chosen.append(hit[0])
            if a.requests_only:
                print('\n'.join(x['request'] for x in chosen));return 0
            items=[{'request':x['request'],'mode':'snapshot','body':get_section(x),
                    'source_content_hash':x['content_hash']} for x in chosen]
    except(OSError,ValueError,KeyError,TypeError) as e:print(str(e),file=sys.stderr);return 2
    try:
        output='以下是实际读取的正文。来源身份与范围见各条；当前素材事实不由案例补充。\n\n'
        output+='\n\n'.join('['+x['request']+']\n'+x['body'] for x in items)
        sys.stdout.write(output+'\n')
        sys.stdout.flush()
        if task:record_readings(items,task)
    except BrokenPipeError:
        try:sys.stdout.close()
        except OSError:pass
        print('正文输出失败，未写入成功读取记录',file=sys.stderr);return 2
    except(OSError,ValueError,KeyError,TypeError) as e:
        print(str(e),file=sys.stderr);return 2
    return 0
if __name__=='__main__':raise SystemExit(main())
