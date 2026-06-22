#!/usr/bin/env python3
"""
market_game_scan.py — 全市场博弈扫描器
=========================================
覆盖：期货（商品+金融）、A股大盘、A股板块、个股

使用方法：
    python market_game_scan.py --mode futures         # 期货全扫描
    python market_game_scan.py --mode futures --contracts I2609,RB2609  # 指定合约
    python market_game_scan.py --mode trigger --date 20260622          # trigger day 验证
    python market_game_scan.py --mode ashare                                 # A股大盘+板块
    python market_game_scan.py --mode usstock --tickers NVDA,AAPL          # 美股
    python market_game_scan.py --mode hkstock --tickers 0700.HK,9988.HK   # 港股
    python market_game_scan.py --mode all                                    # 全量扫描

依赖：akshare, yfinance, pandas, numpy
"""

import argparse
import sys
import time
import json
from datetime import datetime, timedelta

# ─────────────────────────────────────────────
# 1. 期货扫描核心
# ─────────────────────────────────────────────

def scan_futures_all():
    """期货全品种扫描：主力合约发现 → 日线 → 席位 → 排序输出"""
    import akshare as ak
    import pandas as pd

    print("\n" + "="*70)
    print("期货全品种 Market Game 扫描")
    print("="*70)

    # ── Step 1: 主力合约发现 ──────────────────
    # 已知的主力月份（可从 futures_contract_info_* 动态获取）
    # 这里硬编码 2026-06 的活跃合约，换月时更新年月
    contracts = [
        # 黑色
        ("I2609",  "铁矿石",  "dce"),
        ("RB2609", "螺纹钢",  "shfe"),
        ("HC2609", "热卷",    "shfe"),
        ("J2609",  "焦炭",    "dce"),
        ("JM2609", "焦煤",    "dce"),
        # 有色
        ("CU2607", "沪铜",    "shfe"),
        ("AL2607", "沪铝",    "shfe"),
        ("ZN2607", "沪锌",    "shfe"),
        ("NI2607", "沪镍",    "shfe"),
        ("PB2607", "沪铅",    "shfe"),
        ("SN2607", "沪锡",    "shfe"),
        ("SS2607", "不锈钢",  "shfe"),
        # 贵金属
        ("AU2608", "黄金",    "shfe"),
        ("AG2608", "白银",    "shfe"),
        # 能化
        ("SC2608", "原油",    "ine"),
        ("RU2609", "橡胶",    "shfe"),
        ("FU2609", "燃料油",  "shfe"),
        ("BU2606", "沥青",    "shfe"),
        # 化工
        ("MA2609", "甲醇",    "czce"),
        ("TA2609", "PTA",     "czce"),
        ("EG2609", "乙二醇",  "dce"),
        ("PP2609", "聚丙烯",  "dce"),
        ("PE2609", "聚乙烯",  "dce"),
        ("PVC2609","PVC",     "dce"),
        ("PF2609", "短纤",    "czce"),
        # 农产品
        ("M2609",  "豆粕",    "dce"),
        ("Y2609",  "豆油",    "dce"),
        ("P2609",  "棕榈油",  "dce"),
        ("SR2609", "白糖",    "czce"),
        ("CF2609", "棉花",    "czce"),
        ("RM2609", "菜粕",    "czce"),
        ("OI2609", "菜油",    "czce"),
        # 广期所
        ("LC2607", "碳酸锂",  "gfex"),
    ]

    results = []
    failed = []

    for code, name, exchange in contracts:
        try:
            time.sleep(0.15)
            df = ak.futures_zh_daily_sina(symbol=code)
            if df is None or len(df) < 2:
                failed.append((name, code, "数据不足"))
                continue

            last  = df.iloc[-1]
            prev  = df.iloc[-2]
            chg_1d = (last['close'] - prev['close']) / prev['close'] * 100

            # 近5日涨跌
            if len(df) >= 5:
                chg_5d = (last['close'] - df.iloc[-5]['close']) / df.iloc[-5]['close'] * 100
            else:
                chg_5d = chg_1d

            results.append({
                'code': code, 'name': name, 'exchange': exchange,
                'close': float(last['close']),
                'vol':   int(last['volume']),
                'oi':    int(last['hold']),
                'chg_1d': chg_1d,
                'chg_5d': chg_5d,
            })
        except Exception as e:
            failed.append((name, code, str(e)[:60]))

    # 按持仓量排序
    results.sort(key=lambda x: x['oi'], reverse=True)

    print(f"\n✅ 成功: {len(results)} 个 | ❌ 失败: {len(failed)} 个")
    print(f"\n{'品种':8s} {'合约':8s} {'收盘':>12s} {'1日涨跌':>8s} {'5日涨跌':>8s} {'成交量':>10s} {'持仓量':>10s}")
    print("-"*75)
    for r in results:
        print(f"{r['name']:8s} {r['code']:8s} {r['close']:>12.2f} {r['chg_1d']:>+8.2f}% {r['chg_5d']:>+8.2f}% {r['vol']:>10,} {r['oi']:>10,}")

    if failed:
        print(f"\n❌ 失败列表:")
        for n, c, e in failed:
            print(f"  {n}({c}): {e}")

    return results


def scan_futures_seats(target_codes=None):
    """席位级别多空分析（批量）"""
    import akshare as ak

    if target_codes is None:
        # 重点关注品种
        target_codes = [
            ("I2609",  "铁矿石"),
            ("RB2609", "螺纹钢"),
            ("M2609",  "豆粕"),
            ("CF2609", "棉花"),
            ("SR2609", "白糖"),
            ("JM2609", "焦煤"),
            ("RU2609", "橡胶"),
            ("MA2609", "甲醇"),
            ("TA2609", "PTA"),
            ("AU2608", "黄金"),
            ("AG2608", "白银"),
            ("CU2607", "沪铜"),
            ("AL2607", "沪铝"),
            ("SC2608", "原油"),
            ("PP2609", "聚丙烯"),
            ("EG2609", "乙二醇"),
            ("FU2609", "燃料油"),
            ("Y2609",  "豆油"),
            ("P2609",  "棕榈油"),
            ("OI2609", "菜油"),
        ]

    today = "20260622"  # 可从参数传入

    print("\n" + "="*70)
    print("期货席位多空分析")
    print("="*70)

    all_seats = []

    for code, name in target_codes:
        try:
            time.sleep(0.4)
            dl = ak.futures_hold_pos_sina(symbol="多单持仓", contract=code, date=today)
            ds = ak.futures_hold_pos_sina(symbol="空单持仓", contract=code, date=today)

            if dl is None or ds is None or len(dl) == 0 or len(ds) == 0:
                print(f"\n【{name}({code})】 席位数据为空")
                continue

            top5_l = dl.head(5)
            top5_s = ds.head(5)

            sum_l = top5_l['多单持仓'].sum()
            sum_s = top5_s['空单持仓'].sum()
            chg_l = top5_l['比上交易增减'].sum()
            chg_s = top5_s['比上交易增减'].sum()
            net = sum_l - sum_s

            # 量价结构：需要持仓量数据
            try:
                df = ak.futures_zh_daily_sina(symbol=code)
                if df is not None and len(df) >= 2:
                    price_chg = (df.iloc[-1]['close'] - df.iloc[-2]['close']) / df.iloc[-2]['close'] * 100
                    oi_now = int(df.iloc[-1]['hold'])
                    oi_prev = int(df.iloc[-2]['hold']) if len(df) >= 2 else oi_now
                    oi_chg = (oi_now - oi_prev) / oi_prev * 100 if oi_prev > 0 else 0
                else:
                    price_chg, oi_chg = 0, 0
            except:
                price_chg, oi_chg = 0, 0

            # 量价信号判断
            if price_chg > 0 and oi_chg > 0:
                price_signal = "多头主动进攻"
            elif price_chg < 0 and oi_chg > 0:
                price_signal = "空头主动进攻"
            elif price_chg > 0 and oi_chg < 0:
                price_signal = "空头平仓推动（弱多）"
            elif price_chg < 0 and oi_chg < 0:
                price_signal = "多空双平（震荡）"
            else:
                price_signal = "主力观望"

            ratio = sum_l / sum_s if sum_s > 0 else 0

            # 判断力量方向
            if net > 0:
                direction = f"多头优势 {net:+,}手"
            else:
                direction = f"空头优势 {abs(net):+,}手"

            # 综合评分（简单版）
            score = 0
            if ratio > 1.2: score += 2
            elif ratio < 0.8: score -= 2
            if chg_l > 0 and chg_s < 0: score += 1  # 多加空减
            if chg_l < 0 and chg_s > 0: score -= 1  # 多减空加
            if abs(oi_chg) > 5: score += 1 if oi_chg > 0 else -1

            print(f"\n【{name}({code})】")
            print(f"  Top5 多:{sum_l:>8,} 手  空:{sum_s:>8,} 手  净:{net:>+9,} 手  多/空比:{ratio:.3f}")
            print(f"  6/22变化: 多{chg_l:>+8,.0f}  空{chg_s:>+8,.0f}")
            print(f"  量价信号: {price_signal} | 价格{price_chg:+.2f}% | OI{oi_chg:+.1f}%")
            print(f"  席位方向: {direction}")
            print(f"  综合评分: {score:+d} ({'偏多' if score>0 else '偏空' if score<0 else '中性'})")

            # 打印前3大多空席位
            for _, row in top5_l.head(3).iterrows():
                print(f"    多:{row['会员简称']:10s} {row['多单持仓']:>7,} ({row['比上交易增减']:>+8.0f})")
            for _, row in top5_s.head(3).iterrows():
                print(f"    空:{row['会员简称']:10s} {row['空单持仓']:>7,} ({row['比上交易增减']:>+8.0f})")

            all_seats.append({
                'code': code, 'name': name,
                'sum_l': int(sum_l), 'sum_s': int(sum_s),
                'net': int(net), 'ratio': ratio,
                'chg_l': float(chg_l), 'chg_s': float(chg_s),
                'price_chg': price_chg, 'oi_chg': oi_chg,
                'price_signal': price_signal, 'score': score,
            })

        except Exception as e:
            print(f"\n【{name}({code})】 失败: {str(e)[:80]}")

    if all_seats:
        print("\n" + "="*70)
        print("席位综合排序（按 score）")
        print("="*70)
        all_seats.sort(key=lambda x: x['score'], reverse=True)
        for i, s in enumerate(all_seats, 1):
            print(f"{i:2d}. {s['name']:8s}({s['code']}) score={s['score']:+d} | "
                  f"多/空={s['ratio']:.3f} | {s['price_signal']} | {s['net']:+,}手净")

    return all_seats


def futures_recommend():
    """
    期货综合推荐
    基于日线 + 席位的双重信号，输出推荐
    """
    seats = scan_futures_seats()

    print("\n" + "="*70)
    print("期货 Market Game 推荐结论")
    print("="*70)

    buy_candidates = [s for s in seats if s['score'] >= 1]
    short_candidates = [s for s in seats if s['score'] <= -1]
    neutral = [s for s in seats if s['score'] == 0]

    print(f"\n🟢 做多候选 ({len(buy_candidates)} 个):")
    if buy_candidates:
        for s in buy_candidates:
            print(f"  {s['name']}({s['code']}): score={s['score']} | 多/空比={s['ratio']:.3f} | {s['price_signal']}")
    else:
        print("  无")

    print(f"\n🔴 做空候选 ({len(short_candidates)} 个):")
    if short_candidates:
        for s in short_candidates:
            print(f"  {s['name']}({s['code']}): score={s['score']} | 多/空比={s['ratio']:.3f} | {s['price_signal']}")
    else:
        print("  无")

    print(f"\n⚪ 中性 ({len(neutral)} 个):")
    if neutral:
        for s in neutral:
            print(f"  {s['name']}({s['code']}): score=0 | {s['price_signal']}")


# ─────────────────────────────────────────────
# 2. A股 Trigger Day 扫描
# ─────────────────────────────────────────────

def scan_ashare_trigger(date=None):
    """
    A股 Trigger Day 扫描
    验证前后 5 窗口，判断单日信号真伪
    """
    import akshare as ak
    import pandas as pd

    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    print("\n" + "="*70)
    print(f"A股 Trigger Day 扫描 — {date}")
    print("="*70)

    # 涨停/跌停池
    try:
        zt = ak.stock_zt_pool_em(date=date)
        dt = ak.stock_dt_pool_em(date=date)
        print(f"\n📊 涨停: {len(zt)} 只 | 跌停: {len(dt)} 只")
    except Exception as e:
        print(f"\n涨跌停池获取失败: {e}")
        zt, dt = [], []

    # 申万行业涨跌
    try:
        sw = ak.stock_board_industry_spot_em()
        sw_top5 = sw.nlargest(5, '涨跌幅')[['板块名称','涨跌幅','成交量']]
        sw_bot5 = sw.nsmallest(5, '涨跌幅')[['板块名称','涨跌幅','成交量']]
        print(f"\n申万行业涨幅TOP5:")
        for _, r in sw_top5.iterrows():
            print(f"  {r['板块名称']:15s} {r['涨跌幅']:+.2f}%")
        print(f"\n申万行业跌幅TOP5:")
        for _, r in sw_bot5.iterrows():
            print(f"  {r['板块名称']:15s} {r['涨跌幅']:+.2f}%")
    except Exception as e:
        print(f"\n申万行业数据获取失败: {e}")

    # 北向资金
    try:
        bx = ak.stock_hsgt_north_net_flow_in_em(symbol="北向资金", indicator="今日")
        if bx is not None and len(bx) > 0:
            latest = bx.iloc[-1]
            print(f"\n北向资金: {latest.get('今日净流入','?')} 亿元")
    except Exception as e:
        print(f"\n北向资金获取失败: {e}")

    return zt, dt


# ─────────────────────────────────────────────
# 3. 美股博弈扫描
# ─────────────────────────────────────────────

def scan_usstocks(tickers=None):
    """美股博弈扫描：PE + Short Interest + PCR"""
    try:
        import yfinance as yf
    except ImportError:
        print("❌ yfinance 未安装: pip install yfinance")
        return

    if tickers is None:
        tickers = ["SPY", "QQQ", "NVDA", "AAPL", "TSLA", "AMD", "META"]

    print("\n" + "="*70)
    print("美股 Market Game 扫描")
    print("="*70)

    for ticker in tickers:
        try:
            yf_t = yf.Ticker(ticker)
            info = yf_t.info

            pe = info.get('forwardPE', 'N/A')
            short_float = info.get('shortPercentOfFloat', 'N/A')
            borrow_rate = info.get('borrowRate', 'N/A')
            price = info.get('currentPrice', info.get('regularMarketPrice', 'N/A'))

            # PCR from options
            try:
                opts = yf_t.options
                if opts:
                    chain = yf_t.option_chain(opts[0])
                    call_vol = chain.calls['volume'].sum()
                    put_vol = chain.puts['volume'].sum()
                    pcr_vol = put_vol / call_vol if call_vol > 0 else 0
                    call_oi = chain.calls['openInterest'].sum()
                    put_oi = chain.puts['openInterest'].sum()
                    pcr_oi = put_oi / call_oi if call_oi > 0 else 0
                else:
                    pcr_vol, pcr_oi = 0, 0
            except:
                pcr_vol, pcr_oi = 0, 0

            # 信号判断
            signals = []
            if isinstance(borrow_rate, (int, float)) and borrow_rate > 0.05:
                signals.append("借券费率高(空头成本高)")
            if isinstance(short_float, (int, float)) and short_float > 0.2:
                signals.append(f"做空比例高({short_float:.1%})")
            if pcr_vol > 1.2:
                signals.append(f"PCR成交量高({pcr_vol:.2f})")
            elif pcr_vol < 0.7:
                signals.append(f"PCR成交量低({pcr_vol:.2f})")

            signal_str = " | ".join(signals) if signals else "中性"
            direction = "⚠️ 偏空" if pcr_vol > 1.2 else ("🟢 偏多" if pcr_vol < 0.7 else "⚪ 中性")

            print(f"\n{ticker}  现价:{price}  PE:{pe}  做空比例:{short_float}  借券费:{borrow_rate}")
            print(f"  PCR成交量={pcr_vol:.2f}  PCR持仓量={pcr_oi:.2f}  信号:{signal_str}  方向:{direction}")

        except Exception as e:
            print(f"\n{ticker}: 获取失败 — {str(e)[:60]}")


# ─────────────────────────────────────────────
# 4. 港股博弈扫描
# ─────────────────────────────────────────────

def scan_hkstocks(tickers=None):
    """港股博弈扫描：AH溢价 + 南向资金"""
    try:
        import yfinance as yf
    except ImportError:
        print("❌ yfinance 未安装")
        return

    import akshare as ak

    if tickers is None:
        tickers = ["0700.HK", "9988.HK", "9618.HK", "1810.HK", "3690.HK"]

    print("\n" + "="*70)
    print("港股 Market Game 扫描")
    print("="*70)

    # 南向资金
    try:
        sx = ak.stock_hsgt_north_net_flow_in_em(symbol="南向资金", indicator="今日")
        if sx is not None and len(sx) > 0:
            print(f"\n南向资金(今日): {sx.iloc[-1].get('今日净流入','?')} 亿元")
    except Exception as e:
        print(f"\n南向资金获取失败: {e}")

    # 个股行情
    for ticker in tickers:
        try:
            y = yf.Ticker(ticker)
            info = y.info
            price = info.get('currentPrice', info.get('regularMarketPrice', 'N/A'))
            pe = info.get('forwardPE', 'N/A')
            print(f"\n{ticker}  现价:{price}  PE:{pe}")
        except Exception as e:
            print(f"\n{ticker}: {str(e)[:60]}")


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Market Game 全市场扫描器")
    parser.add_argument('--mode', choices=['futures', 'ashare', 'trigger', 'usstock', 'hkstock', 'all'],
                        default='all', help='扫描模式')
    parser.add_argument('--date', default=None, help='日期 YYYYMMDD')
    parser.add_argument('--contracts', default=None, help='指定合约，逗号分隔')
    parser.add_argument('--tickers', default=None, help='指定股票代码，逗号分隔')
    parser.add_argument('--seats-only', action='store_true', help='仅席位分析（期货）')

    args = parser.parse_args()

    if args.mode == 'futures':
        scan_futures_all()
        if not args.seats_only:
            futures_recommend()
    elif args.mode == 'trigger':
        scan_ashare_trigger(args.date)
    elif args.mode == 'ashare':
        scan_ashare_trigger(args.date)
    elif args.mode == 'usstock':
        tickers = args.tickers.split(',') if args.tickers else None
        scan_usstocks(tickers)
    elif args.mode == 'hkstock':
        tickers = args.tickers.split(',') if args.tickers else None
        scan_hkstocks(tickers)
    elif args.mode == 'all':
        print("="*70)
        print("Market Game 全量扫描")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70)
        scan_futures_all()
        futures_recommend()
        scan_ashare_trigger(args.date)

if __name__ == '__main__':
    main()
