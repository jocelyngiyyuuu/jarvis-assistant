#!/usr/bin/env python3
"""Read-only first-turn tool selection check for installed Ollama models."""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis_core.assistant_agent import AGENT_PROMPT, READ_ONLY_TOOLS  # noqa: E402
from jarvis_core.local_ai import LocalAI  # noqa: E402


CASES = (
    ("Máy đang dùng bao nhiêu RAM?", "get_system_status"),
    ("CPU hiện đang thế nào?", "get_system_status"),
    ("Cho tôi biết dung lượng ổ đĩa còn lại", "get_system_status"),
    ("Uptime của máy là bao lâu?", "get_system_status"),
    ("Kiểm tra tình trạng hệ thống", "get_system_status"),
    ("Máy có đang thiếu RAM không?", "get_system_status"),
    ("Bạn nhớ tôi thích môn gì?", "search_memory"),
    ("Trong bộ nhớ, tôi có sở thích nào?", "search_memory"),
    ("Tôi đã bảo Jarvis nhớ điều gì về thiết bị?", "search_memory"),
    ("Hãy tìm trong ghi nhớ chuyện lịch học", "search_memory"),
    ("Tôi thích đồ uống nào theo những gì đã lưu?", "search_memory"),
    ("Bạn còn nhớ mục tiêu của tôi không?", "search_memory"),
    ("Tìm file báo cáo trong máy", "search_files"),
    ("Có file tên kế hoạch ở đâu?", "search_files"),
    ("Tìm tài liệu README trong thư mục home", "search_files"),
    ("Hãy tìm đường dẫn file ngân sách", "search_files"),
    ("File tên notes.md nằm ở đâu?", "search_files"),
    ("Tìm file ảnh kỳ nghỉ", "search_files"),
    ("Tôi có lời nhắc nào đang chờ?", "list_pending_reminders"),
    ("Liệt kê lịch nhắc chưa hoàn thành", "list_pending_reminders"),
    ("Sắp tới Jarvis sẽ nhắc tôi gì?", "list_pending_reminders"),
    ("Những việc tôi đã hẹn nhắc là gì?", "list_pending_reminders"),
    ("Xem danh sách reminder đang đợi", "list_pending_reminders"),
    ("Có lời nhắc nào ngày mai không?", "list_pending_reminders"),
    ("Giải thích RAM là gì", None),
    ("1 cộng 1 bằng mấy?", None),
    ("Chào Jarvis", None),
    ("Kể một câu chuyện ngắn", None),
    ("Python là ngôn ngữ gì?", None),
    ("Sự khác biệt giữa RAM và SSD là gì?", None),
)


def evaluate(model, cases=CASES):
    client = LocalAI(model=model, timeout=120)
    client.keep_alive = "2m"
    rows = []
    for prompt, expected in cases:
        started = time.monotonic()
        try:
            response = client.tool_chat(
                [{"role": "system", "content": AGENT_PROMPT},
                 {"role": "user", "content": prompt}],
                READ_ONLY_TOOLS,
            )
            calls = [
                call.get("function", {}).get("name")
                for call in response.get("tool_calls", [])
                if isinstance(call, dict)
            ]
            passed = (expected in calls) if expected else not calls
            rows.append({
                "prompt": prompt, "expected": expected, "called": calls,
                "passed": passed, "seconds": round(time.monotonic() - started, 2),
            })
            print(f"{model}: {len(rows)}/{len(cases)}", file=sys.stderr, flush=True)
        except RuntimeError as error:
            rows.append({
                "prompt": prompt, "expected": expected, "called": [],
                "passed": False, "error": str(error)[:200],
                "seconds": round(time.monotonic() - started, 2),
            })
            print(f"{model}: {len(rows)}/{len(cases)} error", file=sys.stderr, flush=True)
    return {"model": model, "passed": sum(row["passed"] for row in rows),
            "total": len(rows), "cases": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="+", help="Installed Ollama model tags")
    parser.add_argument("--quick", action="store_true", help="Run five representative cases")
    parser.add_argument("--case", type=int, action="append", help="Run one 1-based case index")
    parser.add_argument("--output", type=Path, help="Write detailed JSON results to a file")
    args = parser.parse_args()
    quick_cases = (CASES[0], CASES[6], CASES[12], CASES[18], CASES[24])
    cases = (
        tuple(CASES[index - 1] for index in args.case)
        if args.case else quick_cases if args.quick else CASES
    )
    results = [evaluate(model, cases) for model in args.models]
    if args.output:
        args.output.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps([{"model": row["model"], "passed": row["passed"],
                           "total": row["total"]} for row in results],
                         ensure_ascii=False))
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))
