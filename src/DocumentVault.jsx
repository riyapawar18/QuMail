import React, { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  X, FileText, Download, Paperclip, ShieldAlert, AlertCircle, RefreshCcw,
  FolderLock, CalendarDays, Building2, BadgeCheck, Lock,
} from "lucide-react";
import { apiFetch, readJson } from "./api";

const sanitizeFileName = (name, fallback) => {
  const base = String(name || fallback || "document").replace(/[\\/:*?"<>|]/g, "-").trim();
  return /\.pdf$/i.test(base) ? base : `${base}.pdf`;
};

export default function DocumentVault({ onClose, onAttach }) {
  const [documents, setDocuments] = useState([]);
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  const [disclaimer, setDisclaimer] = useState("");
  const [busyAction, setBusyAction] = useState(""); // download | attach | ""

  const load = async () => {
    setStatus("loading");
    setError("");
    try {
      const res = await apiFetch("/api/digilocker/documents");
      const data = await readJson(res);
      if (!res.ok) throw new Error(data.error || `Vault request failed (${res.status})`);
      setDocuments(data.documents || []);
      setDisclaimer(data.disclaimer || "");
      setStatus("ready");
    } catch (err) {
      setError(err.message || "Could not reach the document vault.");
      setStatus("error");
    }
  };

  useEffect(() => {
    load();
  }, []);

  const fetchBlob = async (doc) => {
    const res = await apiFetch(`/api/digilocker/documents/${encodeURIComponent(doc.id)}/download`);
    if (!res.ok) {
      const data = await readJson(res);
      throw new Error(data.error || `Download failed (${res.status})`);
    }
    return res.blob();
  };

  const handleDownload = async (doc) => {
    setBusyAction("download");
    setError("");
    try {
      const blob = await fetchBlob(doc);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = sanitizeFileName(doc.name, doc.id);
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyAction("");
    }
  };

  const handleAttach = async (doc) => {
    setBusyAction("attach");
    setError("");
    try {
      const blob = await fetchBlob(doc);
      const file = new File([blob], sanitizeFileName(doc.name, doc.id), { type: "application/pdf" });
      onAttach(file, doc);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyAction("");
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[150] bg-black/30 backdrop-blur-[3px] flex items-center justify-center p-3 sm:p-6"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.97, y: 14 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.97, y: 14 }}
        transition={{ duration: 0.18 }}
        className="bg-white w-full max-w-[1080px] h-[88vh] sm:h-[82vh] rounded-[28px] shadow-2xl border border-white/60 overflow-hidden flex flex-col"
      >
        {/* Header */}
        <div className="shrink-0 px-5 sm:px-7 py-4 border-b border-gray-100 flex items-center gap-3 bg-gradient-to-r from-white to-[#F7F9FC]">
          <div className="w-10 h-10 rounded-2xl bg-blue-50 border border-blue-100 flex items-center justify-center">
            <FolderLock size={19} className="text-primary" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-[18px] font-black tracking-tight text-gray-900">Document Vault</h2>
              <span className="text-[9px] font-black uppercase tracking-[0.14em] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 border border-amber-200">
                Mock DigiLocker
              </span>
            </div>
            <p className="text-[12px] text-gray-500 truncate">
              Synthetic sample documents for the demo — not verified records
            </p>
          </div>
          <button
            onClick={onClose}
            className="ml-auto p-2 rounded-full hover:bg-gray-100 text-gray-500 transition-colors"
            aria-label="Close vault"
          >
            <X size={20} />
          </button>
        </div>

        {/* Disclaimer */}
        <div className="shrink-0 mx-5 sm:mx-7 mt-4 flex items-start gap-2.5 rounded-2xl bg-amber-50 border border-amber-100 px-4 py-3">
          <ShieldAlert size={16} className="text-amber-600 mt-0.5 shrink-0" />
          <p className="text-[12px] text-amber-800 leading-relaxed">
            {disclaimer ||
              "This is a mock DigiLocker integration. Every document is a synthetic sample, not a government-issued record, and no real identity or Aadhaar data is used."}
          </p>
        </div>

        {error && (
          <div className="shrink-0 mx-5 sm:mx-7 mt-3 flex items-center gap-2 rounded-xl bg-red-50 border border-red-100 px-4 py-2.5 text-[12px] text-red-700">
            <AlertCircle size={15} className="shrink-0" />
            <span className="flex-1">{error}</span>
            <button onClick={() => setError("")} className="text-red-400 hover:text-red-600 p-1">
              <X size={14} />
            </button>
          </div>
        )}

        {/* Body */}
        <div className="flex-1 min-h-0 flex flex-col lg:flex-row gap-0 lg:gap-5 p-5 sm:p-7 pt-4">
          {/* Document list */}
          <div className="flex-1 min-h-0 overflow-y-auto scrollbar-thin pr-0 lg:pr-1">
            {status === "loading" && (
              <div className="h-full min-h-[220px] flex flex-col items-center justify-center text-gray-400">
                <div className="w-10 h-10 border-[3px] border-blue-100 border-t-primary rounded-full animate-spin mb-3" />
                <p className="text-[13px] font-medium">Loading vault documents…</p>
              </div>
            )}

            {status === "error" && (
              <div className="h-full min-h-[220px] flex flex-col items-center justify-center text-center p-6">
                <div className="w-14 h-14 rounded-full bg-red-50 flex items-center justify-center mb-3">
                  <AlertCircle size={26} className="text-red-400" />
                </div>
                <p className="text-[14px] font-bold text-gray-700">Vault unavailable</p>
                <p className="text-[12px] text-gray-500 mt-1 max-w-[260px]">{error}</p>
                <button
                  onClick={load}
                  className="mt-4 flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-white text-[12.5px] font-bold hover:bg-blue-700 transition-colors"
                >
                  <RefreshCcw size={14} /> Try again
                </button>
              </div>
            )}

            {status === "ready" && documents.length === 0 && (
              <div className="h-full min-h-[220px] flex flex-col items-center justify-center text-center p-6">
                <div className="w-14 h-14 rounded-full bg-gray-50 flex items-center justify-center mb-3">
                  <FileText size={26} className="text-gray-300" />
                </div>
                <p className="text-[14px] font-bold text-gray-700">No documents yet</p>
                <p className="text-[12px] text-gray-500 mt-1 max-w-[260px]">
                  The mock DigiLocker has no sample documents configured.
                </p>
              </div>
            )}

            {status === "ready" && documents.length > 0 && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {documents.map((doc) => {
                  const active = selected?.id === doc.id;
                  return (
                    <button
                      key={doc.id}
                      onClick={() => setSelected(doc)}
                      className={`text-left rounded-2xl border p-4 transition-all group ${
                        active
                          ? "border-primary/40 bg-blue-50/70 shadow-md shadow-blue-600/5"
                          : "border-gray-100 bg-white hover:border-gray-200 hover:shadow-md"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="w-10 h-10 rounded-xl bg-gray-50 border border-gray-100 flex items-center justify-center shrink-0 group-hover:bg-white">
                          <FileText size={18} className={active ? "text-primary" : "text-gray-400"} />
                        </div>
                        <span className="text-[9px] font-black uppercase tracking-[0.12em] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 border border-amber-200">
                          Sample
                        </span>
                      </div>
                      <p className="text-[13.5px] font-bold text-gray-900 mt-3 leading-snug line-clamp-2">
                        {doc.name}
                      </p>
                      <p className="text-[11.5px] text-gray-500 mt-1 truncate">{doc.issuer}</p>
                      <div className="flex items-center gap-2 mt-3 text-[10.5px] text-gray-400 font-medium">
                        <span className="px-1.5 py-0.5 rounded bg-gray-50 border border-gray-100">
                          {doc.type}
                        </span>
                        <span>{doc.issue_date}</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Detail panel */}
          <AnimatePresence mode="wait">
            {selected && (
              <motion.div
                key={selected.id}
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 12 }}
                transition={{ duration: 0.16 }}
                className="lg:w-[340px] shrink-0 mt-4 lg:mt-0 rounded-2xl border border-gray-100 bg-[#FAFBFC] flex flex-col overflow-hidden max-h-[46%] lg:max-h-none"
              >
                <div className="px-5 py-4 border-b border-gray-100 flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-[15px] font-black text-gray-900 leading-snug">{selected.name}</p>
                    <p className="text-[11.5px] text-gray-500 mt-1">{selected.sample_label}</p>
                  </div>
                  <button
                    onClick={() => setSelected(null)}
                    className="p-1.5 rounded-full hover:bg-gray-100 text-gray-400 shrink-0"
                    aria-label="Close document details"
                  >
                    <X size={15} />
                  </button>
                </div>

                <div className="px-5 py-4 space-y-3 overflow-y-auto scrollbar-thin">
                  <Meta icon={Building2} label="Issuer" value={selected.issuer} />
                  <Meta icon={CalendarDays} label="Issued" value={selected.issue_date} />
                  <Meta icon={BadgeCheck} label="Status" value="Sample · not verified" />
                  <Meta icon={Lock} label="Type" value={`${selected.type} · ${selected.size_kb || 0} KB`} />
                  <Meta icon={FileText} label="Document ID" value={selected.id} mono />

                  <div className="rounded-xl bg-white border border-gray-100 p-3 mt-1">
                    <p className="text-[10px] font-black uppercase tracking-[0.14em] text-gray-400 mb-1.5">
                      Preview
                    </p>
                    <p className="text-[11.5px] text-gray-500 leading-relaxed">
                      A synthetic one-page PDF rendered by the mock DigiLocker. It contains fictional
                      holder details and a clear "SAMPLE" watermark.
                    </p>
                  </div>
                </div>

                <div className="mt-auto px-5 py-4 border-t border-gray-100 bg-white space-y-2">
                  <button
                    onClick={() => handleAttach(selected)}
                    disabled={!!busyAction}
                    className="w-full flex items-center justify-center gap-2 h-10 rounded-xl bg-primary hover:bg-blue-700 disabled:opacity-60 text-white text-[13px] font-bold transition-colors"
                  >
                    {busyAction === "attach" ? (
                      <span className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                    ) : (
                      <Paperclip size={15} />
                    )}
                    Attach to Email
                  </button>
                  <button
                    onClick={() => handleDownload(selected)}
                    disabled={!!busyAction}
                    className="w-full flex items-center justify-center gap-2 h-10 rounded-xl bg-white hover:bg-gray-50 disabled:opacity-60 border border-gray-200 text-gray-700 text-[13px] font-semibold transition-colors"
                  >
                    {busyAction === "download" ? (
                      <span className="w-4 h-4 border-2 border-gray-200 border-t-gray-500 rounded-full animate-spin" />
                    ) : (
                      <Download size={15} />
                    )}
                    Download sample PDF
                  </button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </motion.div>
    </motion.div>
  );
}

function Meta({ icon: Icon, label, value, mono = false }) {
  return (
    <div className="flex items-start gap-2.5">
      <div className="w-6 h-6 rounded-lg bg-white border border-gray-100 flex items-center justify-center shrink-0 mt-0.5">
        <Icon size={12} className="text-gray-400" />
      </div>
      <div className="min-w-0">
        <p className="text-[10px] font-black uppercase tracking-[0.12em] text-gray-400">{label}</p>
        <p className={`text-[12px] text-gray-700 font-medium break-words ${mono ? "font-mono" : ""}`}>
          {value || "—"}
        </p>
      </div>
    </div>
  );
}
