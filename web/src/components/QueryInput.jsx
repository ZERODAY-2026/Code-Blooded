import React from 'react';
import { Send, Sparkles, AlertCircle, CheckCircle2, HelpCircle, ShieldAlert, CornerDownLeft, Search } from 'lucide-react';
import { EXAMPLE_QUESTIONS } from '../exampleQuestions';

// Categorized metadata for the 5 persona questions (Section 14 archetypes)
const QUESTION_META = [
  { label: 'Answerable', icon: CheckCircle2, color: 'text-emerald-700 bg-emerald-50 border-emerald-200' },
  { label: 'Ambiguous', icon: HelpCircle, color: 'text-amber-700 bg-amber-50 border-amber-200' },
  { label: 'Refusal', icon: ShieldAlert, color: 'text-rose-700 bg-rose-50 border-rose-200' },
  { label: 'Follow-up', icon: CornerDownLeft, color: 'text-blue-700 bg-blue-50 border-blue-200' },
  { label: 'Empty Result', icon: Search, color: 'text-purple-700 bg-purple-50 border-purple-200' },
];

export default function QueryInput({
  query,
  setQuery,
  onAsk,
  loading,
  apiHealthy
}) {
  const handleSubmit = (e) => {
    e.preventDefault();
    if (query.trim() && !loading && apiHealthy !== false) {
      onAsk(query.trim());
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  const isAskDisabled = loading || !query.trim() || apiHealthy === false;
  const disabledTooltip = apiHealthy === false
    ? "Can't reach the DataMind API. Start the backend with 'python run.py' (see README)."
    : !query.trim()
      ? "Enter a natural language question first"
      : "";

  return (
    <div className="clay-card p-6 sm:p-7 transition-all">
      <form onSubmit={handleSubmit}>
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <div className="flex items-center space-x-2.5">
            <div className="h-7 w-7 rounded-lg clay-btn-primary flex items-center justify-center text-white shrink-0 shadow-2xs">
              <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            </div>
            <div>
              <label htmlFor="user-query-input" className="block text-xs font-extrabold uppercase tracking-wider text-blue-950 font-mono">
                Natural Language Query Interface
              </label>
              <div className="flex items-center space-x-2 mt-0.5">
                {apiHealthy === false ? (
                  <span className="inline-flex items-center text-[10px] font-mono font-bold text-rose-700 bg-rose-100/80 px-2 py-0.5 rounded-full border border-rose-200">
                    <span className="h-1.5 w-1.5 rounded-full bg-rose-500 mr-1.5 animate-pulse" />
                    API Offline (Port 8000)
                  </span>
                ) : apiHealthy === true ? (
                  <span className="inline-flex items-center text-[10px] font-mono font-bold text-emerald-800 bg-emerald-100/80 px-2 py-0.5 rounded-full border border-emerald-200">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 mr-1.5" />
                    Snowflake DM_WH Connected
                  </span>
                ) : (
                  <span className="inline-flex items-center text-[10px] font-mono font-semibold text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full">
                    Checking backend...
                  </span>
                )}
                <span className="text-[11px] text-slate-400 font-mono hidden md:inline">• Grounded via sqlglot & Gemma 4</span>
              </div>
            </div>
          </div>

          <span className="text-[11px] font-mono text-slate-500 hidden sm:flex items-center space-x-1.5 bg-[#faf5ec] px-2.5 py-1 rounded-lg border border-[#ede1cd]">
            <span>Press</span>
            <kbd className="px-1.5 py-0.5 bg-white border border-[#dacbb5] rounded text-[10px] text-blue-950 font-extrabold shadow-2xs">Enter ↵</kbd>
            <span>to execute</span>
          </span>
        </div>

        <div className="relative mt-2">
          <textarea
            id="user-query-input"
            rows="3"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything about your data... (e.g. 'Which 5 nations had the longest life expectancy in 2018?')"
            className="clay-input w-full p-4 pr-36 text-sm sm:text-base font-medium text-blue-950 placeholder:text-slate-400 focus:border-blue-500 focus:ring-4 focus:ring-blue-500/15 focus:outline-none resize-none font-sans transition-all leading-relaxed"
            disabled={loading}
            aria-label="Natural language query input"
          />
          <div className="absolute right-3.5 bottom-3.5" title={disabledTooltip}>
            <button
              type="submit"
              disabled={isAskDisabled}
              className="inline-flex items-center px-4 py-2 text-xs font-extrabold text-white clay-btn-primary disabled:opacity-40 disabled:cursor-not-allowed transition-all cursor-pointer focus:ring-2 focus:ring-blue-500 focus:outline-none shadow-md"
              aria-label={loading ? 'Executing query against Snowflake' : 'Ask question'}
            >
              {loading ? (
                <>
                  <svg className="animate-spin -ml-1 mr-2 h-3.5 w-3.5 text-white" fill="none" viewBox="0 0 24 24" aria-hidden="true">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                  <span>Querying...</span>
                </>
              ) : (
                <>
                  <Send className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />
                  <span>Ask Data</span>
                </>
              )}
            </button>
          </div>
        </div>
      </form>

      {/* Persona Question Chips (Always shown, even if API is down) */}
      <div className="mt-5 pt-4 border-t border-[#ede2d2] flex items-center flex-wrap gap-2.5">
        <span className="text-xs font-bold text-blue-950 flex items-center mr-1 font-mono">
          <Sparkles className="h-3.5 w-3.5 mr-1.5 text-amber-500 shrink-0" aria-hidden="true" />
          Persona Archetypes:
        </span>
        {EXAMPLE_QUESTIONS.map((questionText, idx) => {
          const meta = QUESTION_META[idx] || { label: 'Example', color: 'text-slate-700 bg-slate-50 border-slate-200' };
          return (
            <button
              key={idx}
              type="button"
              onClick={() => setQuery(questionText)}
              disabled={loading}
              className="clay-pill px-3 py-1.5 text-xs font-semibold focus:ring-2 focus:ring-blue-500 focus:outline-none cursor-pointer flex items-center space-x-1.5"
              aria-label={`Fill question: ${questionText}`}
            >
              <span className={`text-[10px] font-mono font-extrabold px-1.5 py-0.2 rounded border ${meta.color}`}>
                {meta.label}
              </span>
              <span className="truncate max-w-[260px] sm:max-w-none">{questionText}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
