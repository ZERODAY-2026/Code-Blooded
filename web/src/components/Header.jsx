import React from 'react';
import { Database, Sparkles, CheckCircle2, BarChart2, Search } from 'lucide-react';

export default function Header({ activeTab, setActiveTab, activeDataset, setActiveDataset, datasets }) {
  return (
    <header className="border-b border-slate-200 bg-white sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between items-center h-16">
          {/* Logo & Product Name */}
          <div className="flex items-center space-x-3">
            <div className="h-9 w-9 rounded-lg bg-sky-600 flex items-center justify-center text-white shadow-sm">
              <Database className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <span className="font-bold text-lg text-slate-900 tracking-tight">DataMind Receipts</span>
                <span className="bg-sky-50 text-sky-700 text-xs font-semibold px-2 py-0.5 rounded-full border border-sky-200">
                  PS1
                </span>
              </div>
              <p className="text-xs text-slate-500 font-mono">Talk to Your Data • Grounded in Snowflake SQL</p>
            </div>
          </div>

          {/* Navigation Tabs */}
          <div className="flex items-center space-x-1 bg-slate-100 p-1 rounded-lg border border-slate-200">
            <button
              onClick={() => setActiveTab('workbench')}
              className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                activeTab === 'workbench'
                  ? 'bg-white text-slate-900 shadow-sm border border-slate-200/50'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <Search className="h-3.5 w-3.5 text-sky-600" />
              <span>Query Workbench</span>
            </button>
            <button
              onClick={() => setActiveTab('eval')}
              className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                activeTab === 'eval'
                  ? 'bg-white text-slate-900 shadow-sm border border-slate-200/50'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <BarChart2 className="h-3.5 w-3.5 text-emerald-600" />
              <span>Eval Harness (30 Qs)</span>
            </button>
          </div>

          {/* Dataset Selector & Live Badges */}
          <div className="flex items-center space-x-4">
            <div className="flex items-center space-x-2">
              <label className="text-xs font-medium text-slate-500">Dataset:</label>
              <select
                value={activeDataset}
                onChange={(e) => setActiveDataset(e.target.value)}
                className="bg-slate-50 border border-slate-300 text-slate-800 text-xs rounded-lg px-2.5 py-1.5 font-medium focus:ring-1 focus:ring-sky-500 focus:outline-none"
              >
                {datasets.map((ds) => (
                  <option key={ds.id} value={ds.id}>
                    {ds.name} ({ds.badge})
                  </option>
                ))}
              </select>
            </div>

            {/* Model & DB Status Pills */}
            <div className="hidden md:flex items-center space-x-2">
              <span className="inline-flex items-center px-2 py-1 rounded-md text-[11px] font-mono font-medium bg-emerald-50 text-emerald-700 border border-emerald-200">
                <CheckCircle2 className="h-3 w-3 mr-1 text-emerald-600" />
                Live Snowflake
              </span>
              <span className="inline-flex items-center px-2 py-1 rounded-md text-[11px] font-mono font-medium bg-sky-50 text-sky-700 border border-sky-200">
                <Sparkles className="h-3 w-3 mr-1 text-sky-600" />
                Gemma 4-26B
              </span>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}
