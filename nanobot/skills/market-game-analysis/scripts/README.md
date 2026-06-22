# Market Game Scan — 全市场博弈扫描脚本

## 快速使用

```bash
# 期货全扫描（主力合约 + 日线 + 席位 + 推荐）
python market_game_scan.py --mode futures

# 仅看席位分析
python market_game_scan.py --mode futures --seats-only

# A股 Trigger Day 验证
python market_game_scan.py --mode trigger --date 20260622

# 美股博弈
python market_game_scan.py --mode usstock --tickers NVDA,AAPL,TSLA

# 港股博弈
python market_game_scan.py --mode hkstock --tickers 0700.HK,9988.HK

# 全量扫描
python market_game_scan.py --mode all
```

## 覆盖范围

| 模式 | 品种数 | 数据 |
|------|--------|------|
| futures | ~31个主力合约 | 日线 + 席位净持仓 + 量价信号 + 综合评分 |
| trigger | A股全市场 | 涨停/跌停池 + 申万行业 + 北向资金 |
| usstock | 自选 | PE + Short Interest + Borrow Rate + PCR |
| hkstock | 自选 | 南向资金 + 个股行情 |

## 依赖

```
akshare
yfinance
pandas
numpy
```

## 合约月份说明

期货主力合约代码需随时间更新。当前配置为 2026-06 到期的合约：

| 品种 | 代码 | 品种 | 代码 |
|------|------|------|------|
| 铁矿石 | I2609 | 黄金 | AU2608 |
| 螺纹钢 | RB2609 | 白银 | AG2608 |
| 热卷 | HC2609 | 沪铜 | CU2607 |
| 焦炭 | J2609 | 沪铝 | AL2607 |
| 焦煤 | JM2609 | 沪镍 | NI2607 |
| 豆粕 | M2609 | 沪锡 | SN2607 |
| 白糖 | SR2609 | 原油 | SC2608 |
| 甲醇 | MA2609 | 橡胶 | RU2609 |
| PTA | TA2609 | 碳酸锂 | LC2607 |

**换月时**：将合约代码中的月份后缀（如 2609 → 2607）更新为当前活跃月份。
