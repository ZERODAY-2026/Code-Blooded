import React, { useState, useEffect } from 'react';
import Header from './components/Header';
import QueryInput from './components/QueryInput';
import AnswerCard from './components/AnswerCard';
import ReceiptCard from './components/ReceiptCard';
import EvidenceTable from './components/EvidenceTable';
import EvalView from './components/EvalView';
import { SUGGESTED_QUERIES, SAMPLE_RESPONSES } from './mockData';

export default function App() {
  const [activeTab, setActiveTab] = useState('workbench'); // 'workbench' | 'eval'
  const [datasets, setDatasets] = useState([]);
  const [activeDataset, setActiveDataset] = useState('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId] = useState(() => 'sess_' + Math.random().toString(36).substring(2, 10));
  const [highlightedCell, setHighlightedCell] = useState(null); // { rowIndex, column }
  const [lastSubmittedQuestion, setLastSubmittedQuestion] = useState('');

  // Initial state shows only the question box and chips; NO answer card until a question is submitted.
  const [currentResponse, setCurrentResponse] = useState(null);
  const [evalSummary, setEvalSummary] = useState(null);

  // 1. Fetch datasets from GET /datasets. If it fails, leave empty/disabled.
  useEffect(() => {
    fetch('/datasets')
      .then(res => {
        if (!res.ok) throw new Error('GET /datasets failed');
        return res.json();
      })
      .then(data => {
        const list = Array.isArray(data) ? data : (data.datasets || []);
        setDatasets(list);
        if (list.length > 0) {
          const firstId = list[0].id || list[0].dataset_id || list[0].name;
          if (firstId) setActiveDataset(firstId);
        }
      })
      .catch(() => {
        // Empty/disabled if GET /datasets fails
        setDatasets([]);
      });
  }, []);

  // 2. Fetch evaluation summary from GET /eval-summary
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

  // 3. Handle Dataset Switch (POST /switch-dataset)
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
      // Handled gracefully in offline mode
    }
  };

  // 4. Handle Ask Question (POST /ask)
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
          answer: errData.detail || `Server returned status ${response.status}: ${response.statusText}`,
          sql: null,
          evidence: [],
          explanation: [],
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
        setCurrentResponse({
          status: 'error',
          answer: 'Unable to reach backend service at POST /ask. Ensure FastAPI server is running on port 8000.',
          sql: null,
          evidence: [],
          explanation: ['Connection refused on localhost:8000'],
          rows: null,
          retries: [],
          flags: ['Backend Offline'],
          confidence: null,
          model: null,
          timings_ms: null
        });
      }
    } finally {
      setLoading(false);
    }
  };

  const currentSuggested = import.meta.env.VITE_USE_MOCKS === 'true'
    ? (SUGGESTED_QUERIES[activeDataset] || SUGGESTED_QUERIES.owid || [])
    : [];

  const hasRows = currentResponse?.rows?.columns && currentResponse.rows.columns.length > 0;

  return (
    <div className="min-h-screen flex flex-col font-sans">
      {/* Persistent Amber Banner: ONLY when VITE_USE_MOCKS=true */}
      {import.meta.env.VITE_USE_MOCKS === 'true' && (
        <div className="bg-amber-400 text-amber-950 font-bold text-xs py-1.5 px-4 text-center font-mono tracking-wide border-b border-amber-500 shadow-2xs">
          MOCK DATA — not from Snowflake or Gemma
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
              suggestedQueries={currentSuggested}
            />

            {/* Split Screen Workbench: Only rendered AFTER a question is submitted */}
            {currentResponse && (
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
            )}
          </div>
        ) : (
          <EvalView evalSummary={evalSummary} />
        )}
      </main>

      {/* Footer: Clean team attribution without hardcoded claims */}
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
