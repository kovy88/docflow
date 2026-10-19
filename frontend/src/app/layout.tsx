import type { Metadata } from "next";
import Link from "next/link";
import { AccessBanner } from "@/components/AccessBanner";
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
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
          <Link
            href="/"
            className="flex items-center gap-2 text-base font-bold tracking-tight"
          >
            <span className="grid h-7 w-7 place-items-center rounded bg-accent text-sm text-white">
              D
            </span>
            Docflow
          </Link>
          <span className="hidden text-sm text-muted sm:inline">
            extrakce dokumentů s ověřenými citacemi
          </span>
          <nav className="ml-auto flex items-center gap-4 text-sm">
            <Link href="/" className="text-muted hover:text-foreground">
              Dokumenty
            </Link>
            <Link
              href="/templates"
              className="text-muted hover:text-foreground"
            >
              Šablony
            </Link>
            <Link href="/evals" className="text-muted hover:text-foreground">
              Evaluace
            </Link>
            <a
              href="https://logicflow.cz"
              target="_blank"
              rel="noopener noreferrer"
              className="hidden border-l border-line pl-4 text-xs text-muted hover:text-foreground md:inline"
            >
              Postaveno Logic flow ↗
            </a>
          </nav>
        </header>
        <AccessBanner />
        <main className="flex min-h-0 flex-1 flex-col">{children}</main>
      </body>
    </html>
  );
}
