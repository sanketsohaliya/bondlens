import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useContext, useEffect } from "react";
import { api, UNAUTHORIZED_EVENT } from "../api/client";
import type { User } from "../api/types";
import SignIn from "../components/SignIn";

type AuthState = {
  user: User;
  signOut: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

/** Hard gate: children render only with a valid session. */
export default function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: api.auth.me,
    retry: false,
    staleTime: Infinity,
  });
  const logout = useMutation({
    mutationFn: api.auth.logout,
    onSuccess: () => qc.resetQueries(), // clear every user-scoped cache
  });

  // Any data call returning 401 (expired cookie) drops us to sign-in.
  useEffect(() => {
    const onUnauthorized = () => qc.resetQueries();
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [qc]);

  if (me.isPending) {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center">
        <div className="text-zinc-600 animate-pulse">BondLens</div>
      </div>
    );
  }
  if (me.isError || !me.data) {
    return <SignIn onSignedIn={(user) => qc.setQueryData(["auth", "me"], user)} />;
  }
  return (
    <AuthContext.Provider value={{ user: me.data, signOut: () => logout.mutate() }}>
      {children}
    </AuthContext.Provider>
  );
}
