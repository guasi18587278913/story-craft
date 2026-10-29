# 读取与核对

以下命令在技能根目录运行，或把脚本换成绝对位置。`TASK_DIR` 指已存在的绝对任务目录。记录与审稿产物都在任务目录，输入保持不变。用用户指定的产物位置；未指定时在本次工作的目录下创建任务子目录，不用系统临时目录。方法读取和事实核对沿用持久目录检查；审稿与首读另做仓库边界和输出组件检查，拒绝写回技能仓库或经软链越界。显式指定的仓库外合法临时目录可用于离线测试，但不自动选作真实任务的存储位置。

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

首读、初审、局部复审和用户反馈轮均显式沿用任务受众／知识范围、形式／已定长度；已知不重问，未定写未定，不靠 CLI 默认值补成普通外行或两分钟。先从工作表填写下面两个变量（占位文字须替换），本稿约定与估时不同则分别写清；同点长短版按各稿约定更新 `TASK_MEDIUM`。长度／实际时长放在 `--medium`，没有另加长度参数；这些参数不代表脚本已核查人称、目的或锁句等所有任务约束。

```bash
TASK_AUDIENCE='本任务受众；已知知识范围（未定项写未定）'
TASK_MEDIUM='本任务形式；本稿已定长度／时长（未定写未定）；当前稿估时（如有）'
python3 scripts/run_review_panel.py --task-dir "$TASK_DIR" --draft draft-23-初稿.md --engine codex --audience "$TASK_AUDIENCE" --medium "$TASK_MEDIUM" --focus "这篇只讲的那一点" --source "$TASK_DIR/source.md"
python3 scripts/run_review_panel.py --task-dir "$TASK_DIR" --draft draft-23.md --engine claude --scope local --groups 5 --changes changes.md --audience "$TASK_AUDIENCE" --medium "$TASK_MEDIUM" --source "$TASK_DIR/source.md"
```

在 Claude Code 里写的稿用 `--engine codex`，在 Codex 里写的用 `--engine claude`。按[修改范围](revision/index.md#先按修改范围选审稿员)选组：整篇 `--scope full` 默认 1–8 加 10，局部 `--scope local` 必须显式给 `--groups` 和非空 `--changes` 文件，缺参数会在调用前拒绝。`--groups 5` 只核事实，`--groups 4,10` 只核相关表达和理解；标题封面用 9。纯错字、标点或排版修改不运行本脚本。几类改动取相关组并集，不自动补齐九位。

`changes.md` 只列当前稿的句段位置、逐字短引、改动类型和受影响范围，不给预期答案、不夹带稿外原材料；路径相对任务目录。例如：“第 2 段‘只有 1 家付了 300 元’，核试用与付费数量及金额；其他段不改。”脚本把范围发给每位已选审稿员，稿件仍提供理解所需的前后文。第 5 位另拿 `source.md` 和风险反例；按现实现，`--focus` 只给第 1、2、3、9 位，第 10 位无来源、无看点，不强制所有组接看点。首读没有 `--focus`，不能把来源、看点或工作表塞进读者属性。实际外发前分别核实当前任务对所用服务和数据的授权；正文首读许可不等于另一模型及原材料外发许可，显式 `--source` 只选文件、不代表授权。用意判断按[改稿说明](revision/index.md#2-八个审稿员分组审)，允许可理解的潜台词和有兑现的悬念，只对声称成功的稿件检查对应过程。

每次执行（包括 `--prompts-only`）创建独立的 `review/<草稿文件名去扩展名>/run-…/`，不覆盖以前的轮次。以输出 JSON 的 `review_dir` 为准：`inputs/` 保存送审正文、用到的原材料及修改范围，`prompts/` 保存提示，`summary.json` 记录 `run_id`、`draft_sha256`、其他输入与提示指纹、实际分组和各组状态。指纹对应保存和送审的 UTF-8 文本。已有输出会被拒绝；不能因旧目录有意见就记成本轮完成。

汇总在调用前写入，此后随各组返回更新。缺少模型命令、启动失败、超时、非零退出或空返回都记失败，退出码非零；不自动重试。`prompts_only` 状态只证明生成了提示；`completed` 只证明本轮所选组取得了非空返回，不代表作者已采纳修改或改后复核完成。另在改稿记录里分别记这四个阶段及所对应稿件，旧稿意见不算本轮完成。

另一家模型用不了时，可加 `--prompts-only` 生成本轮提示，在允许的范围内交给本工具另开的干净上下文去审，记录并说明“同模型替代”，不称外部模型已审。干净上下文也不可用或不许发送时，保留未完成原因，只做本地核查；不换服务、不读凭据、不自动重试。外审非用户明确交付前提的，可据本地核查交稿并标未完成；是明确前提的只能标待审，不称最终验收。只要正文也在正文外用一句话说明影响采用的未审状态或关键事实缺口。Codex 思考强度默认 xhigh，可用 `--effort` 改；Claude 用自己的默认。

审稿提示会按是否拿到原材料区分判断依据：只有第 5 位核对来源并引用原句，其余只据稿面诊断。意见按“必须修／待核原文／可选增强”输出，由写稿方核实后取舍；脚本的成功状态只表示取得返回，不证明分类、事实判断或建议正确。

## 首读

范围与命令在[首读执行](../agents/first-reader.md)，沿用上面的 `TASK_AUDIENCE`、`TASK_MEDIUM`，只传读者属性和正文，不传看点、来源或工作表整包。故障处理与配置顺序看 `run_first_reader.py --help`。只运行已有脚本，不用钩子，不把认证信息放进稿件或提示词；不能因失败而查找凭据或换服务。

## 维护

改了卡片或路由目录，按上面[读卡与核对](#读卡与核对)重建指纹再核。改稿手册在 `references/revision/`，条目原样保留出处，改过要核链接；审稿员脚本 `scripts/run_review_panel.py` 按条目锚点截取标准，手册改了锚点要跟着改脚本里的分组。改完跑 `python3 scripts/check_integrity.py`：它核方法快照和本地链接，并调用 `check_templates.py` 核故事卡和写法卡。Codex 和 Claude Code 用同一套脚本，不用钩子。

改审稿脚本时运行 `python3 -B -m unittest discover -s tests -v`，离线核输出归属、失败记录和范围分组，不调用模型。提示语的效果另用实际样例检查，不能把脚本测试通过当成写作效果已验证。
