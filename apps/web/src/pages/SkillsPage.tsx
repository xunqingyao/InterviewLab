import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ensureLogin } from "../api";
import { Icon } from "../components/Icon";
import {
  Badge,
  Button,
  EmptyState,
  LevelMeter,
  LinkButton,
  PageHeader,
  Segmented,
  Skeleton,
  StatTile,
} from "../components/ui";
import {
  averageLevel,
  dayHeading,
  dayKey,
  evidenceMeta,
  formatTime,
  hintMeta,
  levelMeta,
  prettyRequirement,
} from "../lib/format";

type SkillEvidence = {
  id: string;
  skill_id: string;
  requirement_id: string;
  level: number | null;
  hint_level: string;
  independent: boolean;
  evidence_strength: string;
  observed_at: string;
  source: string;
  source_assessment_id?: string | null;
};

type SourceFilter = "all" | "interview" | "exercise";

const SOURCE_LABEL: Record<string, string> = {
  interview: "面试",
  exercise: "训练",
};

export default function SkillsPage() {
  const [source, setSource] = useState<SourceFilter>("all");
  const [requirement, setRequirement] = useState("all");

  const query = useQuery({
    queryKey: ["skills"],
    queryFn: async () => {
      await ensureLogin();
      return api<{ items: SkillEvidence[] }>("/api/v1/skills");
    },
    refetchInterval: 10000,
  });

  const items = useMemo(() => {
    const rows = [...(query.data?.items ?? [])];
    rows.sort((a, b) => (a.observed_at < b.observed_at ? 1 : -1));
    return rows;
  }, [query.data]);

  const requirements = useMemo(() => {
    const seen = new Map<string, { id: string; count: number; levels: (number | null)[] }>();
    for (const item of items) {
      const key = item.requirement_id || item.skill_id;
      const entry = seen.get(key) ?? { id: key, count: 0, levels: [] };
      entry.count += 1;
      entry.levels.push(item.level);
      seen.set(key, entry);
    }
    return [...seen.values()].sort((a, b) => (a.id < b.id ? -1 : 1));
  }, [items]);

  const filtered = useMemo(
    () =>
      items.filter(
        (item) =>
          (source === "all" || item.source === source) &&
          (requirement === "all" || item.requirement_id === requirement || item.skill_id === requirement),
      ),
    [items, source, requirement],
  );

  const grouped = useMemo(() => {
    const groups: { key: string; heading: string; rows: SkillEvidence[] }[] = [];
    for (const item of filtered) {
      const key = dayKey(item.observed_at);
      const last = groups[groups.length - 1];
      if (last && last.key === key) last.rows.push(item);
      else groups.push({ key, heading: dayHeading(item.observed_at), rows: [item] });
    }
    return groups;
  }, [filtered]);

  const independent = items.filter((item) => item.independent);
  const mastered = items.filter((item) => item.independent && (item.level ?? 0) >= 2);
  const average = averageLevel(items.map((item) => item.level));

  return (
    <div className="stack-32 page-enter">
      <PageHeader
        eyebrow="技能证据"
        title="每一条结论，都能追到一句原话"
        description="这里按时间列出所有岗位要求的证据。看过提示或示范的作答会被标出来，不会算进独立掌握。"
        actions={
          <LinkButton to="/hall" variant="ghost" iconLeft="layers">
            面试大厅
          </LinkButton>
        }
      />

      <div className="stat-row">
        <StatTile icon="layers" label="证据总数" value={items.length} unit="条" hint="面试与训练累计" />
        <StatTile
          icon="user"
          label="独立作答"
          value={independent.length}
          unit={`/ ${items.length}`}
          hint="没有看提示或示范"
          tone={independent.length ? "success" : "neutral"}
        />
        <StatTile
          icon="checkCircle"
          label="独立掌握"
          value={mastered.length}
          unit={`/ ${items.length}`}
          hint="独立作答且达到 L2 及以上"
          tone={mastered.length ? "success" : "neutral"}
        />
        <StatTile
          icon="target"
          label="平均等级"
          value={average === null ? "—" : average.toFixed(1)}
          unit="/ 3"
          hint={average === null ? "还没有有效评级" : levelMeta(Math.round(average)).label}
        />
      </div>

      <div className="toolbar">
        <Segmented
          ariaLabel="证据来源"
          value={source}
          onChange={(value) => setSource(value as SourceFilter)}
          options={[
            { value: "all", label: `全部 ${items.length}` },
            { value: "interview", label: "面试" },
            { value: "exercise", label: "训练" },
          ]}
        />
        <span className="toolbar-side muted-small">
          {query.isFetching ? "同步中…" : `共 ${requirements.length} 项岗位要求`}
        </span>
      </div>

      {requirements.length > 1 ? (
        <div className="chip-row" role="group" aria-label="按岗位要求筛选">
          <button
            type="button"
            className={requirement === "all" ? "chip is-active" : "chip"}
            onClick={() => setRequirement("all")}
          >
            全部要求
          </button>
          {requirements.map((item) => (
            <button
              type="button"
              key={item.id}
              className={requirement === item.id ? "chip is-active" : "chip"}
              onClick={() => setRequirement(item.id)}
            >
              {prettyRequirement(item.id)}
              <span className="chip-count">{item.count}</span>
            </button>
          ))}
        </div>
      ) : null}

      {query.isLoading ? (
        <div className="card stack-16">
          {[0, 1, 2, 3].map((key) => (
            <Skeleton key={key} height={52} radius={12} />
          ))}
        </div>
      ) : query.isError ? (
        <EmptyState
          icon="alert"
          tone="danger"
          title="证据时间线加载失败"
          description={query.error instanceof Error ? query.error.message : "请确认 API 服务正在运行。"}
          action={
            <Button variant="primary" iconLeft="refresh" onClick={() => query.refetch()}>
              重试
            </Button>
          }
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon="compass"
          title={items.length === 0 ? "还没有积累任何证据" : "没有符合条件的证据"}
          description={
            items.length === 0
              ? "完成一场严格面试后，每一次有效观察都会进入这条时间线。"
              : "换一个来源或岗位要求看看。"
          }
          action={
            items.length === 0 ? (
              <LinkButton to="/" variant="primary" iconLeft="plus">
                开始一场面试
              </LinkButton>
            ) : (
              <Button
                variant="ghost"
                onClick={() => {
                  setSource("all");
                  setRequirement("all");
                }}
              >
                清除筛选
              </Button>
            )
          }
        />
      ) : (
        <div className="timeline">
          {grouped.map((group) => (
            <section className="timeline-group" key={group.key}>
              <header className="timeline-group-head">
                <h2>{group.heading}</h2>
                <span className="muted-small">{group.rows.length} 条</span>
              </header>
              <ol className="timeline-list">
                {group.rows.map((item) => {
                  const level = levelMeta(item.level);
                  const evidence = evidenceMeta(item.evidence_strength);
                  const hint = hintMeta(item.hint_level);
                  return (
                    <li className="timeline-item" key={item.id}>
                      <span className={`timeline-dot tone-${level.tone}`} aria-hidden="true" />
                      <div className="timeline-body">
                        <div className="timeline-head">
                          <strong>{prettyRequirement(item.requirement_id || item.skill_id)}</strong>
                          <LevelMeter level={item.level} size="sm" />
                          <Badge tone={evidence.tone}>{evidence.label}</Badge>
                          {item.independent ? (
                            <Badge tone="success" dot>
                              独立
                            </Badge>
                          ) : (
                            <Badge tone={hint.tone}>{hint.label}</Badge>
                          )}
                        </div>
                        <p className="timeline-meta">
                          <span>{formatTime(item.observed_at)}</span>
                          <span className="dot-sep" />
                          <span>{SOURCE_LABEL[item.source] ?? item.source}</span>
                          {item.source_assessment_id ? (
                            <>
                              <span className="dot-sep" />
                              <span className="mono" title={item.source_assessment_id}>
                                评估 {item.source_assessment_id.slice(0, 8)}
                              </span>
                            </>
                          ) : null}
                        </p>
                      </div>
                    </li>
                  );
                })}
              </ol>
            </section>
          ))}
        </div>
      )}

      <p className="page-footnote">
        <Icon name="info" size={14} />
        时间线每 10 秒同步一次。等级是训练用刻度，不是招聘打分；证据不足的观察会单独标记。
      </p>
    </div>
  );
}
