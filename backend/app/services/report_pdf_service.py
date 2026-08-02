from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    CondPageBreak,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


FONT_NAME = "NotoSansSC"
FONT_PATH = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSansSC-Variable.ttf"
LEVEL_LABELS = {
    "low": "明显不足",
    "medium": "中等",
    "high": "较强",
    "低": "明显不足",
    "中": "中等",
    "高": "较强",
    "初级": "基础",
    "较高": "较强",
    "明显不足": "明显不足",
    "基础": "基础",
    "突出": "突出",
    "暂不评分": "暂不评分",
}
QUALITY_STATUS_LABELS = {
    "valid": "有效",
    "caution": "谨慎解释",
    "invalid": "无效，建议重测",
}
SCORE_KIND_LABELS = {
    "supported": "正式评分",
    "provisional": "证据未充分，暂不评分",
    "unobserved": "未获得公平作答机会",
}


class ReportPdfService:
    def generate(
        self,
        *,
        report: dict[str, Any],
        nickname: str,
        scenario_title: str,
        generated_at: datetime | None = None,
        timezone_name: str = "Asia/Shanghai",
    ) -> bytes:
        _register_font()
        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=16 * mm,
            leftMargin=16 * mm,
            topMargin=17 * mm,
            bottomMargin=17 * mm,
            title=f"{nickname} 的审辩式思维动态测评报告",
            author="审辩式思维动态测评系统",
        )
        styles = _styles()
        all_dimensions = [
            item
            for item in (report.get("dimension_reports") or [])
            if isinstance(item, dict)
        ]
        measurement_quality = report.get("measurement_quality")
        invalid_measurement = (
            isinstance(measurement_quality, dict)
            and measurement_quality.get("status") == "invalid"
        )
        dimensions = [] if invalid_measurement else all_dimensions
        story: list[Any] = [
            Paragraph("审辩式思维动态测评报告", styles["title_cn"]),
            Spacer(1, 5 * mm),
            _metadata_table(
                nickname,
                scenario_title,
                generated_at,
                timezone_name,
                styles,
            ),
            Spacer(1, 7 * mm),
            Paragraph("总体结论", styles["heading_cn"]),
            Paragraph(
                f"<b>总体水平：{_html(_level(report.get('overall_level')))}</b>"
                f"<br/>{_html(report.get('summary'))}",
                styles["body_cn"],
            ),
        ]
        if report.get("fallback_used"):
            story.append(
                Paragraph(
                    "结果提示：本次报告包含降级生成或证据有限提示，请结合原始对话理解。",
                    styles["notice_cn"],
                )
            )
        story.extend(
            _measurement_quality_story(
                measurement_quality,
                all_dimensions,
                styles,
            )
        )
        story.append(Spacer(1, 5 * mm))

        if dimensions:
            overview_title = (
                "维度结果概览（含未测到或暂不评分项）"
                if any(item.get("score") is None for item in dimensions)
                else "六维得分概览"
            )
            story.extend(
                [
                    Paragraph(overview_title, styles["heading_cn"]),
                    _overview_table(dimensions),
                    Spacer(1, 6 * mm),
                ]
            )
        for index, item in enumerate(dimensions, start=1):
            story.extend(_dimension_story(item, index, styles))

        if not invalid_measurement:
            story.extend(
                _list_section("主要优势", report.get("advantages"), styles)
            )
            story.extend(
                _list_section(
                    "改进建议",
                    report.get("improvement_suggestions"),
                    styles,
                )
            )
            story.extend(
                _list_section("发展计划", report.get("development_plan"), styles)
            )
        story.extend(
            [
                Spacer(1, 4 * mm),
                Paragraph("边界说明", styles["heading_cn"]),
                Paragraph(
                    _html(
                        _text(
                            report.get("disclaimer"),
                            default="本报告基于本次情境对话生成，仅用于学习与发展参考。",
                        )
                    ),
                    styles["disclaimer_cn"],
                ),
            ]
        )
        document.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
        return buffer.getvalue()


def _register_font() -> None:
    if FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"PDF font is missing: {FONT_PATH}")
    pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))


def _styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    base = {
        "fontName": FONT_NAME,
        "textColor": colors.HexColor("#27332E"),
        "wordWrap": "CJK",
    }
    return {
        "title_cn": ParagraphStyle(
            "TitleCN",
            parent=sample["Title"],
            fontName=FONT_NAME,
            fontSize=20,
            leading=28,
            textColor=colors.HexColor("#173F2E"),
            alignment=TA_CENTER,
        ),
        "heading_cn": ParagraphStyle(
            "HeadingCN",
            parent=sample["Heading2"],
            fontName=FONT_NAME,
            fontSize=13,
            leading=19,
            textColor=colors.HexColor("#174D38"),
            spaceBefore=6,
            spaceAfter=6,
        ),
        "dimension_cn": ParagraphStyle(
            "DimensionCN",
            parent=sample["Heading3"],
            fontName=FONT_NAME,
            fontSize=12,
            leading=18,
            textColor=colors.HexColor("#173F2E"),
            spaceAfter=5,
        ),
        "body_cn": ParagraphStyle("BodyCN", parent=sample["BodyText"], fontSize=9.2, leading=15, **base),
        "label_cn": ParagraphStyle(
            "LabelCN",
            parent=sample["BodyText"],
            fontName=FONT_NAME,
            fontSize=8.5,
            leading=14,
            textColor=colors.HexColor("#537064"),
            wordWrap="CJK",
        ),
        "quote_cn": ParagraphStyle(
            "QuoteCN",
            parent=sample["BodyText"],
            fontName=FONT_NAME,
            fontSize=8.8,
            leading=14.5,
            textColor=colors.HexColor("#30483E"),
            wordWrap="CJK",
        ),
        "notice_cn": ParagraphStyle(
            "NoticeCN",
            parent=sample["BodyText"],
            fontName=FONT_NAME,
            fontSize=8.5,
            leading=14,
            textColor=colors.HexColor("#7A5B21"),
            backColor=colors.HexColor("#FFF7E5"),
            borderPadding=7,
            spaceBefore=6,
        ),
        "disclaimer_cn": ParagraphStyle(
            "DisclaimerCN",
            parent=sample["BodyText"],
            fontName=FONT_NAME,
            fontSize=8.2,
            leading=14,
            textColor=colors.HexColor("#5D6662"),
            backColor=colors.HexColor("#F3F5F4"),
            borderPadding=8,
            wordWrap="CJK",
        ),
    }


def _metadata_table(
    nickname: str,
    scenario_title: str,
    generated_at: datetime | None,
    timezone_name: str,
    styles: dict[str, ParagraphStyle],
) -> Table:
    return Table(
        [
            ["受测者", Paragraph(_html(nickname), styles["body_cn"])],
            ["测评情境", Paragraph(_html(scenario_title), styles["body_cn"])],
            [
                "报告时间",
                _format_report_time(
                    generated_at,
                    timezone_name,
                ),
            ],
        ],
        colWidths=[27 * mm, 130 * mm],
        style=TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5F6B65")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F8F6")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D7E0DA")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        ),
    )


def _format_report_time(
    generated_at: datetime | None,
    timezone_name: str,
) -> str:
    if generated_at is None:
        return "-"
    try:
        target_zone = ZoneInfo(timezone_name)
        display_zone = timezone_name
    except (ZoneInfoNotFoundError, ValueError):
        target_zone = ZoneInfo("Asia/Shanghai")
        display_zone = "Asia/Shanghai"
    source = (
        generated_at.replace(tzinfo=timezone.utc)
        if generated_at.tzinfo is None
        else generated_at
    )
    return f"{source.astimezone(target_zone):%Y-%m-%d %H:%M} ({display_zone})"


def _measurement_quality_story(
    value: Any,
    dimensions: list[dict[str, Any]],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    if not isinstance(value, dict):
        return []
    dimension_names = {
        str(item.get("dimension_key")): _text(
            item.get("dimension_name"),
            item.get("dimension_key"),
        )
        for item in dimensions
    }

    def dimension_list(key: str) -> str:
        values = value.get(key) or []
        return "、".join(
            dimension_names.get(str(item), str(item))
            for item in values
        ) or "无"

    rows = [
        ["测量状态", QUALITY_STATUS_LABELS.get(str(value.get("status")), "待确认")],
        [
            "整体证据基础指数（ESI）",
            (
                f"{value.get('overall_evidence_sufficiency_index')}/100"
                if value.get("overall_evidence_sufficiency_index") is not None
                else "无法计算"
            ),
        ],
        [
            "技术失败率",
            _percent(value.get("technical_failure_rate")),
        ],
        [
            "总 fallback 比例",
            _percent(value.get("total_fallback_rate")),
        ],
        ["未测到维度", dimension_list("unobserved_dimensions")],
        ["证据未充分维度", dimension_list("provisional_dimensions")],
        [
            "缺失关键事件",
            "、".join(str(item) for item in (value.get("missing_events") or []))
            or "无",
        ],
        [
            "评分污染轮次",
            "、".join(
                str(item)
                for item in (value.get("scoring_contamination_turn_ids") or [])
            )
            or "无",
        ],
        [
            "重测建议",
            "建议重新测评" if value.get("retest_recommended") else "暂不需要",
        ],
    ]
    table = Table(rows, colWidths=[52 * mm, 105 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#537064")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F8F6")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D7E0DA")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story: list[Any] = [
        Spacer(1, 4 * mm),
        Paragraph("测量质量", styles["heading_cn"]),
        table,
    ]
    reasons = [
        str(item).strip()
        for item in (value.get("reasons") or [])
        if str(item).strip()
    ]
    if reasons:
        story.extend(
            [
                Spacer(1, 2 * mm),
                Paragraph(
                    "；".join(reasons),
                    styles["notice_cn"],
                ),
            ]
        )
    return story


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "无法计算"


def _overview_table(dimensions: list[dict[str, Any]]) -> Table:
    rows: list[list[str]] = [["维度", "得分", "水平"]]
    for item in dimensions:
        score = item.get("score")
        rows.append(
            [
                _text(item.get("dimension_name"), item.get("dimension_key"), "未命名维度"),
                "暂不评分" if score is None else f"{score}/5",
                _level(item.get("level_label"), score),
            ]
        )
    table = Table(rows, colWidths=[90 * mm, 30 * mm, 37 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                ("FONTSIZE", (0, 0), (-1, -1), 8.8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDEBE4")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#173F2E")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CED8D2")),
                ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _dimension_story(
    item: dict[str, Any],
    index: int,
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    score = item.get("score")
    name = _text(item.get("dimension_name"), item.get("dimension_key"), "未命名维度")
    score_kind = SCORE_KIND_LABELS.get(
        str(item.get("score_kind")),
        "待确认",
    )
    esi = item.get("evidence_sufficiency_index")
    esi_text = (
        f"{esi}/100"
        if esi is not None
        else "无测量依据"
    )
    story: list[Any] = [
        CondPageBreak(70 * mm),
        KeepTogether(
            [
                Paragraph(f"{index:02d}  {_html(name)}", styles["dimension_cn"]),
                Table(
                    [["得分", "暂不评分" if score is None else f"{score}/5", "水平", _level(item.get("level_label"), score)]],
                    colWidths=[18 * mm, 28 * mm, 18 * mm, 35 * mm],
                    style=TableStyle(
                        [
                            ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#537064")),
                            ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#537064")),
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F8F6")),
                            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D7E0DA")),
                            ("TOPPADDING", (0, 0), (-1, -1), 5),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ]
                    ),
                ),
                Spacer(1, 1.5 * mm),
                Paragraph(
                    f"评分类型：{_html(score_kind)}　证据基础指数（ESI）：{_html(esi_text)}",
                    styles["label_cn"],
                ),
            ]
        ),
        Spacer(1, 2.5 * mm),
        *_field(
            "优势" if item.get("score_kind") == "supported" else "证据说明",
            item.get("strength"),
            styles,
        ),
        *_field("待加强", item.get("weakness"), styles),
    ]
    quotes = [str(value).strip() for value in (item.get("evidence_quotes") or []) if str(value).strip()]
    if quotes:
        first_quote = _quote_table(quotes[0], styles)
        story.append(
            KeepTogether(
                [
                    Paragraph(f"{_html(name)} · 证据引用", styles["label_cn"]),
                    first_quote,
                    Spacer(1, 1.5 * mm),
                ]
            )
        )
        for quote_text in quotes[1:2]:
            story.extend(
                [
                    Paragraph(
                        f"{_html(name)} · 证据引用（续）",
                        styles["label_cn"],
                    ),
                    _quote_table(quote_text, styles),
                    Spacer(1, 1.5 * mm),
                ]
            )
        if len(quotes) > 2:
            story.append(
                Paragraph(
                    f"另有 {len(quotes) - 2} 条证据，可在网页报告中查看完整原话。",
                    styles["label_cn"],
                )
            )
    else:
        empty_evidence_text = (
            "已获得部分相关证据，但尚未达到关键评分门槛。"
            if item.get("score_kind") == "provisional"
            else "本次对话未获得该维度可用于判断的有效证据。"
        )
        story.append(Paragraph(empty_evidence_text, styles["label_cn"]))
    story.extend(_field("建议", item.get("suggestion"), styles))
    story.append(Spacer(1, 5 * mm))
    return story


def _field(label: str, value: Any, styles: dict[str, ParagraphStyle]) -> list[Any]:
    text = _text(value)
    if not text:
        return []
    return [
        KeepTogether(
            [
                Paragraph(label, styles["label_cn"]),
                Paragraph(_html(text), styles["body_cn"]),
                Spacer(1, 1.5 * mm),
            ]
        )
    ]


def _quote_table(text: str, styles: dict[str, ParagraphStyle]) -> Table:
    excerpt, shortened = _excerpt(text)
    suffix = "（节选）" if shortened else ""
    return Table(
        [[Paragraph(f"“{_html(excerpt)}”{suffix}", styles["quote_cn"])]],
        colWidths=[157 * mm],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F2F7F4")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CFE0D7")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        ),
    )


def _list_section(title: str, items: Any, styles: dict[str, ParagraphStyle]) -> list[Any]:
    values = [str(item).strip() for item in (items or []) if str(item).strip()]
    if not values:
        return []
    story: list[Any] = [Paragraph(title, styles["heading_cn"])]
    for index, item in enumerate(values, start=1):
        story.extend([Paragraph(f"{index}. {_html(item)}", styles["body_cn"]), Spacer(1, 1.2 * mm)])
    story.append(Spacer(1, 3 * mm))
    return story


def _excerpt(text: str, limit: int = 220) -> tuple[str, bool]:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized, False
    candidate = normalized[:limit]
    boundary = max(candidate.rfind(mark) for mark in "。！？；")
    if boundary >= limit // 2:
        candidate = candidate[: boundary + 1]
    else:
        candidate = candidate[: limit - 3].rstrip() + "..."
    return candidate, True


def _level(value: Any, score: Any = None) -> str:
    label = _text(value)
    if label in {"明显不足", "基础", "中等", "较强", "突出", "暂不评分"}:
        return label
    if score is None and label in LEVEL_LABELS:
        return LEVEL_LABELS[label]
    if score is None:
        return label or "暂不评分"
    try:
        numeric = float(score)
    except (TypeError, ValueError):
        return LEVEL_LABELS.get(label, label or "待确认")
    if numeric <= 1:
        return "明显不足"
    if numeric < 3:
        return "基础"
    if numeric < 4:
        return "中等"
    if numeric < 5:
        return "较强"
    return "突出"


def _draw_footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont(FONT_NAME, 7.5)
    canvas.setFillColor(colors.HexColor("#7B8781"))
    canvas.drawString(16 * mm, 9 * mm, "审辩式思维动态测评报告")
    canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"第 {document.page} 页")
    canvas.restoreState()


def _html(value: Any) -> str:
    return escape(_text(value)).replace("\n", "<br/>")


def _text(*values: Any, default: str = "") -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


__all__ = ["ReportPdfService"]
