import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { User } from "../api/types";
import Disclaimer from "./Disclaimer";
import LoginShowcase from "./LoginShowcase";
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
        width: 240,
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
    <div className="relative min-h-screen bg-zinc-950 flex flex-col overflow-hidden px-8 py-8">
      {/* ambient glows + a faint dot grid vignetted around the hero */}
      <div className="pointer-events-none absolute -top-32 left-1/3 h-[560px] w-[900px] rounded-full bg-emerald-500/[0.06] blur-3xl" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_bottom,rgba(24,24,27,0.6),transparent_60%)]" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(rgb(63_63_70_/_0.3)_1px,transparent_1px)] [background-size:26px_26px] [mask-image:radial-gradient(ellipse_65%_60%_at_50%_48%,black,transparent)]" />

      {/* Slim navbar: quiet brand top-left, sign-in card top-right.
          The tagline/pills are the hero's own header (LoginShowcase). */}
      <div className="relative flex flex-col lg:flex-row items-center lg:items-start justify-between gap-6">
        <div className="flex items-center gap-2.5">
          <Logo size={34} className="rounded-lg ring-1 ring-zinc-800" />
          <h1 className="text-2xl font-bold tracking-tight text-zinc-100">
            Bond<span className="text-emerald-400">Lens</span>
          </h1>
        </div>

        <div className="w-72 shrink-0 rounded-xl border border-zinc-800/80 bg-zinc-900/70 backdrop-blur-sm shadow-[0_0_50px_-15px_rgba(16,185,129,0.2)] p-5 text-center">
          <p className="text-sm text-zinc-300">Sign in to continue</p>
          {/* [color-scheme:light] stops the browser painting an opaque white
              canvas behind the GIS iframe on our color-scheme:dark page. */}
          <div ref={buttonRef} className="mt-4 flex justify-center [color-scheme:light]" />
          {error && (
            <p className="mt-4 text-xs text-red-400 border border-red-500/30 bg-red-500/10 rounded-md px-3 py-2">
              {error}
            </p>
          )}
        </div>
      </div>

      {/* The hero (tagline + pills + collage) centers itself in the
          space between navbar and footer. */}
      <div className="relative flex-1 hidden lg:block">
        <LoginShowcase />
      </div>

      <div className="relative mt-auto pt-8">
        <Disclaimer />
      </div>
    </div>
  );
}
