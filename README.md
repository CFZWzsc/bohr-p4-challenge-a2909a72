# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-a2909a72` (track: `literature-search`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable pipeline and all supporting documents. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `query-plan-literature-search`
- Summary: 当科研任务给出研究问题或主题、并提供候选文献记录，要求生成可复算的检索式、去重候选集、透明相关性排序与逐条来源证据时使用；产出保留 case_id 与全部 output_contract 字段的 /app/output/result.json。

## Contents

| File |
|------|
| `SKILL.md` |
| `examples/demo-case/case.json` |
| `rules.json` |
| `scripts/run_skill.py` |
| `README.md` |

## How to run

```bash
python scripts/sk_entry.py --input /input --work /app/work --out /app/output
```

A single deterministic pass reads `/input`, computes the deliverable and writes a
contract-complete `/app/output/result.json` plus the human-readable review files
described in `SKILL.md`. The same input always produces byte-identical output; no
network access is used, and fields the materials cannot support are reported
explicitly instead of being left empty.

## Notes

- Pure Python standard library; no network access required.
- Every claim is anchored to paragraph-level evidence; unknown items are reported
  in `uncertainty` instead of being fabricated.
