import { useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ensureLogin } from "../api";
import { Icon } from "./Icon";
import type { IconName } from "./Icon";
import { Skeleton, cx } from "./ui";

type Me = { id: string; display_name: string; dev_auth: boolean };

const NAV: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: "/", label: "准备", icon: "spark", end: true },
  { to: "/hall", label: "面试大厅", icon: "layers" },
  { to: "/training", label: "训练", icon: "target" },
  { to: "/skills", label: "技能证据", icon: "activity" },
];

export default function AppShell() {
  const { pathname } = useLocation();

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  const me = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      await ensureLogin();
      return api<Me>("/api/v1/me");
    },
    staleTime: 5 * 60_000,
    retry: 1,
  });

  const displayName = me.data?.display_name || "本地账号";
  const initial = (displayName.match(/[a-z0-9]/i)?.[0] || "L").toUpperCase();

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>
      <div className="shell-glow" aria-hidden="true" />
      <header className="topbar">
        <div className="topbar-inner">
          <NavLink to="/" className="brand" end>
            <span className="brand-mark">
              <Icon name="spark" size={19} />
            </span>
            <span className="brand-copy">
              <strong>InterviewLab</strong>
              <span>结构化面试训练</span>
            </span>
          </NavLink>

          <nav className="nav" aria-label="主导航">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) => cx("nav-link", isActive && "is-active")}
              >
                <Icon name={item.icon} size={15} />
                <span>{item.label}</span>
              </NavLink>
            ))}
          </nav>

          <div className="topbar-right">
            {me.isLoading ? (
              <Skeleton width={96} height={30} className="user-skeleton" />
            ) : (
              <span className="user-chip" title={me.isError ? "无法连接后端" : displayName}>
                <span className={cx("avatar", me.isError && "avatar-muted")}>{initial}</span>
                <span className="user-copy">
                  <strong>{me.isError ? "后端未连接" : displayName}</strong>
                  <span>{me.data?.dev_auth ? "本地开发模式" : "已登录"}</span>
                </span>
              </span>
            )}
          </div>
        </div>
      </header>

      <main id="main" className="page">
        <Outlet />
      </main>

    </div>
  );
}
