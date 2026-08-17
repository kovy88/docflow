"use client";

/**
 * Hand-rolled UI primitives in the shadcn tradition: small, composable,
 * built on plain Tailwind rather than a component-library dependency. Pulling in
 * shadcn/ui's CLI would be the normal move on a real team project; here it adds a
 * generation step and a components.json for a component count this file covers
 * directly. The visual language (radius, shadow, spacing) matches what shadcn
 * ships, so swapping later is a styling exercise, not a rewrite.
 */

import Link from "next/link";
import { type ReactNode, forwardRef } from "react";
import { cn } from "@/lib/utils";

/* --------------------------------------------------------------- Button */

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type ButtonSize = "sm" | "md" | "lg";

const buttonVariants: Record<ButtonVariant, string> = {
  primary: "bg-brand text-brand-ink shadow-sm shadow-brand/20 hover:bg-brand/90 hover:shadow-md hover:shadow-brand/20",
  secondary: "bg-muted text-ink hover:bg-line",
  outline: "border border-line bg-surface text-ink shadow-sm hover:bg-muted hover:border-subtle/30",
  ghost: "text-ink hover:bg-muted",
  danger: "bg-danger text-white hover:opacity-90",
};

const buttonSizes: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-sm rounded-md gap-1.5",
  md: "h-9 px-4 text-sm rounded-lg gap-2",
  lg: "h-11 px-6 text-base rounded-lg gap-2",
};

export const Button = forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: ButtonVariant;
    size?: ButtonSize;
    loading?: boolean;
  }
>(({ className, variant = "primary", size = "md", loading, disabled, children, ...props }, ref) => (
  <button
    ref={ref}
    disabled={disabled || loading}
    className={cn(
      "inline-flex items-center justify-center font-medium transition-all duration-150",
      "disabled:opacity-50 disabled:cursor-not-allowed",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-canvas",
      buttonVariants[variant],
      buttonSizes[size],
      className,
    )}
    {...props}
  >
    {loading && <Spinner className="h-3.5 w-3.5" />}
    {children}
  </button>
));
Button.displayName = "Button";

/* ----------------------------------------------------------------- Card */

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("app-surface rounded-xl border border-line bg-surface", className)}>
      {children}
    </div>
  );
}

export function CardHeader({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn("flex items-center justify-between gap-4 border-b border-line px-5 py-4", className)}>{children}</div>;
}

export function CardTitle({ className, children }: { className?: string; children: ReactNode }) {
  return <h3 className={cn("text-sm font-semibold text-ink", className)}>{children}</h3>;
}

export function CardBody({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn("p-5", className)}>{children}</div>;
}

/* ---------------------------------------------------------------- Badge */

type BadgeTone = "neutral" | "brand" | "ok" | "warn" | "danger";

const badgeTones: Record<BadgeTone, string> = {
  neutral: "bg-muted text-subtle",
  brand: "bg-brand/10 text-brand",
  ok: "bg-ok/10 text-ok",
  warn: "bg-warn/10 text-warn",
  danger: "bg-danger/10 text-danger",
};

export function Badge({
  tone = "neutral",
  className,
  children,
}: {
  tone?: BadgeTone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        badgeTones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/* ---------------------------------------------------------- Status Badge */

const STATUS_CONFIG: Record<string, { tone: BadgeTone; label: string }> = {
  uploaded: { tone: "neutral", label: "Uploaded" },
  queued: { tone: "neutral", label: "Queued" },
  processing: { tone: "brand", label: "Processing" },
  needs_review: { tone: "warn", label: "Needs review" },
  completed: { tone: "ok", label: "Completed" },
  rejected: { tone: "danger", label: "Rejected" },
  failed: { tone: "danger", label: "Failed" },
};

/** Bar-fill color per status, for the dashboard breakdown — mirrors STATUS_CONFIG's tones. */
export const STATUS_TONE_BAR: Record<string, string> = {
  uploaded: "bg-subtle",
  queued: "bg-subtle",
  processing: "bg-brand",
  needs_review: "bg-warn",
  completed: "bg-ok",
  rejected: "bg-danger",
  failed: "bg-danger",
};

export function StatusBadge({ status }: { status: string }) {
  const config = STATUS_CONFIG[status] ?? { tone: "neutral" as const, label: status };
  const pulsing = status === "processing" || status === "queued";
  return (
    <Badge tone={config.tone}>
      {pulsing && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
      {config.label}
    </Badge>
  );
}

export function ConfidenceBadge({ band }: { band: "high" | "medium" | "low" | null }) {
  if (!band) return <Badge tone="neutral">—</Badge>;
  const config = { high: { tone: "ok" as const, label: "High" }, medium: { tone: "warn" as const, label: "Medium" }, low: { tone: "danger" as const, label: "Low" } }[band];
  return <Badge tone={config.tone}>{config.label}</Badge>;
}

/* --------------------------------------------------------------- Input */

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm text-ink shadow-sm shadow-slate-950/[0.02] placeholder:text-subtle",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:border-brand",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export const Textarea = forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink shadow-sm shadow-slate-950/[0.02] placeholder:text-subtle",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:border-brand",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";

export function Label({ className, children, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label className={cn("mb-1.5 block text-sm font-medium text-ink", className)} {...props}>
      {children}
    </label>
  );
}

/* ------------------------------------------------------------- Skeleton */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton rounded-md", className)} />;
}

/* --------------------------------------------------------------- Spinner */

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cn("animate-spin", className)} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}

/* ----------------------------------------------------------------- Empty */

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      {icon && <div className="text-subtle">{icon}</div>}
      <div>
        <p className="text-sm font-medium text-ink">{title}</p>
        {description && <p className="mt-1 max-w-sm text-sm text-subtle">{description}</p>}
      </div>
      {action}
    </div>
  );
}

/* ------------------------------------------------------------------ Nav */

export function NavLink({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: ReactNode;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
        active ? "bg-brand/10 text-brand" : "text-subtle hover:bg-muted hover:text-ink",
      )}
    >
      {children}
    </Link>
  );
}

/* ---------------------------------------------------------------- Toast */

export type ToastTone = "ok" | "danger" | "brand";

export function Toast({ tone, message }: { tone: ToastTone; message: string }) {
  const dot = { ok: "bg-ok", danger: "bg-danger", brand: "bg-brand" }[tone];
  return (
    <div className="flex items-center gap-2.5 rounded-lg border border-line bg-surface px-4 py-3 text-sm text-ink shadow-lg">
      <span className={cn("h-2 w-2 shrink-0 rounded-full", dot)} />
      {message}
    </div>
  );
}

/* ------------------------------------------------------------------ Bar */

export function ProgressBar({ value, className }: { value: number; className?: string }) {
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}>
      <div
        className="h-full rounded-full bg-brand transition-all duration-500"
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  );
}
