import React from 'react';
import { Table, Download, Info } from 'lucide-react';

export default function EvidenceTable({ rows, highlightedCell, queryId }) {
  if (!rows || !rows.columns || rows.columns.length === 0) return null;

  const rowCount = rows.data ? rows.data.length : 0;
  const isLive = Boolean(queryId);

  const downloadCSV = () => {
    if (!rows.data || rows.data.length === 0) return;
    const headers = rows.columns.join(',');
    const csvRows = rows.data.map(row =>
      row.map(val => (typeof val === 'string' && val.includes(',') ? `"${val}"` : val)).join(',')
    );
    const content = [headers, ...csvRows].join('\n');
    const blob = new Blob([content], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `snowflake_evidence_${Date.now()}.csv`;
    a.click();
    window.URL.revokeObjectURL(url);
  };

  const isCellHighlighted = (rIdx, cIdx) => {
    if (!highlightedCell) return false;
    const isRowMatch = highlightedCell.rowIndex === rIdx;
    if (!isRowMatch) return false;

    const colName = rows.columns[cIdx];
    const targetCol = highlightedCell.column;

    if (typeof targetCol === 'number') {
      return targetCol === cIdx;
    }
    if (typeof targetCol === 'string' && colName) {
      return targetCol.toLowerCase() === colName.toLowerCase();
    }
    return false;
  };

  return (
    <div className="clay-card overflow-hidden">
      <div className="px-6 py-3.5 bg-slate-50/80 border-b border-slate-200/80 flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <Table className="h-4 w-4 text-slate-500" aria-hidden="true" />
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-700 font-mono">
            {isLive ? 'Live result rows' : 'Result rows'} ({rowCount} {rowCount === 1 ? 'row' : 'rows'})
          </h3>
        </div>

        {rowCount > 0 && (
          <button
            onClick={downloadCSV}
            className="clay-pill px-3 py-1 text-xs text-blue-950 font-bold transition-all focus:ring-2 focus:ring-blue-500 focus:outline-none flex items-center space-x-1.5 cursor-pointer"
            aria-label="Export query result as CSV"
          >
            <Download className="h-3 w-3" aria-hidden="true" />
            <span>Export CSV</span>
          </button>
        )}
      </div>

      {rowCount === 0 ? (
        <div className="p-8 text-center text-xs text-slate-500 flex items-center justify-center space-x-1.5">
          <Info className="h-4 w-4 text-slate-400" aria-hidden="true" />
          <span>Zero rows returned by this query on Snowflake.</span>
        </div>
      ) : (
        <div className="overflow-x-auto max-h-80 focus:ring-1 focus:ring-sky-500" tabIndex={0} aria-label="Query results data table">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-slate-100/80 border-b border-slate-200 text-slate-600 font-mono font-bold uppercase">
                <th className="p-3 w-10 text-center text-slate-400 font-normal">#</th>
                {rows.columns.map((col, idx) => (
                  <th key={idx} scope="col" className="p-3 whitespace-nowrap">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 font-mono text-slate-700">
              {rows.data.map((row, rIdx) => (
                <tr key={rIdx} className="hover:bg-slate-50/80 transition-colors">
                  <td className="p-3 text-center text-slate-400 text-[11px] select-none">
                    {rIdx + 1}
                  </td>
                  {row.map((cell, cIdx) => {
                    const highlighted = isCellHighlighted(rIdx, cIdx);
                    return (
                      <td
                        key={cIdx}
                        className={`p-3 whitespace-nowrap transition-all ${
                          highlighted
                            ? 'bg-amber-200 text-amber-950 font-bold ring-2 ring-amber-500 ring-inset shadow-xs scale-[1.02]'
                            : ''
                        }`}
                      >
                        {typeof cell === 'number'
                          ? cell.toLocaleString(undefined, { maximumFractionDigits: 4 })
                          : String(cell ?? '')}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
