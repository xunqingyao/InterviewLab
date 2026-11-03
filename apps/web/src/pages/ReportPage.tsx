import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, ensureLogin } from "../api";
import { Icon } from "../components/Icon";
import {
  Alert,
  Badge,
  Button,
  EmptyState,
  LevelMeter,
  LinkButton,
  Modal,
  PageHeader,
  Skeleton,
  StatTile,
} from "../components/ui";
import {
  assessmentMeta,
  averageLevel,
  cleanLabel,
  errorMessage,
  evidenceMeta,
  hintMeta,
  levelMeta,
  modeMeta,
  friendlyError,
  prettyRequirement,
  reportMeta,
  shortId,
  turnActionLabel,
  turnStatusMeta,
} from "../lib/format";

type Evidence = { message_id: string; quote: string; start_char?: number; end_char?: number };

type Dimension = {
  skill_id: string;
  level: number | null;
  evidence_status: string;
  demonstrated?: string;
  missing?: string;
  hint_level?: string;
  answer_evidence?: Evidence[];
  reference_chunk_ids?: string[];
};

type Assessment = {
  id: string;
  revision: number;
  status: string;
  dimensions?: Dimension[];
};

type ReportTurn = {
  turn_id: string;
  turn_no: number;
  action: string;
  parent_turn_id?: string | null;
  status: string;
  question_text: string;
  answer_text?: string;
  rubric_snapshot_id?: string | null;
  assessment?: Assessment | null;
};

type CoverageEvidence = {
  level: number | null;
  demonstrated?: string;
  missing?: string;
  turn_id?: string;
  turn_no?: number;
  hint_level?: string;
  answer_evidence?: Evidence[];
  assessment_id?: string;
};

type Coverage = {
  skill_id: string;
  requirement_id?: string;
  label?: string;
  levels?: (number | null)[];
  sample_count: number;
  observation_count: number;
  independent_sample_count: number;
  null_count: number;
  assessment_status: string;
  evidence?: CoverageEvidence[];
};

type ReportBody = {
  session_id?: string;
  mode?: string;
  cutoff_turn_no?: number;
  coverage?: Coverage[];
  issues?: { kind?: string; detail?: string; turn_id?: string; skill_id?: string }[];
  next_training?: string[];
  limitations?: string[];
  effective_assessment_count?: number;
  integrity?: string;
};

type RubricSnapshot = { id: string; template_id?: string; criteria?: Record<string, unknown> };

type Report = {
  id: string;
  revision: number;
  status: string;
  body: ReportBody;
  coverage?: Coverage[] | { coverage?: Coverage[]; issues?: unknown[] };
  messages: Record<string, { id: string; turn_id?: string | null; kind?: string; text: string }>;
  questions?: Record<string, string>;
  rubrics?: Record<string, RubricSnapshot>;
  turns: ReportTurn[];
  training?: { eligible?: boolean; reason?: string };
  integrity?: string;
  missing_turn_ids?: string[];
};

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(asText).filter(Boolean).join("；");
  if (value && typeof value === "object") return JSON.stringify(value, null, 2);
  return "";
}

function Quote({ quote, messageId, messages }: { quote: string; messageId: string; messages: Report["messages"] }) {
  const [open, setOpen] = useState(false);
  const full = messages?.[messageId]?.text;
  const canExpand = Boolean(full && full.trim() !== quote.trim());

  return (
    <blockquote className="quote">
      <span className="quote-mark" aria-hidden="true">
        <Icon name="quote" size={14} />
      </span>
      <div className="quote-main">
        <p>{quote}</p>
        {canExpand ? (
          <button type="button" className="quote-more" onClick={() => setOpen((value) => !value)}>
            {open ? "收起原话" : "查看原话上下文"}
            <Icon name="chevronDown" size={13} className={open ? "rotate-180" : undefined} />
          </button>
        ) : null}
        {open ? <p className="quote-context">{full}</p> : null}
      </div>
    </blockquote>
  );
}

function DimensionRow({ dimension, messages }: { dimension: Dimension; messages: Report["messages"] }) {
  const evidence = evidenceMeta(dimension.evidence_status);
  const demonstrated = asText(dimension.demonstrated);
  const missing = asText(dimension.missing);

  return (
    <div className="dimension">
      <div className="dimension-head">
        <span className="dimension-name">{prettyRequirement(dimension.skill_id)}</span>
        <LevelMeter level={dimension.level} />
        <Badge tone={evidence.tone}>{evidence.label}</Badge>
      </div>
      {demonstrated || missing ? (
        <div className="dimension-points">
          {demonstrated ? (
            <p className="dim-positive">
              <Icon name="check" size={14} />
              <span>{demonstrated}</span>
            </p>
          ) : null}
          {missing ? (
            <p className="dim-negative">
              <Icon name="alert" size={14} />
              <span>{missing}</span>
            </p>
          ) : null}
        </div>
      ) : null}
      {(dimension.answer_evidence ?? []).length > 0 ? (
        <div className="quote-list">
          {(dimension.answer_evidence ?? []).slice(0, 3).map((item, index) => (
            <Quote key={`${item.message_id}-${index}`} quote={item.quote} messageId={item.message_id} messages={messages} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function CriteriaView({ criteria }: { criteria: Record<string, unknown> }) {
  const goal = asText(criteria.goal);
  const label = asText(criteria.label);
  const levels = (criteria.levels && typeof criteria.levels === "object" ? criteria.levels : null) as
    | Record<string, string>
    | null;
  const scope = (criteria.scope && typeof criteria.scope === "object" ? criteria.scope : null) as
    | Record<string, unknown>
    | null;
  const checks = Array.isArray(criteria.checks) ? criteria.checks : null;
  const rules = Array.isArray(criteria.rules) ? criteria.rules : null;
  const alternatives = Array.isArray(criteria.acceptable_alternatives) ? criteria.acceptable_alternatives : null;
  const pitfalls = Array.isArray(criteria.pitfalls) ? criteria.pitfalls : null;
  const known = new Set([
    "goal",
    "label",
    "levels",
    "scope",
    "checks",
    "rules",
    "acceptable_alternatives",
    "pitfalls",
    "profile",
    "version",
  ]);
  const rest = Object.keys(criteria).filter((key) => !known.has(key));

  return (
    <div className="criteria">
      {label ? <p className="criteria-label">{label}</p> : null}
      {goal ? <p className="criteria-goal">{goal}</p> : null}
      {scope?.requirement_text ? (
        <div className="criteria-block">
          <h4>对应岗位要求</h4>
          <p>{asText(scope.requirement_text)}</p>
        </div>
      ) : null}
      {levels ? (
        <div className="criteria-block">
          <h4>等级描述</h4>
          <ol className="level-list">
            {["0", "1", "2", "3"].map((key) =>
              levels[key] ? (
                <li key={key} data-tone={levelMeta(Number(key)).tone}>
                  <span className="level-key">L{key}</span>
                  <span>{levels[key]}</span>
                </li>
              ) : null,
            )}
          </ol>
        </div>
      ) : null}
      {checks && checks.length ? (
        <div className="criteria-block">
          <h4>检查点</h4>
          <ul className="plain-list">
            {checks.map((item, index) => (
              <li key={index}>{asText(item)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {rules && rules.length ? (
        <div className="criteria-block">
          <h4>评分规则</h4>
          <ul className="plain-list">
            {rules.map((item, index) => (
              <li key={index}>{asText(item)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {alternatives && alternatives.length ? (
        <div className="criteria-block">
          <h4>可接受的替代说法</h4>
          <div className="tag-cloud">
            {alternatives.map((item, index) => (
              <span className="tag" key={index}>
                {asText(item)}
              </span>
            ))}
          </div>
        </div>
      ) : null}
      {pitfalls && pitfalls.length ? (
        <div className="criteria-block">
          <h4>常见扣分点</h4>
          <ul className="pitfall-list">
            {pitfalls.map((item, index) => (
              <li key={index}>
                <Icon name="alert" size={14} />
                <span>{asText(item)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {rest.length ? (
        <details className="raw-details">
          <summary>其他字段</summary>
          <pre>{JSON.stringify(Object.fromEntries(rest.map((key) => [key, criteria[key]])), null, 2)}</pre>
        </details>
      ) : null}
    </div>
  );
}

export default function ReportPage() {
  const { id = "" } = useParams();
  const [rubricId, setRubricId] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [reviewMsg, setReviewMsg] = useState("");
  const [pendingReport, setPendingReport] = useState(false);

  const query = useQuery({
    queryKey: ["report", id],
    queryFn: async () => {
      await ensureLogin();
      return api<Report>(`/api/v1/interviews/${id}/reports/latest`);
    },
    retry: 1,
    refetchInterval: (current) =>
      current.state.data?.status === "PARTIAL" || pendingReport ? 2000 : false,
  });

  const report = query.data;
  const mode = modeMeta(report?.body?.mode);
  const integrity = reportMeta(report?.status);
  const dimensions = useMemo(
    () => (report?.turns ?? []).flatMap((turn) => turn.assessment?.dimensions ?? []),
    [report],
  );
  const average = averageLevel(dimensions.map((dimension) => dimension.level));
  const coverage = useMemo<Coverage[]>(() => {
    if (!report) return [];
    if (Array.isArray(report.body?.coverage)) return report.body.coverage;
    if (Array.isArray(report.coverage)) return report.coverage;
    return report.coverage?.coverage ?? [];
  }, [report]);
  const assessedRequirements = coverage.filter((item) => item.assessment_status === "ASSESSED").length;
  const issues = report?.body?.issues ?? [];
  const nextTraining = report?.body?.next_training ?? [];

  async function requestReview(assessmentId: string, label: string) {
    setReviewing(assessmentId);
    setReviewMsg("");
    try {
      await ensureLogin();
      await api(`/api/v1/assessments/${assessmentId}/reviews`, {
        method: "POST",
        body: JSON.stringify({ reason: `申请复核：${label}` }),
      });
      await api(`/api/v1/interviews/${id}/reports`, { method: "POST" });
      setReviewMsg("复核申请已提交，报告会重新生成，页面会自动刷新。");
      window.setTimeout(() => query.refetch(), 2000);
    } catch (err) {
      setReviewMsg(`提交失败：${errorMessage(err)}`);
    } finally {
      setReviewing(null);
    }
  }

  async function generateReport() {
    setPendingReport(true);
    setReviewMsg("");
    try {
      await ensureLogin();
      await api(`/api/v1/interviews/${id}/reports`, { method: "POST" });
      setReviewMsg("已提交生成任务，通常几秒后完成。");
      window.setTimeout(() => query.refetch(), 1500);
    } catch (err) {
      setReviewMsg(`生成失败：${errorMessage(err)}`);
      setPendingReport(false);
    }
  }

  if (query.isLoading) {
    return (
      <div className="stack-24 page-enter">
        <div className="card">
          <div className="stack-16">
            <Skeleton width="18%" height={13} />
            <Skeleton width="46%" height={30} />
            <Skeleton width="64%" height={14} />
            <div className="stat-row">
              {[0, 1, 2, 3].map((key) => (
                <Skeleton key={key} height={96} radius={16} />
              ))}
            </div>
            <Skeleton height={220} radius={16} />
          </div>
        </div>
      </div>
    );
  }

  if (query.isError || !report) {
    return (
      <div className="stack-24 page-enter">
        <EmptyState
          icon="file"
          tone="warn"
          title="这场面试还没有报告"
          description={
            query.error instanceof Error
              ? `${friendlyError(query.error.message)}如果是刚结束的面试，报告可能还在生成；也可以手动触发一次。`
              : "报告还没有生成。如果是刚结束的面试，可以在这里触发一次生成。"
          }
          action={
            <div className="row-actions">
              <Button variant="primary" iconLeft="spark" onClick={generateReport} loading={pendingReport}>
                生成复盘报告
              </Button>
              <LinkButton to={`/interviews/${id}`} variant="ghost">
                回到面试
              </LinkButton>
              <LinkButton to="/hall" variant="ghost">
                面试大厅
              </LinkButton>
            </div>
          }
        />
        {reviewMsg ? <Alert tone="accent">{reviewMsg}</Alert> : null}
      </div>
    );
  }

  const cutoff = report.body.cutoff_turn_no ?? report.turns.length;
  const rubric = rubricId ? report.rubrics?.[rubricId] : null;

  return (
    <div className="stack-32 page-enter">
      <PageHeader
        eyebrow="复盘报告"
        title={mode.label === "严格模式" ? "严格面试复盘" : `${mode.label}复盘`}
        description={`共 ${report.turns.length} 题，评分基于本场冻结的简历、岗位要求与评分标准。报告只描述本场表现，不跨场次合并。`}
        meta={
          <>
            <Badge tone={integrity.tone}>{integrity.label}</Badge>
            <Badge tone="neutral">第 {report.revision} 版</Badge>
            <span className="muted-small">
              场次 {shortId(report.body.session_id ?? "")} · 截止第 {cutoff} 题 · 共 {dimensions.length} 项能力评分
            </span>
          </>
        }
        actions={
          <>
            <LinkButton to={`/training?session=${id}`} variant="primary" iconLeft="activity">
              去训练
            </LinkButton>
            <LinkButton to="/hall" variant="ghost" iconLeft="arrowLeft">
              面试大厅
            </LinkButton>
          </>
        }
      />

      {report.status === "PARTIAL" ? (
        <Alert tone="warn" title="报告还在生成">
          部分题目尚未评完，页面每 2 秒自动刷新。看到「未评估」说明评分还没回来，不代表答得不好，也不会计成 0 分。
        </Alert>
      ) : null}
      {reviewMsg ? <Alert tone="accent">{reviewMsg}</Alert> : null}
      {issues.length ? (
        <Alert tone="warn" title={`有 ${issues.length} 处证据需要补充`}>
          {issues[0]?.detail || "部分岗位要求缺少可验证的证据，报告里已按「证据不足」处理。"}
        </Alert>
      ) : null}

      <div className="stat-row">
        <StatTile
          icon="target"
          label="平均等级"
          value={average === null ? "—" : average.toFixed(1)}
          unit="/ 3"
          hint={average === null ? "还没有有效评分" : levelMeta(Math.round(average)).label}
          tone={average === null ? "neutral" : levelMeta(Math.round(average)).tone}
        />
        <StatTile icon="checkCircle" label="已评估题目" value={dimensions.length} unit="项能力" hint={`共 ${report.turns.length} 题`} />
        <StatTile
          icon="compass"
          label="覆盖岗位要求"
          value={assessedRequirements}
          unit={`/ ${coverage.length}`}
          hint="有有效证据的要求数"
          tone={assessedRequirements === coverage.length && coverage.length > 0 ? "success" : "neutral"}
        />
        <StatTile
          icon="chart"
          label="报告完整度"
          value={report.status === "COMPLETE" ? "完整" : "部分"}
          hint={report.missing_turn_ids?.length ? `${report.missing_turn_ids.length} 题缺失` : "全部题目已收录"}
          tone={report.status === "COMPLETE" ? "success" : "warn"}
        />
      </div>

      <nav className="section-nav" aria-label="报告导航">
        <a href="#turns">逐题复盘</a>
        <a href="#coverage">岗位能力</a>
        <a href="#training">训练建议</a>
      </nav>

      <section id="turns" className="stack-20 section-block">
        <div className="section-head">
          <h2>
            逐题复盘
            <Badge tone="neutral">{report.turns.length} 题</Badge>
          </h2>
          <p>每题的评分来自本场评分标准快照；追问会结合前面的回答，不是独立的新题。</p>
        </div>

        <div className="stack-16">
          {report.turns.map((turn) => {
            const action = turnActionLabel(turn.action);
            const status = turnStatusMeta(turn.status);
            const assessment = turn.assessment ?? null;
            const assessmentStatus = assessmentMeta(assessment?.status);
            const turnDimensions = assessment?.dimensions ?? [];

            return (
              <article className="card turn-card" key={turn.turn_id}>
                <header className="turn-card-head">
                  <div className="turn-card-title">
                    <span className="turn-no">{String(turn.turn_no).padStart(2, "0")}</span>
                    <h3>第 {turn.turn_no} 题</h3>
                    <Badge tone="neutral">{action}</Badge>
                    <Badge tone={status.tone}>{status.label}</Badge>
                    {turnDimensions.length ? (
                      <span className="turn-score">
                        <LevelMeter level={averageLevel(turnDimensions.map((dimension) => dimension.level))} size="sm" />
                      </span>
                    ) : (
                      <Badge tone={assessmentStatus.tone}>{assessmentStatus.label}</Badge>
                    )}
                  </div>
                  <div className="turn-card-actions">
                    {turn.rubric_snapshot_id && report.rubrics?.[turn.rubric_snapshot_id] ? (
                      <Button variant="ghost" size="sm" iconLeft="book" onClick={() => setRubricId(turn.rubric_snapshot_id ?? null)}>
                        评分标准
                      </Button>
                    ) : null}
                    {assessment?.id ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        iconLeft="refresh"
                        loading={reviewing === assessment.id}
                        onClick={() => requestReview(assessment.id, `第 ${turn.turn_no} 题`)}
                      >
                        申请复核
                      </Button>
                    ) : null}
                  </div>
                </header>

                <div className="turn-card-body">
                  <div className="turn-block">
                    <span className="turn-block-label">题目</span>
                    <p className="turn-question">{turn.question_text}</p>
                  </div>
                  <div className="turn-block answer-block">
                    <span className="turn-block-label">你的回答</span>
                    {turn.answer_text ? (
                      <p className="turn-answer">{turn.answer_text}</p>
                    ) : (
                      <p className="turn-empty">未作答。跳过的题不参与评分，也不算 0 分。</p>
                    )}
                  </div>

                  {turnDimensions.length ? (
                    <div className="assessment">
                      {turnDimensions.map((dimension, index) => (
                        <DimensionRow key={`${dimension.skill_id}-${index}`} dimension={dimension} messages={report.messages} />
                      ))}
                    </div>
                  ) : (
                    <p className="turn-empty">
                      {assessment ? `本题暂无有效评分：${assessmentStatus.label}。` : "本题还没有评分。"}
                      {report.status === "PARTIAL" ? "报告生成完成后会自动补上。" : ""}
                    </p>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <section id="coverage" className="stack-20 section-block">
        <div className="section-head">
          <h2>
            岗位能力
            <Badge tone="neutral">{coverage.length} 项要求</Badge>
          </h2>
          <p>同一方向的证据会跨题合并。没有有效证据的要求不会算作「会」或「不会」，只标成证据不足。</p>
        </div>

        {coverage.length === 0 ? (
          <EmptyState icon="compass" title="没有覆盖数据" description="这场面试还没有生成岗位能力覆盖。" />
        ) : (
          <div className="coverage-grid">
            {coverage.map((item) => {
              const label = cleanLabel(item.label, item.requirement_id || item.skill_id);
              const levels = item.levels ?? [];
              const evidenceItems = item.evidence ?? [];
              const assessed = item.assessment_status === "ASSESSED";
              return (
                <article className="card coverage-card" key={`${item.skill_id}-${item.requirement_id ?? ""}`}>
                  <header className="coverage-head">
                    <div>
                      <h3>{label}</h3>
                      <p className="coverage-meta">
                        {item.sample_count} 次观察 · {item.independent_sample_count} 次独立作答
                        {item.null_count ? ` · ${item.null_count} 次未评估` : ""}
                      </p>
                    </div>
                    {assessed ? <LevelMeter level={averageLevel(levels)} /> : <Badge tone="neutral">证据不足</Badge>}
                  </header>

                  {evidenceItems.length ? (
                    <div className="coverage-evidence">
                      {evidenceItems.map((evidence, index) => (
                        <div className="evidence-item" key={`${evidence.turn_id ?? "t"}-${index}`}>
                          <div className="evidence-head">
                            <span className="evidence-turn">第 {evidence.turn_no ?? "—"} 题</span>
                            <LevelMeter level={evidence.level} size="sm" showText={false} />
                            {evidence.hint_level ? (
                              <Badge tone={hintMeta(evidence.hint_level).tone}>{hintMeta(evidence.hint_level).label}</Badge>
                            ) : null}
                          </div>
                          {asText(evidence.demonstrated) ? <p className="dim-positive">{asText(evidence.demonstrated)}</p> : null}
                          {asText(evidence.missing) ? <p className="dim-negative">{asText(evidence.missing)}</p> : null}
                          {(evidence.answer_evidence ?? []).slice(0, 1).map((quote, quoteIndex) => (
                            <Quote
                              key={`${quote.message_id}-${quoteIndex}`}
                              quote={quote.quote}
                              messageId={quote.message_id}
                              messages={report.messages}
                            />
                          ))}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="turn-empty">{assessed ? "这项要求没有留下可引用的原话。" : "本场没有覆盖到这项要求的有效证据。"}</p>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </section>

      <section id="training" className="stack-20 section-block">
        <div className="section-head">
          <h2>训练建议</h2>
          <p>只针对本场暴露出的证据缺口安排最小训练单元，不会把全站题库都摆出来。</p>
        </div>

        <div className="training-layout">
          <article className="card training-card">
            <h3 className="side-title">
              <Icon name="activity" size={16} /> 优先补强
            </h3>
            {nextTraining.length ? (
              <div className="tag-cloud">
                {nextTraining.map((skill) => (
                  <span className="tag tag-strong" key={skill}>
                    {prettyRequirement(skill)}
                  </span>
                ))}
              </div>
            ) : (
              <p className="turn-empty">这场面试没有推荐补强的要求。</p>
            )}
            <p className="field-hint">
              练习会先给你最小提示，复测必须独立作答。看过提示或示范的作答不会算作独立掌握。
            </p>
            <LinkButton to={`/training?session=${id}`} variant="primary" iconRight="arrowRight">
              去训练页生成计划
            </LinkButton>
          </article>

          <article className="card training-card">
            <h3 className="side-title">
              <Icon name="info" size={16} /> 口径说明
            </h3>
            <ul className="plain-list limitations">
              {(report.body.limitations ?? []).map((item, index) => (
                <li key={index}>{item}</li>
              ))}
              <li>报告只反映本场表现，不合并其它场次的证据。</li>
            </ul>
            {report.training?.reason ? <p className="muted-small">{report.training.reason}</p> : null}
            <p className="muted-small">
              报告版本 v{report.revision} · {integrity.label} · 截止第 {cutoff} 题
            </p>
          </article>
        </div>
      </section>

      <p className="page-footnote">
        <Icon name="lock" size={14} />
        评分基于本场冻结的文本与评分标准；对话记录只用于复盘的引用。<Link to="/skills"> 查看技能证据时间线</Link>
      </p>

      <Modal
        open={Boolean(rubricId)}
        onClose={() => setRubricId(null)}
        title="本题评分标准快照"
        subtitle="面试开始时冻结的版本，之后不会随题库更新。"
        size="lg"
        footer={
          <Button variant="ghost" onClick={() => setRubricId(null)}>
            关闭
          </Button>
        }
      >
        {rubric?.criteria ? <CriteriaView criteria={rubric.criteria} /> : <p className="muted-small">这条评分标准没有可展示的内容。</p>}
      </Modal>
    </div>
  );
}
