import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ensureLogin } from "../api";
import { Icon } from "../components/Icon";
import {
  Alert,
  Badge,
  Button,
  EmptyState,
  LinkButton,
  Modal,
  ProgressBar,
  Skeleton,
  Spinner,
} from "../components/ui";
import {
  errorMessage,
  formatChars,
  formatClock,
  formatDateTime,
  formatRelative,
  modeMeta,
  friendlyError,
  phaseLabel,
  prettyRequirement,
  sessionStatusMeta,
  shortId,
  turnStatusMeta,
} from "../lib/format";

type Turn = {
  id: string;
  turn_no: number;
  status: string;
  topic_id?: string | null;
  skill_ids?: string[];
};

type Message = {
  id: string;
  turn_id: string | null;
  kind: string;
  text: string;
};

type Snap = {
  id: string;
  status: string;
  phase: string;
  mode: string;
  state_version: number;
  last_event_seq: number;
  started_at?: string | null;
  soft_deadline_at?: string | null;
  current_question: { turn_id: string; turn_no?: number; text: string } | null;
  turns: Turn[];
  messages: Message[];
};

type Conn = "live" | "reconnecting" | "resync";
type ModalKind = "finish" | "skip" | null;

function TranscriptItem({
  turnNo,
  status,
  question,
  answer,
  defaultOpen = false,
}: {
  turnNo: number;
  status: string;
  question: string;
  answer?: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const statusMeta = turnStatusMeta(status);
  const longAnswer = (answer ?? "").length > 260;

  return (
    <article className="turn-item">
      <header className="turn-head">
        <span className="turn-no">{String(turnNo).padStart(2, "0")}</span>
        <span className="turn-label">第 {turnNo} 题</span>
        <Badge tone={statusMeta.tone}>{statusMeta.label}</Badge>
        <span className="turn-head-rule" />
        <button type="button" className="turn-toggle" onClick={() => setOpen((value) => !value)}>
          {open ? "收起" : "展开"}
          <Icon name="chevronDown" size={14} className={open ? "rotate-180" : undefined} />
        </button>
      </header>
      {open ? (
        <div className="turn-body">
          <p className="turn-question">{question}</p>
          {answer ? (
            <>
              <p className={longAnswer ? "turn-answer is-clamped" : "turn-answer"}>{answer}</p>
              {longAnswer ? (
                <button type="button" className="text-link" onClick={() => setOpen(false)}>
                  收起长回答 <Icon name="chevronDown" size={14} className="rotate-180" />
                </button>
              ) : null}
            </>
          ) : (
            <p className="turn-empty">这一题没有留下回答。</p>
          )}
        </div>
      ) : (
        <p className="turn-preview">{answer ? answer : "（未作答）"}</p>
      )}
    </article>
  );
}

export default function InterviewPage() {
  const { id = "" } = useParams();
  const [snap, setSnap] = useState<Snap | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [conn, setConn] = useState<Conn>("live");
  const [audioStatus, setAudioStatus] = useState("");
  const [recording, setRecording] = useState(false);
  const [recSeconds, setRecSeconds] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [modal, setModal] = useState<ModalKind>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const drafts = useRef<Record<string, string>>({});
  const seenSeq = useRef<Set<number>>(new Set());
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const recTimerRef = useRef<number | undefined>(undefined);
  const questionRef = useRef<HTMLDivElement | null>(null);
  const prevTurnRef = useRef<string | null>(null);

  const refresh = useCallback(async (): Promise<Snap> => {
    await ensureLogin();
    const data = await api<Snap>(`/api/v1/interviews/${id}`);
    setSnap((prev) => {
      if (prev && data.state_version < prev.state_version) return prev;
      return data;
    });
    const turnId = data.current_question?.turn_id;
    setDraft(turnId ? drafts.current[turnId] || "" : "");
    return data;
  }, [id]);

  useEffect(() => {
    let closed = false;
    let source: EventSource | null = null;
    let reconnectTimer: number | undefined;

    async function connect(cursor: number) {
      if (closed) return;
      source?.close();
      await ensureLogin();
      const es = new EventSource(`/api/v1/interviews/${id}/events?cursor=${cursor}`);
      source = es;
      es.onopen = () => setConn("live");
      es.onerror = () => {
        setConn("reconnecting");
        es.close();
        window.clearTimeout(reconnectTimer);
        reconnectTimer = window.setTimeout(() => {
          refresh()
            .then((data) => connect(data.last_event_seq || 0))
            .catch(() => connect(cursor));
        }, 1500);
      };
      const onChange = (ev: Event) => {
        const seq = Number((ev as MessageEvent).lastEventId);
        if (Number.isFinite(seq)) {
          if (seenSeq.current.has(seq)) return;
          seenSeq.current.add(seq);
        }
        refresh().catch(() => undefined);
      };
      es.addEventListener("question.ready", onChange);
      es.addEventListener("answer.accepted", onChange);
      es.addEventListener("phase.changed", onChange);
      es.addEventListener("report.ready", onChange);
      es.addEventListener("session.completed", onChange);
      es.addEventListener("session.cancelled", onChange);
      es.addEventListener("job.failed", () => {
        setError("后台任务失败。题目或报告可能稍后以降级方式给出。");
        refresh().catch(() => undefined);
      });
      es.addEventListener("resync.required", () => {
        setConn("resync");
        es.close();
        refresh()
          .then((data) => connect(data.last_event_seq || 0))
          .catch(() => undefined);
      });
      es.addEventListener("audio.transcribed", (ev) => {
        const data = JSON.parse((ev as MessageEvent).data);
        setDraft(data.transcript || "");
        setAudioStatus("转写完成，确认或修改后再提交。");
      });
    }

    refresh()
      .then((data) => connect(data.last_event_seq || 0))
      .catch((err) => setError(errorMessage(err)));

    return () => {
      closed = true;
      source?.close();
      window.clearTimeout(reconnectTimer);
    };
  }, [id, refresh]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      window.clearInterval(recTimerRef.current);
    };
  }, []);

  const currentTurnId = snap?.current_question?.turn_id ?? null;
  useEffect(() => {
    if (!currentTurnId) return;
    if (prevTurnRef.current === currentTurnId) return;
    prevTurnRef.current = currentTurnId;
    questionRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [currentTurnId]);

  const currentTurn = useMemo(
    () => (snap?.turns ?? []).find((turn) => turn.id === currentTurnId) ?? null,
    [snap, currentTurnId],
  );

  const transcript = useMemo(() => {
    const grouped = new Map<string, { question: string; answer: string }>();
    for (const message of snap?.messages ?? []) {
      if (!message.turn_id) continue;
      const entry = grouped.get(message.turn_id) ?? { question: "", answer: "" };
      if (message.kind === "QUESTION") entry.question = message.text;
      if (message.kind === "ANSWER") entry.answer = message.text;
      grouped.set(message.turn_id, entry);
    }
    const turnNo = new Map((snap?.turns ?? []).map((turn) => [turn.id, turn.turn_no]));
    const status = new Map((snap?.turns ?? []).map((turn) => [turn.id, turn.status]));
    return [...grouped.entries()]
      .filter(([turnId, entry]) => turnId !== currentTurnId && entry.question)
      .map(([turnId, entry]) => ({
        turnId,
        turnNo: turnNo.get(turnId) ?? 0,
        status: status.get(turnId) ?? "ANSWERED",
        question: entry.question,
        answer: entry.answer,
      }))
      .sort((a, b) => a.turnNo - b.turnNo);
  }, [snap, currentTurnId]);

  const timing = useMemo(() => {
    const deadline = snap?.soft_deadline_at ? new Date(snap.soft_deadline_at).getTime() : null;
    const started = snap?.started_at ? new Date(snap.started_at).getTime() : null;
    if (!deadline || Number.isNaN(deadline)) return null;
    const remaining = Math.max(0, Math.round((deadline - now) / 1000));
    const total = started && !Number.isNaN(started) ? Math.max(1, Math.round((deadline - started) / 1000)) : null;
    return { remaining, total, percent: total ? (remaining / total) * 100 : 100, over: remaining === 0 };
  }, [snap, now]);

  async function submit() {
    if (!snap?.current_question || busy) return;
    if (!draft.trim()) return;
    setBusy(true);
    setError("");
    drafts.current[snap.current_question.turn_id] = draft;
    try {
      await api(`/api/v1/interviews/${id}/answers`, {
        method: "POST",
        headers: { "Idempotency-Key": `web-${snap.current_question.turn_id}` },
        body: JSON.stringify({
          turn_id: snap.current_question.turn_id,
          text: draft,
          expected_state_version: snap.state_version,
        }),
      });
      setDraft("");
      setAudioStatus("");
      setBusy(false);
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  async function skip() {
    if (!snap?.current_question || confirmBusy) return;
    setConfirmBusy(true);
    setError("");
    try {
      await api(`/api/v1/interviews/${id}/skip`, {
        method: "POST",
        body: JSON.stringify({
          expected_state_version: snap.state_version,
          turn_id: snap.current_question.turn_id,
          reason: "skip",
        }),
      });
      setDraft("");
      setModal(null);
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setConfirmBusy(false);
    }
  }

  async function finish() {
    if (!snap || confirmBusy) return;
    setConfirmBusy(true);
    setError("");
    try {
      await api(`/api/v1/interviews/${id}/finish`, {
        method: "POST",
        body: JSON.stringify({ expected_state_version: snap.state_version }),
      });
      setModal(null);
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setConfirmBusy(false);
    }
  }

  async function startRecording() {
    if (recording || busy) return;
    setAudioStatus("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recorderRef.current = recorder;
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.start();
      setRecording(true);
      setRecSeconds(0);
      recTimerRef.current = window.setInterval(() => setRecSeconds((value) => value + 1), 1000);
      setAudioStatus("正在录音，说完点一下停止。");
    } catch (err) {
      setAudioStatus(`无法使用麦克风：${errorMessage(err)}`);
    }
  }

  async function stopRecording() {
    const recorder = recorderRef.current;
    const turnId = snap?.current_question?.turn_id;
    if (!recorder || !turnId) return;
    window.clearInterval(recTimerRef.current);
    setRecording(false);
    setAudioStatus("正在上传录音…");
    await new Promise<void>((resolve) => {
      recorder.onstop = () => resolve();
      recorder.stop();
    });
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
    if (blob.size === 0) {
      setAudioStatus("没有录到声音，请重试。");
      return;
    }
    const form = new FormData();
    form.append("file", blob, "answer.webm");
    try {
      const res = await fetch(`/api/v1/interviews/${id}/audio-drafts/${turnId}`, {
        method: "POST",
        body: form,
        credentials: "include",
      });
      if (!res.ok) throw new Error(`上传失败（${res.status}）`);
      setAudioStatus("已上传，正在等待转写结果…");
    } catch (err) {
      setAudioStatus(`上传失败：${errorMessage(err)}`);
    }
  }

  function speakQuestion() {
    if (!snap?.current_question || typeof speechSynthesis === "undefined") return;
    speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(snap.current_question.text);
    utterance.lang = "zh-CN";
    speechSynthesis.speak(utterance);
  }

  if (!snap) {
    if (error) {
      return (
        <div className="stack-24 page-enter">
          <EmptyState
            icon="alert"
            tone="danger"
            title="这场面试打不开"
            description={friendlyError(error)}
            action={
              <div className="row-actions">
                <LinkButton to="/hall" variant="primary" iconLeft="arrowLeft">
                  回到面试大厅
                </LinkButton>
                <LinkButton to="/" variant="secondary" iconLeft="plus">
                  新开一场
                </LinkButton>
              </div>
            }
          />
        </div>
      );
    }
    return (
      <div className="stack-24 page-enter">
        <div className="card">
          <div className="stack-16">
            <Skeleton width="22%" height={13} />
            <Skeleton width="72%" height={26} />
            <Skeleton width="100%" height={14} />
            <Skeleton width="88%" height={14} />
            <Skeleton width="100%" height={160} radius={14} />
          </div>
        </div>
      </div>
    );
  }

  const status = sessionStatusMeta(snap.status);
  const mode = modeMeta(snap.mode);
  const waiting = snap.status === "ACTIVE" && !snap.current_question;
  const closing = snap.status === "CLOSING";
  const completed = snap.status === "COMPLETED";
  const cancelled = snap.status === "CANCELLED" || snap.status === "FAILED";
  const answered = snap.turns.filter((turn) => turn.status === "ANSWERED").length;
  const skipped = snap.turns.filter((turn) => turn.status === "SKIPPED").length;
  const questionNo = currentTurn?.turn_no ?? snap.current_question?.turn_no ?? 0;
  const canRecord = typeof MediaRecorder !== "undefined" && Boolean(navigator.mediaDevices);
  const connText = conn === "live" ? "实时同步" : conn === "resync" ? "正在重新同步" : "断线重连中";

  return (
    <div className="stack-24 page-enter">
      <header className="session-bar">
        <div className="session-bar-left">
          <Link to="/hall" className="back-link">
            <Icon name="arrowLeft" size={15} /> 面试大厅
          </Link>
          <span className="session-bar-divider" />
          <div className="session-bar-title">
            <h1>{mode.label} · 面试进行中</h1>
            <p>
              <span className="mono" title={snap.id}>
                {shortId(snap.id)}
              </span>
              <span className="dot-sep" />
              开始于 {formatDateTime(snap.started_at)}
              <span className="dot-sep" />
              {snap.turns.length} 题已生成
            </p>
          </div>
        </div>
        <div className="session-bar-right">
          <Badge tone={status.tone} dot={snap.status === "ACTIVE"}>
            {status.label}
          </Badge>
          <span className={`conn-chip conn-${conn}`} title={connText}>
            <i aria-hidden="true" />
            {connText}
          </span>
          {!completed && !cancelled ? (
            <Button variant="secondary" size="sm" iconLeft="stop" onClick={() => setModal("finish")} disabled={busy}>
              结束面试
            </Button>
          ) : null}
        </div>
      </header>

      {conn !== "live" ? (
        <Alert tone="warn" title="网络中断了">
          页面会自动重连并补读错过的事件，同一道题不会被显示两次，你正在写的内容也不会丢。
        </Alert>
      ) : null}
      {error ? (
        <Alert tone="danger" title="操作没有完成">
          {error}
        </Alert>
      ) : null}

      <div className="interview-layout">
        <div className="stack-20">
          {completed ? (
            <section className="card finish-card">
              <span className="finish-icon">
                <Icon name="checkCircle" size={26} />
              </span>
              <h2>这场面试结束了</h2>
              <p>
                共作答 {answered} 题{skipped ? `，跳过 ${skipped} 题` : ""}。复盘报告会按冻结的评分标准逐题生成；没评完的题会标成「未评估」，不会算成 0 分。
              </p>
              <div className="finish-actions">
                <LinkButton to={`/interviews/${id}/report`} variant="primary" iconLeft="file">
                  查看复盘报告
                </LinkButton>
                <LinkButton to={`/training?session=${id}`} variant="ghost" iconLeft="activity">
                  进入训练
                </LinkButton>
                <LinkButton to="/hall" variant="ghost">
                  回到大厅
                </LinkButton>
              </div>
            </section>
          ) : cancelled ? (
            <EmptyState
              icon="alert"
              tone="danger"
              title="这场面试没有正常结束"
              description="会话被取消或异常终止。已经提交的回答仍然可以在大厅里找到，必要的话重新开一场面试。"
              action={
                <LinkButton to="/" variant="primary" iconLeft="plus">
                  重新开始一场
                </LinkButton>
              }
            />
          ) : snap.current_question ? (
            <section className="card question-card" ref={questionRef}>
              <div className="question-head">
                <div className="question-head-left">
                  <span className="question-index">第 {questionNo} 题</span>
                  <Badge tone="neutral">{currentTurn ? turnStatusMeta(currentTurn.status).label : "待回答"}</Badge>
                  {(currentTurn?.skill_ids ?? []).slice(0, 3).map((skill) => (
                    <span className="tag" key={skill}>
                      {prettyRequirement(skill)}
                    </span>
                  ))}
                </div>
                <Button variant="ghost" size="sm" iconLeft="volume" onClick={speakQuestion}>
                  朗读
                </Button>
              </div>

              <p className="question-text">{snap.current_question.text}</p>

              <div className="composer">
                <textarea
                  className="textarea composer-input"
                  value={draft}
                  disabled={busy}
                  placeholder="按「背景 → 做法 → 结果 → 复盘」讲清楚一件事。有数字就给数字，有取舍就说取舍。"
                  onChange={(event) => {
                    setDraft(event.target.value);
                    drafts.current[snap.current_question!.turn_id] = event.target.value;
                  }}
                  onKeyDown={(event) => {
                    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                      event.preventDefault();
                      submit();
                    }
                  }}
                  aria-label="你的回答"
                />
                <div className="composer-foot">
                  <div className="composer-tools">
                    {canRecord ? (
                      <button
                        type="button"
                        className={recording ? "record-btn is-recording" : "record-btn"}
                        onClick={recording ? stopRecording : startRecording}
                        disabled={busy}
                      >
                        <Icon name={recording ? "stop" : "mic"} size={15} />
                        {recording ? `停止录音 ${formatClock(recSeconds)}` : "语音作答"}
                      </button>
                    ) : null}
                    <span className="muted-small">{formatChars(draft)}</span>
                    {timing ? (
                      <span
                        className={timing.over ? "composer-timer is-over" : "composer-timer"}
                        title={timing.over ? "已到建议时长" : "建议剩余时间"}
                      >
                        <Icon name="clock" size={12} />
                        {formatClock(timing.remaining)}
                      </span>
                    ) : null}
                    {audioStatus ? <span className="composer-audio">{audioStatus}</span> : null}
                  </div>
                  <div className="composer-actions">
                    <span className="kbd-hint">
                      <kbd>⌘</kbd>
                      <kbd>↵</kbd>
                      提交
                    </span>
                    <Button variant="ghost" size="sm" iconLeft="skip" onClick={() => setModal("skip")} disabled={busy}>
                      跳过
                    </Button>
                    <Button variant="primary" iconRight="arrowRight" onClick={submit} loading={busy} disabled={!draft.trim()}>
                      提交答案
                    </Button>
                  </div>
                </div>
              </div>
            </section>
          ) : closing ? (
            <section className="card state-card">
              <div className="state-orbit">
                <Spinner size={26} />
              </div>
              <h2>评分官正在收尾</h2>
              <p>
                最后一题已经结束，评分官还在赶最后几题的分，通常一分钟内结束。到点会出报告；来不及评完的题会标成「未完成评估」，不会当成 0 分。
              </p>
              <ProgressBar value={72} tone="warn" />
              <p className="muted-small">报告生成后，这里会出现入口。</p>
            </section>
          ) : waiting ? (
            <section className="card state-card">
              <div className="state-orbit">
                <Spinner size={26} />
              </div>
              <h2>{phaseLabel(snap.phase)}，面试官正在准备下一题</h2>
              <p>题目来自已审核题库，面试官会根据前面的回答选择追问、换题或结束。通常几秒钟内就会出现。</p>
            </section>
          ) : (
            <EmptyState
              icon="inbox"
              title="当前没有待回答的问题"
              description={
                snap.status === "CREATED"
                  ? "这场面试还没有开始。回到大厅重新进入，或准备一场新的面试。"
                  : "刷新一下看看，或者回到大厅查看这场面试的状态。"
              }
              action={
                <LinkButton to="/hall" variant="secondary" iconLeft="arrowLeft">
                  回到大厅
                </LinkButton>
              }
            />
          )}

          <section className="card transcript-card">
            <header className="card-head">
              <div className="card-head-main">
                <h2>对话记录</h2>
                <p>只保留已经结束的题目。当前题在上方作答，避免重复。</p>
              </div>
              <span className="muted-small">{transcript.length} 题</span>
            </header>
            {transcript.length === 0 ? (
              <p className="turn-empty">还没有结束的题目。你提交后，这里会按题号归档。</p>
            ) : (
              <div className="turn-list">
                {transcript.map((item) => (
                  <TranscriptItem
                    key={item.turnId}
                    turnNo={item.turnNo}
                    status={item.status}
                    question={item.question}
                    answer={item.answer}
                    defaultOpen={item.turnNo === transcript[transcript.length - 1].turnNo}
                  />
                ))}
              </div>
            )}
          </section>
        </div>

        <aside className="interview-side">
          <section className="card side-card">
            <h3 className="side-title">
              <Icon name="clock" size={16} /> 本场节奏
            </h3>
            {timing ? (
              <>
                <div className="timer-block">
                  <span className={timing.over ? "timer-value is-over" : "timer-value"}>{formatClock(timing.remaining)}</span>
                  <span className="timer-caption">{timing.over ? "已到建议时长" : "建议剩余时间"}</span>
                </div>
                <ProgressBar value={timing.percent} tone={timing.over ? "danger" : timing.remaining < 120 ? "warn" : "accent"} />
                <p className="field-hint">
                  {timing.over ? "可以随时结束。已经答的题不会丢。" : `建议在 ${formatDateTime(snap.soft_deadline_at)} 前结束，也可以提前交卷。`}
                </p>
              </>
            ) : (
              <p className="muted-small">这场面试没有设置软性时长。</p>
            )}
            <dl className="mini-list">
              <div>
                <dt>已作答</dt>
                <dd>{answered} 题</dd>
              </div>
              <div>
                <dt>已跳过</dt>
                <dd>{skipped} 题</dd>
              </div>
              <div>
                <dt>模式</dt>
                <dd>{mode.label}</dd>
              </div>
              <div>
                <dt>阶段</dt>
                <dd>{phaseLabel(snap.phase)}</dd>
              </div>
            </dl>
          </section>

          <section className="card side-card">
            <h3 className="side-title">
              <Icon name="compass" size={16} /> 作答提示
            </h3>
            <ul className="tip-list">
              <li>先给结论，再展开过程；面试官只能据此追问。</li>
              <li>把「我做了什么」和「结果如何验证」分开说。</li>
              <li>不确定的地方直说边界，比硬编一个数字好。</li>
              <li>追问会接在前面的回答上，尽量保持上下文一致。</li>
            </ul>
          </section>

          <section className="card side-card side-quiet">
            <h3 className="side-title">
              <Icon name="shield" size={16} /> 隐私与状态
            </h3>
            <p className="field-hint">
              回答会用于生成本场报告与技能证据。录音只用于转写，转写结果会先放进输入框让你确认。
            </p>
            <p className="muted-small">
              状态版本 v{snap.state_version} · 最近同步 {formatRelative(new Date(now).toISOString())}
            </p>
          </section>
        </aside>
      </div>

      <Modal
        open={modal === "finish"}
        onClose={() => !confirmBusy && setModal(null)}
        title="现在结束面试？"
        subtitle="结束后会进入评分收尾，通常一分钟内出报告。"
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setModal(null)} disabled={confirmBusy}>
              继续作答
            </Button>
            <Button variant="danger" onClick={finish} loading={confirmBusy} iconLeft="stop">
              确认结束
            </Button>
          </>
        }
      >
        <ul className="modal-list">
          <li>当前没提交的草稿不会保留。</li>
          <li>已提交的回答都会进入复盘，不受影响。</li>
          <li>来不及评完的题会标成「未评估」，不算 0 分。</li>
        </ul>
      </Modal>

      <Modal
        open={modal === "skip"}
        onClose={() => !confirmBusy && setModal(null)}
        title="跳过这一题？"
        subtitle="跳过的题会记为未作答，不参与评分。"
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setModal(null)} disabled={confirmBusy}>
              回去作答
            </Button>
            <Button variant="secondary" onClick={skip} loading={confirmBusy} iconLeft="skip">
              跳过此题
            </Button>
          </>
        }
      >
        <p className="modal-text">如果只是暂时卡住，可以先写你确定的部分；实在没有思路再跳过。</p>
      </Modal>
    </div>
  );
}
