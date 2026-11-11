# 反事实对照：士兵是否听到商人的镇长付款发言

种子 42，20 tick，模式 rule。唯一差别：实验组在 Broker 层抑制投递 {"recipient": "soldier", "actor": "merchant", "claim": ["mayor_late_payment", "answer_mayor"]}，客观事件本身不变。

## 目标发言的出现与投递（按组各自认定）

对照组中该发言的出现：

| 事件 | tick | 投递 |
|---|---|---|
| E0019（answer_mayor） | 5 | merchant:full, owner:full, soldier:full |
| E0055（answer_mayor） | 14 | merchant:full, owner:full, soldier:full |

实验组中该发言的出现（事件照常发生，仅士兵未收到）：

| 事件 | tick | 投递 |
|---|---|---|
| E0019（answer_mayor） | 5 | merchant:full, owner:full |
| E0070（answer_mayor） | 17 | merchant:full, owner:full |

- 对照组每次出现士兵均完整听到：True；实验组每次出现均被抑制且有审计：True
- 客观事件前缀一致到 tick 8（首个分歧 tick：9）
- 实验组抑制审计记录：2 条；对照组抑制：0 条

## 事件总数

对照组 85；实验组 83；差 -2

## 士兵行动对照

| 指标 | 对照组 | 实验组 |
|---|---|---|
| ask | 3 | 3 |
| confront | 3 | 3 |
| speak | 2 | 3 |
| search | 1 | 1 |
| move | 10 | 9 |
| confront_mayor_accusation | 2 | 2 |
| first_ask_tick | 4 | 4 |
| first_confront_tick | 13 | 16 |
| first_backroom_search_tick | 10 | 13 |
| first_letter_found_tick | 10 | 13 |

## 士兵信念终值对照

| 命题 | 对照组 | 实验组 |
|---|---|---|
| mayor_embezzled | 0.60/heard | 0.60/heard |
| investigation_widening | 0.60/inferred | （无） |
| anonymous_letter_exists | 1.00/experienced | 1.00/experienced |
| owner_hiding_something | 0.65/inferred | （无） |
