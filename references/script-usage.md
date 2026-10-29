# 读取与核对

以下命令在技能根目录运行，或把脚本换成绝对位置。`TASK_DIR` 指已存在的绝对任务目录。记录与审稿产物都在任务目录，输入保持不变。用用户指定的产物位置；未指定时在本次工作的目录下创建任务子目录，不用系统临时目录。方法读取、事实核对和首读共用同一个目录校验。

## 读卡与核对

```bash
python3 scripts/read_methods.py --list-templates
python3 scripts/read_methods.py --template 30 --task-dir "$TASK_DIR"
python3 scripts/read_methods.py --template LX07 --task-dir "$TASK_DIR"
python3 scripts/read_methods.py --record-get-page "$TASK_DIR/current-page.json" --task-dir "$TASK_DIR"
python3 scripts/read_methods.py --verify-claims prompt-23.md revision-23.md --task-dir "$TASK_DIR"
```

`--list-templates` 只显示路由目录，不记已读。`--template` 后面写卡号，可以一次写几张：故事卡是文件名前的数字，去掉前导零（`09-…` 写 `9`）；写法卡是文件名前四位（如 `LX07`）。脚本先核 `references/story-cards/catalog.json` 里的指纹，对不上就不显示；对上了显示整张卡，案例都在卡里，并在任务目录记一条 `card:卡号`。不带 `--task-dir` 只显示、不记录。

写稿提示和改稿记录里用 `[card:30]`、`[card:LX07]` 标明用过的卡；旧写法 `[template:30]` 照样认作 `[card:30]`。卡片改过以后，改之前的读取记录算过期，要重读。

实时返回用文件写入工具保存为 JSON，再用 `--record-get-page` 显示和记录，支持工具包装、页面列表和带 request 的记录。避免把长正文直接塞进 shell 命令。方法记录写 `[method:concepts/structure#30]`，不能替代读卡记录。

`method-reads/reads.jsonl` 保存请求、读取时间、模式与正文指纹；旁边保存对应原文证据。核对失败退出非零，缺读就补读再判断是否适用。读取记录只证明显示过该版本，不证明认知深度或方法使用质量。

已有记录且版本未变可以复用。整页与小节分别记请求，不用标题或短摘要充当已读。补查具体方法时可运行 `read_methods.py --stage structure --method 30 --section 通用禁忌 --task-dir "$TASK_DIR"`；其 `--list` 与 `--requests-only` 都不记已读。

改了卡片或路由目录，审过以后重建指纹，再核一遍：

```bash
python3 scripts/check_templates.py --rebuild
python3 scripts/check_templates.py
python3 scripts/check_templates.py --source-dir "$PAGES_DIR"
```

`--rebuild` 按卡片目录重写 catalog 里的编号、名字、状态（故事卡第 3 行）、用途（写法卡在路由目录哪张表）和指纹，并列出新增、删掉和改过的卡；卡片缺块、断链时不改写。默认运行只查本地：五块、状态行、准入两组、路由目录的链接、指纹和本机路径，`check_integrity.py` 也会调用它。`--source-dir` 接保存的 get_page 原页（上面的 `PAGES_DIR` 就是放原页的目录），目录或 JSON 文件都行，可写几个，逐行核故事卡里以 `> ` 开头的案例原文能在原页正文里找到；找不到、原页缺失或读不了都报错。原页只拿来核对，不放进技能目录。`check_integrity.py --current-json` 对比提供的实时页与方法快照。没有实时返回时只叫本地核验。

## 顺读与改句核对

```bash
python3 scripts/pacing_review.py --task-dir "$TASK_DIR" --draft draft-30-初稿.md
```

改稿时自己念一遍，可以先跑这一条：保存稿件快照，给出逐段字数、首句、段尾和288字/分钟的估时，路径可相对任务目录。工具不生成音频，也不代替朗读感受或故事判断。它的 `--review`、`--final` 两个参数核对旧版“划走句”记录，改稿流程里用不上。

## 合并检查

```bash
python3 scripts/check_quotes_numbers.py --source "$TASK_DIR/source.md" --draft "$TASK_DIR/draft-30.md"
```

改稿每改完一轮都跑：把稿里的引号和数字逐条回原文找，未命中、单位和约数疑点逐项回材料核，结果记进 `revision-<稿名>.md`。命中只说明文字对得上，说话人、口径、条件和完成状态仍要人看，做法见[改稿](revision/index.md#4-核有没有改歪)。要更细的逐句对照，可以再跑 `audit_draft.py --source … --draft … --out-dir …`，这一步不是必做。

字数按汉字、字母和数字计，不含空白标点；时长按每分钟约288字估计。需要人工改写记录用 `audit_draft.py --help` 查看 `--rewrites-json`。单项排错再用 `check_quotes_numbers.py`、`mark_source_sentences.py`、`length_and_lint.py` 的帮助。

## 审稿员

```bash
python3 scripts/run_review_panel.py --task-dir "$TASK_DIR" --draft draft-23-初稿.md --engine codex --audience "普通人，不是工程师" --medium "抖音口播，AI 配音，两分钟以内" --focus "这篇只讲的那一点"
```

在 Claude Code 里写的稿用 `--engine codex`，在 Codex 里写的用 `--engine claude`。脚本按[改稿](revision/index.md#2-八个审稿员分组审)的分组，从改稿手册截出每位审稿员那几条标准的全文，第 5 位另带风险反例和 `source.md`；第 10 位是用意审稿员，只拿稿子、观众和形式，逐句、再逐段问想让观众明白什么、没读过原文的人能不能自己说出来。默认这九位同时跑，意见存在 `review/<稿名>/`，提示存在 `review/<稿名>/prompts/`，汇总在 `summary.json`。`--groups 1,4,5` 只跑其中几位；`--groups 9` 是标题封面那位，只在要起标题时用；`--groups 10` 只跑用意审稿员。`--prompts-only` 只生成提示、不调用模型，给另一家用不了时改用本工具干净会话去审。Codex 审稿员的思考强度默认 xhigh，用 `--effort` 改；Claude 审稿员用它自己的默认。失败或超时的那一位记在汇总里，不自动重试，退出码非零。

审稿提示会按是否拿到原材料区分判断依据：只有第 5 位核对来源并引用原句，其余只据稿面诊断。意见按“必须修／待核原文／可选增强”输出，由写稿方核实后取舍；脚本的成功状态只表示取得返回，不证明分类、事实判断或建议正确。

## 首读

范围与命令在[首读执行](../agents/first-reader.md)，故障处理与配置顺序看 `run_first_reader.py --help`。只运行已有脚本，不用钩子，不把认证信息放进稿件或提示词。

## 维护

改了卡片或路由目录，按上面[读卡与核对](#读卡与核对)重建指纹再核。改稿手册在 `references/revision/`，条目原样保留出处，改过要核链接；审稿员脚本 `scripts/run_review_panel.py` 按条目锚点截取标准，手册改了锚点要跟着改脚本里的分组。改完跑 `python3 scripts/check_integrity.py`：它核方法快照和本地链接，并调用 `check_templates.py` 核故事卡和写法卡。Codex 和 Claude Code 用同一套脚本，不用钩子。
