import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { User } from "../api/types";
import Logo from "./Logo";

const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined;

// Minimal typings for the GIS script loaded in index.html.
type Google = {
  accounts: {
    id: {
      initialize: (config: object) => void;
      renderButton: (el: HTMLElement, options: object) => void;
      prompt: () => void;
    };
  };
};
declare global {
  interface Window {
    google?: Google;
  }
}

export default function SignIn({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const buttonRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!CLIENT_ID) {
      setError("VITE_GOOGLE_CLIENT_ID is not configured");
      return;
    }
    let cancelled = false;
    const init = () => {
      const gsi = window.google?.accounts.id;
      if (!gsi || !buttonRef.current) return false;
      gsi.initialize({
        client_id: CLIENT_ID,
        auto_select: true, // silent sign-in when already approved
        callback: async (res: { credential: string }) => {
          try {
            onSignedIn(await api.auth.google(res.credential));
          } catch (e) {
            setError(`Sign-in failed: ${e instanceof Error ? e.message : String(e)}`);
          }
        },
      });
      gsi.renderButton(buttonRef.current, {
        theme: "filled_black",
        size: "large",
        shape: "pill",
        width: 280,
      });
      gsi.prompt(); // One Tap: detect an existing Google session
      return true;
    };
    // The GIS script loads async; poll briefly until it is ready.
    if (!init()) {
      const timer = setInterval(() => {
        if (cancelled || init()) clearInterval(timer);
      }, 100);
      return () => {
        cancelled = true;
        clearInterval(timer);
      };
    }
  }, [onSignedIn]);

  return (
    <div className="relative min-h-screen bg-zinc-950 flex items-center justify-center overflow-hidden">
      {/* ambient glow */}
      <div className="pointer-events-none absolute -top-40 left-1/2 -translate-x-1/2 h-[480px] w-[720px] rounded-full bg-emerald-500/[0.07] blur-3xl" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_bottom,rgba(24,24,27,0.6),transparent_60%)]" />

      <div className="relative mx-4 w-full max-w-sm">
        <div className="rounded-2xl border border-zinc-800/80 bg-zinc-900/60 backdrop-blur-sm shadow-[0_0_60px_-15px_rgba(16,185,129,0.15)] p-8 text-center">
          <div className="flex items-center justify-center gap-2.5">
            <Logo size={34} className="rounded-lg ring-1 ring-zinc-800" />
            <h1 className="text-[26px] font-bold tracking-tight text-zinc-100">
              Bond<span className="text-emerald-400">Lens</span>
            </h1>
          </div>
          <p className="mt-2 text-sm text-zinc-400">Indian corporate-bond screener</p>
          <p className="mt-1 text-xs text-zinc-600">Sign in to continue</p>

          {/* [color-scheme:light] stops the browser painting an opaque white
              canvas behind the GIS iframe on our color-scheme:dark page. */}
          <div ref={buttonRef} className="mt-8 flex justify-center [color-scheme:light]" />
          {error && (
            <p className="mt-4 text-xs text-red-400 border border-red-500/30 bg-red-500/10 rounded-md px-3 py-2">
              {error}
            </p>
          )}
        </div>

        <p className="mt-6 px-2 text-center text-xs leading-relaxed text-zinc-500">
          Not investment advice. Prices, yields and ratings are derived from exchange/depository
          data that may be delayed, incomplete or inaccurate; verify independently before acting.
          Bonds carry credit and liquidity risk, including loss of principal.
        </p>
      </div>
    </div>
  );
}
