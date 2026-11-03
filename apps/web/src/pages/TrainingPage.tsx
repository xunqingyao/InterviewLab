import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, ensureLogin } from "../api";
import { Icon } from "../components/Icon";
import {
  Alert,
  Badge,
  Button,
  EmptyState,
  LevelMeter,
  LinkButton,
  PageHeader,
  ProgressBar,
  Skeleton,
  StatTile,
} from "../components/ui";
import {
  cleanLabel,
  errorMessage,
  friendlyError,
  evidenceMeta,
  formatDateTime,
  hintMeta,
  isUuid,
  modeMeta,
  prettyRequirement,
  truncate,
  sessionStatusMeta,
  shortId,
} from "../lib/format";

type Dimension = {
  skill_id: string;
  level: number | null;
  evidence_status: string;
  demonstrated?: string;
  missing?: string;
  hint_level?: string;
};

type Gap = { priority?: string; skill_id?: string; description?: string };

type Submission = {
  id: string;
  hint_level: string;
  independent: boolean;
  text?: string;
  result?: {
    status?: string;
    dimensions?: Dimension[];
    verification_gaps?: Gap[];
  } | null;
};

type Item = {
  id: string;
  kind: string;
  question_text: string;
  template_key: string;
  variant_template_key?: string | null;
  hint_templates?: { level: string; text: string }[];
  requirement_id?: string;
  requirement_label?: string;
  submissions: Submission[];
};

type Plan = {
  id: string;
  session_id: string;
  skill_ids?: string[];
  rationale?: string;
  items: Item[];
};

type SessionRow = {
  id: string;
  status: string;
  phase: string;
  mode: string;
  created_at: string;
};

type ReportLite = {
  id: string;
  status: string;
  training?: { eligible?: boolean; reason?: string };
  body?: {
    next_training?: string[];
    coverage?: { label?: string; requirement_id?: string; skill_id: string }[];
    training?: { eligible?: boolean; reason?: string };
  };
};

type TrainingJob = {
  id: string;
  status: string;
  attempt_count: number;
  last_error_code?: string | null;
  error_reason?: string | null;
  no_confirmed_weakness?: boolean;
};

const HINT_OPTIONS = [
  { level: "NONE", label: "独立作答", note: "计入独立掌握" },
  { level: "NUDGE", label: "看一句提示", note: "不算独立掌握" },
  { level: "STRUCTURE", label: "看回答结构", note: "不算独立掌握" },
  { level: "EXAMPLE", label: "看示范答案", note: "不算独立掌握" },
];

function Feedback({ submission }: { submission: Submission }) {
  const result = submission.result;
  const dimensions = result?.dimensions ?? [];
  const gaps = result?.verification_gaps ?? [];

  return (
    <div className="feedback">
      <div className="feedback-head">
        <Badge tone={submission.independent ? "success" : "warn"}>
          {submission.independent ? "独立作答" : hintMeta(submission.hint_level).label}
        </Badge>
        {result?.status ? <span className="muted-small">{result.status === "ASSESSED" ? "已评分" : "评分中"}</span> : <span className="muted-small">评分中…</span>}
      </div>
      {submission.text ? <p className="feedback-text">{submission.text}</p> : null}
      {dimensions.length ? (
        <div className="feedback-dims">
          {dimensions.map((dimension, index) => (
            <div className="feedback-dim" key={`${dimension.skill_id}-${index}`}>
              <div className="feedback-dim-head">
                <span>{prettyRequirement(dimension.skill_id)}</span>
                <LevelMeter level={dimension.level} size="sm" />
                <Badge tone={evidenceMeta(dimension.evidence_status).tone}>{evidenceMeta(dimension.evidence_status).label}</Badge>
              </div>
              {dimension.demonstrated ? <p className="dim-positive">{dimension.demonstrated}</p> : null}
              {dimension.missing ? <p className="dim-negative">{dimension.missing}</p> : null}
            </div>
          ))}
        </div>
      ) : null}
      {gaps.length ? (
        <div className="feedback-gaps">
          {gaps.slice(0, 2).map((gap, index) => (
            <p key={index}>
              <Icon name="info" size={13} />
              {gap.description}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ExerciseCard({
  item,
  isRetest,
  onSubmitted,
}: {
  item: Item;
  isRetest: boolean;
  onSubmitted: () => Promise<void> | void;
}) {
  const [hint, setHint] = useState("NONE");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [openForm, setOpenForm] = useState(item.submissions.length === 0);
  const [showHints, setShowHints] = useState(false);
  const [showQuestion, setShowQuestion] = useState(false);
  const latest = item.submissions[item.submissions.length - 1];
  const hintText = (item.hint_templates ?? []).find((template) => template.level === hint)?.text;
  const options = isRetest ? HINT_OPTIONS.slice(0, 1) : HINT_OPTIONS;
  const questionLong = item.question_text.length > 190;

  async function submit() {
    if (!text.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      await ensureLogin();
      await api(`/api/v1/exercises/${item.id}/submissions`, {
        method: "POST",
        body: JSON.stringify({ text, hint_level: hint }),
      });
      setText("");
      setOpenForm(false);
      await onSubmitted();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={isRetest ? "unit-item is-retest" : "unit-item"}>
      <div className="unit-head">
        <span className="unit-kind">{isRetest ? "复测" : "练习"}</span>
        {latest ? (
          <Badge tone={latest.independent ? "success" : "warn"}>{latest.independent ? "已独立完成" : "已提交"}</Badge>
        ) : (
          <span className="muted-small">还没作答</span>
        )}
        {!isRetest && (item.hint_templates ?? []).length ? (
          <button type="button" className="text-link unit-hint-toggle" onClick={() => setShowHints((value) => !value)}>
            {showHints ? "收起提示" : "需要提示？"}
            <Icon name="chevronDown" size={13} className={showHints ? "rotate-180" : undefined} />
          </button>
        ) : null}
      </div>

      <div className={questionLong && !showQuestion ? "unit-question is-clamped" : "unit-question"}>{item.question_text}</div>
      {questionLong ? (
        <button type="button" className="text-link" onClick={() => setShowQuestion((value) => !value)}>
          {showQuestion ? "收起题目" : "展开题目"}
          <Icon name="chevronDown" size={13} className={showQuestion ? "rotate-180" : undefined} />
        </button>
      ) : null}

      {isRetest ? (
        <p className="unit-note">
          <Icon name="lock" size={13} /> 复测必须独立作答，选择提示不会计入独立掌握。
        </p>
      ) : null}

      {showHints && !isRetest ? (
        <div className="hint-list">
          {(item.hint_templates ?? []).map((template) => (
            <div className="hint-row" key={template.level}>
              <Badge tone={hintMeta(template.level).tone}>{hintMeta(template.level).label}</Badge>
              <p>{template.text}</p>
            </div>
          ))}
        </div>
      ) : null}

      {latest ? <Feedback submission={latest} /> : null}

      {openForm ? (
        <div className="exercise-form">
          {!isRetest ? (
            <div className="field">
              <span className="field-label">这次怎么看提示</span>
              <div className="hint-picker">
                {options.map((option) => (
                  <button
                    type="button"
                    key={option.level}
                    className={option.level === hint ? "hint-choice is-active" : "hint-choice"}
                    onClick={() => setHint(option.level)}
                  >
                    <strong>{option.label}</strong>
                    <span>{option.note}</span>
                  </button>
                ))}
              </div>
              {hintText ? <p className="hint-text">{hintText}</p> : null}
            </div>
          ) : null}
          <textarea
            className="textarea"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={isRetest ? "不看提示，按自己的理解完整答一遍。" : "写清楚做法、结果和验证方式，尽量给数字。"}
            disabled={busy}
            aria-label="训练作答"
          />
          {error ? <Alert tone="danger">{error}</Alert> : null}
          <div className="exercise-form-foot">
            <span className="muted-small">{latest ? "再提交一次会追加一条记录。" : "提交后会自动评分。"}</span>
            <Button variant="primary" onClick={submit} loading={busy} disabled={!text.trim()} iconRight="arrowRight">
              提交{isRetest ? "复测" : "练习"}
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="ghost" size="sm" iconLeft="refresh" onClick={() => setOpenForm(true)}>
          {latest ? "再练一次" : "开始作答"}
        </Button>
      )}
    </div>
  );
}

export default function TrainingPage() {
  const [params, setParams] = useSearchParams();
  const [sessionId, setSessionId] = useState(params.get("session") ?? params.get("session_id") ?? "");
  const [manualId, setManualId] = useState("");
  const [manualError, setManualError] = useState("");
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState("");

  useEffect(() => {
    const next = params.get("session") ?? params.get("session_id") ?? "";
    if (next !== sessionId) setSessionId(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  function chooseSession(next: string) {
    setSessionId(next);
    setParams(next ? { session: next } : {});
    setGenError("");
    setManualError("");
  }

  const sessions = useQuery({
    queryKey: ["interviews"],
    queryFn: async () => {
      await ensureLogin();
      return api<{ items: SessionRow[] }>("/api/v1/interviews");
    },
    enabled: !sessionId,
  });

  const report = useQuery({
    queryKey: ["training-report", sessionId],
    queryFn: async () => {
      await ensureLogin();
      return api<ReportLite>(`/api/v1/interviews/${sessionId}/reports/latest`);
    },
    enabled: Boolean(sessionId),
    retry: 0,
  });

  const task = useQuery({
    queryKey: ["training-job", sessionId],
    queryFn: async () => {
      await ensureLogin();
      return api<{ job: TrainingJob | null }>(`/api/v1/training-plans/job?session_id=${sessionId}`);
    },
    enabled: Boolean(sessionId),
    refetchInterval: (query) => {
      const status = query.state.data?.job?.status;
      return status && ["QUEUED", "RUNNING", "RETRY_WAIT"].includes(status) ? 1200 : false;
    },
  });
  const trainingJob = task.data?.job;
  const jobPending = Boolean(trainingJob && ["QUEUED", "RUNNING", "RETRY_WAIT"].includes(trainingJob.status));

  const plans = useQuery({
    queryKey: ["training"],
    queryFn: async () => {
      await ensureLogin();
      return api<{ items: Plan[] }>("/api/v1/training-plans");
    },
    enabled: Boolean(sessionId),
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      const plan = items.find((entry) => entry.session_id === sessionId);
      if (!plan) return generating || jobPending ? 1200 : false;
      const pending = plan.items.some((item) => item.submissions.some((submission) => !submission.result));
      return pending ? 2000 : 10000;
    },
  });

  const plan = useMemo(
    () => (plans.data?.items ?? []).find((entry) => entry.session_id === sessionId) ?? null,
    [plans.data, sessionId],
  );

  useEffect(() => {
    if (trainingJob?.status === "SUCCEEDED") void plans.refetch();
  }, [trainingJob?.id, trainingJob?.status, sessionId]);

  const units = useMemo(() => {
    if (!plan) return [];
    const exercises = plan.items.filter((item) => item.kind === "EXERCISE");
    const retests = plan.items.filter((item) => item.kind === "RETEST");
    const used = new Set<string>();
    const paired = exercises.map((exercise) => {
      const match = retests.find((retest) => retest.template_key === exercise.variant_template_key && !used.has(retest.id));
      if (match) used.add(match.id);
      return { exercise, retest: match ?? null, key: exercise.id };
    });
    const extras = retests
      .filter((retest) => !used.has(retest.id))
      .map((retest) => ({ exercise: null, retest, key: retest.id }));
    return [...paired, ...extras];
  }, [plan]);

  const totalUnits = plan?.items.length ?? 0;
  const doneUnits = useMemo(
    () => (plan ? plan.items.filter((item) => item.submissions.length > 0).length : 0),
    [plan],
  );
  const independentCount = useMemo(
    () =>
      plan ? plan.items.filter((item) => item.submissions.some((submission) => submission.independent)).length : 0,
    [plan],
  );
  const nextTraining = report.data?.body?.next_training ?? [];
  const trainingEligibility = report.data?.training ?? report.data?.body?.training;
  const eligible = trainingEligibility?.eligible;
  const coverageLabels = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of report.data?.body?.coverage ?? []) {
      map.set(item.skill_id, cleanLabel(item.label, item.requirement_id || item.skill_id));
    }
    return map;
  }, [report.data]);

  async function generatePlan() {
    if (!sessionId) return;
    setGenerating(true);
    setGenError("");
    try {
      await ensureLogin();
      await api("/api/v1/training-plans", { method: "POST", body: JSON.stringify({ session_id: sessionId }) });
      await Promise.all([task.refetch(), plans.refetch()]);
    } catch (err) {
      setGenError(friendlyError(errorMessage(err)));
    } finally {
      setGenerating(false);
    }
  }

  const sortedSessions = useMemo(() => {
    const items = [...(sessions.data?.items ?? [])];
    items.sort((a, b) => {
      const rank = (status: string) => (status === "COMPLETED" ? 0 : status === "ACTIVE" ? 1 : 2);
      const diff = rank(a.status) - rank(b.status);
      return diff !== 0 ? diff : a.created_at < b.created_at ? 1 : -1;
    });
    return items;
  }, [sessions.data]);

  if (!sessionId) {
    return (
      <div className="stack-32 page-enter">
        <PageHeader
          eyebrow="训练"
          title="挑一场面试，把没讲清楚的地方练回来"
          description="训练计划来自某一场面试的复盘报告，围绕证据不足或尚未独立掌握的要求安排最小单元。"
          actions={
            <LinkButton to="/hall" variant="ghost" iconLeft="arrowLeft">
              面试大厅
            </LinkButton>
          }
        />

        <section className="card">
          <header className="card-head">
            <div className="card-head-main">
              <h2>选择要训练的面试</h2>
              <p>只会读取这一场的复盘报告，不会跨场次合并证据。</p>
            </div>
            <Badge tone="neutral">{sortedSessions.length} 场</Badge>
          </header>

          {sessions.isLoading ? (
            <div className="stack-12">
              {[0, 1, 2].map((key) => (
                <Skeleton key={key} height={64} radius={14} />
              ))}
            </div>
          ) : sessions.isError ? (
            <Alert tone="danger" title="读取面试列表失败">
              {sessions.error instanceof Error ? sessions.error.message : "请确认 API 正在运行。"}
            </Alert>
          ) : sortedSessions.length === 0 ? (
            <EmptyState
              icon="inbox"
              title="还没有可选择的面试"
              description="训练计划必须基于一场已经结束的面试。先完成一场面试，再回来训练。"
              action={
                <LinkButton to="/" variant="primary" iconLeft="plus">
                  准备第一场面试
                </LinkButton>
              }
            />
          ) : (
            <div className="picker-list">
              {sortedSessions.map((session) => {
                const status = sessionStatusMeta(session.status);
                const mode = modeMeta(session.mode);
                return (
                  <button type="button" className="picker-row" key={session.id} onClick={() => chooseSession(session.id)}>
                    <span className={`session-glyph tone-${status.tone}`}>
                      <Icon name="activity" size={18} />
                    </span>
                    <span className="picker-main">
                      <span className="picker-title">
                        {mode.label}面试
                        <Badge tone={status.tone}>{status.label}</Badge>
                      </span>
                      <span className="picker-meta">
                        <span className="mono">{shortId(session.id)}</span>
                        <span className="dot-sep" />
                        {formatDateTime(session.created_at)}
                      </span>
                    </span>
                    <Icon name="chevronRight" size={16} />
                  </button>
                );
              })}
            </div>
          )}

          <details className="manual-entry">
            <summary>直接输入 session id</summary>
            <div className="manual-entry-row">
              <input
                className="input mono"
                value={manualId}
                onChange={(event) => {
                  setManualId(event.target.value);
                  setManualError("");
                }}
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                aria-label="Session id"
              />
              <Button
                variant="secondary"
                onClick={() => {
                  if (!isUuid(manualId)) {
                    setManualError("这不像一个合法的 session id。");
                    return;
                  }
                  chooseSession(manualId.trim());
                }}
              >
                使用
              </Button>
            </div>
            {manualError ? <p className="field-hint danger-text">{manualError}</p> : null}
          </details>
        </section>

        <p className="page-footnote">
          <Icon name="info" size={14} />
          看过提示或示范的作答会被标成「不算独立掌握」，复测必须独立完成。
        </p>
      </div>
    );
  }

  const reportMissing = report.isError || (report.data === undefined && report.isFetched && !report.isFetching);

  return (
    <div className="stack-24 page-enter">
      <PageHeader
        eyebrow="训练"
        title={plan ? "本场训练计划" : "为本场生成训练计划"}
        description="练习先给最小提示，复测必须独立作答。只有独立完成的作答才算掌握。"
        actions={
          <Button variant="ghost" iconLeft="arrowLeft" onClick={() => chooseSession("")}>
            更换场次
          </Button>
        }
      />

      <div className="session-summary-bar">
        <span className="mono" title={sessionId}>
          {shortId(sessionId)}
        </span>
        <span className="dot-sep" />
        <span>{report.data ? `报告状态：${report.data.status === "COMPLETE" ? "完整" : "部分"}` : "读取报告中…"}</span>
        {nextTraining.length ? (
          <>
            <span className="dot-sep" />
            <span>待补强 {nextTraining.length} 项</span>
          </>
        ) : null}
      </div>

      {report.isLoading ? (
        <div className="card stack-16">
          <Skeleton width="30%" height={22} />
          <Skeleton width="80%" height={14} />
          <Skeleton height={80} radius={14} />
        </div>
      ) : reportMissing ? (
        <EmptyState
          icon="file"
          tone="warn"
          title="这场面试还没有复盘报告"
          description="训练计划必须依据一场面试的复盘报告生成。先去报告页生成报告，再回来训练。"
          action={
            <div className="row-actions">
              <LinkButton to={`/interviews/${sessionId}/report`} variant="primary" iconLeft="file">
                去生成报告
              </LinkButton>
              <Button variant="ghost" onClick={() => chooseSession("")}>
                换一场
              </Button>
            </div>
          }
        />
      ) : eligible === false ? (
        <EmptyState
          icon="shield"
          title="这场面试还不适合训练"
          description={trainingEligibility?.reason || "报告里没有足够的证据缺口来安排训练单元。"}
          action={
            <LinkButton to={`/interviews/${sessionId}/report`} variant="secondary" iconLeft="file">
              查看复盘报告
            </LinkButton>
          }
        />
      ) : plan ? (
        <>
          <div className="stat-row">
            <StatTile icon="activity" label="训练单元" value={units.length} unit="组" hint="练习 + 变式复测" />
            <StatTile icon="checkCircle" label="已作答" value={doneUnits} unit={`/ ${totalUnits}`} hint="含练习与复测" />
            <StatTile
              icon="target"
              label="独立完成"
              value={independentCount}
              unit={`/ ${totalUnits}`}
              hint="看过提示的不计入"
              tone={independentCount > 0 ? "success" : "neutral"}
            />
            <StatTile icon="chart" label="完成度" value={totalUnits ? Math.round((doneUnits / totalUnits) * 100) : 0} unit="%" hint="按已提交的题目计算" />
          </div>

          <div className="training-wrap">
            <div className="stack-20">
              <section className="card plan-card">
                <header className="card-head">
                  <div className="card-head-main">
                    <h2>计划说明</h2>
                    <p>{plan.rationale || "围绕证据不足或尚未独立掌握的岗位要求安排最小训练单元。"}</p>
                  </div>
                  <Badge tone="accent">本场专属</Badge>
                </header>
                <div className="tag-cloud">
                  {(plan.skill_ids ?? []).map((skill) => (
                    <span className="tag tag-strong" key={skill}>
                      {coverageLabels.get(skill) || prettyRequirement(skill)}
                    </span>
                  ))}
                </div>
                <div className="plan-progress">
                  <ProgressBar value={totalUnits ? (doneUnits / totalUnits) * 100 : 0} />
                  <span className="muted-small">
                    {doneUnits} / {totalUnits} 题已作答
                  </span>
                </div>
              </section>

              {units.map((unit, index) => (
                <section className="card unit-card" key={unit.key}>
                  <header className="unit-card-head">
                    <span className="unit-index">单元 {String(index + 1).padStart(2, "0")}</span>
                    <h3>
                      <span
                        title={cleanLabel(
                          (unit.exercise ?? unit.retest)?.requirement_label,
                          (unit.exercise ?? unit.retest)?.requirement_id,
                        )}
                      >
                        {truncate(
                          cleanLabel(
                            (unit.exercise ?? unit.retest)?.requirement_label,
                            (unit.exercise ?? unit.retest)?.requirement_id,
                          ),
                          34,
                        )}
                      </span>
                    </h3>
                    <span className="unit-card-rule" />
                  </header>
                  {unit.exercise ? (
                    <ExerciseCard item={unit.exercise} isRetest={false} onSubmitted={async () => {
                        await plans.refetch();
                      }} />
                  ) : null}
                  {unit.retest ? (
                    <ExerciseCard item={unit.retest} isRetest onSubmitted={async () => {
                        await plans.refetch();
                      }} />
                  ) : null}
                </section>
              ))}
            </div>

            <aside className="training-side">
              <section className="card side-card sticky">
                <h3 className="side-title">
                  <Icon name="compass" size={16} /> 怎么练
                </h3>
                <ul className="tip-list">
                  <li>先自己写一版，再决定要不要看提示。</li>
                  <li>复测题和练习题不同，必须独立写完。</li>
                  <li>评分看的是具体做法和验证方式，不是字数。</li>
                  <li>看提示的作答会保留，但不算独立掌握。</li>
                </ul>
                <LinkButton to={`/interviews/${sessionId}/report`} variant="ghost" size="sm" iconLeft="file">
                  回到本场报告
                </LinkButton>
              </section>

              {nextTraining.length ? (
                <section className="card side-card">
                  <h3 className="side-title">
                    <Icon name="target" size={16} /> 待补强
                  </h3>
                  <ul className="plain-list">
                    {nextTraining.map((skill) => (
                      <li key={skill}>{coverageLabels.get(skill) || prettyRequirement(skill)}</li>
                    ))}
                  </ul>
                </section>
              ) : null}
            </aside>
          </div>
        </>
      ) : (
        <section className="card generate-card">
          <div className="generate-head">
            <span className="generate-icon">
              <Icon name="activity" size={22} />
            </span>
            <div>
              <h2>这场面试可以生成训练计划</h2>
              <p>报告确认了 {nextTraining.length} 条待补强的岗位要求。生成后会得到「练习 + 变式复测」的配对单元。</p>
            </div>
          </div>
          {nextTraining.length ? (
            <div className="tag-cloud">
              {nextTraining.map((skill) => (
                <span className="tag tag-strong" key={skill}>
                  {coverageLabels.get(skill) || prettyRequirement(skill)}
                </span>
              ))}
            </div>
          ) : null}
          <Alert tone="accent" title="关于独立掌握">
            练习时可以看提示，但复测必须独立作答。看过提示或示范的作答会单独记录，不会混进独立掌握。
          </Alert>
          {genError ? <Alert tone="danger">{genError}</Alert> : null}
          {task.isError ? <Alert tone="danger">无法读取生成任务状态，请刷新重试。</Alert> : null}
          {trainingJob?.status === "DEAD" ? (
            <Alert tone="danger">训练计划生成失败：{trainingJob.error_reason || `后台任务未通过处理（${trainingJob.last_error_code || "未知错误"}）`}。可以点击下方按钮重新生成。</Alert>
          ) : null}
          {trainingJob?.status === "CANCELLED" ? <Alert tone="warn">生成任务已取消。</Alert> : null}
          {trainingJob?.status === "SUCCEEDED" && !plan ? (
            <Alert tone="warn">{trainingJob.no_confirmed_weakness ? "当前没有可生成练习的训练目标，请查看报告。" : "任务已完成，正在读取训练计划。"}</Alert>
          ) : null}
          {jobPending ? <Alert tone="accent">教练正在生成训练计划，请稍候；生成完成后会自动显示。{trainingJob?.status === "RETRY_WAIT" ? " 上次处理失败，正在重试。" : ""}</Alert> : null}
          <Button variant="primary" size="lg" iconLeft="spark" onClick={generatePlan} loading={generating || jobPending} disabled={jobPending}>
            根据报告生成训练计划
          </Button>
        </section>
      )}

      <p className="page-footnote">
        <Icon name="info" size={14} />
        训练提交后会异步评分，页面会在结果回来时自动刷新。
      </p>
    </div>
  );
}
