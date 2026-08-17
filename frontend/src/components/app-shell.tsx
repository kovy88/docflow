"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import { NavLink, Spinner } from "@/components/ui";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard", icon: IconGrid },
  { href: "/documents", label: "Documents", icon: IconFile },
  { href: "/review", label: "Review queue", icon: IconCheck },
  { href: "/settings", label: "Settings", icon: IconGear },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const { status, session, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (status === "unauthenticated") router.replace("/login");
  }, [status, router]);

  if (status === "loading") {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner className="h-6 w-6 text-subtle" />
      </div>
    );
  }
  if (status === "unauthenticated" || !session) return null;

  return (
    <div className="flex h-screen overflow-hidden bg-canvas">
      <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-surface">
        <div className="flex h-[72px] items-center gap-2.5 border-b border-line px-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand text-brand-ink shadow-sm shadow-brand/25">
            <IconLogo className="h-4 w-4" />
          </div>
          <span className="text-[15px] font-semibold tracking-tight">Docflow</span>
        </div>

        <nav className="flex-1 space-y-1 p-3 pt-5">
          <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-subtle">Workspace</p>
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.href} href={item.href} active={pathname.startsWith(item.href)}>
              <item.icon className="h-4 w-4 shrink-0" />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-line p-3">
          <div className="flex items-center gap-2.5 rounded-lg px-2 py-2.5">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand/10 text-xs font-semibold text-brand">
              {session.organization.name.slice(0, 2).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium text-ink">{session.organization.name}</p>
              <p className="truncate text-xs text-subtle">{session.user.email}</p>
            </div>
          </div>
          <button
            onClick={logout}
            className="mt-1 flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-subtle transition-colors hover:bg-muted hover:text-ink"
          >
            <IconLogout className="h-4 w-4" />
            Sign out
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl px-6 py-8 lg:px-10 lg:py-10">{children}</div>
      </main>
    </div>
  );
}

const AUTH_PANEL_POINTS = [
  "Any document type — invoices, contracts, POs, receipts, or your own schema",
  "Three-layer validation catches what the model gets wrong",
  "Low-confidence extractions route to a human, automatically",
];

export function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="flex flex-col">
        <div className="flex h-[72px] items-center border-b border-line px-6 lg:px-10">
          <Link href="/" className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand text-brand-ink shadow-sm shadow-brand/25">
              <IconLogo className="h-4 w-4" />
            </div>
            <span className="text-[15px] font-semibold tracking-tight">Docflow</span>
          </Link>
        </div>
        <div className="flex flex-1 items-center justify-center px-6 py-12">{children}</div>
      </div>

      <div className="split-panel relative hidden flex-col justify-between overflow-hidden px-12 py-12 text-white lg:flex">
        <div />
        <div>
          <p className="text-2xl font-medium leading-snug text-white/95">
            &ldquo;The demo dashboard shows real numbers — cost per document,
            latency, review rate — not a mockup.&rdquo;
          </p>
          <div className="mt-6 space-y-3">
            {AUTH_PANEL_POINTS.map((point) => (
              <div key={point} className="flex items-start gap-2.5 text-sm text-white/80">
                <IconCheck className="mt-0.5 h-4 w-4 shrink-0 text-white/60" />
                {point}
              </div>
            ))}
          </div>
        </div>
        <p className="text-xs text-white/50">Docflow is a demonstration product.</p>
      </div>
    </div>
  );
}

export function PlainHeader() {
  return (
    <header className="flex h-[72px] items-center justify-between border-b border-line bg-surface px-6 lg:px-10">
      <Link href="/" className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand text-brand-ink shadow-sm shadow-brand/25">
          <IconLogo className="h-4 w-4" />
        </div>
        <span className="text-[15px] font-semibold tracking-tight">Docflow</span>
      </Link>
      <Link href="/login" className="text-sm font-medium text-subtle transition-colors hover:text-ink">Sign in</Link>
    </header>
  );
}

/* Inline icon set — no icon-library dependency for ~10 glyphs. */
function IconLogo({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
      <path d="M6 3h9l5 5v13a1 1 0 01-1 1H6a1 1 0 01-1-1V4a1 1 0 011-1z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  );
}
function IconGrid({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
  );
}
function IconFile({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
      <path d="M14 2v6h6" />
    </svg>
  );
}
function IconCheck({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M22 11.08V12a10 10 0 11-5.93-9.14" />
      <path d="M22 4L12 14.01l-3-3" />
    </svg>
  );
}
function IconGear({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
    </svg>
  );
}
function IconLogout({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4" />
      <path d="M16 17l5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  );
}

function IconLayers({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 2l9 5-9 5-9-5 9-5z" />
      <path d="M3 12l9 5 9-5" />
      <path d="M3 17l9 5 9-5" />
    </svg>
  );
}
function IconUsers({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 00-3-3.87" />
      <path d="M16 3.13a4 4 0 010 7.75" />
    </svg>
  );
}
function IconSwap({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M17 1l4 4-4 4" />
      <path d="M3 11V9a4 4 0 014-4h14" />
      <path d="M7 23l-4-4 4-4" />
      <path d="M21 13v2a4 4 0 01-4 4H3" />
    </svg>
  );
}
function IconZap({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" stroke="none">
      <path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z" />
    </svg>
  );
}

export const Icons = {
  Grid: IconGrid,
  File: IconFile,
  Check: IconCheck,
  Gear: IconGear,
  Logout: IconLogout,
  Logo: IconLogo,
  Layers: IconLayers,
  Users: IconUsers,
  Swap: IconSwap,
  Zap: IconZap,
};
