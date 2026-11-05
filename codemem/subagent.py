import json
from openai import OpenAI
import os


class SubAgent:
    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model

    def explore(self, task_desc: str) -> str:
        """
        子代理的核心方法：接收任务，自己去用工具探索，只返回一段总结。
        """
        print(f"   [子代理启动] 任务: {task_desc}")

        # 子代理专属的系统提示词，要求极其严格
        system_prompt = """你是一个专业的信息收集子代理。
你的任务是使用工具去搜索、查找或探索信息。
限制条件：
1. 你只负责收集信息，绝对不要修改任何文件。
2. 探索完成后，你只需输出一小段包含核心结论的总结（比如找到了什么路径、包含了什么关键配置）。
3. 绝对不要把文件的完整内容或大段终端输出返回！只提炼重点！
4.你运行在 Windows 操作系统上，必须使用 dir, type 等 Windows 支持的命令。"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_desc}
        ]

        # 为了节约成本，子代理最多只准执行 3 步
        for _ in range(3):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                # 注意：这里只是模拟，为了不依赖主文件的 TOOLS 配置，我们直接在子代理里执行 shell
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "run_shell",
                            "description": "执行 shell 命令",
                            "parameters": {
                                "type": "object",
                                "properties": {"command": {"type": "string"}},
                                "required": ["command"],
                            },
                        }
                    }
                ]
            )

            msg = response.choices[0].message
            messages.append(msg.model_dump(exclude_unset=True))

            if not msg.tool_calls:
                # 子代理觉得任务完成了，返回总结
                print(f"  [子代理完成] 返回总结: {msg.content[:50]}...")
                return msg.content

            # 执行子代理的工具调用
            for tool_call in msg.tool_calls:
                if tool_call.function.name == "run_shell":
                    import subprocess
                    cmd = json.loads(tool_call.function.arguments).get("command", "")
                    print(f"     [子代理执行命令]: {cmd}")

                    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8",
                                         errors="replace")
                    output = (res.stdout or "") + (res.stderr or "")
                    # 限制返回长度，防止子代理自己撑爆
                    output = output[:500]

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": "run_shell",
                        "content": output
                    })

        # 如果 3 步还没搞完，强制中断并返回警告
        return "子代理探索失败或超时。"