import React from 'react';
import { Send, Sparkles, HelpCircle } from 'lucide-react';

export default function QueryInput({
  query,
  setQuery,
  onAsk,
  loading,
  suggestedQueries,
  activeDataset
}) {
  const handleSubmit = (e) => {
    e.preventDefault();
    if (query.trim() && !loading) {
      onAsk(query.trim());
    }
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
      <form onSubmit={handleSubmit}>
        <div className="relative">
          <textarea
            rows="2"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask anything about your data... (e.g. 'What are the top 5 countries by life expectancy in 2019?')"
            className="w-full rounded-lg border border-slate-300 p-3.5 pr-28 text-sm text-slate-900 placeholder:text-slate-400 focus:border-sky-500 focus:ring-1 focus:ring-sky-500 focus:outline-none resize-none font-sans"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="absolute right-2.5 bottom-3.5 inline-flex items-center px-4 py-2 rounded-lg text-xs font-semibold text-white bg-sky-600 hover:bg-sky-700 disabled:bg-slate-300 disabled:cursor-not-allowed transition-all shadow-sm"
          >
            {loading ? (
              <>
                <svg className="animate-spin -ml-1 mr-2 h-3.5 w-3.5 text-white" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                Querying...
              </>
            ) : (
              <>
                <Send className="h-3.5 w-3.5 mr-1.5" />
                Ask Data
              </>
            )}
          </button>
        </div>
      </form>

      {/* Suggested Prompt Chips */}
      <div className="mt-3.5 pt-3 border-t border-slate-100 flex items-center flex-wrap gap-2">
        <span className="text-xs font-medium text-slate-400 flex items-center mr-1">
          <Sparkles className="h-3 w-3 mr-1 text-sky-500" />
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
            className="text-xs bg-slate-50 hover:bg-sky-50 text-slate-600 hover:text-sky-700 border border-slate-200 hover:border-sky-200 px-2.5 py-1 rounded-md transition-colors text-left"
          >
            {suggested}
          </button>
        ))}
      </div>
    </div>
  );
}
