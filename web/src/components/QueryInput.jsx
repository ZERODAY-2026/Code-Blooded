import React from 'react';
import { Send, Sparkles, CornerDownLeft } from 'lucide-react';

export default function QueryInput({
  query,
  setQuery,
  onAsk,
  loading,
  suggestedQueries
}) {
  const handleSubmit = (e) => {
    e.preventDefault();
    if (query.trim() && !loading) {
      onAsk(query.trim());
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <div className="clay-card p-6 transition-all hover:shadow-[0_20px_36px_-6px_rgba(15,23,42,0.09)]">
      <form onSubmit={handleSubmit}>
        <div className="flex items-center justify-between mb-2.5">
          <label htmlFor="user-query-input" className="block text-xs font-extrabold uppercase tracking-wider text-slate-600 font-mono flex items-center space-x-1.5">
            <span className="h-2 w-2 rounded-full bg-sky-500"></span>
            <span>Natural Language Query Interface</span>
          </label>
          <span className="text-[11px] font-mono text-slate-400 hidden sm:flex items-center space-x-1">
            <span>Press</span>
            <kbd className="px-1.5 py-0.5 bg-slate-100 border border-slate-300 rounded text-[10px] text-slate-600 font-semibold shadow-2xs">Enter ↵</kbd>
            <span>to execute</span>
          </span>
        </div>

        <div className="relative">
          <textarea
            id="user-query-input"
            rows="2"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything about your data... (e.g. 'What are the top 5 countries by life expectancy in 2019?')"
            className="clay-input w-full p-4 pr-32 text-sm text-slate-900 placeholder:text-slate-400 focus:border-sky-500 focus:ring-2 focus:ring-sky-500/50 focus:outline-none resize-none font-sans transition-all leading-relaxed"
            disabled={loading}
            aria-label="Natural language query input"
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="absolute right-3 bottom-3.5 inline-flex items-center px-4 py-2 text-xs font-bold text-white clay-btn-primary disabled:opacity-40 disabled:cursor-not-allowed transition-all cursor-pointer focus:ring-2 focus:ring-sky-500 focus:outline-none"
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
      </form>

      {/* Suggested Prompt Chips */}
      {Array.isArray(suggestedQueries) && suggestedQueries.length > 0 && (
        <div className="mt-4 pt-3 border-t border-slate-200/60 flex items-center flex-wrap gap-2">
          <span className="text-xs font-bold text-slate-500 flex items-center mr-1">
            <Sparkles className="h-3.5 w-3.5 mr-1 text-sky-500" aria-hidden="true" />
            Suggested:
          </span>
          {suggestedQueries.map((suggested, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => {
                setQuery(suggested);
                onAsk(suggested);
              }}
              disabled={loading}
              className="clay-pill px-3.5 py-1 text-xs text-slate-700 font-semibold focus:ring-2 focus:ring-sky-500 focus:outline-none cursor-pointer"
              aria-label={`Ask suggested query: ${suggested}`}
            >
              {suggested}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
