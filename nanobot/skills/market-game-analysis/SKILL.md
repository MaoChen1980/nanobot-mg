---
name: market-game-analysis
description: >
  市场博弈结构分析工具：通过外显数据（量/价/持仓/席位变化）推测幕后各方动机和实力对比，判断力量走向。
  当用户询问期货品种多空分析、A股大盘/板块博弈、主力持仓结构、席位变化、资金流向时，必须使用此 Skill。
  当用户询问某个品种/板块是否有机会、方向判断、做多还是做空时，必须使用此 Skill。
  关键词：多空、博弈、主力、持仓、席位、空头、多头、期货、A股、板块、机会、方向。
always: false
---

# 市场博弈结构分析（Market Game Analysis）

## 核心理念

**"听过其言，不如观其行。"**

- 成交量、持仓量、价格变化是实力的唯一可靠外显信号。
- 叙事/基本面/政策解读都是噪音——只有行为才是信号。
- 市场无客观真理，只有力量对比。

---

## 一键执行

```bash
# 期货全扫描（主力合约 + 日线 + 席位 + 推荐）
python workspace/skills/market-game-analysis/scripts/market_game_scan.py --mode futures

# A股 Trigger Day 验证
python workspace/skills/market-game-analysis/scripts/market_game_scan.py --mode trigger --date 20260622

# 美股博弈
python workspace/skills/market-game-analysis/scripts/market_game_scan.py --mode usstock --tickers NVDA,AAPL

# 港股博弈
python workspace/skills/market-game-analysis/scripts/market_game_scan.py --mode hkstock --tickers 0700.HK,9988.HK

# 全量扫描（期货 + A股）
python workspace/skills/market-game-analysis/scripts/market_game_scan.py --mode all
```

---

## 核心框架：三问穿透法

```
看到动作 → 追问谁做的、为什么做、能不能持续 → 判断方向 → 站在实力强的那边
```

### 三问

1. **谁在做？** —— 找到背后最大的玩家（国家队/产业资本/聪明钱/散户）
2. **为什么做？** —— 动机是什么（套保/投机/护盘/撤退）
3. **有没有实力持续？** —— 资金够不够，动机强不强

---

## 量价背离核心信号

| 信号 | 含义 |
|------|------|
| 持仓量↑ + 价格↑ | 多头主动加仓，上涨趋势健康 |
| 持仓量↑ + 价格↓ | 空头主动加仓，下跌趋势健康（空头控盘） |
| 持仓量↓ + 价格↑ | ⚠️ 空头平仓推动（弱多），警惕假突破 |
| 持仓量↓ + 价格↓ | ⚠️ 多空双平，趋势可能衰竭 |
| 成交量暴增 + 价格暴跌 | 多头踩踏 or 空头强力砸盘 |
| 成交量缩 + 价格跌 | 空头控盘，多头不接盘 |

---

## 期货品种分析

### 数据获取

```python
import akshare as ak

# 日线行情（量/价/持仓）
df = ak.futures_zh_daily_sina(symbol='AU2608')   # 黄金主力
df = ak.futures_zh_daily_sina(symbol='RB2609')   # 螺纹钢主力
df = ak.futures_zh_daily_sina(symbol='I2609')    # 铁矿石主力

# 多空持仓（席位级别）
dl = ak.futures_hold_pos_sina(symbol='多单持仓', contract='AU2608', date='20260622')
ds = ak.futures_hold_pos_sina(symbol='空单持仓', contract='AU2608', date='20260622')
```

### ⚠️ 关键规则

1. **主力合约代码**：不能用 X0 系列（如 IF0/RB0/I0），数据严重失真。
   - 正确：`RB2609`（螺纹钢 2026年9月合约）
   - 错误：`RB0`（已过期或数据拼接错误）
2. **当前主力合约**（2026-06）：
   - 黑色系：I2609 / RB2609 / HC2609 / J2609 / JM2609
   - 有色：CU2607 / AL2607 / ZN2607 / NI2607 / SN2607
   - 贵金属：AU2608 / AG2608
   - 能化：SC2608 / RU2609 / MA2609 / TA2609 / FU2609
   - 农产品：M2609 / Y2609 / P2609 / SR2609 / CF2609 / RM2609 / OI2609

### 持仓结构分析步骤

1. 拉取日线 → 看价格趋势 + 成交量 + 持仓量变化
2. 拉取多空持仓 → 计算前5合计多空比
3. 追踪席位变化 → 重点关注增减仓最大的会员
4. 判断多空是否分化 → 同一会员多空态度？
5. 结合量价信号 → 持仓量涨+价格跌=空头控盘

### 空头控盘教科书信号

```
铁矿石 I2609（2026-06-22）：
- 国泰君安净空 58,666 手，6/22 加空 5,141 手
- 空头继续加仓 + 价格下跌 = 空头主动控盘
→ 持仓量↑ + 价格↓ = 空头主动加仓 = 空头控盘
```

---

## A股大盘分析

### 核心框架：谁是大庄

**A股大庄 = 国家队（汇金/社保/证金）**

| 资金 | 特点 |
|------|------|
| 国家队 | 维护稳定，不是盈利，可以无限期持有 |
| 社保基金 | 超长周期，加仓AI算力/光伏/化工 |
| 汇金 | 重仓银行，守金融 |
| 外资（北向）| 最接近聪明钱，持续性有限 |

### 数据获取

```python
# 指数数据
df = ak.index_zh_a_hist(symbol='000001', period='daily', start_date='20260501', end_date='20250621')

# 北向资金
bx = ak.stock_hsgt_north_net_flow_in_em(symbol='北向资金', indicator='今日')

# 板块涨跌（申万）
sw = ak.stock_board_industry_spot_em()
```

---

## Trigger Day 观察法（单日异动验证）

### 何时使用

- 用户描述单日异常行情："今天 X 板块 +5%"
- 用户问"这算不算突破 / 起爆 / 见顶"
- 用户对单日信号做归因，需要验证信号真假

### 铁律 1：单日异常信号需放入前后 5 日窗口验证

**Trigger Day 判定矩阵：**

| 前置趋势 (T-5~T-1) | T 日信号 | T+1~T+5 验证 | 结论 |
|---|---|---|---|
| 上行（MA5>MA20） | + 大涨/突破 | 续涨 / 不破 T 低 | ✅ 真突破 / 主升 |
| 上行 | + 大涨 | 快速回吐 / 跌破 | ⚠️ 末端加速 / 警惕 |
| 震荡 | ± 大涨 | 续涨突破前高 | ✅ 变盘向上 |
| 震荡 | ± 大涨 | 快速回落 | ❌ 假突破 / 诱多 |
| 下行（MA5<MA20） | + 大涨 | 1-2日续涨后回落 | ⚠️ 超跌反弹（弱）|
| 下行 | - 大跌 / 破位 | 续跌 / 放量阴线 | ❌ 真破位 / 趋势恶化 |

### 铁律 2：板块判断必须用行业指数，不用单只 ETF

- ✅ 正确：查申万行业指数 / 同花顺 BK 开头指数（如 BK0475 半导体）
- ❌ 错误：用单只行业 ETF（如 512480）代表板块涨跌

---

## 股指期货分析（IF / IC / IM / IH）

### ⚠️ 核心认知：股指期货是保险工具，不是方向指标

机构用股指期货做什么？
1. 持有股票现货 → 卖出期货做空对冲（持仓量高 = 都在买保险）
2. 市场风险可控 → 平掉期货空单，不需要保险了（持仓量下降）

**所以持仓量下降的正确解读：**
> 机构觉得大盘跌不动了，保险需求下降。**不是"不看好"，而是"不需要对冲了"。**

### 股指期货量价信号

| 信号 | 含义 |
|------|------|
| 持仓量↑ + 价格↑ | 真多头加仓 OR 机构加保险（需结合现货判断） |
| 持仓量↑ + 价格↓ | 空头加仓（投机性做空），市场偏弱 |
| 持仓量↓ + 价格↑ | ✅ 偏多信号：机构平套保空单，推高期货价格 |
| 持仓量↓ + 价格↓ | ✅ 偏多信号：机构觉得风险可控，不需要保险 |
| 成交额暴增 | 方向选择临近 |

---

## 美股博弈分析

### 数据获取

```python
import yfinance as yf

nvda = yf.Ticker('NVDA')
info = nvda.info
pe = info.get('forwardPE')
short_float = info.get('shortPercentOfFloat')  # 做空比例
borrow_rate = info.get('borrowRate')           # 借券费率

# 期权 PCR
opts = nvda.options
chain = nvda.option_chain(opts[0])
pcr_vol = chain.puts['volume'].sum() / chain.calls['volume'].sum()
pcr_oi  = chain.puts['openInterest'].sum() / chain.calls['openInterest'].sum()
```

### 博弈指标

| 指标 | 含义 |
|------|------|
| Short Interest / Float > 20% | 做空比例高，空头情绪浓 |
| Borrow Rate 升高 + 做空比例下降 | 空头被迫平仓，反弹信号 |
| PCR > 1.2 | 看跌情绪浓 |
| PCR < 0.7 | 看涨情绪浓 |
| IV Rank > 70 + 价格高位 | 期权溢价贵，机构在买保险=警惕 |

---

## 港股博弈分析

### 数据获取

```python
import yfinance as yf

# 港股日线
df = yf.download('0700.HK', period='1mo', interval='1d')

# 南向资金
sx = ak.stock_hsgt_north_net_flow_in_em(symbol='南向资金', indicator='今日')
```

---

## 推荐输出格式

执行完脚本后，按以下格式输出结论：

```
## [品种] 博弈结构分析
**数据：XXXX年XX月XX日**

### 日线走势（近5天）
[表格：日期/收盘/成交量/持仓量]

### 多空持仓
| 排名 | 多方会员 | 多单 | 变化 | 空方会员 | 空单 | 变化 |
|------|---------|------|------|---------|------|------|
| 1 | xxx | xxx | ±xxx | xxx | xxx | ±xxx |

### 综合判断
| 维度 | 信号 | 方向 |
|------|------|------|
| 多空结构 | xxx | ↑/↓ |
| 量价信号 | xxx | ↑/↓ |
| 席位变化 | xxx | ↑/↓ |

**结论：[方向]，[目标位]，止损[位]**
```

---

## 常见误区

| 误区 | 正确做法 |
|------|---------|
| "超跌=要反弹" | 超跌只是叙事，反弹需要资金接盘 |
| "国家队护盘=会涨" | 国家队护的是稳定，不是帮你赚钱 |
| "持仓量下降=空头撤退=不看好方向" | 持仓量下降 = 机构平仓对冲空单 = 市场风险可控（偏多信号） |
| "用 ETF 代表板块涨跌" | 必须用申万行业指数 / 同花顺 BK 指数 |
| "用 IF0/IC0 等 X0 系列合约" | 必须用具体月份合约（IF2606） |

---

## Verification

- [ ] 分析时有明确的"谁在做、为什么、能不能持续"三问
- [ ] 期货分析包含：主力合约（非X0）+ 日线 + 多空持仓 + 量价背离检查
- [ ] A股分析包含：国家队动向 + 外资 + 成交额 + 板块分化
- [ ] 结论有方向、有目标位（或区间）、有止损位
- [ ] 区分"有机会"和"该行动"——不推荐盲目抄底
- [ ] 输出格式结构清晰，包含多空力量量化对比
- **Self-optimization**: 此 Skill 可自我进化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
  - **Always 审查**：如果此 skill 的行为已成为 LLM 自然倾向（不注入也会正确执行），将 frontmatter 的 `always: true` 改为 `false`，反之如果发现此 skill 值得每轮注入，改为 `always: true`
