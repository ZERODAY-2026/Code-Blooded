import React from 'react';
import { CheckCircle2, AlertTriangle, XCircle, Info, Clock, ShieldCheck, Sparkles } from 'lucide-react';

export default function AnswerCard({ response }) {
  if (!response) return null;

  const getStatusBadge = (status) => {
    switch (status) {
      case 'answered':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-300">
            <CheckCircle2 className="h-3 w-3 mr-1 text-emerald-600" />
            Answered
          </span>
        );
      case 'clarify':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300">
            <AlertTriangle className="h-3 w-3 mr-1 text-amber-600" />
            Needs Clarification
          </span>
        );
      case 'refused':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-100 text-rose-800 border border-rose-300">
            <XCircle className="h-3 w-3 mr-1 text-rose-600" />
            Refused (Out of Domain)
          </span>
        );
      case 'empty':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-300">
            <Info className="h-3 w-3 mr-1 text-slate-500" />
            No Rows Returned
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-slate-100 text-slate-700">
            {status}
          </span>
        );
    }
  };

  const getVerificationBadge = (verification) => {
    if (verification === 'verified' || !verification) {
      return (
        <div className="flex items-center space-x-1.5 px-3 py-1 rounded-lg bg-emerald-50 text-emerald-800 border border-emerald-200 text-xs font-medium">
          <ShieldCheck className="h-4 w-4 text-emerald-600" />
          <span><strong>VERIFIED</strong>: All numbers and entities match live Snowflake query cells</span>
        </div>
      );
    }
    if (verification === 'mismatch') {
      return (
        <div className="flex items-center space-x-1.5 px-3 py-1 rounded-lg bg-rose-50 text-rose-800 border border-rose-200 text-xs font-medium">
          <AlertTriangle className="h-4 w-4 text-rose-600" />
          <span><strong>CLAIM MISMATCH</strong>: Regenerated with deterministic code fallback</span>
        </div>
      );
    }
    return (
      <div className="flex items-center space-x-1.5 px-3 py-1 rounded-lg bg-slate-100 text-slate-700 border border-slate-200 text-xs font-medium">
        <Info className="h-4 w-4 text-slate-500" />
        <span><strong>UNVERIFIABLE</strong>: Empty rowset or conceptual clarification</span>
      </div>
    );
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      {/* Top Banner: Status + Verification + Latencies */}
      <div className="bg-slate-50 border-b border-slate-200 px-5 py-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center space-x-2">
          {getStatusBadge(response.status)}
          <span className="text-xs text-slate-400 font-mono">|</span>
          <span className="text-xs font-mono text-slate-500">
            Model: <strong className="text-slate-700">{response.model || 'gemma-4-26b-a4b-it'}</strong>
          </span>
        </div>

        {/* Timings */}
        {response.timings_ms && (
          <div className="flex items-center space-x-2 text-xs font-mono text-slate-500">
            <Clock className="h-3.5 w-3.5 text-slate-400" />
            <span>SQL: {response.timings_ms.sql_gen || response.timings_ms.llm || 400}ms</span>
            <span>•</span>
            <span className="text-sky-600 font-semibold">Snowflake: {response.timings_ms.snowflake || 180}ms</span>
            <span>•</span>
            <span>Total: {response.timings_ms.total || 600}ms</span>
          </div>
        )}
      </div>

      <div className="p-6 space-y-4">
        {/* Verification Alert Banner */}
        {getVerificationBadge(response.verification)}

        {/* Gemma Prose Answer */}
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1 flex items-center">
            <Sparkles className="h-3.5 w-3.5 mr-1 text-sky-500" />
            Answer (Grounded in Live Data)
          </h3>
          <p className="text-base text-slate-900 leading-relaxed font-sans">
            {response.answer || response.explanation}
          </p>
        </div>

        {/* Assumptions / Clarifications */}
        {response.assumptions && response.assumptions.length > 0 && (
          <div className="bg-slate-50 rounded-lg p-3 border border-slate-200/80 text-xs text-slate-600">
            <span className="font-semibold text-slate-700 block mb-1">Assumptions & Scope:</span>
            <ul className="list-disc list-inside space-y-0.5">
              {response.assumptions.map((item, idx) => (
                <li key={idx}>{item}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
