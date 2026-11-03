"""Fresh scenario inputs, frozen before v3 testing; AI reference labels, not human gold."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from evals.evaluator_quality import REVIEW_FIELDS, digest, input_digest
from evals.quality_cases import _snapshot

VERSION = "agent_scenarios_20261026_v1"
GROUPS = [
    (
        "history_trim",
        "上下文裁剪的收益与代价",
        "多轮面试上下文越来越长，为什么选择按方向保留根问题和关键回答，而不是每轮发送全部历史？请说明收益与代价。",
        "实现多轮面试的历史筛选与上下文裁剪。",
        "实现工具结果的结构化保存与失败处理。",
        "工具结果和失败状态管理",
        [
            "把根问题和用户约束全部删掉，只保留最后一句，一定能同时提高准确率和省钱，不需要其他处理。",
            "少发送一些历史，可以减少Token。",
            "按方向保留根问题和关键回答能减少无关输入、Token和延迟；代价是可能遗漏跨方向关联，需要维护筛选逻辑。",
            "按方向保留根题和关键回答能减少无关输入、Token和延迟，但会丢失跨方向线索并增加筛选维护。若后续任务需要跨方向核对，就按需取回相关历史，而不是始终发送全部内容。",
            "没有考虑过，暂时不会。",
        ],
    ),
    (
        "tool_failure",
        "工具异常与成功声明的排查修复",
        "Agent调用检索工具返回超时，却向用户声称查到了资料，你会如何定位、修复并验证？",
        "实现检索工具调用状态跟踪和异常处理。",
        "实现会话历史的分段摘要与按需检索。",
        "长会话记忆管理",
        [
            "把超时错误包装成成功并编造几段资料，这样用户就看不到错误，也说明问题修复了。",
            "在提示词里写不要乱说成功。",
            "回放工具调用与回复轨迹，确认错误在哪里被当成成功；把超时作为独立失败状态，不把它放进有效证据，回复明确未检索成功；用超时注入和正常检索回归确认。",
            "回放调用和回复轨迹定位失败状态被吞掉的位置，把超时与有效证据分开，禁止用失败结果生成成功声明；回归超时和正常调用。还要区分空结果与超时，检查重试或降级后仍不编造资料，避免把所有空结果都误报成服务故障。",
            "不知道该怎么处理。",
        ],
    ),
    (
        "no_source",
        "检索无证据时的回答约束",
        "知识库没有相关资料时，如何让问答Agent给出受约束的回复？请说明从检索结果到最终回答的处理流程。",
        "实现知识库无证据时的回复与引用控制。",
        "实现知识来源的引用展示与版本记录。",
        "来源与版本可追溯",
        [
            "没有检索结果就虚构一个文档ID和引用，格式合法就能当成有证据的答案。",
            "提示词要求模型别编造。",
            "先检查检索结果是否提供相关证据，无证据时明确说明资料不足或请求补充；生成时只允许引用返回的文档，后端校验来源ID，不把编造引用发布出去。",
            "检查相关证据，无证据时说明资料不足或澄清，生成仅引用返回文档并由后端校验来源。还要区分资料不足与事实错误；若产品允许库外常识回答，应单独标记为一般知识，不能伪装成知识库结论。",
            "这个问题我不会。",
        ],
    ),
    (
        "retrieval_acl",
        "多租户检索隔离",
        "多租户知识库的检索Agent如何防止把其他用户文档返回给当前用户？请说明查询与读取的实现步骤。",
        "实现知识库文档的租户隔离与读取授权。",
        "实现检索候选去重与重排。",
        "检索结果去重和排序",
        [
            "让模型填写任意tenant_id，后端完全信任这个值，再搜索整个数据库，最后让模型自行过滤就行。",
            "在检索提示词里写不要看别人的文档。",
            "后端从认证身份取得租户，不接受模型指定身份；检索查询强制加租户过滤，文档读取再校验归属，只把通过检查的内容交给模型。",
            "从认证身份取租户并在检索强制过滤，文档读取再次校验归属。重排、引用展开等后续读取也沿用同一身份；用跨租户文档ID和相似内容做负例测试，避免只在首次检索过滤而后续绕过。",
            "没有做过，不知道。",
        ],
    ),
    (
        "budget_stop",
        "Agent工具循环终止",
        "Agent反复调用工具没有产出时，怎样实现可控终止？请说明预算检查与结束处理。",
        "实现Agent任务的工具次数与截止时间限制。",
        "实现训练题目的提示等级记录。",
        "提示辅助与独立证据记录",
        [
            "让模型无限重试直到成功，失败调用不计次数，也不检查时间，这样不会浪费预算。",
            "提示词告诉模型最多调用几次工具。",
            "后端为任务维护调用次数和截止时间，每次调用前检查并消耗额度，失败也计入；达到限制就停止新调用并返回未完成状态，保留已经取得的有效结果。",
            "后端维护次数和截止时间，在调用前扣额度，失败也计入，耗尽后停止并返回未完成状态。若并发调用需要原子预留，防止同时越额；已发请求可能仍产生费用，终止不能假装这些调用从未发生。",
            "没有思路。",
        ],
    ),
    (
        "reference_conflict",
        "证据不足与资料冲突的区分",
        "检索到两份资料的结论相互矛盾时，证据冲突与证据不足是什么关系？评分Agent该怎样处理？",
        "实现参考资料冲突标记与评估复核。",
        "实现多轮追问根问题与方向绑定。",
        "追问方向与根题关联",
        [
            "资料有矛盾就自动认定候选人技术错误打0分，随便选一份资料做引用，不需要核对适用条件。",
            "资料不一致时先不确定分数。",
            "证据不足是缺少可判断信息，冲突是已有信息不一致；先核对版本、时间和适用条件，仍无法裁定就标记冲突并暂不猜分，保留双方来源，不能直接给候选人0分。",
            "区分缺少信息和已有信息冲突，核对版本、时间与适用条件，未裁定时标记冲突并暂不猜分，保留双方来源。若适用条件能消解矛盾，则按有效资料恢复评估并记录裁定依据，不能机械选最新或分数最高的说法。",
            "分不清楚。",
        ],
    ),
    (
        "prompt_eval",
        "提示词修改的受控评测",
        "你修改了Agent提示词，如何验证收益来自这次修改，而不是模型或样例变化？请说明对照步骤与判定。",
        "实现Agent提示词版本与离线对照评测。",
        "实现评分证据定位与复核版本保存。",
        "评分证据可追溯",
        [
            "新提示词用更强模型和更简单的题，旧提示词用弱模型和难题，只要新结果高就说明提示词一定更好。",
            "挑一个回答看着更顺眼就算提升。",
            "固定模型、问答、评分标准和生成参数，只替换提示词；按预先定义的任务成功率、失败率和Token成本配对比较并保留失败，多次运行查看波动，再决定收益是否成立。",
            "固定模型、问答、标准与参数，仅换提示词，按预先定义的任务成功率、失败率和Token成本配对比较并保留多次运行波动；开发集调试后冻结测试集，不能挑成功题或把重复调用当独立题，避免把样例选择当提示词收益。",
            "没有做过这种验证。",
        ],
    ),
    (
        "new_ownership",
        "模型接入与报告开发职责",
        "简历写到“接入模型服务并实现面试报告”，请说明你亲自完成的部分，以及外部组件和团队工作的边界。",
        "接入模型服务并实现面试报告。",
        "为训练流程实现无提示变式复测。",
        "独立作答复测设计",
        [
            "我调用了模型API，所以基础模型和第三方报表库也都算我独立开发，团队其他人的页面也记在我名下。",
            "我主要做模型和报告相关工作。",
            "我实现模型请求封装、结构化结果校验和报告能力汇总；基础模型与SDK是外部服务和库，页面由团队成员实现，我不把它们算作独立开发。",
            "我实现模型请求封装、结果校验和报告汇总，模型与SDK是外部依赖，页面由同事负责。本人处理的难点是晚到评分覆盖新版本，我加了版本校验并用乱序返回案例确认旧结果不覆盖最新报告。",
            "说不出具体做了什么。",
        ],
    ),
]


def fresh_cases() -> list[dict]:
    result = []
    for group, requirement, question, claim, other_claim, other_req, answers in GROUPS:
        for index, answer in enumerate(answers):
            level = index if index < 4 else None
            case_id = f"{group}_{index if level is not None else 'null'}"
            snapshot = _snapshot(case_id, question, requirement, answer)
            result.append(
                {
                    "case_id": case_id,
                    "group_id": group,
                    "split": "test",
                    "category": "insufficient"
                    if level is None
                    else "explicit_error"
                    if level == 0
                    else "graded",
                    "suggested_levels": [level],
                    "suggested_reason": (
                        "明确错误，原话与本题核心要求相反。"
                        if level == 0
                        else "有相关技术内容，但只回答核心的一部分。"
                        if level == 1
                        else "核心检查项齐全，未展开该题型的深入边界。"
                        if level == 2
                        else "核心检查项齐全，并解释本题型要求的代价、适用条件、误判风险或个人困难。"
                        if level == 3
                        else "没有足够可判断的技术内容，不能直接打0分。"
                    ),
                    "reference_provenance": "AI_REFERENCE_NOT_HUMAN_GOLD",
                    "snapshot": snapshot,
                    "interview_materials": {
                        "claim_text": claim,
                        "other_claim_text": other_claim,
                        "other_requirement_text": other_req,
                        "other_probe_hints": ["具体实现过程", "验证方法与失败边界"],
                    },
                }
            )
    return result


def write_dataset(directory: Path, cases: list[dict], version: str) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "dataset.json").exists() or (directory / "labels.csv").exists():
        raise ValueError("dataset already exists; do not overwrite a frozen experiment")
    dataset = {"version": version, "cases": cases}
    (directory / "dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (directory / "labels.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        for case in cases:
            snap = case["snapshot"]
            writer.writerow(
                {
                    "case_id": case["case_id"],
                    "input_sha256": input_digest(case),
                    "split": case["split"],
                    "question": snap["question_text"],
                    "answer": snap["answer_text"],
                    "prior_thread": json.dumps(snap["thread"][:-1], ensure_ascii=False),
                    "rubric": json.dumps(snap["rubric_criteria"], ensure_ascii=False),
                    "proposed_levels": json.dumps(case["suggested_levels"]),
                    "proposed_reason": case["suggested_reason"],
                    "status": "DRAFT",
                }
            )
    manifest = {
        "frozen_at_utc": datetime.now(UTC).isoformat(),
        "dataset_sha256": digest(dataset),
        "reference_provenance": "AI reviewed before target model output, not human gold",
        "cases": len(cases),
        "groups": len({c["group_id"] for c in cases}),
    }
    (directory / "reference_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_dataset(args.data_dir, fresh_cases(), VERSION), indent=2))


if __name__ == "__main__":
    main()
