#!/usr/bin/env python3
"""核方法快照、条目、相对链接与锚点，并调用 check_templates 核故事卡和写法卡；传入刚从 get_page 获取的 JSON 才对比知识库当前版本。"""
import sys
sys.dont_write_bytecode=True
import argparse
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import unquote
from read_methods import ROOT, digest, get_section, manifest

EXPECTED={
 'concepts/storytelling':{''},
 'concepts/structure':{str(x) for x in range(23,35)}|{'35','46','通用禁忌','缺口'},
 'concepts/opening_hook':{'29','30','31','32','33','2','3','4','9','22','通用禁忌'},
 'concepts/body_expression':{'1','8','16','19','21','22','26','27','28','33','34','38','41','49','50','不照学'},
 'concepts/body_expression_quick':{'写完自查'},
 'concepts/ending':{'4','10','11','12','13','14','15','16','照单全收'}
}
def pages(value,request=None):
    if isinstance(value,list):
        for item in value:yield from pages(item)
    elif isinstance(value,dict):
        if value.get('isError'):raise ValueError('当前读取结果包含工具错误')
        if 'page' in value:yield from pages(value['page'],value.get('request',request))
        elif 'compiled_truth' in value:yield request,value
        elif 'structuredContent' in value and value['structuredContent']:
            yield from pages(value['structuredContent'],request)
        elif 'content' in value:
            for block in value['content']:
                if block.get('type')=='text':
                    try:decoded=json.loads(block['text'])
                    except(ValueError,KeyError):continue
                    yield from pages(decoded,request)
        else:raise ValueError('不是 get_page 页面或页面列表')

def section_from_full(text,section):
    if not section:return text
    heads=list(re.finditer(r'^(#{1,6})\s+(.+)$',text,re.M))
    hits=[]
    for i,m in enumerate(heads):
        title=m.group(2)
        if section.isdigit():match=bool(re.match(re.escape(section)+r'\.\s',title))
        else:match=section in title
        if match:
            level=len(m.group(1));end=len(text)
            for n in heads[i+1:]:
                if len(n.group(1))<=level:end=n.start();break
            hits.append(text[m.start():end].strip())
    if len(hits)!=1:raise ValueError('无法从整页唯一定位：'+section)
    return hits[0]

def anchors(text):
    out=set(re.findall(r'<a\s+id=["\']([^"\']+)["\']',text))
    used={}
    for h in re.findall(r'^#{1,6}\s+(.+)$',text,re.M):
        h=re.sub(r'[*`~]','',h).strip().lower()
        h=''.join(c for c in h if c.isspace() or c in '-_' or unicodedata.category(c)[0] in 'LN')
        key=re.sub(r'\s','-',h)
        count=used.get(key,0);used[key]=count+1
        out.add(key+('-'+str(count) if count else ''))
    return out

def check_links(root=ROOT):
    errors=[];count=0
    for file in root.rglob('*.md'):
        text=file.read_text(encoding='utf-8')
        # Code blocks may contain illustrative links and are not navigation.
        text=re.sub(r'```.*?```','',text,flags=re.S)
        for target in re.findall(r'!?\[[^\]\n]*\]\(([^)]+)\)',text):
            target=target.strip('<>')
            if target.startswith(('http://','https://','mailto:')):continue
            if target.startswith(('/','~/','file://')):
                errors.append(str(file.relative_to(root))+' 本机路径链接：'+target);continue
            pathpart,sep,anchor=unquote(target).partition('#')
            dest=(file.parent/pathpart).resolve() if pathpart else file
            count+=1
            if pathpart and dest!=root.resolve() and root.resolve() not in dest.parents:
                errors.append(str(file.relative_to(root))+' 链接指到技能目录外：'+target);continue
            if not dest.exists():errors.append(str(file.relative_to(root))+' 链接不存在：'+target)
            elif sep and anchor and dest.is_file() and dest.suffix=='.md':
                if anchor not in anchors(dest.read_text(encoding='utf-8')):
                    errors.append(str(file.relative_to(root))+' 锚点不存在：'+target)
    for file in [*root.rglob('*.md'),*root.rglob('*.yaml')]:
        text=file.read_text(encoding='utf-8')
        visible=re.sub(r'https?://[^\s<>"，。；]+','',text)
        roots=('Users','home','root','private','tmp','var','Volumes','etc','opt','mnt','srv','workspace')
        if re.search(r'/(?:'+'|'.join(roots)+r')/',visible):
            errors.append(str(file.relative_to(root))+' 正文含本机绝对路径')
    return count,errors

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--current-json',action='append',type=Path,default=[],help='刚读取的 get_page JSON，可多次传入；不直接读 Wiki 文件')
    p.add_argument('--require-all',action='store_true',help='要求所有来源节均提供当前版本')
    a=p.parse_args()
    errors=[];changed=[];missing=[];checked=[];compared=0
    try:
        data=manifest();entries=data['entries']
        expected={base+('#'+s if s else '') for base,sects in EXPECTED.items() for s in sects}
        actual=[e['request'] for e in entries]
        if len(actual)!=len(set(actual)):errors.append('来源条目重复')
        if set(actual)!=expected:errors.append('来源集合与已定清单不同：'+repr(sorted(set(actual)^expected)))
        for e in entries:
            body=get_section(e)
            if len(''.join(body.split()))!=e['char_count']:errors.append(e['key']+' 字数不一致')
        for name,sha in data['snapshot_files'].items():
            if digest((ROOT/name).read_text(encoding='utf-8'))!=sha:errors.append('分类文件改变：'+name)
        link_count,link_errors=check_links();errors.extend(link_errors)
        current={}
        for path in a.current_json:
            for request,page in pages(json.loads(path.read_text(encoding='utf-8'))):
                key=request or page['slug']+('#'+str(page['section']) if page.get('section') else '')
                if key in current and current[key]!=page:raise ValueError('当前版本有冲突：'+key)
                current[key]=page
        if a.current_json or a.require_all:
            for e in entries:
                page=current.get(e['request'])
                full=False
                if page is None:
                    page=current.get(e['page']);full=True
                if page is None:missing.append(e['request']);continue
                if page.get('slug')!=e['page']:raise ValueError('页名与请求不符：'+e['request'])
                section=e['source_section']
                if page.get('section') is not None and str(page['section'])!=section:
                    raise ValueError('返回节与请求不符：'+e['request'])
                body=section_from_full(page['compiled_truth'],section) if (full or (section and not page.get('section'))) else page['compiled_truth']
                compared+=1
                reasons=[]
                if page.get('content_hash')!=e['content_hash']:reasons.append('content_hash 已变')
                if digest(body)!=e.get('source_section_sha256',e['section_sha256']):reasons.append('该节正文已变')
                if e.get('anonymization') and not reasons:
                    from check_templates import validate_edits
                    if validate_edits(body,e['anonymization'])!=get_section(e):
                        reasons.append('专名泛化与记录不一致')
                checked.append(e['request'])
                if reasons:changed.append({'request':e['request'],'reason':reasons})
    except(OSError,ValueError,KeyError,TypeError) as e:
        print(json.dumps({'error':str(e),'live_comparison':'failed' if a.current_json else 'not_run'},ensure_ascii=False,indent=2));return 2
    try:
        from check_templates import validate
        templates=validate()
        errors.extend(templates['errors'])
    except(OSError,ValueError,KeyError,TypeError) as e:
        templates={'error':str(e)};errors.append('卡片库：'+str(e))
    result={'entries':len(entries),'local_links':link_count,'templates':templates,'errors':errors,'error_count':len(errors),
            'live_comparison':'compared_supplied_get_page_results' if a.current_json else 'not_run',
            'compared':compared,'checked':checked,'changed':changed,'missing_current_sections':missing,
            'comparison_labels':{'checked':'已核','changed':'有变化','missing_current_sections':'未提供（未检查）'},
            'notice':'当前版本读取由调用者提供；没有 --current-json 时只查本地，未核知识库当前版本。'}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return int(bool(errors or changed or (a.require_all and missing)))
if __name__=='__main__':raise SystemExit(main())
