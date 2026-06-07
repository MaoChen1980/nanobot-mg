"""Debug root-cause tool — read conversation history and suggest investigation direction.

When tools fail repeatedly, this tool reads the full conversation and applies
20 root-cause-analysis methods to recommend the best debug approach.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from loguru import logger

from nanobot.agent.llm_context import chat
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


_RCA_METHODS = """
1. **分解法 (Divide & Conquer)** — Break into independent sub-problems.
   - 通用: 空调不制冷 → 分解电源/遥控器/滤网/压缩机 → 滤网堵塞
   - 软件: 页面加载慢 → 分解DNS/服务端/前端/资源加载 → 服务端占90%

2. **对比法 (Comparison)** — Compare outcomes under different conditions.
   - 通用: 同锅饭有人腹泻有人没事 → 对比饮食差异 → 凉拌菜久放
   - 软件: Chrome正常IE报错 → 对比报错/请求/HTML → IE不支持ES6语法

3. **回退法 (Rollback)** — Revert to known-good then re-apply changes.
   - 通用: 文档排版错乱 → 回退正确版本逐步应用 → "插入图片"后页边距变
   - 软件: 部署后登录失败 → 回滚稳定版本逐个提交 → JWT过期配置未生效

4. **假设法 (Hypothesis Testing)** — "If X then Y should Z". Predict & verify.
   - 通用: 植物枯萎 → 假设"浇水过量" → 停水松土新叶应停止发黄 ✓
   - 软件: API偶尔500 → 假设"连接池泄漏" → 重启后连接数应回归正常 ✓

5. **逆推法 (Reverse Inference)** — Trace backward from the failure.
   - 通用: 迟到→堵车→事故→违规变道→缺高峰值守
   - 软件: 下单没收到确认邮件→查MQ无任务→查订单日志"SMTP connection refused"→邮件服务器防火墙变更

6. **尝试法 (Trial & Error)** — Iterate plausible fixes when space is small.
   - 通用: WiFi连不上 → 重启路由→忘记网络重连→改DNS，第三步成功
   - 软件: 编译符号未找到 → 清理重建→更新依赖→检查大小写→重启IDE→索引缓存问题

7. **透视法 (Look Inside)** — Examine internal state, not just the surface.
   - 通用: 家庭开销超标 → 查明细账单，外卖500涨到2000
   - 软件: 接口返回空数组但有数据 → 打SQL日志/ORM映射/序列化前后 → null字段导致Jackson过滤整条记录

8. **单变量法 (Single Variable)** — Change one factor at a time.
   - 通用: 蛋糕塌陷 → 固定其他条件分别改打发时间/糖量/蛋白温度 → 打发时间关键
   - 软件: 压测TPS波动 → 固定并发/数据/硬件，调JVM堆大小 → 2GB频繁GC，4GB稳定

9. **边界法 (Boundary Testing)** — Check edge, extreme, empty, or null cases.
   - 通用: 电梯满员报警 → 测试95%/100%/101%载重 → 边界值100%
   - 软件: 金额计算错误 → 测试0/0.01/最大值/负数 → 0时除法未做非零检查

10. **复现法 (Reproduction)** — Find stable minimal steps to reproduce.
    - 通用: 汽车偶尔启动困难 → 低温+油量<1/4+放置12h → 稳定复现
    - 软件: 并发bug偶现 → 固定线程数/数据/循环1000次+Thread.sleep(1) → 稳定复现死锁

11. **排除法 (Elimination)** — Disable/remove parts, see if problem goes away.
    - 通用: 水温忽冷忽热 → 关闭其他用水点依然 → 排除其他干扰
    - 软件: Spring Boot启动失败 → 逐个注释@Component → 某DataSource配置冲突

12. **置换法 (Substitution)** — Replace suspicious part with known-good.
    - 通用: 台灯不亮 → 换灯泡无效，换电源线后亮 → 电源线断路
    - 软件: jar包无法运行 → 换其他JRE一样 → 换本机正常jar成功 → 原jar包损坏

13. **堆栈法 / 依赖链追溯 (Stack Trace / Chain Tracing)** — Walk dependency chain.
    - 通用: 网购未发货 → 已付款→支付扣款未通知→MQ积压→消费者连接池耗尽
    - 软件: CORS跨域失败 → 查请求头Origin→后端响应头→网关层去掉该头→漏配

14. **日志注入法 (Log Injection)** — Insert targeted logging at decision points.
    - 通用: 成绩下降 → 记录手机使用/睡眠一周 → 与成绩负相关
    - 软件: 多线程丢失记录 → 每步加"进入/退出+ID" → finally块提前清空队列

15. **时间回溯法 (Time Travel)** — Trace timestamps from failure backward.
    - 通用: 文件被误删 → 最后修改3:00删除3:05 → 查3:00-3:05谁操作
    - 软件: 配置被覆盖 → 查etcd变更历史 → 10:23批量更新→CI脚本未加环境判断

16. **静候法 (Wait & Observe)** — Extend observation for intermittent problems.
    - 通用: 电脑随机蓝屏 → 内存测试24h，第18h报错 → 内存热稳定不良
    - 软件: 服务一周后OOM → 监控GC/堆转储 → 某缓存线性增长无过期

17. **分层剥离法 (Layer Stripping)** — Bypass outer layers, test inner directly.
    - 通用: 网站无法访问 → 直接IP可访问 → DNS解析错误 → hosts被改
    - 软件: A调用B超时 → 在A内curl B的IP:端口正常 → 边车限流配置过低

18. **离群分析 (Outlier Analysis)** — Compare features of failed vs passed cases.
    - 通用: 某批面包发酵失败 → 对比后发现新牌酵母开封超30天
    - 软件: 部分用户登录报错 → token中带换行符，Base64解码失败

19. **强制失败法 (Force Failure)** — Induce extreme conditions to verify resilience.
    - 通用: 测试应急响应 → 模拟"主库不可用" → 观察切换时间
    - 软件: 测试重试 → 模拟前两次500第三次200 → 验证客户端是否真重试

20. **同行评审法 / 橡皮鸭法 (Peer Review / Rubber Ducking)** — Explain aloud.
    - 通用: 忘带钥匙 → 朋友反问"出门前最后一步？" → 穿鞋时视线离开鞋柜
    - 软件: 向同事解释为什么总走false分支 → 说到一半发现`=`而非`==`
"""


@tool_parameters(
    build_parameters_schema(
        problem=p("string", "The specific problem, error, or symptom to debug. Describe it clearly — what happened, what was expected, and any relevant context."),
        focus_method=p("string", "Optional — constrain analysis to one specific method: 'divide_conquer', 'comparison', 'rollback', 'hypothesis_testing', 'reverse_inference', 'trial_error', 'look_inside', 'single_variable', 'boundary_testing', 'reproduction', 'elimination', 'substitution', 'chain_tracing', 'log_injection', 'time_travel', 'wait_observe', 'layer_stripping', 'outlier_analysis', 'force_failure', 'peer_review'."),
        required=["problem"],
    )
)
class DebugRootCauseTool(Tool):
    """Analyse conversation history and recommend a root-cause investigation direction."""

    name = "debug_root_cause_tool"
    description = (
        "**Purpose**: You describe a problem you're stuck on, and this tool returns "
        "a structured debug plan: recommended investigation method(s) + specific "
        "directions to examine. It reads the full conversation for context, so your "
        "problem description can be brief — the tool already has the background.\n\n"
        "**When to call — you are in one of these situations**:\n"
        "- You tried a few approaches but keep getting different errors, no clear pattern\n"
        "- You don't know where to start investigating — the problem space feels too large\n"
        "- A tool failed 2+ times and retrying the same thing won't help\n"
        "- The error is intermittent or non-deterministic and you need a systematic strategy\n"
        "- You need to step back and choose an investigation method instead of guessing\n\n"
        "**Output**: Recommended method(s) from 20 RCA approaches (divide & conquer, "
        "comparison, rollback, hypothesis testing, reverse inference, trial & error, "
        "look inside, single variable, boundary testing, reproduction, elimination, "
        "substitution, chain tracing, log injection, time travel, wait & observe, "
        "layer stripping, outlier analysis, force failure, peer review) + concrete "
        "things to examine. You decide which tools to use for the actual investigation.\n\n"
        "**How it differs from other tools**:\n"
        "- `diagnose_tool` searches code + git history for matching error text\n"
        "- `assess_me_tool` audits what you know vs assume (cognition audit)\n"
        "- `reframe_tool` re-states the problem cleanly for a fresh perspective\n"
        "- `debug_root_cause_tool` gives you a **systematic investigation strategy** — "
        "which method to use and what to look for"
    )

    read_only = True

    def __init__(self) -> None:
        self._messages: ContextVar[list[dict[str, Any]]] = ContextVar(
            "debug_root_cause_messages", default=[]
        )

    def set_context(self, messages: list[dict[str, Any]]) -> None:
        """Set the conversation messages for analysis."""
        self._messages.set(messages)

    async def execute(
        self,
        problem: str,
        focus_method: str = "",
        **kwargs: Any,
    ) -> str:
        messages = self._messages.get()
        if not messages:
            return "Error: no active session — cannot read conversation history."

        from nanobot.agent.assess_me import format_conversation

        conversation = format_conversation(messages)

        lines = [
            "You are a root-cause analysis expert. Your task is to read the conversation below "
            "and recommend the most effective investigation method.",
            "",
            "## Available Methods",
            _RCA_METHODS.strip(),
            "",
            "## Output Format",
            "Return two sections in your response:",
            "",
            "**Recommended methods**: List applicable methods ordered by recommendation priority "
            "(most effective first). You may also suggest investigation methods beyond the 20 listed "
            "above if they better fit the problem — describe what they are and why they apply.",
            "",
            "**Investigation directions**: For each recommended method, give specific things to "
            "look for, comparisons to make, hypotheses to test, or state to examine. Be concrete — "
            "what exactly should the agent examine?",
            "",
            "## Important",
            "- Do NOT suggest specific tool calls (grep, read, exec, etc.) — the agent "
            "will decide which tools to use based on your direction",
            "- Focus on WHAT to investigate, not HOW to investigate it",
        ]

        if problem:
            lines += [
                "",
                "## Problem to Debug",
                problem,
            ]

        if focus_method:
            lines += [
                "",
                "## Constrain To Method",
                focus_method,
            ]

        lines += [
            "",
            "## Conversation",
            conversation,
        ]

        prompt = "\n".join(lines)

        try:
            resp = await chat(
                [{"role": "user", "content": prompt}],
            )
        except Exception as e:
            logger.warning("debug_root_cause LLM call failed: {}", e)
            return f"Error: LLM call failed — {e}"

        return (resp.content or "").strip() or "(empty response)"
