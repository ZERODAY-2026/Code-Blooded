import React, { useState } from 'react';
import {
  FileText,
  Code,
  Table,
  Hash,
  Flag,
  Cpu,
  Copy,
  Check,
  ExternalLink,
  ShieldCheck,
  Clock
} from 'lucide-react';
import EvidenceTable from './EvidenceTable';

export default function ReceiptCard({ response, highlightedCell }) {
  const [activeTab, setActiveTab] = useState('how'); // 'how' | 'sql' | 'rows' | 'query_id' | 'flags' | 'model_calls'
  const [copiedSql, setCopiedSql] = useState(false);
  const [copiedQueryId, setCopiedQueryId] = useState(false);

  if (!response) return null;

  const sql = response.sql;
  const explanation = Array.isArray(response.explanation) ? response.explanation : [];
  const flags = Array.isArray(response.flags) ? response.flags : [];
  const gemmaCalls = Array.isArray(response.gemma_calls) ? response.gemma_calls : [];
  const queryId = response.query_id;
  const snowsightUrl = response.snowsight_url;
  const modelName = response.model?.name || (typeof response.model === 'string' ? response.model : null);
  const modelProvider = response.model?.provider;

  const handleCopySql = () => {
    if (sql) {
      navigator.clipboard.writeText(sql);
      setCopiedSql(true);
      setTimeout(() => setCopiedSql(false), 2000);
    }
  };

  const handleCopyQueryId = () => {
    if (queryId) {
      navigator.clipboard.writeText(queryId);
      setCopiedQueryId(true);
      setTimeout(() => setCopiedQueryId(false), 2000);
    }
  };

  const tabs = [
    { id: 'how', label: 'How I got this', icon: FileText, count: explanation.length },
    { id: 'sql', label: 'SQL', icon: Code, available: !!sql },
    { id: 'rows', label: 'Rows', icon: Table, count: response.rows?.data?.length },
    { id: 'query_id', label: 'Query ID', icon: Hash, available: !!queryId },
    { id: 'flags', label: 'Flags', icon: Flag, count: flags.length },
    { id: 'model_calls', label: 'Model calls', icon: Cpu, count: gemmaCalls.length }
  ];

  return (
    <div className="clay-card overflow-hidden">
      {/* Header Bar */}
      <div className="bg-slate-900 text-white px-6 py-3.5 flex flex-wrap items-center justify-between gap-2 shadow-inner">
        <div className="flex items-center space-x-2">
          <div className="h-2.5 w-2.5 rounded-full bg-sky-400 animate-pulse"></div>
          <h3 className="text-xs font-bold uppercase tracking-wider font-mono">
            Execution Receipt & Audit Log
          </h3>
          <span className="text-[10px] font-mono bg-sky-950 text-sky-300 border border-sky-800 px-2 py-0.5 rounded-full shadow-xs">
            AST Verified
          </span>
        </div>

        <div className="flex items-center space-x-2 text-xs font-mono text-slate-400">
          <span>Role: DM_READER</span>
          <span>•</span>
          <span className="text-emerald-400 flex items-center">
            <ShieldCheck className="h-3.5 w-3.5 mr-1" />
            SELECT only
          </span>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="bg-slate-100/70 border-b border-slate-200/80 px-4 pt-3 flex flex-wrap gap-1.5" role="tablist" aria-label="Receipt details tabs">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              role="tab"
              aria-selected={isActive}
              aria-controls={`receipt-panel-${tab.id}`}
              id={`receipt-tab-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
              className={`inline-flex items-center space-x-1.5 px-3.5 py-2 text-xs font-medium rounded-t-xl transition-all focus:ring-2 focus:ring-sky-500 focus:outline-none cursor-pointer ${
                isActive
                  ? 'bg-white text-slate-900 font-bold shadow-xs border-t border-l border-r border-slate-200/90 -mb-px'
                  : 'text-slate-600 hover:text-slate-900 hover:bg-white/50'
              }`}
            >
              <Icon className={`h-3.5 w-3.5 ${isActive ? 'text-sky-600' : 'text-slate-400'}`} aria-hidden="true" />
              <span>{tab.label}</span>
              {tab.count !== undefined && tab.count > 0 && (
                <span className={`text-[10px] px-1.5 py-0.2 rounded-full font-mono ${
                  isActive ? 'bg-sky-100 text-sky-800' : 'bg-slate-200 text-slate-700'
                }`}>
                  {tab.count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Tab Panels */}
      <div className="p-6 font-sans">
        {/* 1. "How I got this" Panel */}
        {activeTab === 'how' && (
          <div id="receipt-panel-how" role="tabpanel" aria-labelledby="receipt-tab-how" className="space-y-3">
            <h4 className="text-xs font-bold uppercase tracking-wider text-slate-500 font-mono">
              Step-by-Step SQL Derivation (explanation[]):
            </h4>
            {explanation.length === 0 ? (
              <p className="text-xs text-slate-500 italic">No explanation steps returned by the API.</p>
            ) : (
              <ol className="list-decimal list-inside space-y-2 text-xs font-mono text-slate-800 bg-slate-50/80 p-4 rounded-xl border border-slate-200/80 leading-relaxed shadow-xs">
                {explanation.map((step, idx) => (
                  <li key={idx} className="pl-1">
                    <span className="font-sans text-slate-800">{step}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}

        {/* 2. "SQL" Panel */}
        {activeTab === 'sql' && (
          <div id="receipt-panel-sql" role="tabpanel" aria-labelledby="receipt-tab-sql" className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-500 font-mono">
                Verified SQL Query
              </span>
              {sql && (
                <button
                  type="button"
                  onClick={handleCopySql}
                  className="clay-chip px-3 py-1 text-xs text-slate-700 hover:text-slate-900 focus:ring-2 focus:ring-sky-500 focus:outline-none flex items-center space-x-1.5 cursor-pointer"
                  aria-label="Copy SQL query"
                >
                  {copiedSql ? (
                    <>
                      <Check className="h-3.5 w-3.5 text-emerald-600" aria-hidden="true" />
                      <span className="text-emerald-700 font-bold">Copied!</span>
                    </>
                  ) : (
                    <>
                      <Copy className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
                      <span>Copy SQL</span>
                    </>
                  )}
                </button>
              )}
            </div>

            {sql ? (
              <pre className="bg-slate-950 text-slate-100 p-4 rounded-xl font-mono text-xs overflow-x-auto leading-relaxed border border-slate-800 shadow-inner">
                <code>{sql}</code>
              </pre>
            ) : (
              <div className="p-4 bg-slate-50 rounded-xl border border-slate-200 text-xs text-slate-500 italic">
                No SQL query generated for this request.
              </div>
            )}
          </div>
        )}

        {/* 3. "Rows" Panel */}
        {activeTab === 'rows' && (
          <div id="receipt-panel-rows" role="tabpanel" aria-labelledby="receipt-tab-rows">
            {response.rows && response.rows.columns ? (
              <EvidenceTable rows={response.rows} highlightedCell={highlightedCell} queryId={queryId} />
            ) : (
              <div className="p-6 text-center text-xs text-slate-500 bg-slate-50 rounded-xl border border-slate-200">
                No database rows associated with this response.
              </div>
            )}
          </div>
        )}

        {/* 4. "Query ID" Panel */}
        {activeTab === 'query_id' && (
          <div id="receipt-panel-query_id" role="tabpanel" aria-labelledby="receipt-tab-query_id" className="space-y-4 font-mono text-xs">
            <div>
              <span className="text-slate-500 block text-[11px] uppercase font-bold mb-1.5">
                Snowflake Query ID (query_id)
              </span>
              {queryId ? (
                <div className="flex items-center space-x-2">
                  <span className="p-2.5 bg-slate-100/90 border border-slate-300 rounded-lg font-bold text-slate-900 select-all max-w-full truncate shadow-xs">
                    {queryId}
                  </span>
                  <button
                    type="button"
                    onClick={handleCopyQueryId}
                    className="clay-chip p-2 text-slate-600 hover:text-slate-900 focus:ring-2 focus:ring-sky-500 focus:outline-none cursor-pointer"
                    aria-label="Copy Query ID"
                    title="Copy Query ID"
                  >
                    {copiedQueryId ? <Check className="h-4 w-4 text-emerald-600" /> : <Copy className="h-4 w-4" />}
                  </button>
                </div>
              ) : (
                <span className="text-slate-400 italic">No Query ID generated.</span>
              )}
            </div>

            {snowsightUrl && (
              <div className="pt-3 border-t border-slate-100">
                <span className="text-slate-500 block text-[11px] uppercase font-bold mb-1.5">
                  Snowsight Web Console Link
                </span>
                <a
                  href={snowsightUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="clay-chip inline-flex items-center space-x-1.5 px-3 py-1.5 text-sky-700 hover:text-sky-900 font-bold focus:ring-2 focus:ring-sky-500 focus:outline-none"
                >
                  <span>Open Query in Snowflake Snowsight</span>
                  <ExternalLink className="h-3.5 w-3.5 ml-1" aria-hidden="true" />
                </a>
              </div>
            )}
          </div>
        )}

        {/* 5. "Flags" Panel */}
        {activeTab === 'flags' && (
          <div id="receipt-panel-flags" role="tabpanel" aria-labelledby="receipt-tab-flags" className="space-y-3">
            <span className="text-xs font-bold uppercase tracking-wider text-slate-500 font-mono">
              Flags & Verification Warnings (flags[])
            </span>
            {flags.length === 0 ? (
              <div className="p-4 bg-emerald-50/80 border border-emerald-200 text-emerald-900 rounded-xl text-xs flex items-center space-x-2 shadow-xs">
                <ShieldCheck className="h-4 w-4 text-emerald-600 shrink-0" aria-hidden="true" />
                <span>No flags reported. All guardrails passed cleanly.</span>
              </div>
            ) : (
              <ul className="space-y-2 text-xs font-mono">
                {flags.map((flag, idx) => (
                  <li key={idx} className="p-3.5 bg-amber-50/80 border border-amber-200 text-amber-950 rounded-xl flex items-center space-x-2 shadow-xs">
                    <Flag className="h-3.5 w-3.5 text-amber-600 shrink-0" aria-hidden="true" />
                    <span>{flag}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* 6. "Model calls" Panel */}
        {activeTab === 'model_calls' && (
          <div id="receipt-panel-model_calls" role="tabpanel" aria-labelledby="receipt-tab-model_calls" className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
              <div>
                <span className="text-xs text-slate-400 uppercase font-mono block">Model Name</span>
                <span className="text-sm font-bold text-slate-800 font-mono">
                  {modelName || 'Not specified by API'}
                </span>
                {modelProvider && (
                  <span className="text-xs text-slate-500 ml-1.5 font-mono">({modelProvider})</span>
                )}
              </div>

              {response.timings_ms && (
                <div className="text-xs font-mono text-slate-500 flex items-center space-x-2 bg-slate-50 px-3 py-1 rounded-full border border-slate-200/60 shadow-xs">
                  <Clock className="h-3.5 w-3.5 text-slate-400" />
                  <span>Total Latency: {response.timings_ms.total || 0}ms</span>
                </div>
              )}
            </div>

            <div className="space-y-2">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-500 font-mono block">
                Gemma Model Call Trace (gemma_calls[]):
              </span>

              {gemmaCalls.length === 0 ? (
                <p className="text-xs text-slate-500 italic">No model call trace provided by API.</p>
              ) : (
                <div className="overflow-x-auto border border-slate-200/80 rounded-xl shadow-xs">
                  <table className="w-full text-left text-xs font-mono border-collapse">
                    <thead>
                      <tr className="bg-slate-100/80 text-slate-600 border-b border-slate-200">
                        <th className="p-3">Call Role</th>
                        <th className="p-3">Latency (ms)</th>
                        <th className="p-3">Valid First Try?</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 text-slate-800">
                      {gemmaCalls.map((call, idx) => (
                        <tr key={idx} className="hover:bg-slate-50/80">
                          <td className="p-3 font-bold text-slate-900 capitalize">{call.role}</td>
                          <td className="p-3">{call.ms} ms</td>
                          <td className="p-3">
                            {call.valid_first_try ? (
                              <span className="clay-chip px-2.5 py-0.5 text-[11px] font-bold text-emerald-800 bg-emerald-50 border border-emerald-300">
                                Yes
                              </span>
                            ) : (
                              <span className="clay-chip px-2.5 py-0.5 text-[11px] font-bold text-amber-900 bg-amber-50 border border-amber-300">
                                No (Repaired)
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
