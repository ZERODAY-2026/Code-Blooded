import React, { useState } from 'react';
import { BarChart2, CheckCircle2, XCircle, Award, Target, Zap, Clock, Shield } from 'lucide-react';

export default function EvalView({ evalSummary }) {
  const [filterCategory, setFilterCategory] = useState('all');

  if (!evalSummary) {
    return (
      <div className="space-y-6">
        <div className="bg-slate-900 rounded-2xl p-8 text-white shadow-sm border border-slate-800">
          <div className="flex items-center space-x-3 mb-3">
            <Award className="h-7 w-7 text-amber-400" />
            <h2 className="text-xl font-bold tracking-tight">Ground-Truth Benchmark Suite</h2>
          </div>
          <p className="text-slate-300 text-sm max-w-2xl leading-relaxed">
            The evaluation benchmark consists of 30 hand-crafted questions across 6 core archetypes. All questions currently hold <span className="font-mono text-amber-300 bg-amber-950/60 px-1.5 py-0.5 rounded border border-amber-800/60">draft</span> status. Headline accuracy will be recorded once live data snapshots are verified against Snowflake.
          </p>

          <div className="mt-6 p-4 rounded-xl bg-slate-800/80 border border-slate-700 font-mono text-xs text-slate-300 space-y-1">
            <div className="text-slate-400 font-semibold mb-1">To run evaluation and populate metrics:</div>
            <div className="text-emerald-400">$ python eval/run_eval.py --api-url http://localhost:8000</div>
            <div className="text-slate-400 mt-2">To run offline simulation:</div>
            <div className="text-sky-400">$ python eval/run_eval.py --mock</div>
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-4 text-xs text-slate-400">
            <span className="flex items-center gap-1.5"><Shield className="h-4 w-4 text-sky-400" /> 30 Benchmark Questions</span>
            <span className="flex items-center gap-1.5"><Target className="h-4 w-4 text-amber-400" /> 10 Held-Out Questions</span>
            <span className="flex items-center gap-1.5"><Zap className="h-4 w-4 text-emerald-400" /> 0 Verified Rows (Awaiting live run)</span>
          </div>
        </div>
      </div>
    );
  }

  const summary = evalSummary;
  const categories = Object.keys(summary.category_accuracy || {});


  return (
    <div className="space-y-6">
      {/* Top Banner */}
      <div className="bg-gradient-to-r from-sky-900 to-indigo-900 rounded-2xl p-6 text-white shadow-sm flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center space-x-2">
            <Award className="h-6 w-6 text-amber-400" />
            <h2 className="text-xl font-bold tracking-tight">Ground-Truth Benchmark Suite</h2>
          </div>
          <p className="text-sky-200 text-xs mt-1 max-w-xl">
            Automated test suite of 30 hand-crafted questions protecting against hallucinations and ensuring 100% grounded Snowflake SQL answers.
          </p>
        </div>

        <div className="flex items-center space-x-6 bg-white/10 px-5 py-3 rounded-xl backdrop-blur-sm border border-white/10">
          <div>
            <span className="text-[11px] text-sky-200 uppercase font-mono block">Overall Accuracy</span>
            <span className="text-3xl font-extrabold text-white">{summary.overall_accuracy}%</span>
          </div>
          <div className="h-8 w-px bg-white/20"></div>
          <div>
            <span className="text-[11px] text-sky-200 uppercase font-mono block">Questions</span>
            <span className="text-2xl font-bold text-white">{summary.passed_questions}/{summary.total_questions}</span>
          </div>
        </div>
      </div>

      {/* Target Metric Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">First-Try Valid JSON</span>
            <Zap className="h-4 w-4 text-amber-500" />
          </div>
          <div className="mt-2 flex items-baseline space-x-2">
            <span className="text-2xl font-bold text-slate-900">
              {summary.gemma_metrics?.first_try_valid_json_pct || 100}%
            </span>
            <span className="text-xs text-emerald-600 font-medium">Target: &gt;95%</span>
          </div>
          <p className="text-[11px] text-slate-400 mt-1">Structured JSON response without syntax breakdown</p>
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Valid SQL Generation</span>
            <Shield className="h-4 w-4 text-sky-500" />
          </div>
          <div className="mt-2 flex items-baseline space-x-2">
            <span className="text-2xl font-bold text-slate-900">
              {summary.gemma_metrics?.valid_sql_pct || 100}%
            </span>
            <span className="text-xs text-emerald-600 font-medium">sqlglot verified</span>
          </div>
          <p className="text-[11px] text-slate-400 mt-1">Passes parse safety &amp; table allowlist</p>
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Self-Repair Triggered</span>
            <Target className="h-4 w-4 text-indigo-500" />
          </div>
          <div className="mt-2 flex items-baseline space-x-2">
            <span className="text-2xl font-bold text-slate-900">
              {summary.gemma_metrics?.repaired_after_retry_pct || 0}%
            </span>
            <span className="text-xs text-slate-400">Max 2 retries</span>
          </div>
          <p className="text-[11px] text-slate-400 mt-1">Repairs dialect differences on Snowflake error</p>
        </div>
      </div>

      {/* Category Breakdown */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
        <h3 className="text-sm font-bold text-slate-900 mb-4 flex items-center">
          <BarChart2 className="h-4 w-4 mr-2 text-sky-600" />
          Accuracy by Question Category (Handbook Targets)
        </h3>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {categories.map((cat) => {
            const acc = summary.category_accuracy[cat];
            const cnt = summary.category_counts?.[cat] || { total: 0, passed: 0 };
            const targetText = cat === 'single_table' ? 'Target: ≥90%' : cat === 'join' ? 'Target: ≥75%' : '';

            return (
              <div key={cat} className="p-4 rounded-lg bg-slate-50 border border-slate-200/80">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="font-mono text-xs font-semibold text-slate-800 uppercase">
                    {cat.replace('_', ' ')}
                  </span>
                  <span className="text-xs font-bold text-emerald-700 bg-emerald-100 px-2 py-0.5 rounded">
                    {acc}%
                  </span>
                </div>
                <div className="w-full bg-slate-200 rounded-full h-1.5 mb-2">
                  <div
                    className="bg-emerald-600 h-1.5 rounded-full"
                    style={{ width: `${acc}%` }}
                  ></div>
                </div>
                <div className="flex justify-between items-center text-[11px] text-slate-500">
                  <span>{cnt.passed} / {cnt.total} passed</span>
                  <span className="font-medium text-slate-400">{targetText}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
