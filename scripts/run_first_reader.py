#!/usr/bin/env python3
"""向 DeepSeek 发一次独立首读请求，保留去密钥的请求、返回和用量；不自动重试。"""
import sys
sys.dont_write_bytecode = True
import argparse
from contextlib import contextmanager
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = 'https://api.deepseek.com/chat/completions'
MODEL = 'deepseek-flash'
MAX_CALLS = 20
DEFAULT_LIMIT = 6
DEFAULT_AUDIENCE = '没接触过这个行业的普通观众，没看过原材料'
DEFAULT_KEY_FILE = Path.home()/'.config/story-craft/deepseek.env'
MAX_TOKENS = 8192

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def sha(value):
    if isinstance(value,str): value=value.encode('utf-8')
    return hashlib.sha256(value).hexdigest()

class Failure(Exception):
    def __init__(self,code,message,halt=False):
        self.code,self.halt,self.reason=code,halt,message
        self.message='没做独立审查：'+message
        super().__init__(self.message)

def scrub(text,key):
    if not key:return text
    # Catch literal and JSON/hex-escaped echoes without ever persisting the key.
    pattern=''.join('(?:'+re.escape(c)+'|'+re.escape('\\u%04x' % ord(c))+'|'+re.escape('\\x%02x' % ord(c))+')' for c in key)
    return re.sub(pattern,'[REDACTED]',text,flags=re.I)

def clean_value(value,key):
    if isinstance(value,str):return scrub(value,key)
    if isinstance(value,list):return [clean_value(x,key) for x in value]
    if isinstance(value,dict):return {scrub(str(k),key):clean_value(v,key) for k,v in value.items()}
    return value

def clean_response(raw,key):
    text=raw.decode('utf-8',errors='replace')
    try:
        value=json.loads(text)
    except ValueError:
        if any(line.startswith('data:') for line in text.splitlines()) and not keepalive_only(raw):
            # Never persist raw deltas: a credential can be split across chunks.
            try:
                value=decode_response(raw)
            except (Failure,UnicodeError,ValueError):
                return json.dumps({'transport':'sse','incomplete':True,'received_bytes':len(raw),'note':'不完整分片未落盘，避免可重组的密钥回显。'},ensure_ascii=False),True
            cleaned=clean_value(value,key)
            return json.dumps(cleaned,ensure_ascii=False),cleaned!=value
        cleaned=scrub(text,key)
        return cleaned,cleaned!=text
    cleaned=clean_value(value,key)
    if cleaned!=value:return json.dumps(cleaned,ensure_ascii=False),True
    return text,False

def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def read_key(key_file=None):
    filename=key_file or os.environ.get('STORY_CRAFT_DEEPSEEK_KEY_FILE')
    source='configured_file' if filename else 'default_config' if DEFAULT_KEY_FILE.exists() else 'environment'
    if not filename and source=='default_config':filename=DEFAULT_KEY_FILE
    value=''
    if filename:
        try:
            with Path(filename).expanduser().open(encoding='utf-8') as f:
                for line in f:
                    m=re.match(r'^\s*(?:export\s+)?DEEPSEEK_API_KEY\s*=\s*(.*)$',line)
                    if m:
                        bits=shlex.split(m.group(1),comments=True,posix=True)
                        value=bits[0] if len(bits)==1 else ''
                        break
        except (OSError,ValueError,UnicodeError):
            raise Failure('missing_key','无法安全读取指定配置中的 DeepSeek 密钥。')
    else:
        value=os.environ.get('DEEPSEEK_API_KEY','').strip()
    if not value:
        raise Failure('missing_key','读不到已有 DeepSeek 密钥。')
    if len(value)>4096 or any(ord(c)<33 or ord(c)>126 for c in value):
        raise Failure('invalid_key','已有密钥格式异常。')
    return value,source

def prompt_text(draft,audience,medium):
    text=(ROOT/'agents/first-reader.md').read_text(encoding='utf-8')
    a,b='<!-- reader-prompt:begin -->','<!-- reader-prompt:end -->'
    if text.count(a)!=1 or text.count(b)!=1:
        raise Failure('prompt_error','读者提示词边界缺失。')
    system=text.split(a,1)[1].split(b,1)[0].strip()
    user=json.dumps({'读者画像':audience,'呈现方式':medium,'稿子':draft},ensure_ascii=False)
    return [{'role':'system','content':system},{'role':'user','content':user}]

def build_payload(draft,audience,medium,key):
    if not draft.strip(): raise Failure('empty_draft','稿件为空。')
    messages=prompt_text(draft,audience,medium)
    outbound='\n'.join(m['content'] for m in messages)
    if scrub(outbound,key)!=outbound or re.search(r'\bsk-[A-Za-z0-9_-]{16,}\b',outbound):
        raise Failure('sensitive_input','稿件或画像含疑似密钥，已拒绝外发。')
    # Exclude only bounded public URLs, then inspect actual local-path forms.
    visible=re.sub(r'https?://[^\s，。；：、<>"\\]+','',outbound)
    private_roots=r'(?:file://|/(?:Users|Volumes|home|root|tmp|private|var|etc|mnt|workspace|opt|srv)/|(?<![A-Za-z0-9])[A-Za-z]:[\\/]|~[/\\]|(?<![A-Za-z0-9_.])\.\.?/)'
    if re.search(private_roots,visible):
        raise Failure('path_in_input','稿件或画像含本机路径，请只提供正文。')
    return {'model':MODEL,'messages':messages,'thinking':{'type':'disabled'},'max_tokens':MAX_TOKENS,'stream':False}

def budget_path(argument=None,task_dir=None):
    configured=os.environ.get('STORY_CRAFT_READER_BUDGET')
    if configured and argument and Path(configured).expanduser().resolve()!=Path(argument).expanduser().resolve():
        raise Failure('budget_mismatch','参数与环境变量指定了不同账本，请统一配置。')
    value=argument or configured
    if value:return Path(value).expanduser().resolve()
    if task_dir:return task_dir/'first-reader'/'budget.json'
    raise Failure('budget_missing','需要任务目录以确定默认账本。')

def read_budget(path,create=False,limit=None):
    try:
        if path.exists():data=json.loads(path.read_text(encoding='utf-8'))
        elif create:data={'authorized':True,'limit':limit or DEFAULT_LIMIT,'attempts':[]}
        else:raise Failure('budget_missing','指定账本不存在，请准备已授权的账本。')
    except (OSError,ValueError):raise Failure('budget_invalid','无法读取有效调用账本。')
    if (not isinstance(data,dict) or type(data.get('limit')) is not int or
        not 1<=data['limit']<=MAX_CALLS or data.get('authorized') is not True or
        not isinstance(data.get('attempts'),list) or
        any(not isinstance(x,dict) or not x.get('id') for x in data['attempts'])):
        raise Failure('budget_invalid','账本须已授权，上限为 1–20，调用记录须有效。')
    if path.exists() and limit is not None and limit>data['limit']:
        raise Failure('budget_limit_increase','已有账本不能通过命令行调高，请先与用户确认额度。')
    return data

def available(data,count):
    if data.get('stop_reason'):raise Failure('budget_stopped','该账本已停用：'+str(data['stop_reason']))
    if len(data['attempts'])+count>data['limit']:
        raise Failure('budget_exhausted','剩余额度不足以送出全部稿件，一份也未发送；请先询问用户。')

def lock_parent(path):
    # Default ledger locks its existing task directory, including first creation.
    return path.parent.parent if path.name=='budget.json' and path.parent.name=='first-reader' else path.parent

@contextmanager
def directory_locks(paths):
    handles=[]
    try:
        for path in sorted(set(paths),key=str):
            fd=os.open(path,os.O_RDONLY);handles.append(fd);fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:
        for fd in reversed(handles):fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

@contextmanager
def ledger(path):
    with directory_locks([lock_parent(path)]):
        data=read_budget(path)
        yield data
        write_json(path,data)

def attempt(out,request_hash):
    return {'id':str(uuid.uuid4()),'reserved_at':now(),'status':'reserved',
            'output_dir':str(out),'request_sha256':request_hash}

def reserve(path,out,request_hash):
    with ledger(path) as data:
        available(data,1);item=attempt(out,request_hash);data['attempts'].append(item)
    return item['id']

def settle(path,attempt_id,status,usage=None,reason=None,halt=False):
    with ledger(path) as data:
        item=next(x for x in data['attempts'] if x['id']==attempt_id)
        item.update(status=status,finished_at=now(),usage=usage,reason=reason)
        if halt:data['stop_reason']=reason or status

def cancel_reserved(path,ids):
    with ledger(path) as data:
        data['attempts']=[x for x in data['attempts'] if not(x['id'] in ids and x['status']=='reserved')]

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None

@contextmanager
def deadline(seconds):
    # macOS/Linux main-thread process: a wall deadline also stops endless keep-alives.
    old_handler=signal.getsignal(signal.SIGALRM)
    old_timer=signal.getitimer(signal.ITIMER_REAL)
    def expired(signum,frame):
        raise TimeoutError('total request deadline')
    signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,seconds)
    try: yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,*old_timer)
        signal.signal(signal.SIGALRM,old_handler)

def post_once(payload,key,timeout,trace):
    raw=json.dumps(payload,ensure_ascii=False).encode('utf-8')
    request=urllib.request.Request(ENDPOINT,data=raw,method='POST',
        headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
    opener=urllib.request.build_opener(NoRedirect())
    try:
        with deadline(timeout):
            try:
                response=opener.open(request,timeout=timeout)
            except urllib.error.HTTPError as e:
                response=e
            with response:
                trace['http_status']=response.code
                trace['request_id']=response.headers.get('x-request-id') or response.headers.get('x-ds-request-id')
                while True:
                    block=response.read1(8192)
                    if not block:break
                    trace['raw'].extend(block)
                    if len(trace['raw'])>1024*1024:
                        raise Failure('response_too_large','接口返回异常大，已停止读取。')
    except (TimeoutError,socket.timeout):
        trace['timed_out']=True
    except (urllib.error.URLError,OSError):
        trace['transport_error']=True

def keepalive_only(raw):
    text=raw.decode('utf-8',errors='replace')
    if not raw:return False
    lines=[x.strip() for x in text.splitlines() if x.strip()]
    return not lines or all(x.startswith(':') or x in ['data: [DONE]','event: ping'] for x in lines)

def decode_response(raw):
    try:
        text=raw.decode('utf-8-sig').strip()
    except UnicodeError:
        raise Failure('invalid_response','接口返回无法解码。')
    try:
        value=json.loads(text)
    except ValueError:
        value=None
    else:
        if not isinstance(value,dict):
            raise Failure('invalid_response','接口JSON不是预期对象。')
        return value
    chunks=[];last={};usage=None;done=False;finish=None
    for line in text.splitlines():
        if not line.startswith('data:'):continue
        data=line[5:].strip()
        if data=='[DONE]':done=True;continue
        try:event=json.loads(data)
        except ValueError:
            raise Failure('invalid_response','接口分片不是有效JSON。')
        if not isinstance(event,dict):
            raise Failure('invalid_response','接口分片不是预期对象。')
        if 'error' in event:return event
        last.update({k:v for k,v in event.items() if k not in ['choices','usage']})
        if event.get('usage') is not None:usage=event['usage']
        choices=event.get('choices',[])
        if not isinstance(choices,list):raise Failure('invalid_response','接口分片choices格式异常。')
        for choice in choices:
            if not isinstance(choice,dict):raise Failure('invalid_response','接口分片choice格式异常。')
            delta=choice.get('delta',{})
            if not isinstance(delta,dict):raise Failure('invalid_response','接口分片delta格式异常。')
            content=delta.get('content')
            if isinstance(content,str):chunks.append(content)
            if choice.get('finish_reason') is not None:finish=choice['finish_reason']
    if chunks and (done or finish):
        return {**last,'choices':[{'message':{'content':''.join(chunks)},'finish_reason':finish}],'usage':usage}
    raise Failure('invalid_response','接口没有返回可读取的完整正文。')

def run_one(item,key,source,budget,timeout):
    out=item['out'];draft=item['draft'];payload=item['payload'];attempt_id=item['id']
    request_text=json.dumps(payload,ensure_ascii=False)
    receipt={'provider':'DeepSeek','endpoint':ENDPOINT,'requested_model':MODEL,'status':'requesting',
             'started_at':now(),'independent_return':False,'attempts':1,'budget_attempt_id':attempt_id,
             'request_sha256':sha(request_text),'reviewed_draft_sha256':sha(draft),'key_source':source}
    target=out/'receipt.json'
    trace={'raw':bytearray(),'http_status':None};usage=None;started=time.monotonic()
    error=None;feedback=None
    try:out.mkdir()
    except OSError:raise Failure('output_unavailable','新序号目录无法独占创建，已有产物保持原样。')
    try:
        write_json(target,receipt)
        (out/'draft-reviewed.md').write_text(draft,encoding='utf-8')
        (out/'request.json').write_text(request_text,encoding='utf-8')
        with ledger(budget) as data:
            if data.get('stop_reason'):raise Failure('budget_stopped','该账本已停用，未发送此稿。')
            next(x for x in data['attempts'] if x['id']==attempt_id)['status']='requesting'
        post_once(payload,key,timeout,trace)
        response_text,redacted=clean_response(bytes(trace['raw']),key)
        receipt['response_redacted']=redacted
        (out/'response.txt').write_text(response_text,encoding='utf-8')
        status=trace['http_status']
        if status==402:raise Failure('payment_required','HTTP 402，账户欠费或余额不足。',True)
        if status in [401,403]:raise Failure('authentication_error','接口拒绝当前认证。',True)
        if trace.get('timed_out'):
            if keepalive_only(bytes(trace['raw'])):
                raise Failure('keep_alive_failure','DeepSeek 排队或故障，可看 status.deepseek.com，稍后再试。')
            raise Failure('request_timeout','请求超时，没有完整返回。')
        if trace.get('transport_error'):raise Failure('transport_error','网络或TLS连接失败。')
        if status!=200:raise Failure('http_error','接口返回 HTTP '+str(status)+'。')
        if keepalive_only(bytes(trace['raw'])):raise Failure('keep_alive_failure','DeepSeek 排队或故障，可看 status.deepseek.com，稍后再试。')
        response=clean_value(decode_response(response_text.encode('utf-8')),key)
        write_json(out/'response.json',response)
        usage=response.get('usage')
        if response.get('error'):raise Failure('provider_error','接口返回错误对象，没有有效审查正文。')
        choices=response.get('choices') or []
        if not isinstance(choices,list) or not choices:raise Failure('empty_content','接口没有有效 choices 正文。')
        choice=choices[0]
        if not isinstance(choice,dict) or not isinstance(choice.get('message'),dict):raise Failure('invalid_response','接口message格式异常。')
        content=choice['message'].get('content')
        if not isinstance(content,str) or not content.strip():raise Failure('empty_content','接口正文为空（不把思考内容当反馈）。')
        if choice.get('finish_reason') is None:
            raise Failure('finish_reason_missing','接口未返回结束原因。')
        if choice.get('finish_reason')!='stop':
            raise Failure('incomplete_content','接口正文未完整结束。')
        feedback=content.strip()+'\n'
        (out/'feedback.md').write_text(feedback,encoding='utf-8')
        receipt.update(status='completed',independent_return=True,response_id=response.get('id'),returned_model=response.get('model'),
                       feedback_sha256=sha(feedback),response_sha256=sha(response_text))
    except Failure as e:error=e
    except (OSError,ValueError,KeyError,TypeError):
        error=Failure('local_error','本地读取、解析或保存失败。')
    except KeyboardInterrupt:
        error=Failure('interrupted','调用被中断。')
    except Exception:
        error=Failure('unexpected_error','客户端发生异常。')
    finally:
        if trace['raw'] and not (out/'response.txt').exists():
            # Cleanup must not invoke an external-data parser a second time.
            saved={'received_bytes':len(trace['raw']),'note':'异常返回未完整保存，避免未经解析的敏感回显。'}
            receipt['response_redacted']=True
            write_json(out/'response.txt',saved)
        if error:
            receipt.update(status='failed',independent_return=False,error_code=error.code,error=error.message)
        receipt.update(http_status=trace['http_status'],request_id=scrub(trace.get('request_id') or '',key),usage=usage,
                       duration_seconds=round(time.monotonic()-started,3),finished_at=now())
        write_json(out/'usage.json',{'usage':usage,'reported':isinstance(usage,dict),'note':'只记接口返回的token数；没有返回时不假报0，也不推定免费。'})
        write_json(target,receipt)
        if attempt_id:
            settle(budget,attempt_id,'completed' if receipt['independent_return'] else error.code,
                   usage,reason=error.reason if error else None,halt=error.halt if error else False)
    return {'independent_return':receipt['independent_return'],'status':receipt['status'],'http_status':receipt['http_status'],
            'usage':usage,'receipt':str(target),'feedback':str(out/'feedback.md') if receipt['independent_return'] else None,
            'message':'已取得 DeepSeek 独立审查返回。' if receipt['independent_return'] else receipt.get('error')}

def run(task_dir,draft_paths,audience=DEFAULT_AUDIENCE,medium='约两分钟口播',key_file=None,budget_file=None,
        allow_external=False,timeout=180,limit=None):
    from audit_draft import persistent
    if not allow_external:raise Failure('external_not_authorized','请先按首读执行说明核实外发范围。')
    if type(timeout) is not int or not 30<=timeout<=600:raise Failure('invalid_timeout','超时应为 30–600 秒。')
    if limit is not None and (type(limit) is not int or not 1<=limit<=MAX_CALLS):
        raise Failure('invalid_limit','新建账本上限应为 1–20。')
    if not audience.strip() or not medium.strip() or not draft_paths:
        raise Failure('invalid_arguments','需要稿件、读者画像和呈现方式。')
    try:
        if not Path(task_dir).expanduser().is_absolute():raise ValueError('任务目录应为绝对路径。')
        task=persistent(Path(task_dir))
        if not task.is_dir():raise ValueError('任务目录不存在。')
        review=persistent(task/'first-reader')
        if review!=task/'first-reader' or (review.exists() and not review.is_dir()):
            raise ValueError('首读输出目录应是任务中的实际目录。')
        if not os.access(task,os.W_OK):raise ValueError('任务目录不可写。')
        key,source=read_key(key_file)
        prepared=[]
        for path in draft_paths:
            path=Path(path).expanduser();path=path if path.is_absolute() else task/path
            draft=path.read_text(encoding='utf-8')
            prepared.append({'name':path.stem,'draft':draft,'payload':build_payload(draft,audience,medium,key)})
        budget=budget_path(budget_file,task)
        create=not budget_file and not os.environ.get('STORY_CRAFT_READER_BUDGET')
        if not lock_parent(budget).is_dir():raise ValueError('账本的父目录不存在。')
        # No files or directories have been created before both preflight passes.
        available(read_budget(budget,create,limit),len(prepared))
        with directory_locks([task,lock_parent(budget)]):
            data=read_budget(budget,create,limit);available(data,len(prepared))
            allocated=list(review.iterdir()) if review.exists() else []
            allocated += [Path(x['output_dir']) for x in data['attempts'] if x.get('output_dir') and Path(x['output_dir']).parent==review]
            seq=max([int(m.group(1)) for p in allocated if (m:=re.match(r'^(\d+)-',p.name))] or [0])
            for n,item in enumerate(prepared,seq+1):
                item['out']=review/(f'{n:02d}-'+item['name'])
                reservation=attempt(item['out'],sha(json.dumps(item['payload'],ensure_ascii=False)))
                item['id']=reservation['id'];data['attempts'].append(reservation)
            review.mkdir(exist_ok=True)
            write_json(budget,data)
    except Failure:raise
    except (OSError,ValueError,TypeError,UnicodeError):
        raise Failure('preflight_failed','参数、目录、稿件或配置预检失败，请修正后在同一任务目录重跑。')
    results=[]
    try:
        for item in prepared:
            with ledger(budget) as data:
                if data.get('stop_reason'):break
            results.append(run_one(item,key,source,budget,timeout))
    finally:
        cancel_reserved(budget,{x['id'] for x in prepared})
    completed=len(results)==len(prepared) and all(x['independent_return'] for x in results)
    return {'independent_return':completed,'requested':len(prepared),'sent':len(results),'results':results}

class SafeParser(argparse.ArgumentParser):
    def error(self,message):
        self.print_usage(sys.stderr)
        self.exit(2,str(Failure('invalid_arguments','参数不完整或无效，请用 --help 查看。'))+'\n')

def main():
    p=SafeParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter,epilog="""
配置与恢复（两种客户端相同）：
- 默认 DeepSeek chat/completions，deepseek-flash，非流式，关闭 thinking，最大8192输出词元。
- 账本顺序：--budget-file、STORY_CRAFT_READER_BUDGET、任务/first-reader/budget.json。
  参数和环境指定不同账本会拒绝。已有账本不能用 --limit 调高；共享进程用同一账本。
- 密钥顺序：--key-file、STORY_CRAFT_DEEPSEEK_KEY_FILE、用户配置目录 story-craft/deepseek.env、
  DEEPSEEK_API_KEY。脚本直读配置，认证头不落盘，回执仅记录来源类别。
- 整批预检失败：零请求、零占次、零落盘；修正后原目录重跑。
- 请求开始后即占次，不自动重试。401/403或402会停用账本，先修认证或余额；
  不删除已发生的请求。排队超时保留账本，是否再试按剩余额度决定。
- --timeout 同时限制网络读取与总时限（默认180秒，30–600）。
- 输出：first-reader/递增序号-稿件名/，旧结果保留；request.json、draft-reviewed.md、
  response.txt/json、usage.json、receipt.json；仅有效返回生成 feedback.md。
- 缺结束原因记 finish_reason_missing，截断记 incomplete_content，用量缺失写未返回。
""")
    p.add_argument('--task-dir',type=Path,required=True)
    p.add_argument('--draft',type=Path,action='append',required=True)
    p.add_argument('--audience',default=DEFAULT_AUDIENCE,help='读者画像；省略时使用无行业背景的普通观众');p.add_argument('--medium',default='约两分钟口播')
    p.add_argument('--key-file',type=Path,help='已有配置文件；不打印密钥')
    p.add_argument('--budget-file',type=Path,help='已有共享账本，与环境变量不同时拒绝')
    p.add_argument('--allow-external',action='store_true',help='已按首读说明核实外发范围')
    p.add_argument('--timeout',type=int,default=180)
    p.add_argument('--limit',type=int,help='只在新建默认账本时设定，范围 1–20')
    a=p.parse_args()
    try:r=run(a.task_dir,a.draft,a.audience,a.medium,a.key_file,a.budget_file,a.allow_external,a.timeout,a.limit)
    except Failure as e:
        print(json.dumps({'independent_return':False,'error_code':e.code,'message':e.message},ensure_ascii=False));return 2
    print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r['independent_return'] else 1
if __name__=='__main__':raise SystemExit(main())
