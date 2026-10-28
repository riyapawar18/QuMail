import React from "react";
import { motion } from "framer-motion";
import { ShieldCheck, ArrowRight, UserRound, Mail, FileText, Lock, AlertCircle } from "lucide-react";

export default function LoginScreen({
  onGuest,
  onGoogle,
  gmailAvailable = false,
  busy = false,
  error = "",
}) {
  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4 sm:p-6 font-sans relative overflow-hidden">
      {/* Soft background accents */}
      <div className="pointer-events-none absolute -top-24 -left-24 w-[420px] h-[420px] rounded-full bg-blue-300/30 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-32 -right-24 w-[420px] h-[420px] rounded-full bg-purple-300/20 blur-3xl" />

      <motion.div
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: "easeOut" }}
        className="relative w-full max-w-[460px]"
      >
        <div className="bg-white/90 backdrop-blur-xl rounded-[32px] shadow-2xl border border-white/70 overflow-hidden">
          <div className="px-7 pt-9 pb-8 sm:px-10 sm:pt-10">
            <div className="flex items-center gap-3 mb-7">
              <img
                src="/logo.png"
                alt="QuMail"
                className="w-14 h-14 object-contain drop-shadow-sm mix-blend-multiply"
              />
              <div>
                <h1 className="text-[24px] font-black tracking-tight text-gray-900 leading-none">QuMail</h1>
                <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-gray-500 mt-1">
                  Quantum-Safe Email
                </p>
              </div>
            </div>

            <h2 className="text-[26px] sm:text-[30px] font-black text-gray-900 leading-[1.15] tracking-tight">
              Secure email,<br />ready for the quantum era.
            </h2>
            <p className="text-[14px] text-gray-500 mt-3 leading-relaxed">
              Encrypted messages, optional post-quantum key exchange, and a document vault — try the full
              experience without connecting a mailbox.
            </p>

            {error && (
              <div className="mt-5 flex items-start gap-2 rounded-xl bg-red-50 border border-red-100 px-3.5 py-3 text-[12.5px] text-red-700">
                <AlertCircle size={16} className="mt-0.5 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            <div className="mt-7 flex flex-col gap-2.5">
              <button
                onClick={onGuest}
                disabled={busy}
                className="group w-full flex items-center justify-center gap-2.5 bg-primary hover:bg-blue-700 disabled:opacity-60 text-white font-bold text-[14.5px] h-12 rounded-2xl shadow-lg shadow-blue-600/20 transition-all"
              >
                <UserRound size={18} />
                {busy ? "Preparing demo…" : "Continue as Guest"}
                {!busy && <ArrowRight size={17} className="group-hover:translate-x-0.5 transition-transform" />}
              </button>

              <button
                onClick={onGoogle}
                disabled={busy || !gmailAvailable}
                title={gmailAvailable ? "Sign in with Google" : "Google OAuth is not configured on this deployment"}
                className="w-full flex items-center justify-center gap-2.5 bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed text-gray-800 font-semibold text-[14.5px] h-12 rounded-2xl border border-gray-200 shadow-sm transition-all"
              >
                <GoogleMark />
                Continue with Google
              </button>
              {!gmailAvailable && (
                <p className="text-[11px] text-gray-400 text-center leading-snug px-2">
                  Google sign-in is not configured on this deployment. Guest demo works fully offline.
                </p>
              )}
            </div>

            <div className="mt-7 rounded-2xl bg-[#F5F7FA] border border-gray-100 p-4">
              <p className="text-[10.5px] font-black uppercase tracking-[0.16em] text-gray-400 mb-2.5">
                What the guest demo includes
              </p>
              <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-[12px] text-gray-600 font-medium">
                <Feature icon={Mail} label="Sample inbox & sent" />
                <Feature icon={ShieldCheck} label="Real AES-256-GCM" />
                <Feature icon={FileText} label="Mock document vault" />
                <Feature icon={Lock} label="No Gmail connection" />
              </div>
            </div>
          </div>

          <div className="px-7 sm:px-10 py-4 bg-gray-50/80 border-t border-gray-100">
            <p className="text-[11px] text-gray-500 leading-relaxed text-center">
              Guest mode is a demonstration. Messages are encrypted with a per-session key and are{" "}
              <strong className="text-gray-700">never sent to Gmail</strong>. Documents are synthetic
              samples, not government-issued records.
            </p>
          </div>
        </div>

        <p className="text-center text-[11px] text-gray-500 mt-5 font-medium">
          QuMail · Quantum-safe mail, documents, and key exchange
        </p>
      </motion.div>
    </div>
  );
}

function Feature({ icon: Icon, label }) {
  return (
    <div className="flex items-center gap-2">
      <div className="w-6 h-6 rounded-lg bg-white border border-gray-200 flex items-center justify-center shrink-0">
        <Icon size={12} className="text-primary" />
      </div>
      <span>{label}</span>
    </div>
  );
}

function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.1H42V20H24v8h11.3C33.7 32.7 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.6-.4-3.9z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.6 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.3 0-9.7-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.1H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4.1 5.5l6.2 5.2C36.9 39.2 44 34 44 24c0-1.3-.1-2.6-.4-3.9z" />
    </svg>
  );
}
