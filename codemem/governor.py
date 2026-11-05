import re
import json
class Governor:
    def __init__(self, max_messages=6):
        # 设定一个阈值，当消息超过 10 条时触发裁剪（为了测试方便，设小一点）
        self.max_messages = max_messages

    def detect_hard_constraint(self, text: str) -> bool:
        if not text:
            return False
        pattern = r"(不要|别|不能|禁止|必须|一定|切记|接口|API)"
        return bool(re.search(pattern, text))

    def collapse_file_reads(self, messages: list[dict]) -> list[dict]:
        """扫描消息，如果同一个文件被多次 read_file，只保留最后一次，前面的替换为占位符。"""
        # 1. 找出每个文件最后一次被读取的 tool_call_id
        latest_reads = {}
        for msg in messages:
            if msg["role"] == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if tc["function"]["name"] == "read_file":
                        try:
                            args = json.loads(tc["function"]["arguments"])
                            path = args.get("path")
                            if path:
                                latest_reads[path] = tc["id"]
                        except Exception:
                            pass

        # 2. 替换掉不是最新的 tool_result
        new_messages = []
        for msg in messages:
            if msg["role"] == "tool" and msg["name"] == "read_file":
                # 如果这个 id 不在 latest_reads 的 values 里，说明不是最后一次
                if msg["tool_call_id"] not in latest_reads.values():
                    folded_msg = msg.copy()
                    folded_msg["content"] = "[折叠提示] 该文件被重复读取，旧内容已被清理。请参考最近一次的读取结果。"
                    new_messages.append(folded_msg)
                    continue
            new_messages.append(msg)

        return new_messages

    def before_llm_call(self, messages: list[dict]) -> list[dict]:
        print(f"\n[Governor] 拦截到 {len(messages)} 条消息。")

        # ==========================================
        # 新增：强制截断单条过长的消息（防止撑爆 API 字符上限）
        # ==========================================
        for msg in messages:
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
                if len(msg["content"]) > 5000:
                    msg["content"] = msg["content"][:5000] + "\n...[系统提示: 该工具输出过长，已被治理层截断]..."

        # 原有的文件折叠逻辑
        messages = self.collapse_file_reads(messages)

        if len(messages) <= self.max_messages:
            print(f"   消息数安全，无需裁剪。")
            return messages


        #  新增：先进行文件折叠
        messages = self.collapse_file_reads(messages)

        if len(messages) <= self.max_messages:
            print(f"   消息数安全，无需裁剪。")
            return messages

        print(f"   消息数超限 ({len(messages)} > {self.max_messages})，触发压缩！")

        safe_messages = []
        other_messages = []

        for i, msg in enumerate(messages):
            # 系统提示词和最开始的用户任务绝对不能删
            if i == 0 and msg["role"] == "system":
                safe_messages.append(msg)
            elif i == 1 and msg["role"] == "user":
                safe_messages.append(msg)
            else:
                other_messages.append(msg)

        # 算出要丢弃的旧消息
        keep_recent = self.max_messages - len(safe_messages)
        if keep_recent < 2:
            keep_recent = 2

        dropped_messages = other_messages[:-keep_recent]
        recent_messages = other_messages[-keep_recent:]

        new_messages = safe_messages.copy()

        #  核心亮点：生成一条历史摘要，塞在历史消息前面
        if dropped_messages:
            summary_text = f"[历史摘要] 你之前已经处理了 {len(dropped_messages)} 条中间消息。请记住你正在完成任务，不要重复执行之前的步骤！"
            new_messages.append({"role": "user", "content": summary_text})

        new_messages.extend(recent_messages)
        print(f"   裁剪完成，保留 {len(new_messages)} 条核心消息（含 1 条摘要）。")

        return new_messages