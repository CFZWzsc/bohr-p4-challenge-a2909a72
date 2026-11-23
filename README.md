# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-a2909a72` (track: `literature-search`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable entry point and all supporting scripts. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `query-plan-literature-search`
- Summary: 当科研任务给出研究问题或主题、并提供候选文献记录，要求生成可复算的检索式、去重候选集、透明相关性排序与逐条来源证据时使用；产出保留 case_id 与全部 output_contract 字段的 /app/output/result.json。

## Contents

| File |
|------|
| `SKILL.md` |
| `rules.json` |
| `scripts/run_skill.py` |
| `README.md` |

## How to run

```bash
python scripts/run_skill.py --case /input/case.json --output /app/output/result.json
```

The entry point is `SKILL.md`; input/output contracts, error handling and a minimal
usage example are documented there.

## Notes

- Pure Python standard library; no network access required.
- The result JSON keeps the input case id, the main results, per-item evidence,
  a run status and an uncertainty section.
