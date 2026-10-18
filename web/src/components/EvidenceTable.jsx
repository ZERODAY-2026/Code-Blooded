import React from 'react';
import { Table, Download } from 'lucide-react';

export default function EvidenceTable({ rows }) {
  if (!rows || !rows.columns || rows.columns.length === 0) return null;

  const downloadCSV = () => {
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

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      <div className="px-5 py-3 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <Table className="h-4 w-4 text-slate-500" />
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-700">
            Live Query Result Cells ({rows.data.length} rows)
          </h3>
        </div>

        <button
          onClick={downloadCSV}
          className="inline-flex items-center space-x-1 text-xs text-slate-500 hover:text-slate-800 px-2 py-1 rounded hover:bg-slate-200/60 transition-colors"
          title="Export CSV"
        >
          <Download className="h-3 w-3" />
          <span>Export CSV</span>
        </button>
      </div>

      <div className="overflow-x-auto max-h-80">
        <table className="w-full text-left border-collapse text-xs">
          <thead>
            <tr className="bg-slate-100/75 border-b border-slate-200 text-slate-600 font-mono font-semibold uppercase">
              {rows.columns.map((col, idx) => (
                <th key={idx} className="p-3 whitespace-nowrap">
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 font-mono text-slate-700">
            {rows.data.map((row, rIdx) => (
              <tr key={rIdx} className="hover:bg-slate-50/80 transition-colors">
                {row.map((cell, cIdx) => (
                  <td key={cIdx} className="p-3 whitespace-nowrap">
                    {typeof cell === 'number'
                      ? cell.toLocaleString(undefined, { maximumFractionDigits: 4 })
                      : String(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
