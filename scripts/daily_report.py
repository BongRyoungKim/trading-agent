"""
일일 보고서 수동 실행 CLI

Usage:
    python scripts/daily_report.py              # 오늘 KST 날짜 보고서
    python scripts/daily_report.py 2026-04-08   # 특정 날짜 보고서
    python scripts/daily_report.py --run-tasks  # 향후 과제 자동 실행 포함
    python scripts/daily_report.py --open       # 생성 후 파일 열기 (Windows)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Project root를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.report.generator import DailyReportGenerator


def main() -> None:
    parser = argparse.ArgumentParser(description="일일 트레이딩 보고서 생성")
    parser.add_argument(
        "date",
        nargs="?",
        default=None,
        help="보고서 날짜 (YYYY-MM-DD). 미입력 시 오늘 KST.",
    )
    parser.add_argument(
        "--journal",
        default="data/journal.db",
        help="journal.db 경로 (기본: data/journal.db)",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="심볼 목록 (기본: LINK/KRW BTC/KRW XRP/KRW)",
    )
    parser.add_argument(
        "--run-tasks",
        action="store_true",
        help="향후 과제 자동 실행 포함 (파라미터 검토, 경보 저장 등)",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        dest="open_file",
        help="생성 완료 후 보고서 파일 열기 (Windows: explorer, macOS: open)",
    )
    args = parser.parse_args()

    gen = DailyReportGenerator(
        journal_path=args.journal, symbols=args.symbols, run_tasks=args.run_tasks
    )
    path = gen.generate(target_date=args.date)

    if args.run_tasks and gen.last_task_results:
        executed = [r for r in gen.last_task_results if r.status == "executed"]
        if executed:
            print(f"\n[자동 실행 작업 결과] {len(executed)}건 실행:")
            for r in executed:
                print(f"  ✅ {r.title}: {r.detail}")
                if r.output_path:
                    print(f"     📄 {r.output_path}")
    print(f"보고서 저장 완료: {path}")

    if args.open_file:
        import subprocess  # noqa: S404
        import platform
        system = platform.system()
        if system == "Windows":
            subprocess.Popen(["explorer", str(path)])  # noqa: S603, S607
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])  # noqa: S603, S607
        else:
            print(f"파일 자동 열기 미지원 OS: {system}. 경로: {path}")


if __name__ == "__main__":
    main()
