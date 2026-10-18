import React, { useState, useEffect } from 'react';
import { AlertCircle, AlertTriangle } from 'lucide-react';
import Header from './components/Header';
import QueryInput from './components/QueryInput';
import AnswerCard from './components/AnswerCard';
import ReceiptCard from './components/ReceiptCard';
import EvidenceTable from './components/EvidenceTable';
import EvalView from './components/EvalView';
import SystemOverview from './components/SystemOverview';
import { SAMPLE_RESPONSES } from './mockData';

export default function App() {
  const [activeTab, setActiveTab] = useState('workbench'); // 'workbench' | 'eval'
  const [datasets, setDatasets] = useState([]);
  const [activeDataset, setActiveDataset] = useState('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId] = useState(() => 'sess_' + Math.random().toString(36).substring(2, 10));
  const [highlightedCell, setHighlightedCell] = useState(null); // { rowIndex, column }
  const [lastSubmittedQuestion, setLastSubmittedQuestion] = useState('');
  const [apiHealthy, setApiHealthy] = useState(null); // null = checking, true = healthy, false = offline

  // Initial state shows only the question box and chips; NO answer card until a question is submitted.
  const [currentResponse, setCurrentResponse] = useState(null);
  const [evalSummary, setEvalSummary] = useState(null);

  // 1. Check API health on load (GET /health)
  useEffect(() => {
    fetch('/health')
      .then(res => {
        if (!res.ok) throw new Error('Health check failed');
        return res.json();
      })
      .then(() => setApiHealthy(true))
      .catch(() => setApiHealthy(false));
  }, []);

  // 2. Fetch datasets from GET /datasets. If it fails, provide schema preview.
  useEffect(() => {
    fetch('/datasets')
      .then(res => {
        if (!res.ok) throw new Error('GET /datasets failed');
        return res.json();
      })
      .then(data => {
        const list = Array.isArray(data) ? data : (data.datasets || []);
        if (list.length > 0) {
          setDatasets(list);
          const firstId = list[0].id || list[0].dataset_id || list[0].name;
          if (firstId) setActiveDataset(firstId);
        } else {
          throw new Error('Empty dataset list');
        }
      })
      .catch(() => {
        // Fallback schema preview options when backend is offline
        const previewDatasets = [
          { id: 'owid', name: 'OWID Life Expectancy', badge: 'Preview' },
          { id: 'tpch', name: 'TPC-H SF1 Benchmark', badge: 'Preview' }
        ];
        setDatasets(previewDatasets);
        if (!activeDataset) setActiveDataset('owid');
      });
  }, []);

  // 3. Fetch evaluation summary from GET /eval-summary
  useEffect(() => {
    fetch('/eval-summary')
      .then(res => {
        if (!res.ok) throw new Error('GET /eval-summary failed');
        return res.json();
      })
      .then(data => setEvalSummary(data))
      .catch(() => {
        setEvalSummary(null);
      });
  }, []);

  // 4. Handle Dataset Switch (POST /switch-dataset)
  const handleDatasetChange = async (newDatasetId) => {
    setActiveDataset(newDatasetId);
    try {
      await fetch('/switch-dataset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dataset: newDatasetId,
          dataset_id: newDatasetId
        })
      });
    } catch {
      // Graceful offline handling
    }
  };

  // 5. Handle Ask Question (POST /ask)
  const handleAsk = async (questionToAsk) => {
    const q = questionToAsk || query;
    if (!q) return;

    setLoading(true);
    setHighlightedCell(null);
    setLastSubmittedQuestion(q);

    try {
      const response = await fetch('/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: q,
          session_id: sessionId,
          dataset: activeDataset
        })
      });

      if (response.ok) {
        const data = await response.json();
        setCurrentResponse(data);
      } else {
        const errData = await response.json().catch(() => ({}));
        setCurrentResponse({
          status: 'error',
          answer: errData.detail || `Server returned error (${response.status}): ${response.statusText}`,
          sql: null,
          evidence: [],
          explanation: [`Request failed with status ${response.status}`],
          rows: null,
          retries: [],
          flags: [`HTTP ${response.status}`],
          confidence: null,
          model: null,
          timings_ms: null
        });
      }
    } catch {
      // Mock fallback ONLY if VITE_USE_MOCKS dev flag is true
      const useMock = import.meta.env.VITE_USE_MOCKS === 'true';
      if (useMock && SAMPLE_RESPONSES[q]) {
        setCurrentResponse(SAMPLE_RESPONSES[q]);
      } else {
        // Plain error state card — never a blank screen
        setCurrentResponse({
          status: 'error',
          answer: "Unable to reach the DataMind API service at POST /ask. Start the backend server on port 8000 (see README).",
          sql: null,
          evidence: [],
          explanation: ["Connection failed on http://localhost:8000/ask"],
          rows: null,
          retries: [],
          flags: ["Backend Connection Error"],
          confidence: null,
          model: null,
          timings_ms: null
        });
      }
    } finally {
      setLoading(false);
    }
  };

  const hasRows = currentResponse?.rows?.columns && currentResponse.rows.columns.length > 0;

  return (
    <div className="min-h-screen flex flex-col font-sans">
      {/* Persistent Amber Banner: ONLY when VITE_USE_MOCKS=true */}
      {import.meta.env.VITE_USE_MOCKS === 'true' && (
        <div className="bg-amber-400 text-amber-950 font-bold text-xs py-1.5 px-4 text-center font-mono tracking-wide border-b border-amber-500 shadow-2xs">
          MOCK DATA — not from Snowflake or Gemma
        </div>
      )}

      {/* Persistent Offline Notice: When GET /health fails */}
      {apiHealthy === false && (
        <div className="bg-rose-50 border-b border-rose-200 px-4 py-2 text-center text-xs text-rose-900 font-medium flex items-center justify-center space-x-2 shadow-2xs font-mono">
          <AlertCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
          <span><strong>API Offline:</strong> Can't reach the DataMind API at <code>http://localhost:8000/health</code>. Start the backend with <code>python run.py</code> (see README).</span>
        </div>
      )}

      {/* Header */}
      <Header
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        activeDataset={activeDataset}
        onDatasetChange={handleDatasetChange}
        datasets={datasets}
      />

      {/* Main Content */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
        {activeTab === 'workbench' ? (
          <div className="space-y-6">
            {/* Top Prompt Command Input */}
            <QueryInput
              query={query}
              setQuery={setQuery}
              onAsk={handleAsk}
              loading={loading}
              apiHealthy={apiHealthy}
              showArchetypes={Boolean(currentResponse)}
            />

            {/* Split Screen Workbench: Rendered AFTER a question is submitted, otherwise show SystemOverview */}
            {currentResponse ? (
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
                {/* Left Column: Grounded Answer Card + 6-Tab Execution Receipt */}
                <div className={hasRows ? "lg:col-span-7 space-y-6" : "lg:col-span-12 space-y-6"}>
                  <AnswerCard
                    response={currentResponse}
                    onHighlightCell={setHighlightedCell}
                    typedQuestion={lastSubmittedQuestion}
                  />

                  <ReceiptCard
                    response={currentResponse}
                    highlightedCell={highlightedCell}
                  />
                </div>

                {/* Right Column: Sticky Live Snowflake Rows Evidence Table */}
                {hasRows && (
                  <div className="lg:col-span-5 lg:sticky lg:top-24">
                    <EvidenceTable
                      rows={currentResponse.rows}
                      highlightedCell={highlightedCell}
                      queryId={currentResponse.query_id}
                    />
                  </div>
                )}
              </div>
            ) : (
              <SystemOverview
                onSelectQuestion={(selectedQuery) => {
                  setQuery(selectedQuery);
                  const el = document.getElementById('user-query-input');
                  if (el) {
                    el.focus();
                    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                  }
                }}
              />
            )}
          </div>
        ) : (
          <EvalView evalSummary={evalSummary} />
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-200/80 bg-white/70 backdrop-blur-sm py-4 mt-auto">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col sm:flex-row items-center justify-between text-xs text-slate-500 gap-2 font-mono">
          <div>
            Team <strong className="text-slate-800">Code Blooded</strong> • Hacktoberfest Hack Day Dharwad × Vidkarya
          </div>
          <div>
            MIT License
          </div>
        </div>
      </footer>
    </div>
  );
}
