# 种子组对照表（rule/mock 确定性轨迹）

模式：rule；tick 数：20；种子：42, 7, 101, 2024, 555

| run | 种子 | 事件 | 士兵读信 | 商人听闻信 | 老板知信外泄 | 士兵信镇长贪腐 | 预测 | 证伪 | 信念变化 | 长期记忆晋升 |
|---|---|---|---|---|---|---|---|---|---|---|
| rule_seed42 | 42 | 85 | 1.00/experienced | 0.70/heard | 0.90/inferred | 0.60/heard | 60 | 13 | 8 | 11 |
| rule_seed7 | 7 | 87 | 1.00/experienced | 0.70/heard | 0.90/inferred | 0.60/heard | 60 | 23 | 8 | 10 |
| rule_seed101 | 101 | 83 | 1.00/experienced | 0.70/heard | 0.90/inferred | 0.60/heard | 60 | 12 | 5 | 3 |
| rule_seed2024 | 2024 | 80 | 1.00/experienced | 0.70/heard | 0.90/inferred | 0.60/heard | 60 | 14 | 2 | 0 |
| rule_seed555 | 555 | 81 | 1.00/experienced | 0.70/heard | 0.90/inferred | 0.60/heard | 60 | 23 | 5 | 6 |

- 轨迹多样性（两两 Jaccard 距离均值，(tick,类型，主体） 事件集合）：**0.7391**
- 关键结果一致性（士兵最终亲读信）：**5/5**（1.0）
- 总事件数：min 80 / mean 83.2 / max 87
