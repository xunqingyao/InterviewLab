export type Tone = "neutral" | "accent" | "success" | "warn" | "danger" | "violet";

type Meta = { label: string; tone: Tone };

const SESSION_STATUS: Record<string, Meta> = {
  CREATED: { label: "待开始", tone: "neutral" },
  ACTIVE: { label: "面试中", tone: "accent" },
  CLOSING: { label: "收尾评分", tone: "warn" },
  COMPLETED: { label: "已完成", tone: "success" },
  CANCELLED: { label: "已取消", tone: "neutral" },
  FAILED: { label: "异常结束", tone: "danger" },
};

const PHASE: Record<string, string> = {
  PLANNING: "规划中",
  GENERATING: "出题中",
  AWAITING_ANSWER: "等待回答",
  IDLE: "空闲",
};

const MODE: Record<string, Meta> = {
  STRICT: { label: "严格模式", tone: "neutral" },
  ASSISTED: { label: "辅助模式", tone: "violet" },
  RETEST: { label: "复测模式", tone: "warn" },
};

const TURN_STATUS: Record<string, Meta> = {
  RESERVED: { label: "已预留", tone: "neutral" },
  READY: { label: "待回答", tone: "accent" },
  ANSWERED: { label: "已作答", tone: "success" },
  SKIPPED: { label: "已跳过", tone: "warn" },
  ABANDONED: { label: "已放弃", tone: "neutral" },
};

const ACTION: Record<string, string> = {
  ASK: "新问题",
  FOLLOW_UP: "追问",
  END: "结束题",
};

const ASSESSMENT: Record<string, Meta> = {
  PENDING: { label: "评估中", tone: "warn" },
  ASSESSED: { label: "已评估", tone: "success" },
  INSUFFICIENT_EVIDENCE: { label: "证据不足", tone: "neutral" },
  INVALID_OUTPUT: { label: "输出异常", tone: "warn" },
  FAILED: { label: "评估失败", tone: "danger" },
};

const EVIDENCE: Record<string, Meta> = {
  DEMONSTRATED: { label: "已展示", tone: "success" },
  PARTIAL: { label: "部分展示", tone: "warn" },
  MISSING: { label: "缺失", tone: "danger" },
  INSUFFICIENT_EVIDENCE: { label: "证据不足", tone: "neutral" },
  NOT_APPLICABLE: { label: "不适用", tone: "neutral" },
  REFERENCE_CONFLICT: { label: "与资料冲突", tone: "violet" },
};

const HINT: Record<string, Meta> = {
  NONE: { label: "独立作答", tone: "success" },
  NUDGE: { label: "看过提示", tone: "warn" },
  STRUCTURE: { label: "看过结构", tone: "warn" },
  EXAMPLE: { label: "看过示范", tone: "violet" },
};

const REPORT: Record<string, Meta> = {
  COMPLETE: { label: "完整报告", tone: "success" },
  PARTIAL: { label: "部分报告", tone: "warn" },
};

const LEVELS = [
  { label: "未达标", tone: "danger" as Tone },
  { label: "部分掌握", tone: "warn" as Tone },
  { label: "达标", tone: "accent" as Tone },
  { label: "扎实", tone: "success" as Tone },
];

const SKILL_LABELS: Record<string, string> = {
  "backend.idempotency": "幂等与重复提交",
  "backend.jobs": "任务队列与并发",
  "backend.transactions": "事务与并发控制",
  "backend.debugging": "线上问题排查",
  "backend.api": "接口设计",
  "rag.evaluation_design": "RAG 评测设计",
  "rag.retrieval": "检索质量",
  "ai.agent_state": "Agent 状态与恢复",
  "ai.evaluation": "AI 效果评测",
  communication: "表达与取舍",
};

function pick(map: Record<string, Meta>, key: string | null | undefined, fallback: string): Meta {
  if (key && map[key]) return map[key];
  return { label: key || fallback, tone: "neutral" };
}

export function sessionStatusMeta(status?: string | null): Meta {
  return pick(SESSION_STATUS, status, "未知状态");
}

export function phaseLabel(phase?: string | null): string {
  return (phase && PHASE[phase]) || phase || "—";
}

export function modeMeta(mode?: string | null): Meta {
  return pick(MODE, mode, "未设置");
}

export function turnStatusMeta(status?: string | null): Meta {
  return pick(TURN_STATUS, status, "未知");
}

export function turnActionLabel(action?: string | null): string {
  return (action && ACTION[action]) || "题目";
}

export function assessmentMeta(status?: string | null): Meta {
  return pick(ASSESSMENT, status, "未评估");
}

export function evidenceMeta(status?: string | null): Meta {
  return pick(EVIDENCE, status, "未知");
}

export function hintMeta(level?: string | null): Meta {
  return pick(HINT, level, "未知提示");
}

export function reportMeta(status?: string | null): Meta {
  return pick(REPORT, status, "报告");
}

export function levelMeta(level: number | null | undefined) {
  if (level === null || level === undefined || Number.isNaN(level)) {
    return { label: "信息不足", tone: "neutral" as Tone, value: null };
  }
  const clamped = Math.max(0, Math.min(3, Math.round(level)));
  return { ...LEVELS[clamped], value: clamped };
}

export function prettySkill(id?: string | null): string {
  if (!id) return "未命名要求";
  if (SKILL_LABELS[id]) return SKILL_LABELS[id];
  return id.replace(/[._]/g, " · ");
}

export function prettyRequirement(id?: string | null): string {
  if (!id) return "岗位要求";
  const match = /^req[_-]?(\d+)$/i.exec(id);
  if (match) return `岗位要求 ${match[1].padStart(2, "0")}`;
  return prettySkill(id);
}

export function cleanLabel(label?: string | null, fallbackId?: string | null): string {
  const cleaned = (label ?? "").trim().replace(/[：:\s]+$/u, "");
  if (cleaned.length >= 4 && cleaned !== "要求" && cleaned !== "职责") return cleaned;
  return prettyRequirement(fallbackId);
}

export function shortId(id?: string | null, length = 8): string {
  if (!id) return "—";
  return id.slice(0, length);
}

function parse(iso?: string | null): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(iso?: string | null): string {
  const date = parse(iso);
  if (!date) return "—";
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return new Intl.DateTimeFormat("zh-CN", {
    ...(sameYear ? {} : { year: "numeric" }),
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatDate(iso?: string | null): string {
  const date = parse(iso);
  if (!date) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric" }).format(date);
}

export function formatTime(iso?: string | null): string {
  const date = parse(iso);
  if (!date) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatWeekday(iso?: string | null): string {
  const date = parse(iso);
  if (!date) return "";
  return new Intl.DateTimeFormat("zh-CN", { weekday: "long" }).format(date);
}

export function formatRelative(iso?: string | null): string {
  const date = parse(iso);
  if (!date) return "—";
  const diff = Date.now() - date.getTime();
  if (diff < 0) {
    const ahead = Math.abs(diff);
    if (ahead < 60_000) return "即将开始";
    if (ahead < 3_600_000) return `${Math.round(ahead / 60_000)} 分钟后`;
    return formatDateTime(iso);
  }
  if (diff < 45_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.max(1, Math.round(diff / 60_000))} 分钟前`;
  if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)} 小时前`;
  if (diff < 7 * 86_400_000) return `${Math.round(diff / 86_400_000)} 天前`;
  return formatDateTime(iso);
}

export function dayKey(iso?: string | null): string {
  const date = parse(iso);
  if (!date) return "unknown";
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
}

export function dayHeading(iso?: string | null): string {
  const date = parse(iso);
  if (!date) return "未知日期";
  const today = new Date();
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const startOfDay = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const diffDays = Math.round((startOfToday - startOfDay) / 86_400_000);
  if (diffDays === 0) return `今天 · ${formatDate(iso)}`;
  if (diffDays === 1) return `昨天 · ${formatDate(iso)}`;
  return `${formatDate(iso)} · ${formatWeekday(iso)}`;
}

export function formatClock(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const seconds = safe % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${pad(minutes)}:${pad(seconds)}`;
}

export function formatChars(text?: string | null): string {
  const length = (text ?? "").trim().length;
  return `${length.toLocaleString("zh-CN")} 字`;
}

export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

export function friendlyError(message?: string | null): string {
  const text = (message ?? "").trim();
  if (!text) return "操作没有完成，请稍后再试。";
  if (/not found/i.test(text)) return "没有找到对应的数据，可能链接已过期或已被删除。";
  if (/unauthori[sz]ed|\b401\b/i.test(text)) return "登录状态已失效，刷新页面重新登录。";
  if (/forbidden|\b403\b/i.test(text)) return "当前账号没有权限做这件事。";
  if (/conflict|state[_ ]version|\b409\b/i.test(text)) return "页面状态有点旧了，已为你同步最新进度。";
  if (/timeout|timed out|\b504\b/i.test(text)) return "请求超时了，稍后再试一次。";
  if (/failed to fetch|network ?error/i.test(text)) return "网络好像断了，检查连接后再试。";
  return text;
}

export function averageLevel(levels: (number | null | undefined)[]): number | null {
  const values = levels.filter((level): level is number => typeof level === "number" && !Number.isNaN(level));
  if (!values.length) return null;
  return Math.round((values.reduce((sum, value) => sum + value, 0) / values.length) * 10) / 10;
}

export function truncate(text: string, length = 120): string {
  if (text.length <= length) return text;
  return `${text.slice(0, length)}…`;
}

export function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.trim());
}
