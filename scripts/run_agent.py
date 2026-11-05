import sys
import os
# 获取当前文件所在目录的上一级（也就是项目根目录）
root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 强行塞进 Python 的搜索路径最前面
sys.path.insert(0, root_path)

# 下面再写你原来的 import json 等
import json
import os
import subprocess
import sys
import os

# 强制 Windows 终端使用 UTF-8 编码
if sys.platform == "win32":
    os.system("chcp 65001 > nul")
from codemem.governor import Governor
from pathlib import Path
from typing import Any
from codemem.subagent import SubAgent
from codemem.utils.token_utils import TokenTracker
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# 初始化客户端（指向百炼兼容接口）
client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    # 这里改成读取 env 文件里的变量，如果没读到再用默认值
    base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
)
MODEL = os.getenv("AGENT_MODEL", "deepseek-v4.1-flash-1017")
MAX_ITERATIONS = 10

# 工具定义（标准 OpenAI 格式）
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定路径文件的完整内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"],
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "将内容写入指定路径的文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "要写入的完整内容"},
                },
                "required": ["path", "content"],
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "执行一条 shell 命令并返回结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                },
                "required": ["command"],
            },
        }
    },
]


def execute_tool(name: str, tool_input: dict[str, Any]) -> str:
    try:
        if name == "read_file":
            # 强制使用 utf-8 读取，防止乱码崩溃
            return Path(tool_input["path"]).read_text(encoding="utf-8", errors="replace")

        elif name == "write_file":
            Path(tool_input["path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(tool_input["path"]).write_text(tool_input["content"], encoding="utf-8")
            return f"已写入 {tool_input['path']}"

        elif name == "run_shell":
            try:
                result = subprocess.run(
                    tool_input["command"],
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    encoding="utf-8",
                    errors="replace"
                )
                stdout_str = result.stdout if result.stdout else ""
                stderr_str = result.stderr if result.stderr else ""
                output = stdout_str + stderr_str
                return output if output.strip() else "(命令执行成功，无输出)"
            except subprocess.TimeoutExpired:
                return "工具执行出错: 命令执行超时（超过15秒），请换一个更简单的命令。"
            except Exception as e:
                return f"工具执行出错: {type(e).__name__}: {e}"

        else:
            return f"未知工具: {name}"

    except Exception as e:
        return f"工具执行出错: {type(e).__name__}: {e}"


def run_agent(task: str, use_governor: bool = True) -> str:
    import time
    run_id = f"run_{int(time.time())}"
    tracker = TokenTracker(run_id=run_id)

    from codemem.governor import Governor
    governor = Governor()
    subagent = SubAgent(client=client, model=MODEL)
    messages: list[dict] = [
        {"role": "system",
         "content": "你是一个编码代理。运行在 Windows 系统上，请使用 dir, type 等基础 Windows 命令。严禁使用复杂的 for 循环、管道符 (|) 或多重命令连接符 (&&)。每次只能执行一个简单命令。"},
        {"role": "user", "content": task}
    ]

    print(f"[Agent] 任务: {task}")
    print(f"[Agent] 模型: {MODEL}")
    print(f"[Agent] 治理层状态: {'开启 ' if use_governor else '关闭 '}")
    print("=" * 60)

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"\n[Iteration {iteration}]")

        # 🌟 核心逻辑：根据开关决定是否拦截
        if use_governor:
            messages = governor.before_llm_call(messages)

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto"
        )

        # 记录 Token 消耗（注意这里要传入 iteration）
        tracker.track(response, iteration)

        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason
        print(f"  finish_reason: {finish_reason}")

        # 把 assistant 的消息加入历史
        messages.append(msg.model_dump(exclude_unset=True))

        if finish_reason == "stop" or not msg.tool_calls:
            print("\n" + "=" * 60)
            print(f"[Agent] 完成。")
            print(f"[Agent] Token 汇总: {json.dumps(tracker.summary(), indent=2)}")
            return msg.content or "(模型未返回文本)"

        if finish_reason == "tool_calls":
            for tool_call in msg.tool_calls:
                name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                # 🌟 核心治理点：拦截复杂的 shell 搜索请求，派发给子代理！
                if name == "run_shell":
                    cmd = args.get("command", "")
                    # 判定规则：如果是找文件、全局搜索、长输出命令，就派发
                    if any(keyword in cmd for keyword in ["find", "grep", "cat", "head", "tail"]):
                        print(f"  🚦 [Governor拦截] 发现重型探索命令: {cmd}")
                        summary = subagent.explore(f"请在当前目录执行以下命令，并把找到的关键信息总结给我: {cmd}")

                        # 把子代理的简短总结塞回给主上下文
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": "run_shell",
                            "content": f"[子代理总结]: {summary}"
                        })
                        continue  # 跳过下面主代理自己的执行

                print(f"  → 工具调用: {name}")
                print(f"    参数: {json.dumps(args, ensure_ascii=False)[:200]}")

                result = execute_tool(name, args)

                preview = result[:300].replace("\n", " ")
                print(f"    结果预览: {preview}{'...' if len(result) > 300 else ''}")

                # 把工具执行结果加入历史
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": name,
                    "content": result
                })

    print("\n[Agent] 达到最大轮次，未完成。")
    return "(达到最大轮次，任务未完成)"


if __name__ == "__main__":
    import sys

    # 默认开启治理层
    use_governor = True

    # 如果运行命令加了 --no-gov 参数，就关闭治理层
    if "--no-gov" in sys.argv:
        use_governor = False
        # 从参数列表中剔除这个标志，防止被当成任务内容
        sys.argv.remove("--no-gov")

    # 设计一个疯狂重复读取长文件的任务
    task = """请帮我分析这个项目的结构。
    1. 执行 dir /b 查看当前根目录下的文件。
    2. 读取 pyproject.toml 的内容。
    3. 简单总结这个项目依赖了哪些库。"""
    # 将开关传给 run_agent 函数
    result = run_agent(task, use_governor=use_governor)
    print(f"\n[最终回复]\n{result}")