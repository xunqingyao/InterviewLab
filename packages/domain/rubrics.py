"""Server-owned, question-scoped grading standards. Never inspect the candidate's answer."""

import re
from copy import deepcopy
from typing import Any

from domain.text import normalize_text

RUBRIC_POLICY_ID = "rubric_question_v2"
PROFILES = {
    "concept": {
        "label": "概念与原理",
        "checks": ["本题概念的含义是否正确", "能否解释题目所问的关系、区别或执行机制", "解释是否前后一致"],
        "strong": "在核心解释正确的基础上，能说明本题涉及的适用条件或常见误区。",
        "not_required": ["个人项目贡献", "量化指标", "实验测试集", "未被问到的方案对比"],
    },
    "implementation": {
        "label": "实现过程",
        "checks": [
            "实现步骤是否能解决题目中的问题",
            "关键数据、状态或调用顺序是否清楚",
            "是否解释关键步骤的作用",
        ],
        "strong": "能进一步说明本题实现中的关键条件、边界或容易出错的位置。",
        "not_required": ["未被问到的量化优化收益", "未被问到的替代方案", "完整项目背景"],
    },
    "tradeoff": {
        "label": "方案选择",
        "checks": ["说明本题场景的实际需求或限制", "比较题目所涉及方案的关键差别", "选择理由与场景一致"],
        "strong": "能说明所选方案的代价，以及哪些条件变化后会选择另一种方案。",
        "not_required": ["没有要求提供的实验指标", "没有要求讲解的底层源码", "所有可能方案的穷举"],
    },
    "troubleshooting": {
        "label": "问题定位与修复",
        "checks": ["区分问题现象与可能原因", "说明如何定位或确认原因", "修复办法针对原因且说明如何确认恢复"],
        "strong": "能说明本题相关的误判风险、复发条件或修复副作用。",
        "not_required": ["与故障无关的性能指标", "未被问到的完整架构重设计", "必须有真实线上事故"],
    },
    "verification": {
        "label": "验证方法",
        "checks": ["说明具体要验证什么", "验证步骤和条件能够复现", "结果的判定依据能够支持结论"],
        "strong": "能指出验证可能遗漏的本题边界，或排除影响结论的其他因素。",
        "not_required": ["题目没有涉及的数据集", "编造量化收益", "未被问到的个人职责分工"],
    },
    "ownership": {
        "label": "个人职责与经历",
        "checks": ["区分个人工作与团队工作", "说明本人做过的具体步骤或决策", "叙述与本题简历陈述一致"],
        "strong": "能说明本人处理的关键困难和职责边界，不把团队成果全部归为个人成果。",
        "not_required": ["必须主导整个项目", "未被问到的底层原理", "编造收益指标"],
    },
}


def select_profile(question_text: str, probe_hint: str = "") -> str:
    # Resume/JD quotes supply the topic, not the type of question being asked.
    question = re.sub(r"[「“].*?[」”]", "", question_text)
    patterns = [
        ("concept", r"是什么|原理|机制|区别|之间.*关系|如何工作|为什么.*(?:能|会)|概念"),
        ("troubleshooting", r"排查|定位|故障|出错|失败场景|失败原因|出现.*问题|怎么.*修复|如何.*修复"),
        ("tradeoff", r"为什么.*(?:用|选)|为何|不用|权衡|优缺点|方案.*比较|取舍"),
        ("ownership", r"亲自|你本人|个人.*贡献|职责|分工|亲历"),
        ("verification", r"如何验证|怎么验证|如何评估|怎么评估|如何测试|怎么测试|测试方法|如何.*证明"),
    ]
    for profile, pattern in patterns:
        if re.search(pattern, question):
            return profile
    for profile, pattern in [
        ("tradeoff", r"取舍|选择|方案.*比较"),
        ("verification", r"验证|评测|测试"),
        ("troubleshooting", r"失败|故障|排查|边界"),
        ("ownership", r"亲历|贡献|职责"),
        ("concept", r"原理|概念|机制|关系|区别"),
    ]:
        if re.search(pattern, probe_hint):
            return profile
    return "implementation"


def build_question_rubric(
    *,
    question_text: str,
    requirement_id: str,
    requirement_text: str,
    claim_id: str | None = None,
    probe_key: str | None = None,
    probe_hint: str = "",
) -> dict[str, Any]:
    profile = select_profile(question_text, probe_hint)
    definition = deepcopy(PROFILES[profile])
    return {
        "version": "v2",
        "template_id": f"rubric_{profile}_v2",
        "profile": profile,
        "label": definition["label"],
        "scope": {
            "question": normalize_text(question_text),
            "requirement_id": requirement_id,
            "requirement_text": requirement_text,
            "claim_id": claim_id,
            "probe_key": probe_key,
            "probe_hint": probe_hint,
        },
        "checks": definition["checks"],
        "levels": {
            "0": "有明确原话证明本题核心概念或做法错误。离题、未作答或信息不足不能直接打 0 分。",
            "1": "有相关内容，但本题的核心问题只回答了一部分；写明具体缺少哪一步或哪项解释。",
            "2": "本题核心问题已正确回答，关键解释或步骤清楚；不要求额外回答题目之外的内容。",
            "3": definition["strong"],
        },
        "null_rule": "没有足够原话判断、题目不适用或资料冲突时，level 为 null，并说明原因。",
        "not_required": definition["not_required"],
        "rules": [
            "checks 仅在本题实际问到时适用；不能把某项未提到直接认定为不掌握。",
            "仅判断本题涉及的技术内容，不能用提到某个关键词代替正确性判断。",
            "每个分数引用本方向真实回答原话；引用证明文字存在，不自动证明技术结论正确。",
            "允许符合场景的不同实现；表达简短或没有量化指标不是单独扣分理由。",
            "追问合并此前回答判断，补齐的缺口不再计入 missing；保留尚未解决的错误或矛盾。",
        ],
    }
