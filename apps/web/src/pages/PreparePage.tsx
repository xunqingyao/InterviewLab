import { useRef, useState } from "react";
import type { DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ensureLogin } from "../api";
import { Icon } from "../components/Icon";
import {
  Alert,
  Badge,
  Button,
  LinkButton,
  PageHeader,
  Segmented,
  Spinner,
} from "../components/ui";
import { errorMessage, formatChars } from "../lib/format";

type ResumeRow = {
  id: string;
  status: string;
  parse_error?: string | null;
  versions: { id: string; text: string }[];
};

const DURATIONS = [
  { value: "10", label: "10 分钟" },
  { value: "15", label: "15 分钟" },
  { value: "20", label: "20 分钟" },
];

async function waitForResume(id: string): Promise<ResumeRow> {
  for (let i = 0; i < 25; i += 1) {
    const row = await api<ResumeRow>(`/api/v1/resumes/${id}`);
    if (row.status !== "PENDING") return row;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return api<ResumeRow>(`/api/v1/resumes/${id}`);
}

export default function PreparePage() {
  const navigate = useNavigate();
  const [resume, setResume] = useState("");
  const [jd, setJd] = useState("");
  const [duration, setDuration] = useState(15);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [progress, setProgress] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function uploadPdf(file: File) {
    setError("");
    setNotice("");
    setUploading(true);
    try {
      await ensureLogin();
      const form = new FormData();
      form.append("file", file);
      const created = await api<ResumeRow>("/api/v1/resumes", { method: "POST", body: form });
      const row = created.status === "PENDING" ? await waitForResume(created.id) : created;
      if (row.status === "NEEDS_TEXT_INPUT") {
        setError(row.parse_error || "这份 PDF 读不出文字（可能是扫描件）。请把简历粘贴到上面的文本框。");
        return;
      }
      const text = row.versions[0]?.text || "";
      if (!text.trim()) {
        setError("解析结果是空的，请改用粘贴文本。");
        return;
      }
      setResume(text);
      setNotice(`已从「${file.name}」提取 ${formatChars(text)}，请核对后再继续。`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setUploading(false);
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    const file = event.dataTransfer.files?.[0];
    if (file && !uploading) uploadPdf(file);
  }

  function preview() {
    setError("");
    setNotice("");
    if (resume.trim().length < 10) {
      setError("简历内容太短了，至少写几句能支撑追问的经历。");
      return;
    }
    if (jd.trim().length < 10) {
      setError("岗位描述太短了，至少写清楚这条岗位要做什么。");
      return;
    }
    setConfirming(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function start() {
    if (busy) return;
    setError("");
    setBusy(true);
    setProgress("正在保存简历…");
    try {
      await ensureLogin();
      const created = await api<{ id: string }>("/api/v1/resumes", {
        method: "POST",
        body: JSON.stringify({ text: resume }),
      });
      const confirmed = await api<{ id: string }>(`/api/v1/resumes/${created.id}/versions`, {
        method: "POST",
        body: JSON.stringify({ text: resume }),
      });
      setProgress("正在保存岗位…");
      const profile = await api<{ id: string }>("/api/v1/job-profiles", {
        method: "POST",
        body: JSON.stringify({ title: "AI 应用 / Python 后端", text: jd }),
      });
      const jobVersion = await api<{ id: string }>(`/api/v1/job-profiles/${profile.id}/versions`, {
        method: "POST",
        body: JSON.stringify({ text: jd }),
      });
      setProgress("正在创建面试…");
      const session = await api<{ id: string }>("/api/v1/interviews", {
        method: "POST",
        body: JSON.stringify({
          resume_version_id: confirmed.id,
          job_profile_version_id: jobVersion.id,
          mode: "STRICT",
          duration_minutes: duration,
        }),
      });
      setProgress("正在安排第一题…");
      await api(`/api/v1/interviews/${session.id}/start`, {
        method: "POST",
        body: JSON.stringify({ expected_state_version: 0 }),
      });
      navigate(`/interviews/${session.id}`);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
      setProgress("");
    }
  }

  if (confirming) {
    return (
      <div className="stack-32 page-enter">
        <PageHeader
          eyebrow="确认 · 3 / 3"
          title="确认这场面试的冻结内容"
          description="下面两段文本会作为本场唯一的简历与岗位说明被冻结。开始后不能修改这一版，评分也只依据这一版。"
          actions={
            <Button variant="ghost" iconLeft="arrowLeft" onClick={() => setConfirming(false)} disabled={busy}>
              返回修改
            </Button>
          }
        />
        {error ? (
          <Alert tone="danger" title="没有开始成功">
            {error}
          </Alert>
        ) : null}
        <div className="confirm-layout">
          <div className="stack-20">
            <section className="card doc-card">
              <header className="card-head">
                <div>
                  <h2>简历</h2>
                  <p>面试官会根据这些经历追问细节。</p>
                </div>
                <div className="card-head-side">
                  <Badge tone="accent">将冻结</Badge>
                  <span className="muted-small">{formatChars(resume)}</span>
                </div>
              </header>
              <pre className="doc-preview">{resume.trim()}</pre>
            </section>
            <section className="card doc-card">
              <header className="card-head">
                <div>
                  <h2>岗位描述</h2>
                  <p>题目和评分标准都围绕这份岗位要求展开。</p>
                </div>
                <div className="card-head-side">
                  <Badge tone="accent">将冻结</Badge>
                  <span className="muted-small">{formatChars(jd)}</span>
                </div>
              </header>
              <pre className="doc-preview">{jd.trim()}</pre>
            </section>
          </div>

          <aside className="confirm-side">
            <section className="card summary-card">
              <h2 className="side-title">
                <Icon name="shield" size={17} /> 本场设置
              </h2>
              <dl className="summary-list">
                <div>
                  <dt>模式</dt>
                  <dd>严格模式</dd>
                </div>
                <div>
                  <dt>时长</dt>
                  <dd>{duration} 分钟</dd>
                </div>
                <div>
                  <dt>简历</dt>
                  <dd>{formatChars(resume)}</dd>
                </div>
                <div>
                  <dt>岗位</dt>
                  <dd>{formatChars(jd)}</dd>
                </div>
              </dl>
              <Alert tone="accent" title="开始后不能改这一版">
                面试过程中看不到分数；结束后会按冻结的评分标准统一生成复盘。
              </Alert>
              <Button className="btn-block" size="lg" loading={busy} onClick={start} iconRight={busy ? undefined : "arrowRight"}>
                {busy ? progress || "正在开始…" : "确认并开始"}
              </Button>
              <Button className="btn-block" variant="ghost" onClick={() => setConfirming(false)} disabled={busy}>
                再检查一下
              </Button>
            </section>
            <p className="side-footnote">
              想先看看每一题的评分标准？报告生成后可以在「逐题复盘」里打开对应的评分标准快照。
            </p>
          </aside>
        </div>
      </div>
    );
  }

  return (
    <div className="stack-32 page-enter">
      <PageHeader
        eyebrow="新面试 · 严格模式"
        title="把简历和岗位讲清楚，再开始"
        description="先确认简历与岗位描述。确认后两份文本会被冻结，面试官会围绕它们追问具体做法、验证方式和取舍。"
        actions={
          <LinkButton to="/hall" variant="ghost" iconLeft="layers">
            面试大厅
          </LinkButton>
        }
      />

      {error ? (
        <Alert tone="danger" title="请检查一下">
          {error}
        </Alert>
      ) : null}

      <div className="prepare-layout">
        <div className="stack-20">
          <section className="card">
            <header className="card-head">
              <span className="step-chip">01</span>
              <div className="card-head-main">
                <h2>简历</h2>
                <p>粘贴文本，或上传一份数字版 PDF。解析后仍可以手动修改。</p>
              </div>
              <span className="muted-small">{formatChars(resume)}</span>
            </header>

            <textarea
              className="textarea textarea-lg"
              value={resume}
              onChange={(event) => setResume(event.target.value)}
              disabled={busy}
              aria-label="简历文本"
              placeholder="姓名、方向、年限，以及 2–4 段能支撑追问的经历…"
            />

            <div
              className={`upload-zone ${dragActive ? "is-dragging" : ""} ${uploading ? "is-busy" : ""}`}
              onClick={() => !uploading && fileRef.current?.click()}
              onKeyDown={(event) => {
                if ((event.key === "Enter" || event.key === " ") && !uploading) {
                  event.preventDefault();
                  fileRef.current?.click();
                }
              }}
              onDragOver={(event) => {
                event.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={() => setDragActive(false)}
              onDrop={handleDrop}
              role="button"
              tabIndex={0}
              aria-disabled={uploading}
            >
              <input
                ref={fileRef}
                type="file"
                accept=".pdf,application/pdf,.txt,text/plain"
                hidden
                disabled={uploading}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) uploadPdf(file);
                  event.target.value = "";
                }}
              />
              <span className="upload-icon">{uploading ? <Spinner size={18} /> : <Icon name="upload" size={18} />}</span>
              <span className="upload-copy">
                <strong>{uploading ? "正在解析 PDF…" : "上传 PDF 或 TXT"}</strong>
                <span>拖拽到此处，或点击选择文件 · 数字版，最多 10 页 / 5 MB</span>
              </span>
              <span className="upload-action">{uploading ? "请稍候" : "选择文件"}</span>
            </div>

            {notice ? <Alert tone="success">{notice}</Alert> : null}
          </section>

          <section className="card">
            <header className="card-head">
              <span className="step-chip">02</span>
              <div className="card-head-main">
                <h2>岗位描述</h2>
                <p>写清楚这条岗位要做什么、硬性要求和考察重点。</p>
              </div>
              <span className="muted-small">{formatChars(jd)}</span>
            </header>
            <textarea
              className="textarea textarea-lg"
              value={jd}
              onChange={(event) => setJd(event.target.value)}
              disabled={busy}
              aria-label="岗位描述"
              placeholder="职责、技术要求、加分项…"
            />
            <p className="field-hint">
              <Icon name="info" size={14} /> 岗位描述越具体，追问越接近真实面试；空泛的标签只会得到空泛的题目。
            </p>
          </section>
        </div>

        <aside className="prepare-side">
          <section className="card side-card sticky">
            <header className="card-head">
              <span className="step-chip">03</span>
              <div className="card-head-main">
                <h2>面试设置</h2>
                <p>决定题量与节奏。</p>
              </div>
            </header>

            <div className="field">
              <span className="field-label">时长</span>
              <Segmented
                ariaLabel="面试时长"
                options={DURATIONS}
                value={String(duration)}
                onChange={(value) => setDuration(Number(value))}
              />
              <p className="field-hint">按 {duration} 分钟安排题量，也可以随时提前结束。</p>
            </div>

            <div className="mode-box">
              <div className="row-between">
                <strong>严格模式</strong>
                <Badge tone="accent">固定</Badge>
              </div>
              <p>不给提示，也不给示范，最接近真实面试。结束后统一生成复盘。</p>
            </div>

            <Button className="btn-block" size="lg" iconRight="arrowRight" onClick={preview} disabled={busy}>
              预览冻结内容
            </Button>
            <p className="field-hint center">下一步只会预览，不会直接开始。</p>
          </section>

          <section className="card side-card">
            <h3 className="side-title">
              <Icon name="shield" size={16} /> 本场规则
            </h3>
            <ul className="rule-list">
              <li>
                <span className="rule-dot" />
                面试中看不到分数，结束后统一评分。
              </li>
              <li>
                <span className="rule-dot" />
                追问会结合前面的回答，不是独立的新题。
              </li>
              <li>
                <span className="rule-dot" />
                跳过的题记为未作答，不会算成 0 分。
              </li>
              <li>
                <span className="rule-dot" />
                没评完的题会标成「未评估」，并在报告更新后自动补上。
              </li>
            </ul>
            <Link to="/skills" className="text-link">
              看看已有的技能证据 <Icon name="arrowRight" size={14} />
            </Link>
          </section>
        </aside>
      </div>
    </div>
  );
}
