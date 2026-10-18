import React from 'react';
import { Database, Search, BarChart2, Layers } from 'lucide-react';

export default function Header({
  activeTab,
  setActiveTab,
  activeDataset,
  onDatasetChange,
  datasets
}) {
  return (
    <header className="sticky top-0 z-50 backdrop-blur-md bg-[#fffdfa]/85 border-b border-[#f3e9da]/80 shadow-[0_4px_20px_-4px_rgba(30,58,138,0.06)]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between items-center h-16">
          {/* Left: Brand Identity */}
          <div className="flex items-center space-x-3">
            <div className="h-10 w-10 rounded-xl clay-btn-primary flex items-center justify-center text-white shadow-sm" aria-hidden="true">
              <Database className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <span className="font-extrabold text-lg text-blue-950 tracking-tight">DataMind Receipts</span>
                <span className="bg-amber-100 text-amber-900 text-[11px] font-bold px-2 py-0.5 rounded-full border border-amber-300 shadow-2xs">
                  PS1
                </span>
              </div>
              <p className="text-[11px] text-slate-500 font-mono hidden sm:block">Talk to Your Data • Grounded in Snowflake SQL</p>
            </div>
          </div>

          {/* Center: Clean Segmented View Toggle */}
          <nav className="flex items-center space-x-1 bg-[#f4ecdf]/70 p-1 rounded-xl border border-[#e6d8c3]/60 shadow-inner" aria-label="Main Navigation">
            <button
              type="button"
              onClick={() => setActiveTab('workbench')}
              className={`flex items-center space-x-1.5 px-4 py-1.5 rounded-lg text-xs font-bold transition-all focus:ring-2 focus:ring-blue-500 focus:outline-none cursor-pointer ${
                activeTab === 'workbench'
                  ? 'clay-tab-active border border-[#f3e9da]'
                  : 'text-slate-600 hover:text-blue-950 hover:bg-white/40'
              }`}
            >
              <Search className="h-3.5 w-3.5 text-blue-600" aria-hidden="true" />
              <span>Query Workbench</span>
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('eval')}
              className={`flex items-center space-x-1.5 px-4 py-1.5 rounded-lg text-xs font-bold transition-all focus:ring-2 focus:ring-blue-500 focus:outline-none cursor-pointer ${
                activeTab === 'eval'
                  ? 'clay-tab-active border border-[#f3e9da]'
                  : 'text-slate-600 hover:text-blue-950 hover:bg-white/40'
              }`}
            >
              <BarChart2 className="h-3.5 w-3.5 text-emerald-600" aria-hidden="true" />
              <span>Eval Harness</span>
            </button>
          </nav>

          {/* Right: Active Dataset Selector */}
          <div className="flex items-center space-x-2">
            <div className="hidden sm:flex items-center text-xs font-semibold text-slate-500 space-x-1 font-mono">
              <Layers className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
              <span>Schema:</span>
            </div>
            <select
              id="dataset-select"
              value={activeDataset}
              onChange={(e) => onDatasetChange(e.target.value)}
              disabled={!datasets || datasets.length === 0}
              className="clay-input text-blue-950 text-xs px-3 py-1.5 font-bold focus:ring-2 focus:ring-blue-500 focus:outline-none transition-all disabled:opacity-50 cursor-pointer shadow-xs"
              aria-label="Active Snowflake Dataset"
            >
              {datasets && datasets.length > 0 ? (
                datasets.map((ds) => {
                  const id = ds.id || ds.dataset_id || ds.name;
                  const label = ds.name || ds.id || ds.dataset_id;
                  const badge = ds.badge ? ` (${ds.badge})` : '';
                  return (
                    <option key={id} value={id}>
                      {label}{badge}
                    </option>
                  );
                })
              ) : (
                <option value="">No datasets available</option>
              )}
            </select>
          </div>
        </div>
      </div>
    </header>
  );
}
