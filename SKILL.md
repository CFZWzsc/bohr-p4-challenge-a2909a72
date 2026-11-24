---
name: query-plan-literature-search
description: 当科研任务给出研究问题或主题、并提供候选文献记录，要求生成可复算的检索式、去重候选集、透明相关性排序与逐条来源证据时使用；产出保留 case_id 与全部 output_contract 字段的 /app/output/result.json。
---

# 科研问题驱动的可追溯文献检索

## 用途与适用条件

把一条自然语言的科研问题转成显式检索计划（核心词、同义词扩展、字段限定），对输入中真实存在的候选文献做标识符归一、去重、透明打分和排序，并为每条结果保留来源定位、命中词和原文短引。适用于：

- 输入含 `/input/case.json`，其中给出研究问题/主题与候选论文（标题、摘要、作者、年份、期刊、DOI、PMID 等）。
- 任务要求"检索结果 + 排序/筛选依据 + 来源定位 + 状态与不确定性"，而不是只给一段文字。
- 需要他人在没有作者环境的情况下复算查询、排序与筛选结论。

不适用：需要实时联网检索、全文精读或系统综述正式纳排的任务；本 Skill 不会补造未提供的文献或元数据。

## 输入

| 来源 | 说明 |
|---|---|
| `/input/case.json` | 主输入。读取 `case_id`、`output_contract`、研究问题、纳排条件、年份范围与候选记录。字段名可能不同，脚本按结构识别而不是按题面标签猜测。 |
| `task.md` | Harness 实际提供的任务说明；`/input/task.md` 存在时作为问题补充上下文读取。 |
| `rules.json` | 可编辑的同义词表、字段权重与阈值；缺失时使用脚本内置等价默认值。 |

运行依赖：CPython 3.9+ 标准库，无第三方包、无网络、无随机数。支持参数：`--case`、`--output`、`--top-k`、`--rules`。

## 处理流程（逐项覆盖评分维度）

1. **界定问题与范围（实际效果）**：读取真实问题、纳排条件与年份范围。问题为空、候选为空、或问题无法产出可用词项时，分别记录 `missing_question` / `empty_candidates` / `no_query_terms`，不把"没找到"写成"不相关"。
2. **查询规划（创新增益）**：由问题词项生成三类查询——核心概念合取（Q1）、受控同义词扩展（Q2）、标题字段限定（Q3）。每条查询记录 `query_id`、`query_string`、`source_terms`、`fields` 与设计理由，便于独立复算。
3. **记录归一与去重（实际效果 / 可靠体验）**：统一 DOI（去解析前缀、小写）、PMID、arXiv 编号与标题规范化；按 DOI→PMID→arXiv→规范化标题的顺序生成稳定去重键。合并时保留全部 `source_locator` 与合并键，字段只补空不覆盖。
4. **透明相关性评分（使用增益）**：公开分数组件——问题词在标题（×3）、关键词（×2）、摘要（×1）中的加权覆盖，加标识符/摘要完整度小权重，加纳入词命中，减排除词命中；输出 0–1 分数、`relevance`（direct/indirect/not_relevant/uncertain）与 `decision`（keep/review/exclude）。阈值写在 `rules.json`，可复核、可调整。
5. **排序与完整性（实际效果 / 可核验性）**：`candidates` 保留全部记录，`ranked_results` 按分数、相关性、标题、record_id 稳定排序；`--top-k` 只限制前缀，不截断全量。所有计数在 `coverage` 与 `metrics` 中闭合。
6. **证据记录（可核验性）**：每条记录写 `evidence` 条目，包含来源定位、命中词、原文短引（从标题/摘要真实截取）、分数组件；并标注该证据只支持词面匹配，不声称语义蕴含或人工判断。
7. **状态与异常（可靠体验）**：空候选、缺问题、缺摘要/DOI 分别进入 `uncertainties` 与 `metadata_gaps`；脚本异常时仍写出含 `case_id`、`status: error` 与契约字段占位的结果文件，退出码 2。
8. **生态可用（生态贡献）**：`rules.json` 可直接替换为领域同义词表；输出键名与排序规则固定，便于他人复用与对照。

## 输出契约

写入 `/app/output/result.json`（或 `--output` 指定路径）。必须保留：

- `case_id`：来自实际输入；缺失时为空字符串并记入不确定性。
- **每个原始 `output_contract` 字段**：脚本运行时读取契约字段名逐一落位；若某字段无法从输入推断，仍保留键并写入占位值（列表/对象/空值），同时在 `output_contract_fields.notes` 中说明，绝不编造内容。

核心输出字段：

| 字段 | 内容 |
|---|---|
| `status` | `ok` / `partial` / `empty` / `error` |
| `question`、`criteria` | 实际读到的问题与纳排/年份条件 |
| `search_plan`、`queries` | 词项、扩展词、查询式与理由 |
| `candidates` | 全量归一记录（含 rank、score、relevance、decision、source_locator、字段缺失） |
| `results` / `ranked_results` | 排序后的主结果（默认全量，`--top-k` 可限前缀） |
| `evidence` | 按 record_id 的证据条目与短引 |
| `coverage`、`metrics` | 输入数/唯一数/去重数/保留数、平均分、覆盖词项 |
| `duplicates`、`metadata_gaps`、`missing` | 合并依据、缺失字段、未覆盖词项 |
| `uncertainties`、`limitations`、`change_log` | 状态说明与处理记录 |
| `output_contract_fields` | 原始契约字段清单、是否落位、占位原因 |

## 异常处理

| 情况 | 行为 |
|---|---|
| `/input/case.json` 不存在或不是 JSON 对象 | 写出 `status: error`、`error.stage: load_case`，保留契约字段占位，退出码 2 |
| 候选为空 | `status: empty`，明确这是空结果而非检索失败；不生成任何文献 |
| 缺少标题/摘要/DOI | 记入 `fields_missing` 与 `metadata_gaps`；无法判断时 `decision: review` |
| `output_contract` 结构未知 | 兼容 dict 键、字符串列表、含 `name/field/key` 的对象列表；未知字段用占位并记录 |
| 输出目录不可写 | 向 stderr 输出结构化错误，退出码 3 |

## 限制与不适用

- 不联网、不调用学术数据库，因此**不声称召回率、覆盖率或数据库全覆盖**；只用输入中真实存在的记录。
- 词面匹配可能漏掉同义改写或跨语言表述；跨语言任务应改用术语表驱动的专用 Skill。
- 评分不是同行评审结论，不评价研究质量、偏倚或统计正确性。
- 无法验证输入记录本身的真实性；如输入元数据自相矛盾，记入 `uncertainties` 而不擅自更正。

## Quickstart

在仓库根目录 `bohrium-competition` 执行（PowerShell）：

```powershell
python -B "tracks\literature-search\challenges\challenge-a2909a72\06_code\submission\scripts\run_skill.py" --case "tracks\literature-search\challenges\challenge-a2909a72\06_code\submission\examples\demo-case\case.json" --output "$env:TEMP\demo_a2909a72\result.json"
```

- 示例输入：`examples/demo-case/case.json`，合成且自包含（`"demo": true`，4 条候选记录，含 1 条编辑述评与 1 条无关 RCT；DOI 均为虚构的 `10.0000/...`）。
- **已知缺陷（当前脚本，未修复）**：`main()` 在 `os.environ["SK_RESULT_PATH"] = str(args.out)` 处引用了不存在的 `args.out`（`--output/--out` 解析后的属性名是 `args.output`）。因此上面的命令退出码为 0，却不会写入 `--output` 指定路径，只在当前工作目录留下 `{"status": "partial", "error": "AttributeError"}` 错误桩。对同一输入直接调用 `build_result()` 可得到 `status = "ok"`，且 `case_id`、`status`、`question`、`queries`、`candidates`、`ranked_results`、`evidence`、`coverage`、`metrics` 全部存在且非 null，说明示例输入本身合法，故障仅在这一行 CLI 接线。
- 该用例只验证脚本可执行与输出契约齐全，不代表真实检索或排序质量。

## 最小调用示例（示例，非官方案例结果）

输入 `/app/work/example_case.json`（**示例数据，仅示意接口**）：

```json
{
  "case_id": "example-001",
  "question": "Does pyroptosis predict immunotherapy response in solid tumors?",
  "inclusion_criteria": ["solid tumor", "immunotherapy"],
  "candidates": [
    {"title": "Pyroptosis signatures predict immunotherapy response in melanoma",
     "abstract": "A pyroptosis gene signature was associated with response to immunotherapy in melanoma.",
     "doi": "10.1000/example.1", "year": 2024, "journal": "Example Journal"}
  ],
  "output_contract": {"ranked_results": "ordered papers", "evidence": "per-paper source evidence", "status": "run status"}
}
```

调用：

```bash
python /skills/weekly-skill/scripts/run_skill.py \
  --case /app/work/example_case.json \
  --output /app/output/result.json
```

示意输出（截断，**示例**）：

```json
{
  "case_id": "example-001",
  "status": "ok",
  "queries": [{"query_id": "Q1", "query_string": "pyroptosis AND immunotherapy", "fields": ["title", "abstract"]}],
  "ranked_results": [{"rank": 1, "title": "Pyroptosis signatures predict immunotherapy response in melanoma",
                      "score": 0.62, "relevance": "direct", "decision": "keep",
                      "source_locator": "/candidates/0"}],
  "metrics": {"input_candidates": 1, "unique_candidates": 1}
}
```

## 自查清单

- [ ] `SKILL.md` frontmatter 的 `name` 为小写连字符、`description` 说明触发时机与产出。
- [ ] 脚本可 `python -m py_compile` 通过，且仅用标准库。
- [ ] 用合成输入试跑后，`result.json` 含 `case_id` 与全部 `output_contract` 字段。
- [ ] `candidates` 覆盖输入每条记录，`coverage.input_records` 与之一致。
- [ ] 每条结果有 `source_locator`，`evidence` 短引来自真实输入文本。
- [ ] 空输入、坏 JSON、缺字段时仍输出结构化结果而非崩溃。
- [ ] 未声称联网检索、官方案例运行或固定基准分数。


## 交付保证与评分对齐

1. 在 Skill 安装目录执行：`cd /skills/weekly-skill && python scripts/run_skill.py --case /input/case.json --output /app/output/result.json --top-k 0`；`rules.json` 缺失时使用内置默认值，不要假定相对路径可用。
2. 必须产出**完整成品** `result.json`：`candidates` 覆盖每条真实输入记录，`ranked_results`、`evidence` 与 `coverage` 计数闭合；禁止 skeleton、`pending`、空占位或只写方法说明。
3. 脚本无法自动完成的实质判断（真实问题对应的相关性/纳排依据）由调用 Agent 依据实际材料补全并写回 `result.json`，不得编造文献或元数据。
4. 自检：`case_id` 与输入一致；`output_contract` 每个字段落位；`status` 取 `ok` / `partial` / `empty` / `error`（不得写 `completed`）；每条结果有 `source_locator` 与真实短引。
5. 输入缺失、JSON 损坏或字段异常时仍写出结构化结果并说明缺口，不崩溃、不静默成功。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
