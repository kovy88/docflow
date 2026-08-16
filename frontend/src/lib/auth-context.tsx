"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { auth, tokens, type TokenResponse } from "@/lib/api";

type Session = {
  user: TokenResponse["user"];
  organization: TokenResponse["organization"];
};

type AuthContextValue = {
  session: Session | null;
  /** Undetermined vs. determined-absent — a page must not flash a login form
   * while a valid token is still being verified. */
  status: "loading" | "authenticated" | "unauthenticated";
  login: (email: string, password: string) => Promise<void>;
  register: (input: {
    email: string;
    password: string;
    fullName?: string;
    organizationName: string;
  }) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<AuthContextValue["status"]>("loading");
  const router = useRouter();

  const bootstrap = useCallback(async () => {
    if (!tokens.access) {
      setStatus("unauthenticated");
      return;
    }
    try {
      const current = await auth.session();
      setSession({ user: current.user, organization: current.organization });
      setStatus("authenticated");
    } catch {
      tokens.clear();
      setStatus("unauthenticated");
    }
  }, []);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  const login = useCallback(async (email: string, password: string) => {
    const result = await auth.login({ email, password });
    tokens.set(result.access_token, result.refresh_token);
    setSession({ user: result.user, organization: result.organization });
    setStatus("authenticated");
  }, []);

  const register = useCallback(
    async (input: { email: string; password: string; fullName?: string; organizationName: string }) => {
      const result = await auth.register({
        email: input.email,
        password: input.password,
        full_name: input.fullName,
        organization_name: input.organizationName,
      });
      tokens.set(result.access_token, result.refresh_token);
      setSession({ user: result.user, organization: result.organization });
      setStatus("authenticated");
    },
    [],
  );

  const logout = useCallback(() => {
    tokens.clear();
    setSession(null);
    setStatus("unauthenticated");
    router.push("/login");
  }, [router]);

  const value = useMemo(
    () => ({ session, status, login, register, logout }),
    [session, status, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
