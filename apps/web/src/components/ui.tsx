import { useEffect } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import type { LinkProps } from "react-router-dom";
import { Icon } from "./Icon";
import type { IconName } from "./Icon";
import { levelMeta } from "../lib/format";
import type { Tone } from "../lib/format";

export function cx(...values: (string | false | null | undefined)[]): string {
  return values.filter(Boolean).join(" ");
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "soft";
type ButtonSize = "sm" | "md" | "lg";

export function Button({
  variant = "primary",
  size = "md",
  iconLeft,
  iconRight,
  loading = false,
  className,
  children,
  disabled,
  type = "button",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  iconLeft?: IconName;
  iconRight?: IconName;
  loading?: boolean;
}) {
  return (
    <button
      type={type}
      className={cx("btn", `btn-${variant}`, `btn-${size}`, className)}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Spinner size={15} /> : iconLeft ? <Icon name={iconLeft} size={size === "sm" ? 15 : 17} /> : null}
      {children ? <span>{children}</span> : null}
      {iconRight && !loading ? <Icon name={iconRight} size={size === "sm" ? 15 : 17} /> : null}
    </button>
  );
}

export function LinkButton({
  to,
  variant = "secondary",
  size = "md",
  iconLeft,
  iconRight,
  className,
  children,
  ...rest
}: LinkProps & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  iconLeft?: IconName;
  iconRight?: IconName;
  children: ReactNode;
}) {
  return (
    <Link to={to} className={cx("btn", `btn-${variant}`, `btn-${size}`, className)} {...rest}>
      {iconLeft ? <Icon name={iconLeft} size={size === "sm" ? 15 : 17} /> : null}
      <span>{children}</span>
      {iconRight ? <Icon name={iconRight} size={size === "sm" ? 15 : 17} /> : null}
    </Link>
  );
}

export function Badge({
  tone = "neutral",
  dot = false,
  className,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span className={cx("badge", `badge-${tone}`, className)}>
      {dot ? <span className="badge-dot" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  meta,
}: {
  eyebrow?: string;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div className="page-head-text">
        {eyebrow ? (
          <div className="eyebrow">
            <span className="eyebrow-line" aria-hidden="true" />
            {eyebrow}
          </div>
        ) : null}
        <h1>{title}</h1>
        {description ? <p className="lead">{description}</p> : null}
        {meta ? <div className="head-meta">{meta}</div> : null}
      </div>
      {actions ? <div className="head-actions">{actions}</div> : null}
    </header>
  );
}

export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="back-link">
      <Icon name="arrowLeft" size={15} />
      {children}
    </Link>
  );
}

export function Alert({
  tone = "warn",
  title,
  icon,
  children,
  className,
}: {
  tone?: Tone;
  title?: ReactNode;
  icon?: IconName;
  children?: ReactNode;
  className?: string;
}) {
  const fallbackIcon: IconName = tone === "danger" ? "alert" : tone === "success" ? "checkCircle" : tone === "accent" ? "info" : "info";
  return (
    <div className={cx("alert", `alert-${tone}`, className)} role={tone === "danger" ? "alert" : "status"}>
      <span className="alert-icon">
        <Icon name={icon ?? fallbackIcon} size={17} />
      </span>
      <div className="alert-body">
        {title ? <p className="alert-title">{title}</p> : null}
        {children ? <div className="alert-text">{children}</div> : null}
      </div>
    </div>
  );
}

export function EmptyState({
  icon = "inbox",
  tone = "neutral",
  title,
  description,
  action,
}: {
  icon?: IconName;
  tone?: Tone;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={cx("empty-state", `tone-${tone}`)}>
      <span className="empty-icon">
        <Icon name={icon} size={26} />
      </span>
      <h3>{title}</h3>
      {description ? <p>{description}</p> : null}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: { value: T; label: ReactNode; count?: number }[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel?: string;
}) {
  return (
    <div className="segmented" role="tablist" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={option.value === value}
          className={cx("segmented-item", option.value === value && "is-active")}
          onClick={() => onChange(option.value)}
        >
          {option.label}
          {typeof option.count === "number" ? <span className="segmented-count">{option.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function LevelMeter({
  level,
  showText = true,
  size = "md",
}: {
  level: number | null | undefined;
  showText?: boolean;
  size?: "sm" | "md";
}) {
  const meta = levelMeta(level);
  return (
    <span className={cx("level", `level-${size}`)} data-tone={meta.tone}>
      <span className="level-bars" aria-hidden="true">
        {[0, 1, 2, 3].map((index) => (
          <i key={index} className={index <= (meta.value ?? -1) ? "on" : undefined} />
        ))}
      </span>
      {showText ? (
        <span className="level-text">
          {meta.value === null ? "信息不足" : `L${meta.value} · ${meta.label}`}
        </span>
      ) : (
        <span className="sr-only">{meta.value === null ? "信息不足" : `等级 ${meta.value} ${meta.label}`}</span>
      )}
    </span>
  );
}

export function StatTile({
  label,
  value,
  unit,
  hint,
  icon,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  hint?: ReactNode;
  icon?: IconName;
  tone?: Tone;
}) {
  return (
    <div className={cx("stat-tile", `tone-${tone}`)}>
      <div className="stat-top">
        <span className="stat-label">{label}</span>
        {icon ? (
          <span className="stat-icon">
            <Icon name={icon} size={16} />
          </span>
        ) : null}
      </div>
      <div className="stat-value">
        {value}
        {unit ? <span className="stat-unit">{unit}</span> : null}
      </div>
      {hint ? <div className="stat-hint">{hint}</div> : null}
    </div>
  );
}

export function ProgressBar({ value, tone = "accent" }: { value: number; tone?: Tone }) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className={cx("progress", `tone-${tone}`)} role="progressbar" aria-valuenow={Math.round(clamped)} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: `${clamped}%` }} />
    </div>
  );
}

export function Spinner({ size = 18 }: { size?: number }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-label="加载中" role="status" />;
}

export function Skeleton({
  width,
  height = 14,
  radius,
  className,
}: {
  width?: number | string;
  height?: number;
  radius?: number;
  className?: string;
}) {
  return (
    <span
      className={cx("skeleton", className)}
      style={{ width, height, borderRadius: radius }}
      aria-hidden="true"
    />
  );
}

export function Modal({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className={cx("modal", `modal-${size}`)} role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : "对话框"}>
        <div className="modal-head">
          <div>
            <h2>{title}</h2>
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭">
            <Icon name="x" size={18} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-foot">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
