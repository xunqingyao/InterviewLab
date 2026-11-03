import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ensureLogin } from "../api";
import { Icon } from "../components/Icon";
import {
  Badge,
  Button,
  EmptyState,
  LinkButton,
  PageHeader,
  Segmented,
  Skeleton,
  StatTile,
} from "../components/ui";
import {
  formatDate,
  formatDateTime,
  formatRelative,
  formatTime,
  modeMeta,
  phaseLabel,
  sessionStatusMeta,
  shortId,
} from "../lib/format";

type SessionRow = {
  id: string;
  status: string;
  phase: string;
  mode: string;
  state_version: number;
  created_at: string;
};

const OPEN_STATUSES = new Set(["CREATED", "ACTIVE", "CLOSING"]);
const DONE_STATUSES = new Set(["COMPLETED"]);

type Filter = "all" | "open" | "done";

export default function HallPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const query = useQuery({
    queryKey: ["interviews"],
    queryFn: async () => {
      await ensureLogin();
      return api<{ items: SessionRow[] }>("/api/v1/interviews");
    },
    refetchInterval: 15000,
  });

  const rows = useMemo(() => {
    const items = [...(query.data?.items ?? [])];
    items.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
    return items;
  }, [query.data]);

  const open = rows.filter((row) => OPEN_STATUSES.has(row.status));
  const done = rows.filter((row) => DONE_STATUSES.has(row.status));
  const filtered = filter === "open" ? open : filter === "done" ? done : rows;

  return (
    <div className="stack-32 page-enter">
      <PageHeader
        eyebrow="面试大厅"
        title="每一次面试，都是一条可追溯的证据链"
        description="进行中的面试可以接着答；结束的面试会留下逐题复盘和训练建议。"
        actions={
          <LinkButton to="/" iconLeft="plus" variant="primary">
            新面试
          </LinkButton>
        }
      />

      <div className="stat-row">
        <StatTile icon="play" label="进行中" value={open.length} hint={open.length ? "可以随时接着答" : "没有未完成的面试"} />
        <StatTile icon="check" label="已完成" value={done.length} hint="含报告未生成的场次" />
        <StatTile icon="layers" label="总场次" value={rows.length} hint="同一账号下的全部面试" />
      </div>

      <div className="toolbar">
        <Segmented
          ariaLabel="筛选面试"
          value={filter}
          onChange={(value) => setFilter(value as Filter)}
          options={[
            { value: "all", label: `全部 ${rows.length}` },
            { value: "open", label: `进行中 ${open.length}` },
            { value: "done", label: `已完成 ${done.length}` },
          ]}
        />
        <div className="toolbar-side">
          {query.isFetching ? <span className="muted-small">同步中…</span> : null}
          <Button variant="ghost" size="sm" iconLeft="refresh" onClick={() => query.refetch()} disabled={query.isFetching}>
            刷新
          </Button>
        </div>
      </div>

      {query.isLoading ? (
        <div className="session-list">
          {[0, 1, 2].map((key) => (
            <div className="card session-card" key={key}>
              <Skeleton width={44} height={44} radius={14} />
              <div className="stack-8 grow">
                <Skeleton width="38%" height={18} />
                <Skeleton width="62%" height={13} />
              </div>
              <Skeleton width={104} height={34} radius={10} />
            </div>
          ))}
        </div>
      ) : query.isError ? (
        <EmptyState
          icon="alert"
          tone="danger"
          title="大厅加载失败"
          description={query.error instanceof Error ? query.error.message : "请确认 API 服务正在运行。"}
          action={
            <Button variant="primary" iconLeft="refresh" onClick={() => query.refetch()}>
              重试
            </Button>
          }
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={rows.length === 0 ? "spark" : "layers"}
          title={rows.length === 0 ? "还没有面试记录" : filter === "open" ? "没有进行中的面试" : "没有已完成的面试"}
          description={
            rows.length === 0
              ? "先准备一份简历和岗位描述，确认后就能开始第一场严格面试。"
              : "换个筛选条件看看，或者开始一场新的面试。"
          }
          action={
            rows.length === 0 ? (
              <LinkButton to="/" variant="primary" iconLeft="plus">
                准备第一场面试
              </LinkButton>
            ) : (
              <Button variant="ghost" onClick={() => setFilter("all")}>
                查看全部
              </Button>
            )
          }
        />
      ) : (
        <div className="session-list">
          {filtered.map((row) => {
            const status = sessionStatusMeta(row.status);
            const mode = modeMeta(row.mode);
            const isOpen = OPEN_STATUSES.has(row.status);
            const isDone = DONE_STATUSES.has(row.status);
            return (
              <article className={`card session-card ${isOpen ? "is-live" : ""}`} key={row.id}>
                <div className={`session-glyph tone-${status.tone}`}>
                  <Icon name={isOpen ? "play" : isDone ? "file" : "alert"} size={18} />
                </div>
                <div className="session-main">
                  <div className="session-title-row">
                    <h3>
                      {formatDate(row.created_at)} {formatTime(row.created_at)}
                    </h3>
                    <Badge tone={status.tone} dot={isOpen}>
                      {status.label}
                    </Badge>
                    <Badge tone={mode.tone}>{mode.label}</Badge>
                    {isOpen ? <span className="live-hint">{phaseLabel(row.phase)}</span> : null}
                  </div>
                  <p className="session-meta">
                    <span className="mono" title={row.id}>
                      {shortId(row.id)}
                    </span>
                    <span className="dot-sep" />
                    <span title={formatDateTime(row.created_at)}>{formatRelative(row.created_at)}</span>
                  </p>
                </div>
                <div className="session-actions">
                  {isOpen ? (
                    <LinkButton to={`/interviews/${row.id}`} variant="primary" size="sm" iconRight="arrowRight">
                      继续面试
                    </LinkButton>
                  ) : null}
                  {isDone ? (
                    <>
                      <LinkButton to={`/interviews/${row.id}/report`} variant="primary" size="sm" iconLeft="file">
                        复盘报告
                      </LinkButton>
                      <LinkButton to={`/training?session=${row.id}`} variant="ghost" size="sm" iconLeft="activity">
                        训练
                      </LinkButton>
                    </>
                  ) : null}
                  {!isOpen && !isDone ? (
                    <LinkButton to={`/interviews/${row.id}`} variant="ghost" size="sm">
                      查看详情
                    </LinkButton>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      )}

      <p className="page-footnote">
        <Icon name="info" size={14} />
        大厅每 15 秒自动同步一次。进行中的面试请直接进入作答，页面会通过事件流实时更新。
      </p>
    </div>
  );
}
