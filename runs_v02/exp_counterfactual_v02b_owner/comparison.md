# 反事实对照：士兵是否听到商人的镇长付款发言

种子 42，20 tick，模式 rule。唯一差别：实验组在 Broker 层抑制投递 {"recipient": "soldier", "actor": "owner", "claim": ["mayor_hint"]}，客观事件本身不变。

## 目标发言的出现与投递（按组各自认定）

对照组中该发言的出现：

| 事件 | tick | 投递 |
|---|---|---|
| E0026（mayor_hint） | 7 | merchant:full, owner:full, soldier:full |
| E0032（mayor_hint） | 8 | merchant:full, owner:full, soldier:full |
| E0040（mayor_hint） | 11 | merchant:full, owner:full, soldier:full |
| E0045（mayor_hint） | 12 | merchant:full, owner:full, soldier:full |
| E0050（mayor_hint） | 14 | merchant:full, owner:full, soldier:full |
| E0058（mayor_hint） | 16 | merchant:full, owner:full, soldier:full |
| E0063（mayor_hint） | 17 | merchant:full, owner:full, soldier:full |
| E0071（mayor_hint） | 20 | merchant:full, owner:full, soldier:full |

实验组中该发言的出现（事件照常发生，仅士兵未收到）：

| 事件 | tick | 投递 |
|---|---|---|
| E0026（mayor_hint） | 7 | merchant:full, owner:full |
| E0032（mayor_hint） | 8 | merchant:full, owner:full |
| E0040（mayor_hint） | 11 | merchant:full, owner:full |
| E0045（mayor_hint） | 12 | merchant:full, owner:full |
| E0050（mayor_hint） | 14 | merchant:full, owner:full |
| E0058（mayor_hint） | 16 | merchant:full, owner:full |
| E0063（mayor_hint） | 17 | merchant:full, owner:full |
| E0071（mayor_hint） | 20 | merchant:full, owner:full |

- 对照组首次出现士兵完整听到：True；对照组全部出现中士兵的投递分布：{'full': 8, 'muffled': 0, 'absent': 0}；实验组每次出现均被抑制且有审计：True
- 客观事件前缀一致到 tick 20（首个分歧 tick：None）
- 实验组抑制审计记录：8 条；对照组抑制：0 条

## 事件总数

对照组 73；实验组 73；差 +0

## 士兵行动对照

| 指标 | 对照组 | 实验组 |
|---|---|---|
| ask | 6 | 6 |
| confront | 6 | 6 |
| speak | 3 | 3 |
| search | 1 | 1 |
| move | 4 | 4 |
| confront_mayor_accusation | 6 | 6 |
| first_ask_tick | 8 | 8 |
| first_confront_tick | 6 | 6 |
| first_backroom_search_tick | 3 | 3 |
| first_letter_found_tick | 3 | 3 |

## 士兵信念终值对照

| 命题 | 对照组 | 实验组 |
|---|---|---|
| mayor_embezzled | 0.70/heard | 0.60/heard |
| investigation_widening | （无） | （无） |
| anonymous_letter_exists | 1.00/experienced | 1.00/experienced |
| owner_hiding_something | （无） | （无） |
