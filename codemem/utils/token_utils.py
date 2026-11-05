import json
import time
from pathlib import Path


class TokenTracker:
    def __init__(self, run_id="test_run"):
        self.run_id = run_id
        self.calls = []
        self.log_file = Path("run_logs.jsonl")  # 日志会存在项目根目录

    def track(self, response, iteration: int) -> None:
        usage = response.usage
        if not usage:
            return

        # 提取数据
        record = {
            "run_id": self.run_id,
            "iteration": iteration,
            "timestamp": time.time(),
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "total_tokens": usage.prompt_tokens + usage.completion_tokens,
        }
        self.calls.append(record)

        # 实时追加写入文件，防止程序崩溃丢失数据
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def summary(self) -> dict:
        total_input = sum(c["input_tokens"] for c in self.calls)
        total_output = sum(c["output_tokens"] for c in self.calls)
        return {
            "run_id": self.run_id,
            "calls": len(self.calls),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output
        }