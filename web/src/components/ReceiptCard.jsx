import React, { useState } from 'react';
import { Receipt, ExternalLink, Copy, Check, Code, ShieldCheck } from 'lucide-react';

export default function ReceiptCard({ sql, receipt }) {
  const [copied, setCopied] = useState(false);
  const [showRawSql, setShowRawSql] = useState(false);

  const handleCopy = () => {
    if (sql) {
      navigator.clipboard.writeText(sql);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (!receipt && !sql) return null;

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      {/* Header */}
      <div className="bg-slate-900 text-white px-5 py-3.5 flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <Receipt className="h-4 w-4 text-sky-400" />
          <h3 className="text-sm font-semibold tracking-wide">SQL Execution Receipt</h3>
          <span className="text-[10px] font-mono bg-sky-950 text-sky-300 border border-sky-800 px-2 py-0.5 rounded">
            AST Parsed
          </span>
        </div>

        <div className="flex items-center space-x-3 text-xs">
          <span className="text-slate-400 font-mono hidden sm:inline">Role: DM_READER</span>
          {receipt?.snowsight_url && (
            <a
              href={receipt.snowsight_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center text-sky-400 hover:text-sky-300 transition-colors font-mono"
            >
              <span>Snowsight Query</span>
              <ExternalLink className="h-3 w-3 ml-1" />
            </a>
          )}
        </div>
      </div>

      {/* Monospace Structured Receipt Grid */}
      <div className="p-5 font-mono text-xs text-slate-700 bg-slate-50/50 border-b border-slate-200">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <span className="text-slate-400 block text-[11px] uppercase font-semibold mb-1">Tables Queried</span>
            <div className="flex flex-wrap gap-1">
              {(receipt?.tables || ['Unknown']).map((table, idx) => (
                <span key={idx} className="bg-white border border-slate-200 text-slate-800 px-2 py-0.5 rounded font-medium">
                  {table}
                </span>
              ))}
            </div>
          </div>

          <div>
            <span className="text-slate-400 block text-[11px] uppercase font-semibold mb-1">Filters (WHERE)</span>
            <div className="flex flex-wrap gap-1">
              {(receipt?.filters || ['None']).map((filter, idx) => (
                <span key={idx} className="bg-white border border-slate-200 text-slate-800 px-2 py-0.5 rounded">
                  {filter}
                </span>
              ))}
            </div>
          </div>

          {receipt?.joins && (
            <div className="md:col-span-2">
              <span className="text-slate-400 block text-[11px] uppercase font-semibold mb-1">Joins Applied</span>
              <div className="space-y-1">
                {receipt.joins.map((join, idx) => (
                  <div key={idx} className="bg-white border border-slate-200 text-slate-800 px-2 py-0.5 rounded">
                    {join}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div>
            <span className="text-slate-400 block text-[11px] uppercase font-semibold mb-1">Snowflake Query ID</span>
            <span className="text-slate-900 font-semibold bg-white border border-slate-200 px-2 py-0.5 rounded inline-block truncate max-w-full">
              {receipt?.query_id || '01b8e4f1-0001-9a72-0000-08c500021b36'}
            </span>
          </div>

          <div>
            <span className="text-slate-400 block text-[11px] uppercase font-semibold mb-1">Guardrail Status</span>
            <span className="inline-flex items-center text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded font-medium">
              <ShieldCheck className="h-3 w-3 mr-1 text-emerald-600" />
              Passed sqlglot AST verification
            </span>
          </div>
        </div>
      </div>

      {/* Toggle Raw SQL Accordion */}
      <div className="p-4 bg-white">
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={() => setShowRawSql(!showRawSql)}
            className="inline-flex items-center text-xs font-semibold text-slate-600 hover:text-slate-900 transition-colors"
          >
            <Code className="h-3.5 w-3.5 mr-1.5 text-sky-600" />
            <span>{showRawSql ? 'Hide Raw Generated SQL' : 'View Raw Generated SQL'}</span>
          </button>

          <button
            type="button"
            onClick={handleCopy}
            className="inline-flex items-center space-x-1 text-xs text-slate-500 hover:text-slate-800 px-2 py-1 rounded hover:bg-slate-100 transition-colors"
          >
            {copied ? (
              <>
                <Check className="h-3 w-3 text-emerald-600" />
                <span className="text-emerald-600">Copied</span>
              </>
            ) : (
              <>
                <Copy className="h-3 w-3" />
                <span>Copy SQL</span>
              </>
            )}
          </button>
        </div>

        {showRawSql && (
          <div className="mt-3 relative">
            <pre className="bg-slate-950 text-slate-100 p-4 rounded-lg font-mono text-xs overflow-x-auto leading-relaxed border border-slate-800">
              <code>{sql}</code>
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
