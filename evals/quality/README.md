# Evaluator 质量评测运行说明

单／多 Agent 架构对比与本项评分测试的统一实验步骤见 [Agent 效果实验](../../docs/agent-effect-experiments.md)。可先用 `make eval-grading-plan` 预览样例与调用次数，不发起 API 请求。

这里使用合成问答验证评分 Agent。当前 v3 包含 12 个根问题、60 条分级基础样本和 10 条诊断变式，共 70 条；开发集 34 条，测试集 36 条，覆盖六类 rubric 题型。同一题及其变式保持在同一数据划分中。建议等级由 AI 起草，初始状态全部为 DRAFT，不是人工金标。

注入问题组已用于真实模型诊断和提示词调整，因此整组归开发集，不参与封存测试。不是把看过的失败题继续留在测试集后声称泛化改善。

## 1. 准备数据与本地冒烟

在仓库根目录执行：

```bash
make eval-quality-prepare
make eval-quality-smoke
```

已经生成过 dataset.json 和 labels.csv 时，不要再次 prepare 覆盖标注。需要另一套数据时使用 `--data-dir` 指定新目录。冒烟明确使用 FakeGateway，不发起真实 API 请求，即使应用 .env 配置了 live。假模型与草案标签得到的指标只用于排查链路，不可写作模型效果。

已有旧版数据时，用 `.venv/bin/python -m evals.evaluator_quality extend` 追加新样例。程序先备份旧数据与标注，保留已有笔记；曾用于开发的测试组会整组转入开发集并要求重新确认，不会自动把开发样例转成测试样例。

## 2. 人工确认标注

用表格软件或文本编辑器打开 labels.csv，每行包含问题、当前回答、此前问答、冻结 rubric、建议等级和理由。人工检查后填写：

- `expected_levels`：例如 `[0]`、`[2]` 或 `[null]`。有充分依据的数值等级边界可写 `[2,3]`，null 不与数值混用。
- `reason`：人工判断理由；同时检查建议理由是否成立。
- `reviewer`：实际标注人身份，单人标注就如实记录单人，不能写虚构复核人。
- `status`：确认后写 `CONFIRMED`；未检查的样本保留 DRAFT。

不要改写 input_sha256。输入发生变化后，原标注不再自动有效，需要重新审阅。可先确认开发集前五条，使用 `--limit 5` 小批运行。完整测试则需要确认所有选中的测试行；程序不会悄悄跳过尚未确认的难例。

## 3. 真实模型运行

真实调用使用应用配置中的 MODEL_API_BASE、MODEL_API_KEY 与 MODEL_IDENTIFIER，但不依赖业务数据库，也不经过生产任务租约。复用生产的请求构造、Evaluator 上下文、只读工具和输出清洗规则；不启动整场面试，不评测任务队列或检索索引。样例不含真实简历，预置参考资料为空，因此结论范围是这些合成问答上的 Evaluator 表现。

只在决定使用该模型并接受调用成本后运行。例如人工确认前五条开发样本后：

```bash
.venv/bin/python -m evals.evaluator_quality run \
  --mode live --split dev --limit 5 --max-requests 5
```

正式测试集共有 36 条。全部人工确认后，一次运行需要 36 次调用，三次重复需要 108 次：

```bash
.venv/bin/python -m evals.evaluator_quality run \
  --mode live --split test --repeats 3 --max-requests 108
```

每次请求默认限制最多 1800 个输出 token，可用 `--max-output-tokens` 调整。max-requests 是请求次数上限，不是金额上限；输入长度、模型计价和缓存情况仍会影响费用。没有 API Key 会拒绝运行，不会降级为假模型。网络失败不自动重试，并记录为失败样本。中断时 results.jsonl 保留已写入的部分结果，不能把不完整运行当正式结果。

`--allow-draft` 允许探索未确认样本，但报告会标注不能作为人工金标质量数字。禁止先看模型答案再修改封存测试标签以追求高分。

## 4. 输出与人工复核

每次运行写入单独的 runs 目录：

- `metadata.json`：实际运行模式、配置模型、提示词全文与哈希、输入与代码哈希、标注状态、次数和参数。服务返回的模型标识另存于逐条结果，需核对是否发生供应商版本变化。
- `inputs.json`：选中的快照及标注，不会把金标分数送入模型请求。
- `results.jsonl`：每次原始输出、清洗后结果、错误类型、请求标识、token 与延迟。
- `report.json`：有效结果率、等级符合率、错答高分率、正确弃评率、原始引用有效率及分组明细；保留分子、分母，重复运行不当作独立样本。
- 三次重复时另报同一样例的等级一致性；数值 MAE 必须与数值输出覆盖率一起看，失败与弃评不能从等级符合率分母里删掉。
- `evidence_review.csv`：人工检查引文是否支持结论，填写 supports_conclusion、reviewer、notes；定位成功不等于解释正确。自动报告不会把此项记成已通过。

人工复核时只修改判断列：`supports_conclusion` 填 `YES` 或 `NO`，填写真实 reviewer 和 notes。不要改写问题、回答或输出。然后汇总：

```bash
.venv/bin/python -m evals.evaluator_quality review --run-dir evals/quality/runs/运行目录
```

程序校验原始输入、结果及工作表是否一致，统计已复核、待复核和解释获支持的输出比例。空白不会自动记为通过。该比例以整份输出解释为单位，不代表逐个技术断言的准确率。

原始引用统计也计入能够解析但未通过业务校验的输出，避免清洗移除坏引用后导致指标虚高。无法解析的输出仍计入失败率。费用字段保持未知，不把业务预算中的估算常量当实际账单。

## 5. 新旧提示词对比

先完成开发集调试，再冻结模型、数据、人工标签与其他条件。用 `--prompt-file` 读取候选系统提示词，不修改线上提示词。候选实验仍保留相同 LIVE_RULES、上下文、工具和 Schema，只比较系统提示词变化。

两轮真实测试应使用相同 split、limit、repeats 和 max-output-tokens，再比较报告：

```bash
.venv/bin/python -m evals.evaluator_quality compare \
  --before evals/quality/runs/旧运行目录/report.json \
  --after evals/quality/runs/新运行目录/report.json
```

非提示词条件不一致会拒绝对比。即使请求配置相同，也要核对服务返回模型标识与运行日期。比较输出是描述性差异，不自动代表统计显著性；需要逐例复核新增的严重错误。

## 6. 简历中能写什么

人工确认、真实运行、结果复核完成后，可以写：

> 构建覆盖 X 个问题、N 条人工标注问答的离线评测集，在指定模型与冻结 rubric 下评估等级符合率、错答高分和证据引用有效性，保留逐例输出与提示词版本用于回归。

如需写等级符合率，明确模型、样本范围与人工标注方式。只有完成条件一致的新旧版本对比后，才能写提升；百分比与百分点要区分。一次 Evaluator 评测不能证明多 Agent 优于单 Agent，也不能证明训练带来了真实能力提升。

详细评测依据见 `docs/evaluator-evaluation-plan.md`。
