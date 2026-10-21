import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-10">
      <p className="text-sm text-muted">404</p>
      <h1 className="mt-2 text-2xl font-semibold">Stránka nebyla nalezena</h1>
      <p className="mt-2 text-muted">Odkaz může být neplatný nebo záznam už neexistuje.</p>
      <Link href="/" className="mt-6 inline-flex h-10 items-center rounded-md bg-accent px-4 font-medium text-white hover:bg-accent-hover">Zpět na dokumenty</Link>
    </div>
  );
}
