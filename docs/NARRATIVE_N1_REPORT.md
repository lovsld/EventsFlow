# 叙事层 N1 阶段报告（Narrative Observer）

> 阶段：N1 Observer（设计 `docs/NARRATIVE_LAYER_DESIGN.md` §4、§8）。
> N0 门：设计稿经 Hu 于 2026-11-21 批准（该批准一并确认 §0 门控状态：
> 以 V0.2-C §十一 3/5/2、诊所判据登记未达的现状启动第二步）。
> 本阶段纯确定性实现，零 LLM 调用，零涌现层改动。结论：**N1 验收门
> 全部通过**，按阶段纪律停工，等 Hu 指示再进 N2。

## 1. 交付物

| 类别 | 文件 |
|---|---|
| 代码（新包，禁 import v02） | `narrative/__init__.py`、`narrative/observer.py`（CLI：`python -m narrative.observer RUN_DIR OUT_DIR`） |
| 测试 | `tests_narrative/test_observer.py`（16 项）、`tests_narrative/run_isolation_experiment.py`（§7.4 验收脚本） |
| 产物（酒馆） | `narrative_runs/rule_v02c4_seed42/`：provenance / observations / frontier / scenes / issues 五件 |
| 产物（灯塔） | `narrative_runs/c5_lighthouse_seed7/`：同上五件 |
| 隔离实验产物 | `narrative_runs/_isolation/`（iso_off / iso_on 两 run + iso_on_observations） |
| 文档 | 设计稿修订记录追加 N0 批准一行（2026-11-21） |

## 2. 验收门逐项（设计 §8 N1 行）

| 验收项 | 结果 | 证据 |
|---|---|---|
| 事件覆盖 1:1 | ✅ | 酒馆 73/73、灯塔 104/104，obs_id 与 world_log 事件逐 id、逐序一致（测试独立重算） |
| deliveries 反查一致 | ✅ | 测试从原始 world_log 独立重建 delivered/muffled 期望集，与观察记录逐条比对全合 |
| 同输入双跑逐字节相同 | ✅ | `test_determinism_double_run`：五件产物逐字节一致（产物内无时间戳） |
| 输入哈希零变化 | ✅ | 两 run 处理前后五文件 sha256 不变；Observer 内置处理后复算，provenance 记 `input_hash_check_after: pass`；测试另行独立复核 |
| import 隔离 | ✅ | 静态 grep 无 `import v02`；子进程导入 narrative 后 sys.modules 无 v02 |
| §7.4 开/关对照实验 | ✅ | rule/seed42/20tick 复跑两次（关 / 开＋Observer 处理），两份 world_log 与彼此、且与冻结规范轨迹逐字节相同，md5 均为 `cbf84d263ad01e50fca711328c1e78e2`；Observer 对 iso_on 的三件核心产物与对规范 run 的产物逐字节相同 |
| 测试总计 | ✅ | tests_narrative **16/16 通过**（`.venv/bin/python -m pytest tests_narrative/ -q`） |
| 涌现层零改动 | ✅ | `v02/` 与 `runs_v02/` 下无文件在本阶段被修改（mtime 核查为空） |

## 3. 第一手观察（供 N2 参考，非结论）

- 两 run 的 `issues.json` 均为空数组——出处交叉核对（run_id /
  scenario_id / total_events / ticks）全合，claim 引用全部能在
  run_meta 场景快照中找到定义，无重复 event_id、无悬空 delivery。
- 角色名解析按设计 §3.2 落地：character_id 在全部 `characters*/`
  目录中唯一，来源路径逐个记入 provenance（如 merchant→米拉 ←
  `characters_v02/merchant/profile.yaml`；keeper_zhou→周守义 ←
  `characters_c5/`），无未解析、无歧义。
- 信息前沿终局累计（可知事件数 / 总事件数）：酒馆 owner 62、
  merchant 61、soldier 58（/73）；灯塔 a_gui 73、keeper_zhou 73、
  lin_tao 65（/104）。三人可知集合互不相同——信息不对称在客观
  层可直接量化，N2 的 E2 传递边有据可依。
- 场景段：酒馆 3 段（S2 会话自 tick 5 至 20 未闭合，`open=true`
  如实标记，占 53 个成员事件——三人后半夜一直在吧台同场，是数据
  事实而非分段错误）；灯塔 11 段（S1–S9 会话链 + 2 个 loose 段）。
  场景段只作组织视图，N2 抽边以事件为单位，不受段粒度影响。
- `speak_failed` 4 次（灯塔）全部按定性落地：`text=null`、
  `is_failed_attempt=true`、role=plot——尝试在场、内容未送达。

## 4. 实施中发现并修复的一处缺陷（如实记录）

frontier 初版在同一 tick 内把「自身行动事件」与「delivered 记录」
拼接时未在 tick 内去重，同一事件可在一个 tick 的增量里出现两次
（测试 `test_frontier_monotone_and_grounded` 抓获，两 run 均复现）。
已改为 tick 内先去重再并入累计集，修复后 16/16 全过、产物已按
修复版重新生成。本缺陷只存在于叙事层新代码内，未触及任何既有
产物。

## 5. 未做与下一步

- 未做：Extractor（N2）、Writer（N3）、LLM 的任何调用、GitHub
  推送（本阶段产物只在本地；是否推送由 Hu 另行指示）。
- N2 开工前置（设计 §5.5 已预注册）：金标准人工标注先行落盘
  （灯塔 seed7 全链、酒馆双链、诊所 seed42 断点），然后才写
  Extractor；另有设计 §9.2 的待核项——ask 事件 actors 语义在
  验证集上先抽查再定 E3 判定实现。
