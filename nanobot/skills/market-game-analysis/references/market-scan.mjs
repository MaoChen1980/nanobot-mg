#!/usr/bin/env node

const DEFAULT_CODES = [
  "SC0", "FU0", "LU0", "BU0", "AU0", "AG0", "CU0", "AL0", "ZN0", "SN0",
  "RB0", "I0", "JM0", "J0", "M0", "Y0", "P0", "RM0", "MA0", "PP0",
  "V0", "TA0", "FG0", "SR0", "CF0", "C0", "LH0", "LC0"
];

const args = process.argv.slice(2);
const jsonOutput = args.includes("--json");
const requested = args.filter((arg) => !arg.startsWith("--"));
const codes = requested.length
  ? requested.map((code) => code.replace(/^nf_/i, "").toUpperCase())
  : DEFAULT_CODES;

const response = await fetch(`http://hq.sinajs.cn/list=${codes.map((code) => `nf_${code}`).join(",")}`, {
  headers: { Referer: "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0" },
  signal: AbortSignal.timeout(10000),
});

if (!response.ok) throw new Error(`Sina quote request failed: HTTP ${response.status}`);

const raw = new TextDecoder("gb18030").decode(await response.arrayBuffer());
const fetchedAt = new Date();
const numberAt = (fields, index) => {
  const value = Number(fields[index]);
  return Number.isFinite(value) && value !== 0 ? value : null;
};

const rows = raw.split(/\r?\n/).flatMap((line) => {
  const match = line.match(/^var hq_str_(\w+)="(.*)";$/);
  if (!match) return [];
  const fields = match[2].split(",");
  const last = numberAt(fields, 8);
  const previousSettlement = numberAt(fields, 10);
  if (!last || !previousSettlement) return [];
  const volume = numberAt(fields, 13);
  const openInterest = numberAt(fields, 14);
  const sourceDate = fields[17];
  const sourceClock = fields[1];
  const sourceInstant = /^\d{4}-\d{2}-\d{2}$/.test(sourceDate) && /^\d{6}$/.test(sourceClock)
    ? new Date(`${sourceDate}T${sourceClock.slice(0, 2)}:${sourceClock.slice(2, 4)}:${sourceClock.slice(4, 6)}+08:00`)
    : null;
  const ageMinutes = sourceInstant ? (fetchedAt - sourceInstant) / 60000 : null;
  return [{
    code: match[1].replace(/^nf_/i, ""),
    name: fields[0],
    sourceTime: `${sourceDate || "unknown"} ${sourceClock || "unknown"}`,
    ageMinutes,
    isFresh: ageMinutes !== null && ageMinutes >= -1 && ageMinutes <= 15,
    last,
    high: numberAt(fields, 3),
    low: numberAt(fields, 4),
    previousSettlement,
    changePct: (last / previousSettlement - 1) * 100,
    volume,
    openInterest,
    volumeOpenInterest: volume && openInterest ? volume / openInterest : null,
  }];
});

if (!rows.length) throw new Error("No usable quotes returned. Check network access, symbols, and trading session.");

const STALE_THRESHOLD_MINUTES = 15;

rows.sort((left, right) => Math.abs(right.changePct) - Math.abs(left.changePct));
const freshRows = rows.filter((row) => row.isFresh);
const triggers = freshRows.filter((row) => Math.abs(row.changePct) >= 1);
const activeShorts = freshRows.filter((row) => row.changePct <= -1 && row.volumeOpenInterest && row.volumeOpenInterest > 1);
const staleRows = rows.filter((row) => row.ageMinutes !== null && row.ageMinutes > STALE_THRESHOLD_MINUTES);
const hasStaleData = staleRows.length > 0;
const output = {
  fetchedAt: fetchedAt.toISOString(),
  scope: "Liquid continuous-contract watchlist; not a whole-market or member-position scan.",
  quoteCaveat: "Volume/open-interest fields are a third-party intraday proxy. Confirm settlement, open-interest change, and member ranks with the exchange after close.",
  staleThresholdMinutes: STALE_THRESHOLD_MINUTES,
  hasStaleData,
  staleCount: staleRows.length,
  totalCount: rows.length,
  quotes: rows,
  freshQuotes: freshRows,
  staleQuotes: staleRows,
  triggers,
  activeShorts,
};

if (jsonOutput) {
  console.log(JSON.stringify(output, null, 2));
  process.exit(0);
}

const formatRow = (row) => {
  const proxy = row.volumeOpenInterest ? ` | V/OI代理 ${row.volumeOpenInterest.toFixed(2)}` : "";
  const stale = row.ageMinutes !== null && row.ageMinutes > STALE_THRESHOLD_MINUTES;
  const staleTag = stale ? ` ⚠️数据定格(${row.ageMinutes.toFixed(0)}分钟前)` : "";
  return `${row.code.padEnd(4)} ${row.name.padEnd(6)} ${row.changePct >= 0 ? "+" : ""}${row.changePct.toFixed(2)}% | 最新 ${row.last} | 昨结 ${row.previousSettlement}${proxy} | ${row.sourceTime}${staleTag}`;
};

const staleWarning = hasStaleData
  ? `\n⚠️ 警告：${staleRows.length}/${rows.length} 个报价数据定格 >${STALE_THRESHOLD_MINUTES}分钟，分析结论置信度降级。`
  : "";

console.log(`MGA 盘中候选扫描 | ${output.fetchedAt}${staleWarning}`);
console.log(output.scope);
console.log(`新鲜报价 ${freshRows.length}/${rows.length}（${STALE_THRESHOLD_MINUTES} 分钟内）；陈旧报价仅保留在 JSON 原始结果中。`);
console.log("\n涨跌幅绝对值 >= 1%：");
if (triggers.length) {
  triggers.forEach((row) => console.log(`  ${formatRow(row)}`));
} else if (freshRows.length) {
  freshRows.slice(0, 8).forEach((row) => console.log(`  ${formatRow(row)}`));
} else {
  console.log("  无新鲜报价；当前不生成交易候选。");
}
console.log("\n放量下跌候选（仅代理信号，须盘后确认）：");
if (activeShorts.length) {
  activeShorts.forEach((row) => console.log(`  ${formatRow(row)}`));
} else {
  console.log("  无");
}
console.log("\n下一步：核对交易所结算/持仓变化、前二十会员排名、仓单或库存，再决定是否交易。");
