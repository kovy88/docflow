"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  ["/", "Dokumenty"],
  ["/templates", "Šablony"],
  ["/report", "Měření"],
  ["/vyzkouset", "Vyzkoušet"],
] as const;

const more = [
  ["/evals", "Evaluace", "Technické výsledky testování modelů"],
  ["/api", "API", "Dokumentace pro integrátory"],
] as const;

export function AppNavigation() {
  const pathname = usePathname();
  return <NavigationLinks pathname={pathname} />;
}

export function AppNavigationFallback() {
  return <NavigationLinks pathname="" />;
}

function NavigationLinks({ pathname }: { pathname: string }) {
  return (
    <nav aria-label="Hlavní navigace" className="ml-auto flex items-center gap-3 text-sm sm:gap-4">
      {links.map(([href, label]) => {
        const active = href === "/"
          ? pathname === "/" || pathname.startsWith("/documents/")
          : pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link key={href} href={href} aria-current={active ? "page" : undefined}
            onClick={(event) => event.currentTarget.closest("nav")?.querySelector("details")?.removeAttribute("open")}
            className={active ? "font-medium text-accent" : "text-muted hover:text-foreground"}>
            {label}
          </Link>
        );
      })}
      <details className="relative" onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false;
      }} onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.currentTarget.open = false;
          event.currentTarget.querySelector("summary")?.focus();
        }
      }}>
        <summary className={`cursor-pointer ${more.some(([href]) => pathname === href) ? "font-medium text-accent" : "text-muted"}`}>Více</summary>
        <div className="absolute right-0 top-full z-30 mt-3 min-w-48 rounded-lg border border-line bg-surface p-2 shadow-[var(--shadow-float)]">
          {more.map(([href, label, hint]) => (
            <div key={href}>
              <Link href={href} aria-current={pathname === href ? "page" : undefined}
                onClick={(event) => event.currentTarget.closest("details")?.removeAttribute("open")}
                className="block rounded-md px-3 py-2 text-accent hover:bg-accent-soft">{label}</Link>
              <p className="px-3 pb-2 text-xs text-muted">{hint}</p>
            </div>
          ))}
        </div>
      </details>
      <a href="https://logicflow.cz" target="_blank" rel="noopener noreferrer"
        className="hidden border-l border-line pl-4 text-xs text-muted hover:text-foreground md:inline">
        Postaveno Logic flow ↗
      </a>
    </nav>
  );
}
