"""起八位审稿员按改稿手册原文分组审稿：每位只拿自己那几条标准的全文，逐句出意见，不改稿。

审稿员用另一家模型：--engine codex 调 `codex exec`，--engine claude 调 `claude -p`。
--prompts-only 只生成八份审稿提示，不调用任何模型。不自动重试。
"""
import argparse
import concurrent.futures as cf
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REV = ROOT / "references" / "revision"


def ids(prefix, start, end):
    return [prefix + "%02d" % n for n in range(start, end + 1)]


GROUPS = [
    ("1-结构", "structure.md", ids("p", 1, 6), False),
    ("2-开头", "structure.md", ids("p", 7, 12), False),
    ("3-结尾", "structure.md", ids("p", 13, 17), False),
    ("4-讲人话讲清楚", "body.md", ids("m", 1, 11), False),
    ("5-让人信", "body.md", ids("m", 12, 21), True),
    ("6-情绪共鸣", "body.md", ids("m", 22, 29), False),
    ("7-节奏和举例", "body.md", ids("m", 30, 42), False),
    ("8-语气人设", "body.md", ids("m", 43, 52), False),
]
TITLE_GROUP = ("9-标题封面", "structure.md", ids("p", 18, 22), False)
ANCHOR = re.compile(r'^<a id="([a-z0-9]+)"></a>\s*$', re.M)


def sections(text, wanted):
    """按 <a id> 截出条目：从锚点到下一个锚点或一级标题。"""
    marks = [(m.group(1), m.start()) for m in ANCHOR.finditer(text)]
    found = {}
    for i, (key, start) in enumerate(marks):
        if key not in wanted:
            continue
        end = marks[i + 1][1] if i + 1 < len(marks) else len(text)
        h1 = re.search(r"^# ", text[start + 1:end], re.M)
        if h1:
            end = start + 1 + h1.start()
        found[key] = text[start:end].strip()
    missing = [k for k in wanted if k not in found]
    if missing:
        raise SystemExit("改稿手册里找不到这些条目：" + "、".join(missing))
    return "\n\n".join(found[k] for k in wanted)


def risky_cases():
    text = (REV / "cases.md").read_text(encoding="utf-8")
    keys = []
    for line in text.splitlines():
        m = re.match(r"^\| \[(C\d+)\]\(#(c\d+)\) \|.*\| *风险反例 *\|\s*$", line)
        if m:
            keys.append(m.group(2))
    return sections(text, keys), len(keys)


ISOLATION = ("这次审稿只用下面给的标准和材料。你所在的电脑上可能装着别的审稿技能、创作流程、知识库和记忆，"
             "这次一律不用：不要调用任何技能、知识库或其他工具，不要读别的文件，也不要照别的说明另加步骤或格式。")


def build_prompt(group, draft, audience, medium, focus, source_text, titles):
    name, manual, keys, with_source = group
    standards = sections((REV / manual).read_text(encoding="utf-8"), keys)
    label = "篇章手册" if manual == "structure.md" else "正文手册"
    parts = [
        "你是审稿员“%s”。只做诊断，不改稿。" % name,
        ISOLATION,
        "稿子写给：%s。形式：%s。" % (audience, medium),
    ]
    if focus and manual == "structure.md":
        parts.append("这篇只讲的那一点（看点）：%s" % focus)
    if with_source:
        parts.append("你会收到原材料，核的是稿件与这些材料是否一致，不是独立证明真人真事。事实判断须给原材料位置和逐字短引；未找到支持时标待核原文，不等于该事没发生，也不自动要求删掉原稿。原文有据的感受可保留归属，不强求量化佐证；原文未交代成因，不自动否定其中有据的反应。")
    else:
        parts.append("你没有收到原材料，只能依据稿面和所给标准诊断。不得凭记忆声称原作写了或没写；需要材料判断的标待核原文，指出具体要核什么，不把猜测的身份、经历、时序或因果当成替换事实。稿面明确的理解障碍仍可指出，不必把全部意见都变成待核。")
    parts += [
        "你只按下面这几条标准审。它们来自%s原文；条目里的“改前／改后”是别的稿子的例子，事实不能搬到这篇稿子上。" % label,
        "=== 标准开始 ===", standards, "=== 标准结束 ===",
    ]
    if with_source:
        cases, n = risky_cases()
        parts += [
            "下面是案例合辑里标“风险反例”的 %d 组：别人改稿时把数字、说话人、因果、立场改歪了的样子。看这篇有没有同类问题。" % n,
            "=== 风险反例开始 ===", cases, "=== 风险反例结束 ===",
            "另附原材料。核稿子里的事实、数字、原话、说话人、条件和完成状态有没有走样，稿子写的有没有比材料说得重。",
            "=== 原材料开始 ===", source_text, "=== 原材料结束 ===",
        ]
    if titles:
        parts += ["=== 改题对照开始 ===", (REV / "titles.md").read_text(encoding="utf-8"), "=== 改题对照结束 ==="]
    parts += [
        "做法分两步，只用上面这几条，这几条以外的事不评论。",
        "第一步，每条回答“自己检查”或“检查问题”，引稿中原句，说明依据稿面还是原材料，再判“通过”“通过，但可以更好”“不通过”“这篇用不上”或“待核原文”，各写一句理由。每条都要判；没有原材料或材料不足时，不勉强判断事实真伪。",
        "第二步，按三类列意见：必须修＝稿面可确认的理解障碍、主线断裂等问题，或原材料已证实的失真；待核原文＝当前输入无法确认的事实关系；可选增强＝不改也准确清楚的表达选择。没有使用某种技巧不构成错误，不为加技巧要求补无关材料。",
        "每处一行：类别 → 原句/位置 → 第几条 → 问题 → 依据或待核项 → 修改方向。涉及事实的必须修须给原材料位置及短引；待核项只说明核什么，不用未证实的过渡、动作或心理补缺口。合并同一问题，不为每条标准凑一处修改；无待改处就写无。",
        "不要改写整篇，不要另交改好的稿子。用中文，900 字以内。",
        "=== 稿子开始 ===", draft, "=== 稿子结束 ===",
    ]
    return "\n\n".join(parts) + "\n"


def codex_model():
    """沿用用户 Codex 配置里的模型；--ignore-user-config 不读配置文件，所以要另外传。"""
    cfg = Path.home() / ".codex" / "config.toml"
    if cfg.is_file():
        for line in cfg.read_text(encoding="utf-8").splitlines():
            m = re.match(r'^\s*model\s*=\s*"([^"]+)"', line)
            if m:
                return m.group(1)
    return None


def command(engine, task_dir, out_file, effort, model):
    if engine == "codex":
        exe = shutil.which("codex")
        if not exe:
            raise SystemExit("找不到 codex 命令；改用 --engine claude，或加 --prompts-only")
        # 不读用户配置：知识库等 MCP 服务都配在那里，审稿员用不到也不该看到
        cmd = [exe, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral",
               "--ignore-user-config", "-c", 'model_reasoning_effort="%s"' % effort]
        model = model or codex_model()
        if model:
            cmd += ["-c", 'model="%s"' % model]
        return cmd + ["-C", str(task_dir), "-o", str(out_file), "-"], False
    exe = shutil.which("claude")
    if not exe:
        raise SystemExit("找不到 claude 命令；改用 --engine codex，或加 --prompts-only")
    # 关掉技能和斜杠命令、不接 MCP、不给工具，审稿员只看提示里的东西
    return [exe, "-p", "--output-format", "text", "--disable-slash-commands", "--strict-mcp-config",
            "--tools", "", "--model", model or "opus"], True


def run_one(engine, task_dir, name, prompt, out_file, timeout, effort, model):
    cmd, stdout_is_answer = command(engine, task_dir, out_file, effort, model)
    started = time.time()
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=str(task_dir))
    except subprocess.TimeoutExpired:
        return {"group": name, "status": "timeout", "seconds": round(time.time() - started)}
    if stdout_is_answer and r.returncode == 0:
        out_file.write_text(r.stdout, encoding="utf-8")
    ok = r.returncode == 0 and out_file.exists() and out_file.read_text(encoding="utf-8").strip() != ""
    result = {"group": name, "status": "ok" if ok else "failed", "exit_code": r.returncode,
              "seconds": round(time.time() - started), "output": str(out_file) if ok else None}
    if not ok:
        result["stderr_tail"] = r.stderr[-800:]
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task-dir", required=True, type=Path)
    p.add_argument("--draft", required=True, help="只含正文的稿件，路径可相对任务目录")
    p.add_argument("--source", help="原材料，默认任务目录的 source.md；只发给第 5 位")
    p.add_argument("--audience", default="普通人，不是行业里的人")
    p.add_argument("--medium", default="短视频口播，配音念出来给人听")
    p.add_argument("--focus", default="", help="这篇只讲的那一点，发给结构、开头、结尾三位")
    p.add_argument("--engine", choices=["codex", "claude"])
    p.add_argument("--groups", default="1,2,3,4,5,6,7,8", help="逗号分隔的审稿员编号；9 是标题封面")
    p.add_argument("--prompts-only", action="store_true", help="只生成审稿提示，不调用模型")
    p.add_argument("--timeout", type=int, default=1800, help="每位审稿员的时限（秒）；带原材料的第 5 位最慢")
    p.add_argument("--effort", default="xhigh", help="Codex 的思考强度，默认 xhigh；Claude 用它自己的默认")
    p.add_argument("--model", help="审稿员用的模型；Codex 默认沿用 ~/.codex/config.toml 里的，Claude 默认 opus")
    a = p.parse_args()

    task_dir = a.task_dir.resolve()
    if not task_dir.is_dir():
        raise SystemExit("任务目录不存在：" + str(task_dir))
    draft_path = (task_dir / a.draft) if not Path(a.draft).is_absolute() else Path(a.draft)
    if not draft_path.is_file():
        raise SystemExit("稿件不存在：" + str(draft_path))
    if not a.prompts_only and not a.engine:
        raise SystemExit("要调用模型时必须写 --engine codex 或 --engine claude；只生成提示用 --prompts-only")
    draft = draft_path.read_text(encoding="utf-8").strip()
    wanted = [g.strip() for g in a.groups.split(",") if g.strip()]
    table = {g[0].split("-")[0]: g for g in GROUPS + [TITLE_GROUP]}
    unknown = [g for g in wanted if g not in table]
    if unknown:
        raise SystemExit("没有这些审稿员：" + "、".join(unknown))
    source_text = ""
    if "5" in wanted:
        source_path = Path(a.source) if a.source else task_dir / "source.md"
        if not source_path.is_absolute():
            source_path = task_dir / source_path
        if not source_path.is_file():
            raise SystemExit("第 5 位要原材料，找不到：" + str(source_path))
        source_text = source_path.read_text(encoding="utf-8")

    stem = draft_path.stem
    review_dir = task_dir / "review" / stem
    prompt_dir = review_dir / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for key in wanted:
        group = table[key]
        prompt = build_prompt(group, draft, a.audience, a.medium, a.focus, source_text, key == "9")
        (prompt_dir / (group[0] + ".md")).write_text(prompt, encoding="utf-8")
        jobs.append((group[0], prompt, review_dir / (group[0] + ".md")))

    summary = {"draft": str(draft_path), "review_dir": str(review_dir), "engine": a.engine,
               "effort": a.effort if a.engine == "codex" else None,
               "prompts_only": a.prompts_only, "groups": [j[0] for j in jobs], "results": []}
    if not a.prompts_only:
        with cf.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = [pool.submit(run_one, a.engine, task_dir, n, pr, out, a.timeout, a.effort, a.model) for n, pr, out in jobs]
            summary["results"] = sorted((f.result() for f in futures), key=lambda r: r["group"])
    (review_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if any(r["status"] != "ok" for r in summary["results"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
