# 本地部署与运行（本地运行三件套）

ACP Story Simulator V0.2 可以完全脱离 Muse 环境、在自己的机器上用自己的
Gemini API Key 运行。本文档覆盖：部署步骤、三种 Key 配置方式的区别、
中断续跑 `--resume`、额度感知持续循环 `--loop`。

## 1. 部署步骤

```bash
git clone https://github.com/lovsld/EventsFlow.git
cd EventsFlow
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

- rule / mock 模式不需要任何 Key：

```bash
.venv/bin/python main.py --mode rule --ticks 20 --seed 42
.venv/bin/python main.py --mode mock --ticks 20 --seed 42
```

- llm 模式需要 Gemini Key（见下节），运行产物落在 `runs_v02/<run 名>/`：
  `world_log.json`（客观事件日志）、`narrative.md`、`summary.json`、
  `acp.sqlite3`（全审计链）、`characters/<角色>/`（各角色私有记忆与信念）、
  以及三件套新增的 `run_meta.json` 与逐 tick 更新的 `checkpoint.json`。

## 2. 三种 Key 配置方式

优先级从高到低：**原始 Key 池 > 凭据名池 > 单 Key > 凭据库默认**。

| 方式 | 环境变量 | 适用场景 |
|---|---|---|
| 单 Key | `GEMINI_API_KEY`（或 `GOOGLE_API_KEY`） | 本地最简运行，只有一个 Key |
| 原始 Key 池 | `ACP_GEMINI_KEYS` | 本地自备多个 AI Studio Key 轮换摊额度 |
| 凭据名池 | `ACP_GEMINI_CREDENTIALS` | 在 Muse 环境内运行，用连接器里登记的凭据名（不接触 Key 原文） |

### 2.1 单 Key

```bash
export GEMINI_API_KEY='你的Key'
.venv/bin/python main.py --mode llm --ticks 20 --seed 42
```

### 2.2 原始 Key 池（本地推荐）

```bash
export ACP_GEMINI_KEYS='Key一,Key二,Key三'
.venv/bin/python main.py --mode llm --ticks 20 --seed 42
```

行为：

- 请求直接带当前 Key 发出；某个 Key 返回 429/403 时，它会被标记为
  本进程内耗尽并自动轮换到下一个 Key（404 同样轮换——新账号可能
  无权调用某些模型）。
- 全池都 429 时，本次决策以结构化额度错误失败（见 `--loop`）。
- **安全约定**：代码与日志只用序号指代 Key（`key#1`、`key#2`…）；
  Key 原文只存在于进程内存与请求头中，绝不写入日志、异常文本、
  数据库或任何运行产物（有哨兵 Key 测试断言这一点）。

怎么建池：到 [Google AI Studio](https://aistudio.google.com/apikey)
为**每个 Google 账号**各创建一个 API Key（同一账号多建 Key 不增加
免费额度，额度是按账号/项目走的），把多个账号的 Key 用逗号拼起来
即可。不同代次的账号能调用的模型可能不同（较新的账号调不了
gemini-2.5 系列时返回 404，会被自动跳过）。

### 2.3 凭据名池（Muse 环境）

```bash
export ACP_GEMINI_CREDENTIALS='custom.gemini,custom.gemini-2'
```

Key 保存在 Muse 的安全凭据里，代码永远看不到原文（走代理替换）。
本地裸跑没有这个凭据库，用 2.1 / 2.2 即可。

### 2.4 免费额度的事实

- 免费额度**按 Key（账号）按天**计算，多个 Key 就是多天的量摊开用，
  号池不会凭空变出无限额度。
- 额度在**日本时间每天 17:00（JST）**重置（即太平洋时间午夜）。
- 限流还与模型、当时的整体负载有关：慢速、拉开间隔的调用更稳。
  `ACP_GEMINI_MIN_INTERVAL` 可调每个模型的最小调用间隔（秒），也可按
  模型单独设 `ACP_GEMINI_MIN_INTERVAL_<模型名大写下划线>`；不同角色
  可用 `ACP_MODEL_OWNER` / `ACP_MODEL_MERCHANT` / `ACP_MODEL_SOLDIER`
  分配不同模型，把负载摊到不同模型的额度上。

## 3. 中断续跑：--resume

每次运行都会逐 tick 写检查点（`checkpoint.json`：世界状态、各角色
主观状态、在飞的预测、全部 RNG 状态等），`world_log.json` 也逐 tick
增量更新。因此进程被杀、机器重启、额度耗尽停机之后，都可以从最后
一个**已提交**的 tick 继续：

```bash
.venv/bin/python main.py --mode rule --ticks 20 --run-name night1
# ……进程在第 7 个 tick 后被中断……
.venv/bin/python main.py --resume night1 --ticks 13
```

- `--ticks N` 表示**再续跑 N 个 tick**，追加进同一个 run 目录与同一
  个 SQLite，tick 编号连续、不重复、不覆盖。
- `--resume` 时可省略 `--mode`，自动用该 run 创建时的模式；显式给出
  与 `run_meta.json` 不符的 mode 会被拒绝并说明。seed 以 run 的记录
  为准（所有 RNG 状态都从检查点恢复，seed 只剩标签意义）。
- 已完成的 run 也可以续跑（相当于延长它）。
- 一致性保证（有硬验收测试）：rule 模式下「不间断 12 tick」与
  「7 tick + 续跑 5 tick」的 `world_log.json` 逐字一致，审计表计数
  也一致；SIGKILL 中断后同样成立。

## 4. 额度感知持续循环：--loop

```bash
export ACP_GEMINI_KEYS='Key一,Key二,Key三'
.venv/bin/python main.py --mode llm --ticks 60 --loop
```

`--loop` 下 `--ticks` 是本次调用要推进的 tick 数。当某一 tick 的决策
因**全池额度耗尽**（结构化 `QuotaExhausted` 信号，按异常类型判定、
不靠字符串猜测）而失败时：

1. **不提交该 tick**——世界不推进，降级 wait 不会被当作该 tick 的
   结果，失败尝试不落任何审计行；
2. 先落检查点，然后等待 `ACP_QUOTA_SLEEP_MINUTES`（默认 20 分钟）
   后**重试同一个 tick**；
3. 可重复等待，总等待（全 run 墙钟累计）上限
   `ACP_QUOTA_MAX_WAIT_HOURS`（默认 12 小时）；超限则正常退出，
   `summary.json` 里 `quota_stopped: true`，命令行也会提示——之后用
   `--resume` 接着跑即可。

非额度错误（单次 500、超时、输出不合规等）不触发暂停，沿用原有的
降级语义（该角色本 tick 改为 wait，错误如实记录在审计库中）。

相关环境变量：

| 变量 | 默认 | 含义 |
|---|---|---|
| `ACP_QUOTA_SLEEP_MINUTES` | 20 | 每次额度等待的分钟数 |
| `ACP_QUOTA_MAX_WAIT_HOURS` | 12 | 一次运行累计等待上限（小时） |

配合额度在 17:00 JST 重置的节奏：睡 20 分钟重试一次，远比耗尽后
当场放弃更能把长 run 拖过重置点；上限 12 小时保证不会无限挂着。

## 5. 组合用法示例

```bash
# 本地长跑：号池 + 持续循环，一次调用尽量跑满 60 tick
export ACP_GEMINI_KEYS='Key一,Key二,Key三'
.venv/bin/python main.py --mode llm --ticks 60 --run-name long_night --loop

# 中途停机/被杀后，随时从检查点续跑（仍可带 --loop）
.venv/bin/python main.py --resume long_night --ticks 20 --loop
```
