# 反事实对照：士兵是否听到商人的镇长付款发言

种子 42，20 tick，模式 rule。唯一差别：实验组在 Broker 层抑制投递 {"recipient": "soldier", "actor": "merchant", "claim": ["mayor_late_payment", "answer_mayor"]}，客观事件本身不变。

## 目标发言的出现与投递（按组各自认定）

对照组中该发言的出现：

| 事件 | tick | 投递 |
|---|---|---|
| E0019（answer_mayor） | 5 | merchant:full, owner:full, soldier:full |
| E0033（answer_mayor） | 8 | merchant:full, owner:full |
| E0063（answer_mayor） | 15 | merchant:full, owner:full, soldier:full |

实验组中该发言的出现（事件照常发生，仅士兵未收到）：

| 事件 | tick | 投递 |
|---|---|---|
| E0019（answer_mayor） | 5 | merchant:full, owner:full |
| E0027（answer_mayor） | 7 | merchant:full, owner:full |
| E0077（answer_mayor） | 19 | merchant:full, owner:full |

- 对照组首次出现士兵完整听到：True；对照组全部出现中士兵的投递分布：{'full': 2, 'muffled': 0, 'absent': 1}；实验组每次出现均被抑制且有审计：True
- 客观事件前缀一致到 tick 5（首个分歧 tick：6）
- 实验组抑制审计记录：3 条；对照组抑制：0 条

## 事件总数

对照组 87；实验组 81；差 -6

## 士兵行动对照

| 指标 | 对照组 | 实验组 |
|---|---|---|
| ask | 2 | 3 |
| confront | 3 | 2 |
| speak | 2 | 4 |
| search | 1 | 1 |
| move | 11 | 8 |
| confront_mayor_accusation | 1 | 1 |
| first_ask_tick | 4 | 4 |
| first_confront_tick | 11 | 18 |
| first_backroom_search_tick | 8 | 15 |
| first_letter_found_tick | 8 | 15 |

## 士兵信念终值对照

| 命题 | 对照组 | 实验组 |
|---|---|---|
| mayor_embezzled | 0.60/heard | 0.60/heard |
| investigation_widening | 0.60/inferred | （无） |
| anonymous_letter_exists | 1.00/experienced | 1.00/experienced |
| owner_hiding_something | 0.65/inferred | （无） |
