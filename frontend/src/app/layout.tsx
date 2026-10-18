import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { DEMO, REPO_URL } from "@/lib/api";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin", "latin-ext"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin", "latin-ext"],
});

export const metadata: Metadata = {
  title: "Docflow",
  description:
    "Extrakce dat z dokumentů s ověřenými citacemi a kontrolou pravidel.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="cs"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex h-full flex-col">
        <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
          <Link
            href="/"
            className="flex items-center gap-2 font-semibold tracking-tight"
          >
            <span className="grid h-6 w-6 place-items-center rounded bg-accent text-xs text-white">
              D
            </span>
            Docflow
          </Link>
          <span className="hidden text-sm text-muted sm:inline">
            extrakce dokumentů s ověřenými citacemi
          </span>
          <nav className="ml-auto flex gap-4 text-sm">
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
          </nav>
        </header>
        {DEMO && (
          <div className="shrink-0 border-b border-line bg-accent-soft px-4 py-1.5 text-center text-xs text-accent">
            Veřejné demo s předpočítanými výsledky lokálního modelu, jen pro
            čtení.{" "}
            <a href={REPO_URL} className="font-medium underline">
              Spuštění lokálně
            </a>
          </div>
        )}
        <main className="flex min-h-0 flex-1 flex-col">{children}</main>
      </body>
    </html>
  );
}
