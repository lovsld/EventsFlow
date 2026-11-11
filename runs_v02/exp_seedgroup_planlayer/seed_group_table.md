# 种子组对照表（rule/mock 确定性轨迹）

模式：rule；tick 数：20；种子：42, 7, 101, 2024, 555

| run | 种子 | 事件 | 士兵读信 | 商人听闻信 | 老板知信外泄 | 士兵信镇长贪腐 | 预测 | 证伪 | 信念变化 | 长期记忆晋升 |
|---|---|---|---|---|---|---|---|---|---|---|
| rule_seed42 | 42 | 87 | 1.00/experienced | 0.70/heard | 0.90/inferred | 0.60/heard | 60 | 22 | 7 | 10 |
| rule_seed7 | 7 | 87 | 1.00/experienced | 0.70/heard | 0.90/inferred | 0.60/heard | 60 | 22 | 5 | 5 |
| rule_seed101 | 101 | 85 | 1.00/experienced | 0.70/heard | 0.90/inferred | 0.60/heard | 60 | 19 | 4 | 6 |
| rule_seed2024 | 2024 | 84 | 1.00/experienced | 0.70/heard | 0.90/inferred | 0.60/heard | 60 | 11 | 3 | 5 |
| rule_seed555 | 555 | 85 | 1.00/experienced | 0.70/heard | 0.90/inferred | 0.60/heard | 60 | 22 | 6 | 6 |

- 轨迹多样性（两两 Jaccard 距离均值，(tick,类型，主体） 事件集合）：**0.7357**
- 关键结果一致性（士兵最终亲读信）：**5/5**（1.0）
- 总事件数：min 84 / mean 85.6 / max 87
