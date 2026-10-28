import React, { useEffect, useState } from "react";
import SplashAnimation from "./SplashAnimation";
import QuMailApp from "./QuMailApp";
import LoginScreen from "./LoginScreen";
import {
  API_BASE,
  apiFetch,
  readJson,
  getDemoToken,
  setDemoToken,
  clearDemoToken,
} from "./api";

export default function App() {
  const [showSplash, setShowSplash] = useState(true);
  const [checking, setChecking] = useState(true);
  const [auth, setAuth] = useState(null); // { mode: "google" | "demo", profile, token }
  const [gmailAvailable, setGmailAvailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Resolve an existing Google session or a stored guest session on load.
  useEffect(() => {
    let cancelled = false;

    const params = new URLSearchParams(window.location.search);
    const oauthError = params.get("oauth_error");
    const connected = params.get("connected");
    if (oauthError) {
      setError(oauthError);
    }
    if (oauthError || connected) {
      params.delete("oauth_error");
      params.delete("connected");
      const qs = params.toString();
      window.history.replaceState({}, "", window.location.pathname + (qs ? `?${qs}` : ""));
    }

    const bootstrap = async () => {
      let authenticated = null;
      try {
        const cfgRes = await fetch(`${API_BASE}/api/config`);
        const cfg = await readJson(cfgRes);
        if (!cancelled && cfgRes.ok) {
          setGmailAvailable(!!(cfg.gmail_client_configured ?? cfg.gmail_available));
          authenticated = !!cfg.authenticated;
        }
      } catch {
        /* backend unreachable; login screen will show the error on action */
      }

      if (authenticated !== false) {
        try {
          const meRes = await fetch(`${API_BASE}/me`);
          if (meRes.ok) {
            const me = await readJson(meRes);
            if (!cancelled && me && me.email && !me.error) {
              setAuth({ mode: "google", profile: me, token: "" });
              setChecking(false);
              return;
            }
          }
        } catch {
          /* no Google session */
        }
      }

      const token = getDemoToken();
      if (token) {
        try {
          const res = await apiFetch("/api/demo/session");
          if (res.ok) {
            const data = await readJson(res);
            if (!cancelled && data.profile) {
              setAuth({ mode: "demo", profile: data.profile, token });
              setChecking(false);
              return;
            }
          } else {
            clearDemoToken();
          }
        } catch {
          clearDemoToken();
        }
      }

      if (!cancelled) setChecking(false);
    };

    bootstrap();
    return () => {
      cancelled = true;
    };
  }, []);

  const startGuest = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/demo/session`, { method: "POST" });
      const data = await readJson(res);
      if (!res.ok || !data.token) {
        throw new Error(data.error || "Could not start the guest demo session.");
      }
      setDemoToken(data.token);
      setAuth({ mode: "demo", profile: data.profile, token: data.token });
    } catch (err) {
      setError(
        err.message === "Failed to fetch"
          ? "Backend is unreachable. Start the API server and try again."
          : err.message || "Could not start the guest demo session."
      );
    } finally {
      setBusy(false);
    }
  };

  const startGoogle = () => {
    setBusy(true);
    setError("");
    window.location.href = `${API_BASE}/auth/google/start`;
  };

  const exitDemo = async () => {
    const token = auth?.token || getDemoToken();
    if (token) {
      try {
        await fetch(`${API_BASE}/api/demo/session/${encodeURIComponent(token)}`, {
          method: "DELETE",
        });
      } catch {
        /* session may already be gone */
      }
    }
    clearDemoToken();
    setAuth(null);
    setError("");
  };

  const signOut = () => {
    setAuth(null);
    setError("");
  };

  if (showSplash) {
    return <SplashAnimation durationMs={2000} onFinish={() => setShowSplash(false)} />;
  }

  if (checking) {
    return <BootScreen />;
  }

  if (!auth) {
    return (
      <LoginScreen
        onGuest={startGuest}
        onGoogle={startGoogle}
        gmailAvailable={gmailAvailable}
        busy={busy}
        error={error}
      />
    );
  }

  return (
    <QuMailApp
      auth={auth}
      onExitDemo={exitDemo}
      onSignOut={signOut}
    />
  );
}

function BootScreen() {
  return (
    <div className="min-h-screen bg-background flex flex-col items-center justify-center font-sans">
      <img
        src="/logo.png"
        alt="QuMail"
        className="w-20 h-20 object-contain drop-shadow-sm mix-blend-multiply mb-5"
      />
      <div className="w-8 h-8 border-[3px] border-gray-300/60 border-t-primary rounded-full animate-spin" />
      <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-gray-500 mt-5">
        Connecting securely…
      </p>
    </div>
  );
}
