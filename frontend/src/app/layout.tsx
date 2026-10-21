import type { Metadata } from "next";
import Link from "next/link";
import { AccessBanner } from "@/components/AccessBanner";
import { AppNavigation, AppNavigationFallback } from "@/components/AppNavigation";
import { Suspense } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Docflow",
  description:
    "Extrakce dat z dokumentů s ověřenými citacemi a kontrolou pravidel.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="cs" className="h-full antialiased">
      <head>
        <link rel="preconnect" href="https://api.fontshare.com" crossOrigin="" />
        <link rel="preconnect" href="https://cdn.fontshare.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://api.fontshare.com/v2/css?f[]=satoshi@400,500,700&display=swap"
        />
      </head>
      <body className="flex h-full flex-col">
        <header className="flex min-h-[var(--topbar-h)] shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-line bg-surface px-4 py-3 sm:flex-nowrap sm:py-0">
          <Link
            href="/"
            className="flex items-center gap-2 text-base font-bold tracking-tight"
          >
            <span className="grid h-7 w-7 place-items-center rounded bg-accent text-sm text-white">
              D
            </span>
            Docflow
          </Link>
          <span className="hidden text-sm text-muted lg:inline">
            extrakce dokumentů s ověřenými citacemi
          </span>
          <Suspense fallback={<AppNavigationFallback />}>
            <AppNavigation />
          </Suspense>
        </header>
        <Suspense fallback={null}>
          <AccessBanner />
        </Suspense>
        <main className="flex min-h-0 flex-1 flex-col">{children}</main>
      </body>
    </html>
  );
}
