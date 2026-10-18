import React, { useState } from 'react';
import {
  CheckCircle2,
  HelpCircle,
  XCircle,
  Info,
  AlertTriangle,
  Clock,
  Sparkles,
  ChevronDown,
  ChevronUp,
  Target
} from 'lucide-react';

export default function AnswerCard({ response, onHighlightCell, typedQuestion }) {
  const [showRetries, setShowRetries] = useState(false);

  if (!response) return null;

  const modelName = response.model?.name || (typeof response.model === 'string' ? response.model : null);
  const retriesList = Array.isArray(response.retries) ? response.retries : [];
  const hasRetries = retriesList.length > 0;

  // "Interpreted as" ONLY when resolved_question !== the typed question
  const isInterpretedDifferent =
    Boolean(response.resolved_question) &&
    response.resolved_question.trim().toLowerCase() !== (typedQuestion || '').trim().toLowerCase();

  // Distinct border & accent styles per state
  const getCardStyle = (status) => {
    switch (status) {
      case 'clarify':
        return 'border-amber-300 ring-4 ring-amber-100/50 bg-amber-50/20';
      case 'refused':
        return 'border-rose-300 ring-4 ring-rose-100/50 bg-rose-50/20';
      case 'empty':
        return 'border-slate-300 ring-4 ring-slate-100/50 bg-slate-50/30';
      case 'error':
        return 'border-red-300 ring-4 ring-red-100/50 bg-red-50/30';
      case 'unverifiable':
        return 'border-purple-300 ring-4 ring-purple-100/50 bg-purple-50/20';
      case 'answered':
      default:
        return 'border-blue-100 ring-4 ring-blue-50/60 bg-white/95';
    }
  };

  // Status Badge Rendering
  const renderStatusBadge = (status) => {
    switch (status) {
      case 'answered':
        return (
          <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-bold bg-emerald-100 text-emerald-800 border border-emerald-300 shadow-xs">
            <CheckCircle2 className="h-3.5 w-3.5 mr-1.5 text-emerald-600" aria-hidden="true" />
            Answered
          </span>
        );
      case 'clarify':
        return (
          <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-bold bg-amber-100 text-amber-900 border border-amber-300 shadow-xs">
            <HelpCircle className="h-3.5 w-3.5 mr-1.5 text-amber-600" aria-hidden="true" />
            Needs Clarification
          </span>
        );
      case 'refused':
        return (
          <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-bold bg-rose-100 text-rose-900 border border-rose-300 shadow-xs">
            <XCircle className="h-3.5 w-3.5 mr-1.5 text-rose-600" aria-hidden="true" />
            Refused
          </span>
        );
      case 'empty':
        return (
          <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-bold bg-slate-100 text-slate-800 border border-slate-300 shadow-xs">
            <Info className="h-3.5 w-3.5 mr-1.5 text-slate-600" aria-hidden="true" />
            0 Rows Returned
          </span>
        );
      case 'error':
        return (
          <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-bold bg-red-100 text-red-900 border border-red-300 shadow-xs">
            <AlertTriangle className="h-3.5 w-3.5 mr-1.5 text-red-600" aria-hidden="true" />
            Error
          </span>
        );
      case 'unverifiable':
        return (
          <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-bold bg-purple-100 text-purple-900 border border-purple-300 shadow-xs">
            <AlertTriangle className="h-3.5 w-3.5 mr-1.5 text-purple-600" aria-hidden="true" />
            Unverifiable
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-slate-100 text-slate-800 shadow-xs">
            {status}
          </span>
        );
    }
  };

  return (
    <div className={`clay-card overflow-hidden transition-all ${getCardStyle(response.status)}`}>
      {/* Top Banner: Status, Conditional Badges, Timings */}
      <div className="bg-[#fcf9f2] border-b border-[#f1e6d4] px-6 py-3.5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {renderStatusBadge(response.status)}

          {/* Conditional: "Verified numbers" ONLY if confidence === "verified" */}
          {response.confidence === 'verified' && (
            <span className="clay-pill px-2.5 py-0.5 text-[11px] font-mono font-bold bg-emerald-50 text-emerald-800 border-emerald-300">
              Verified numbers
            </span>
          )}

          {/* Conditional: "Ran on Snowflake" ONLY if query_id is set */}
          {response.query_id && (
            <span className="clay-pill px-2.5 py-0.5 text-[11px] font-mono font-bold bg-blue-50 text-blue-800 border-blue-300">
              Ran on Snowflake
            </span>
          )}

          {/* Static: "Read-only" */}
          <span className="clay-pill px-2.5 py-0.5 text-[11px] font-mono text-slate-600 bg-slate-100 border-slate-300">
            Read-only
          </span>

          {/* Conditional: Repaired after N retries badge */}
          {hasRetries && (
            <button
              type="button"
              onClick={() => setShowRetries(!showRetries)}
              className="clay-pill px-2.5 py-0.5 text-[11px] font-mono font-bold bg-amber-100 text-amber-950 border-amber-300 hover:bg-amber-200 focus:ring-2 focus:ring-amber-500 focus:outline-none transition-colors cursor-pointer"
              aria-label={`Repaired after ${retriesList.length} retries, toggle details`}
            >
              <span>Repaired after {retriesList.length} {retriesList.length === 1 ? 'retry' : 'retries'}</span>
              {showRetries ? <ChevronUp className="h-3 w-3 ml-1 inline" /> : <ChevronDown className="h-3 w-3 ml-1 inline" />}
            </button>
          )}

          {/* Model tag ONLY if returned from API */}
          {modelName && (
            <span className="text-xs font-mono text-slate-500 hidden sm:inline ml-1">
              Model: <strong className="text-blue-950">{modelName}</strong>
            </span>
          )}
        </div>

        {/* Timings breakdown (only if timings_ms returned from API) */}
        {response.timings_ms && (
          <div className="flex items-center space-x-2 text-xs font-mono text-slate-500 bg-white/80 px-3 py-1 rounded-full border border-[#f1e6d4] shadow-xs">
            <Clock className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
            {response.timings_ms.gemma !== undefined && <span>Gemma: {response.timings_ms.gemma}ms</span>}
            {response.timings_ms.snowflake !== undefined && (
              <>
                <span>•</span>
                <span className="text-blue-700 font-bold">Snowflake: {response.timings_ms.snowflake}ms</span>
              </>
            )}
            {response.timings_ms.total !== undefined && (
              <>
                <span>•</span>
                <span>Total: {response.timings_ms.total}ms</span>
              </>
            )}
          </div>
        )}
      </div>

      {/* Retries Accordion if expanded */}
      {hasRetries && showRetries && (
        <div className="p-5 bg-amber-50/70 border-b border-amber-200 text-xs font-mono space-y-3">
          <div className="font-bold text-amber-950 flex items-center">
            <AlertTriangle className="h-4 w-4 mr-1.5 text-amber-600" />
            SQL Repair Trace ({retriesList.length} failed attempt{retriesList.length > 1 ? 's' : ''}):
          </div>
          {retriesList.map((retry, idx) => (
            <div key={idx} className="bg-white p-3.5 rounded-xl border border-amber-200 space-y-1.5 shadow-xs">
              <div className="text-slate-500 font-sans text-[11px] font-bold">Attempt {idx + 1} Failed SQL:</div>
              <pre className="bg-[#0b162c] text-slate-100 p-2.5 rounded-lg text-[11px] overflow-x-auto">
                <code>{retry.sql}</code>
              </pre>
              <div className="text-rose-700 text-[11px] font-sans font-medium">
                <strong>Error:</strong> {retry.error}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Main Content Body */}
      <div className="p-6 space-y-5">
        {/* Interpreted as line ONLY when resolved_question !== typed question */}
        {isInterpretedDifferent && (
          <div className="text-xs bg-blue-50/80 border border-blue-200 text-blue-950 rounded-xl px-4 py-2.5 flex items-start space-x-2.5 shadow-xs">
            <Target className="h-4 w-4 text-blue-600 shrink-0 mt-0.5" aria-hidden="true" />
            <div>
              <span className="font-bold text-blue-900 mr-1.5">Interpreted as:</span>
              <span className="italic font-sans text-blue-950">{response.resolved_question}</span>
            </div>
          </div>
        )}

        {/* State-specific Body Content */}
        {response.status === 'clarify' && (
          <div className="space-y-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-amber-900 flex items-center">
              <HelpCircle className="h-4 w-4 mr-1.5 text-amber-600" />
              Clarification Needed
            </h3>
            <p className="text-sm font-medium text-amber-950 bg-amber-50/80 p-4 rounded-xl border border-amber-200 shadow-xs">
              {response.clarifying_question || response.answer || 'Please clarify your question.'}
            </p>
          </div>
        )}

        {response.status === 'refused' && (
          <div className="space-y-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-rose-800 flex items-center">
              <XCircle className="h-4 w-4 mr-1.5 text-rose-600" />
              Request Refused
            </h3>
            <p className="text-sm font-medium text-rose-950 bg-rose-50/80 p-4 rounded-xl border border-rose-200 shadow-xs">
              {response.refusal_reason || response.answer || 'This request cannot be fulfilled within system guardrails.'}
            </p>
          </div>
        )}

        {response.status === 'empty' && (
          <div className="space-y-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 flex items-center">
              <Info className="h-4 w-4 mr-1.5 text-slate-500" />
              Result Empty
            </h3>
            <p className="text-sm text-slate-800 bg-slate-100/70 p-4 rounded-xl border border-slate-200 shadow-xs">
              {response.answer || 'The query executed successfully on Snowflake, but no records matched the filters.'}
            </p>
          </div>
        )}

        {response.status === 'error' && (
          <div className="space-y-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-red-800 flex items-center">
              <AlertTriangle className="h-4 w-4 mr-1.5 text-red-600" />
              Error Encountered
            </h3>
            <p className="text-sm text-red-950 bg-red-50 p-4 rounded-xl border border-red-200 shadow-xs">
              {response.answer || response.detail || 'An error occurred while executing the query.'}
            </p>
          </div>
        )}

        {response.status === 'unverifiable' && (
          <div className="space-y-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-purple-800 flex items-center">
              <AlertTriangle className="h-4 w-4 mr-1.5 text-purple-600" />
              Unverifiable Response
            </h3>
            <p className="text-sm text-purple-950 bg-purple-50 p-4 rounded-xl border border-purple-200 shadow-xs">
              {response.answer || 'The returned answer could not be verified deterministically against database rows.'}
            </p>
          </div>
        )}

        {/* Standard Answer Prose (when answered) */}
        {response.status === 'answered' && response.answer && (
          <div>
            <h3 className="text-xs font-bold uppercase tracking-wider text-blue-900/60 mb-2 flex items-center font-mono">
              <Sparkles className="h-3.5 w-3.5 mr-1.5 text-blue-600" aria-hidden="true" />
              Answer (Grounded in Snowflake SQL)
            </h3>
            <p className="text-base text-slate-900 leading-relaxed font-sans font-normal">
              {response.answer}
            </p>
          </div>
        )}

        {/* Claim Hover / Focus Evidence Chips */}
        {Array.isArray(response.evidence) && response.evidence.length > 0 && (
          <div className="mt-5 pt-4 border-t border-[#f1e6d4]">
            <label className="text-xs font-bold text-blue-950 uppercase tracking-wider block mb-2 font-mono">
              Evidence Tokens (Hover or Focus to Highlight in Rows):
            </label>
            <div className="flex flex-wrap gap-2" role="group" aria-label="Evidence tokens">
              {response.evidence.map((ev, idx) => (
                <button
                  key={idx}
                  type="button"
                  tabIndex={0}
                  onMouseEnter={() => onHighlightCell && onHighlightCell({ rowIndex: ev.row_index, column: ev.column })}
                  onMouseLeave={() => onHighlightCell && onHighlightCell(null)}
                  onFocus={() => onHighlightCell && onHighlightCell({ rowIndex: ev.row_index, column: ev.column })}
                  onBlur={() => onHighlightCell && onHighlightCell(null)}
                  aria-label={`Evidence: ${ev.text} in row ${ev.row_index + 1}, column ${ev.column}`}
                  className="clay-pill px-3 py-1.5 text-xs font-mono font-bold text-blue-950 hover:border-blue-400 focus:bg-amber-100 focus:border-amber-400 focus:ring-2 focus:ring-amber-500 focus:outline-none transition-all cursor-pointer flex items-center space-x-1.5"
                >
                  <span className="font-extrabold text-blue-900">{ev.text}</span>
                  <span className="text-[10px] text-slate-500 font-sans font-normal">
                    [Row {ev.row_index + 1}: {ev.column}]
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Assumptions List if present */}
        {Array.isArray(response.assumptions) && response.assumptions.length > 0 && (
          <div className="bg-[#faf6ee] rounded-xl p-4 border border-[#eee3d1] text-xs text-slate-700 shadow-xs">
            <span className="font-bold text-blue-950 block mb-1.5 font-mono">Stated Assumptions & Scope:</span>
            <ul className="list-disc list-inside space-y-1">
              {response.assumptions.map((item, idx) => (
                <li key={idx} className="leading-relaxed">{item}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
