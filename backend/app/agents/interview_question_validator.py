from __future__ import annotations

import re

from app.agents.progressive_schemas import InterviewPlanOutput, InterviewerOutput


INTERNAL_TERMS = {
    "问题界定",
    "证据评估",
    "推理论证",
    "多元视角",
    "整合决策",
    "动态调整",
    "评分",
    "维度",
    "证据缺口",
    "追问次数",
    "第1阶段",
    "第 1 阶段",
    "下一阶段",
    "DeepSeek",
    "当前目标",
    "行为指标",
}
JUDGMENTAL_TERMS = {
    "回答很好",
    "非常好",
    "做得很好",
    "很成熟",
    "正确",
    "高分",
    "标准答案",
    "充分",
}
LEADING_PATTERNS = (
    "你应该",
    "正确做法",
    "是不是应该",
    "显然",
)
UNSUPPORTED_INFERENCE_TERMS = (
    "焦虑",
    "紧张",
    "害怕",
    "你的性格",
    "你的人格",
    "你的动机",
    "你的情绪",
    "你只在乎",
)

# These rules intentionally match claims made by the interviewer about the
# participant or the interviewer-participant relationship. Ordinary discussion
# of a father, friend, consultant, or mental-health professional must remain
# available in workplace scenarios.
HUMANISTIC_SAFETY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "unsupported_hidden_meaning",
        (
            r"你(?:其实|实际上)(?:并不是|不是|是在|是因为|真正).{0,24}",
            r"你(?:内心深处|潜意识(?:里|中)?).{0,24}",
            r"你真正(?:害怕|担心|想要|在意|抗拒|逃避|渴望|需要)(?:的)?(?:是)?",
            r"(?:这|那)(?:件事)?背后(?:其实|真正)?(?:是|说明|反映).{0,20}(?:你|你的)",
            r"你(?:现在)?之所以.{0,20}是因为.{0,12}(?:童年|父母|潜意识|原生家庭)",
            r"(?:这|那)(?:其实)?(?:源于|来自).{0,12}(?:你的)?(?:童年|潜意识|原生家庭)",
        ),
    ),
    (
        "relational_attachment",
        (
            r"我(?:此刻|现在)?(?:感到|感觉|觉得)(?:自己)?(?:此刻|现在)?(?:和|跟)你"
            r"(?:很|非常|特别)?(?:亲近|亲密)",
            r"你让我(?:很|非常|特别)?(?:感动|心疼|难过|开心|幸福|温暖|骄傲)",
            r"我(?:真的|很|非常|特别)?舍不得(?:和你)?(?:结束|分开|离开)",
            r"我会一直陪(?:着)?你",
            r"我们(?:之间)?(?:有|建立了).{0,8}(?:深刻|特别|亲密|特殊)"
            r".{0,4}(?:关系|联系|连接)",
            r"我(?:爱|喜欢|想念)你",
        ),
    ),
    (
        "role_substitution",
        (
            r"我(?:可以|会|愿意|想|就是|来)?(?:当|做|成为)(?:你|您的)(?:的)?"
            r"(?:父亲|母亲|爸爸|妈妈|伴侣|爱人|男友|女友|朋友|家人)",
            r"(?:你|您)(?:可以|能|不妨)?把我当(?:作|成)(?:你|您的)?(?:的)?"
            r"(?:父亲|母亲|爸爸|妈妈|伴侣|爱人|男友|女友|朋友|家人)",
            r"我会像(?:你|您的)?(?:父亲|母亲|爸爸|妈妈|伴侣|爱人|朋友|家人)一样",
            r"我就是(?:你|您的)(?:父亲|母亲|爸爸|妈妈|伴侣|爱人|男友|女友|朋友|家人)",
        ),
    ),
    (
        "fabricated_self_disclosure",
        (
            r"我(?:也)?(?:曾经|以前|小时候|过去).{0,30}",
            r"我(?:也)?经历过.{0,30}",
            r"我(?:也)?有过(?:同样|类似|这种)(?:经历|感受)",
            r"我的(?:父亲|母亲|爸爸|妈妈|伴侣|丈夫|妻子|男友|女友|孩子|家人)",
            r"这(?:也)?让我(?:想起|回忆起).{0,24}",
            r"我(?:从|通过)(?:这次|我们的|和你的).{0,20}(?:学到|认识到|成长)",
            r"我(?:感到|觉得)(?:很|非常|特别)?"
            r"(?:高兴|开心|难过|伤心|心疼|感动|骄傲|生气|害怕|焦虑|失望|兴奋)",
        ),
    ),
    (
        "prescriptive_authority",
        (
            r"听我的",
            r"照(?:着)?我说的(?:做|办)",
            r"(?:你|您)(?:现在|接下来|首先|先)?(?:应该|必须|务必|最好|一定要)",
            r"(?:你|您)(?:是不是|是否)应该",
            r"我(?:明确)?建议(?:你|您)",
            r"我支持(?:你|您)(?:这样|这么|就这么|按这个|去)?(?:做|决定|选择)",
            r"正确(?:的)?做法(?:就)?是",
            r"(?:你|您)就.{0,12}(?:吧|就行|可以了)",
            r"就按(?:这个|我的|上述|该).{0,12}(?:做|执行|处理|决定)",
            r"我替(?:你|您)决定",
        ),
    ),
    (
        "clinical_role_claim",
        (
            r"我(?:是|会作为|将作为|可以做|能做|愿意做|就是)(?:你|您的)?(?:的)?"
            r"(?:心理咨询师|心理治疗师|治疗师|心理医生)",
            r"作为(?:你|您的)?(?:的)?(?:心理咨询师|心理治疗师|治疗师|心理医生)",
            r"我(?:会|能|可以|来)(?:治疗|疗愈|治愈)(?:你|您)",
            r"我(?:能|可以|会)帮(?:你|您)(?:治疗|疗愈|治愈|康复)",
            r"我们(?:现在)?(?:正在|接下来)?(?:进行|开始|做)(?:一次|这次)?"
            r"(?:心理咨询|心理治疗|治疗)",
            r"这(?:是|属于)(?:一次)?(?:心理咨询|心理治疗|治疗)",
        ),
    ),
)

HUMANISTIC_STYLE_VALIDATION_PATTERNS: tuple[
    tuple[str, tuple[str, ...]], ...
] = (
    (
        "evaluative_praise",
        (
            r"(?:你的|这个|刚才的)(?:回答|判断|分析|思路|选择)"
            r"(?:非常|很|十分)?(?:好|棒|优秀|成熟|专业|理性|正确)",
            r"(?:非常|真|太)(?:好|棒|优秀|成熟|专业)(?:了)?",
            r"这才是.{0,8}(?:正确|成熟|专业|理性)(?:的)?"
            r"(?:回答|判断|做法|选择)",
            r"(?:值得|应该)(?:表扬|肯定|称赞)",
        ),
    ),
    (
        "overclaimed_understanding",
        (
            r"我(?:完全|真正|非常|特别)(?:理解|懂|明白)(?:了)?你.{0,20}",
            r"我(?:能够|能|可以)?感受(?:到)?你(?:内心|心里|的情绪|的感受)"
            r".{0,20}",
            r"我比你(?:自己)?更(?:理解|懂|清楚).{0,20}",
        ),
    ),
    (
        "agreement_pressure",
        (
            r"你(?:应该)?也(?:会)?同意.{0,20}(?:吧|对吗|是不是)",
            r"你(?:是否|是不是)同意(?:我|这个|这种|上述).{0,20}",
            r"难道你不(?:认为|觉得|同意).{0,20}",
            r"(?:没有|不存在)(?:其他|别的)(?:合理)?"
            r"(?:选择|答案|方案).{0,8}(?:吧|对吗)",
            r"(?:这|那)(?:显然|当然)(?:是)?(?:唯一|最好|正确)"
            r".{0,12}(?:吧|对吗)",
        ),
    ),
    (
        "corrective_instruction",
        (
            r"你的(?:理解|思路|想法|判断)(?:有点|有些)?(?:偏了|错了|不对)",
            r"我来告诉你(?:正确|标准)(?:的)?(?:答案|思路|做法)",
            r"你需要(?:纠正|改正)(?:一下)?你的(?:理解|思路|想法|判断)",
            r"(?:正确|标准)(?:的)?(?:答案|思路|做法)(?:应该|就)?是",
        ),
    ),
    (
        "forced_resolution",
        (
            r"(?:不要|别)(?:再)?(?:犹豫|纠结|摇摆)",
            r"你(?:现在|马上|立即)?(?:必须|只能)(?:现在|马上|立即)?"
            r"(?:选|选择|决定)",
            r"(?:现在|马上|立即)(?:就)?(?:选一个|做决定|作出决定)",
        ),
    ),
)

HUMANISTIC_SAFETY_ERROR_CODES = frozenset(
    error_code for error_code, _ in HUMANISTIC_SAFETY_PATTERNS
)

RENDERER_VALIDATION_ERROR_CODES = frozenset(
    {
        "internal_terms",
        "judgmental",
        "leading",
        "unsupported_inference",
        "question_count",
        "unreleased_fact",
        "unexpected_fact",
        "ungrounded_reflection",
        "reflection_quote_ids",
        "missing_reflection",
        "quality_flags",
        "missing_selected_fact",
        "duplicate_question",
        "semantic_duplicate_question",
        "too_long",
        "too_many_sentences",
        "evaluative_praise",
        "overclaimed_understanding",
        "agreement_pressure",
        "corrective_instruction",
        "forced_resolution",
        "plan_question_omission",
    }
)


class InterviewQuestionValidator:
    def validate(
        self,
        output: InterviewerOutput,
        *,
        plan: InterviewPlanOutput,
        allowed_fact_codes: set[str],
        previous_questions: list[str],
        allowed_source_turn_ids: set[int] | None = None,
        source_turn_texts: dict[int, str] | None = None,
        approved_reflection: str | None = None,
        allowed_fact_text: str | None = None,
        enforce_humanistic_safety: bool = True,
    ) -> tuple[bool, list[str]]:
        message = output.message.strip()
        del approved_reflection  # v3.3 accepts faithful natural paraphrases.
        authored_message = self._assistant_authored_text(
            message,
            [item.quote for item in output.reflection_source_quotes],
        )
        errors = self.message_errors(
            authored_message,
            enforce_humanistic_safety=enforce_humanistic_safety,
        )
        question_count = authored_message.count("？") + authored_message.count("?")
        expected_questions = 0 if plan.action == "CONCLUDE" else 1
        if question_count != expected_questions or output.question_count != expected_questions:
            errors.append("question_count")
        if expected_questions == 1 and question_count == 0:
            errors.append("plan_question_omission")
        if plan.action == "RELEASE_EVENT":
            if set(output.introduced_fact_codes) != allowed_fact_codes:
                errors.append("unreleased_fact")
        elif output.introduced_fact_codes:
            errors.append("unexpected_fact")
        if allowed_source_turn_ids is not None and any(
            turn_id not in allowed_source_turn_ids
            for turn_id in output.reflection_turn_ids
        ):
            errors.append("ungrounded_reflection")
        quoted_ids = {item.turn_id for item in output.reflection_source_quotes}
        if quoted_ids != set(output.reflection_turn_ids):
            errors.append("reflection_quote_ids")
        if source_turn_texts is not None:
            for source in output.reflection_source_quotes:
                raw_text = source_turn_texts.get(source.turn_id, "")
                if not raw_text or source.quote not in raw_text:
                    errors.append("ungrounded_reflection")
        if (
            plan.delivery_mode
            in {"reflective_probe", "summary_check", "event_link", "perspective_shift"}
            and plan.reflection_basis_turn_ids
            and not output.reflection_source_quotes
        ):
            errors.append("missing_reflection")
        if not all(output.quality_flags.model_dump().values()):
            errors.append("quality_flags")
        if plan.action == "RELEASE_EVENT" and (
            not allowed_fact_text
            or not self.fact_is_supported(message, allowed_fact_text)
        ):
            errors.append("missing_selected_fact")
        normalized = self._normalize_question(authored_message)
        if normalized and any(
            self._normalize_question(item) == normalized for item in previous_questions
        ):
            errors.append("duplicate_question")
        elif normalized and any(
            self._semantically_similar(normalized, self._normalize_question(item))
            for item in previous_questions[-4:]
        ):
            errors.append("semantic_duplicate_question")
        if len(message) > 90:
            errors.append("too_long")
        if len(re.findall(r"[。！？!?]", authored_message)) > 2:
            errors.append("too_many_sentences")
        return not errors, errors

    @staticmethod
    def message_errors(
        message: str,
        *,
        enforce_humanistic_safety: bool = True,
    ) -> list[str]:
        errors: list[str] = []
        if any(term in message for term in INTERNAL_TERMS):
            errors.append("internal_terms")
        if any(term in message for term in JUDGMENTAL_TERMS):
            errors.append("judgmental")
        if any(pattern in message for pattern in LEADING_PATTERNS):
            errors.append("leading")
        if any(term in message for term in UNSUPPORTED_INFERENCE_TERMS):
            errors.append("unsupported_inference")
        if enforce_humanistic_safety:
            for error_code, patterns in HUMANISTIC_STYLE_VALIDATION_PATTERNS:
                if any(re.search(pattern, message) for pattern in patterns):
                    errors.append(error_code)
            for error_code, patterns in HUMANISTIC_SAFETY_PATTERNS:
                if any(re.search(pattern, message) for pattern in patterns):
                    errors.append(error_code)
        return errors

    @staticmethod
    def _assistant_authored_text(message: str, verified_quotes: list[str]) -> str:
        """Mask only visibly quoted, source-verified user text before speaker checks."""
        authored = message
        wrappers = (
            ("“", "”"),
            ('"', '"'),
            ("‘", "’"),
            ("'", "'"),
            ("「", "」"),
            ("『", "』"),
        )
        for quote in sorted(
            {item.strip() for item in verified_quotes if item.strip()},
            key=len,
            reverse=True,
        ):
            for left, right in wrappers:
                authored = authored.replace(f"{left}{quote}{right}", "〔用户原话〕")
        return authored

    @staticmethod
    def _normalize_question(value: str) -> str:
        return re.sub(r"[\s，。！？?、：；“”‘’]", "", value).lower()

    @staticmethod
    def _semantically_similar(left: str, right: str) -> bool:
        if len(left) < 8 or len(right) < 8:
            return False
        left_pairs = {left[index:index + 2] for index in range(len(left) - 1)}
        right_pairs = {right[index:index + 2] for index in range(len(right) - 1)}
        union = left_pairs | right_pairs
        return bool(union) and len(left_pairs & right_pairs) / len(union) >= 0.72

    @staticmethod
    def fact_is_supported(message: str, fact_text: str) -> bool:
        """Accept a faithful natural rendering while retaining fact guardrails."""
        normalize = lambda value: re.sub(r"[\s，。！？?、：；“”‘’]", "", value)
        rendered = normalize(message)
        fact = normalize(fact_text)
        if not rendered or not fact:
            return False
        if fact in rendered:
            return True

        if "核实" in fact and any(marker in fact_text for marker in ("没核实", "未核实", "尚未")):
            if "核实" not in message:
                return False

        fact_numbers = set(
            re.findall(r"\d+(?:\.\d+)?%?", fact_text.replace("％", "%"))
        )
        rendered_numbers = set(
            re.findall(r"\d+(?:\.\d+)?%?", message.replace("％", "%"))
        )
        if fact_numbers and not fact_numbers.issubset(rendered_numbers):
            return False

        fact_pairs = {fact[index:index + 2] for index in range(len(fact) - 1)}
        rendered_pairs = {
            rendered[index:index + 2] for index in range(len(rendered) - 1)
        }
        if not fact_pairs:
            return fact in rendered
        return len(fact_pairs & rendered_pairs) / len(fact_pairs) >= 0.40


__all__ = [
    "HUMANISTIC_SAFETY_ERROR_CODES",
    "HUMANISTIC_STYLE_VALIDATION_PATTERNS",
    "InterviewQuestionValidator",
    "RENDERER_VALIDATION_ERROR_CODES",
]
