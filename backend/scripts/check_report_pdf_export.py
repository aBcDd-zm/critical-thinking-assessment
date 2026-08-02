from __future__ import annotations

import argparse
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.report_pdf_service import ReportPdfService  # noqa: E402
from app.services.session_service import _report_scenario_title  # noqa: E402


DIMENSIONS = [
    ("problem_definition", "问题界定"),
    ("evidence_evaluation", "证据评估"),
    ("reasoning_argumentation", "推理论证"),
    ("multiple_perspectives", "多元视角"),
    ("integrative_decision", "整合决策"),
    ("dynamic_adjustment", "动态调整"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and validate the formal report PDF.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tmp/pdfs/report_pdf_uat.pdf"),
        help="Path for the generated visual-QA PDF.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = _report_fixture()
    scenario_title = "校园二手书交易平台优化决策"
    content = ReportPdfService().generate(
        report=report,
        nickname="PDF 回归用户",
        scenario_title=scenario_title,
        generated_at=datetime(2026, 7, 17, 12, 30),
        timezone_name="America/Toronto",
    )
    assert content.startswith(b"%PDF-")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)

    reader = PdfReader(args.output)
    assert len(reader.pages) >= 2
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    for required in [
        "审辩式思维动态测评报告",
        "维度结果概览（含未测到或暂不评分项）",
        "暂不评分",
        "（节选）",
        scenario_title,
        "边界说明",
        "测量质量",
        "整体证据基础指数（ESI）",
        "总 fallback 比例",
        "America/Toronto",
        *[name for _, name in DIMENSIONS],
    ]:
        if required not in extracted:
            raise AssertionError(f"PDF is missing expected text: {required}")
    for forbidden in ["localhost", "127.0.0.1", "Session", "Feedback", "high", "medium", "low"]:
        if forbidden in extracted:
            raise AssertionError(f"PDF contains forbidden text: {forbidden}")

    invalid_report = _report_fixture()
    invalid_report.update(
        {
            "overall_level": "结果无效",
            "summary": "测评过程异常，结果不宜解释，建议重新测评。",
            "advantages": ["不应展示的优势"],
            "improvement_suggestions": ["不应展示的改进建议"],
            "development_plan": ["不应展示的发展计划"],
            "measurement_quality": {
                "status": "invalid",
                "technical_failure_rate": 0.5,
                "total_fallback_rate": 0.6,
                "missing_events": ["integration"],
                "unobserved_dimensions": [],
                "provisional_dimensions": [],
                "scoring_contamination_turn_ids": [],
                "retest_recommended": True,
                "reasons": ["技术回退率达到或超过30%"],
                "overall_evidence_sufficiency_index": None,
            },
        }
    )
    invalid_content = ReportPdfService().generate(
        report=invalid_report,
        nickname="无效报告回归用户",
        scenario_title=scenario_title,
        generated_at=datetime(2026, 7, 17, 12, 30),
        timezone_name="America/Toronto",
    )
    invalid_reader = PdfReader(BytesIO(invalid_content))
    invalid_extracted = "\n".join(
        page.extract_text() or ""
        for page in invalid_reader.pages
    )
    for required in [
        "结果无效",
        "测评过程异常",
        "结果不宜解释",
        "测量质量",
        "建议重新测评",
    ]:
        if required not in invalid_extracted:
            raise AssertionError(
                f"Invalid PDF is missing expected text: {required}"
            )
    for forbidden in [
        "5/5",
        "六维得分概览",
        "主要优势",
        "改进建议",
        "发展计划",
        "不应展示的优势",
        "不应展示的改进建议",
        "不应展示的发展计划",
        "本次对话展示了可追溯的判断和行动安排",
    ]:
        if forbidden in invalid_extracted:
            raise AssertionError(
                f"Invalid PDF exposes interpretive content: {forbidden}"
            )

    assert _report_scenario_title(
        scenario_title,
        "本科生",
    ) == scenario_title
    assert (
        _report_scenario_title(
            "软件工程师的协作决策",
            "软件工程师",
        )
        == "熟悉领域的协作决策"
    )
    assert _report_scenario_title(None, None) == "测评情境"

    print("Report PDF export checks passed.")
    print(f"pages={len(reader.pages)}, bytes={len(content)}, output={args.output}")
    return 0


def _report_fixture() -> dict:
    long_quote = (
        "我会先在低端设备和弱网环境连续测试两轮，同步成功率达到99%后再逐步扩大范围，"
        "并继续监控失败率。如果指标下降或再次影响核心任务，就暂停扩大并回退。"
    ) * 5
    reports = []
    for index, (key, name) in enumerate(DIMENSIONS):
        insufficient = key == "evidence_evaluation"
        reports.append(
            {
                "dimension_key": key,
                "dimension_name": name,
                "score": None if insufficient else min(5, 3 + index % 3),
                "assessment_status": "insufficient_evidence" if insufficient else "scored",
                "level_label": "暂不评分" if insufficient else ("high" if index % 2 else "medium"),
                "strength": (
                    "没有释放对应情境或没有获得公平作答机会。"
                    if insufficient
                    else "本次对话展示了可追溯的判断和行动安排。"
                ),
                "weakness": None,
                "evidence_quotes": [] if insufficient else [long_quote, "我会保留可回退的方案。", "第三条证据。"],
                "suggestion": "可以继续说明阈值依据、责任人和复核频率。",
                "evidence_sufficiency_index": None if insufficient else 82,
                "evidence_sufficiency_level": None if insufficient else "medium",
                "score_kind": "unobserved" if insufficient else "supported",
                "evidence_sufficiency_note": "表示证据基础，不是能力分。",
            }
        )
    return {
        "overall_level": "部分结果",
        "summary": (
            "本次仅形成部分维度结果；未测到的维度不作能力判断，"
            "请结合证据基础指数谨慎解释。"
        ),
        "dimension_reports": reports,
        "advantages": ["能将证据、取舍和行动安排连接起来。"],
        "improvement_suggestions": ["进一步说明量化阈值的来源。"],
        "development_plan": ["在后续项目中记录触发条件和复盘结果。"],
        "disclaimer": "本报告仅基于本次情境对话中的有限表现生成，仅用于学习与发展参考。",
        "fallback_used": False,
        "warnings": [],
        "measurement_quality": {
            "status": "caution",
            "technical_failure_rate": 0,
            "total_fallback_rate": 0.1,
            "missing_events": [],
            "unobserved_dimensions": ["evidence_evaluation"],
            "provisional_dimensions": [],
            "scoring_contamination_turn_ids": [],
            "retest_recommended": True,
            "reasons": ["存在未测到维度，不得解释为能力不足"],
            "overall_evidence_sufficiency_index": 66.7,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
