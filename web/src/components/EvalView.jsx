import React from 'react';
import { BarChart2, Award, Zap, Shield, Target, ExternalLink } from 'lucide-react';

export default function EvalView({ evalSummary }) {
  if (!evalSummary) {
    return (
      <div className="clay-card p-8 text-center space-y-4">
        <div className="h-12 w-12 rounded-2xl bg-amber-100 text-amber-700 flex items-center justify-center mx-auto shadow-xs">
          <Award className="h-6 w-6" aria-hidden="true" />
        </div>
        <h2 className="text-lg font-bold text-slate-900">
          No verified evaluation results yet
        </h2>
        <p className="text-sm text-slate-600 max-w-xl mx-auto leading-relaxed">
          Benchmark results will be displayed here once ground-truth verification is run against the live API. All rows count toward accuracy only once verified.
        </p>
        <div className="pt-2">
          <a
            href="https://github.com/ZERODAY-2026/Code-Blooded#13-evaluation-benchmark--correctness-cap"
            target="_blank"
            rel="noreferrer"
            className="clay-pill inline-flex items-center space-x-1.5 px-4 py-2 text-xs font-bold text-sky-700 hover:text-sky-900"
          >
            <span>View evaluation methodology in README</span>
            <ExternalLink className="h-3.5 w-3.5 ml-1" aria-hidden="true" />
          </a>
        </div>
      </div>
    );
  }

  const summary = evalSummary;
  const categories = Object.keys(summary.category_accuracy || {});
  const heldOutCorrect = summary.held_out_correct ?? summary.held_out?.passed ?? 'N/A';
  const heldOutTotal = summary.held_out_total ?? summary.held_out?.total ?? 'N/A';
  const runDate = summary.date || summary.timestamp || 'N/A';
  const evalModel = summary.model || summary.model_name || 'N/A';

  return (
    <div className="space-y-6">
      {/* Top Banner with Verified Held-Out Badge */}
      <div className="clay-card p-6 bg-gradient-to-r from-slate-900 to-sky-950 text-white flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center space-x-2">
            <Award className="h-6 w-6 text-amber-400" aria-hidden="true" />
            <h2 className="text-lg font-bold tracking-tight">30 benchmark questions</h2>
          </div>
          <div className="mt-2 inline-flex items-center px-3 py-1 rounded-full text-xs font-mono font-bold bg-white/10 border border-white/20 text-sky-200 shadow-2xs">
            Held-out eval: {heldOutCorrect} of {heldOutTotal} correct, run on {runDate} with {evalModel}
          </div>
        </div>

        {summary.overall_accuracy !== undefined && (
          <div className="bg-white/10 px-5 py-3 rounded-xl backdrop-blur-sm border border-white/10 text-right">
            <span className="text-[11px] text-sky-200 uppercase font-mono block">Overall Accuracy</span>
            <span className="text-3xl font-extrabold text-white">{summary.overall_accuracy}%</span>
          </div>
        )}
      </div>

      {/* Gemma Metrics Fields (if present from API) */}
      {summary.gemma_metrics && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {summary.gemma_metrics.first_try_valid_json_pct !== undefined && (
            <div className="clay-card p-5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-slate-500 uppercase tracking-wider font-mono">First-Try Valid JSON</span>
                <Zap className="h-4 w-4 text-amber-500" />
              </div>
              <div className="mt-2">
                <span className="text-2xl font-extrabold text-slate-900">{summary.gemma_metrics.first_try_valid_json_pct}%</span>
              </div>
            </div>
          )}

          {summary.gemma_metrics.valid_sql_pct !== undefined && (
            <div className="clay-card p-5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-slate-500 uppercase tracking-wider font-mono">Valid SQL Generation</span>
                <Shield className="h-4 w-4 text-sky-500" />
              </div>
              <div className="mt-2">
                <span className="text-2xl font-extrabold text-slate-900">{summary.gemma_metrics.valid_sql_pct}%</span>
              </div>
            </div>
          )}

          {summary.gemma_metrics.repaired_after_retry_pct !== undefined && (
            <div className="clay-card p-5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-slate-500 uppercase tracking-wider font-mono">Self-Repair Triggered</span>
                <Target className="h-4 w-4 text-indigo-500" />
              </div>
              <div className="mt-2">
                <span className="text-2xl font-extrabold text-slate-900">{summary.gemma_metrics.repaired_after_retry_pct}%</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Per-Category Accuracy Breakdown (never computed in UI) */}
      {categories.length > 0 && (
        <div className="clay-card p-6">
          <h3 className="text-sm font-bold text-slate-900 mb-4 flex items-center font-mono">
            <BarChart2 className="h-4 w-4 mr-2 text-sky-600" />
            Accuracy by Question Category
          </h3>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {categories.map((cat) => {
              const acc = summary.category_accuracy[cat];
              return (
                <div key={cat} className="p-4 rounded-xl bg-slate-50/80 border border-slate-200/80 shadow-2xs">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-mono text-xs font-bold text-slate-800 uppercase">
                      {cat.replace('_', ' ')}
                    </span>
                    <span className="text-xs font-bold text-emerald-800 bg-emerald-100 px-2 py-0.5 rounded-full">
                      {acc}%
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
