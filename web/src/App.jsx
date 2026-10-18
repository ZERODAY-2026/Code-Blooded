import React, { useState, useEffect } from 'react';
import Header from './components/Header';
import QueryInput from './components/QueryInput';
import AnswerCard from './components/AnswerCard';
import ReceiptCard from './components/ReceiptCard';
import EvidenceTable from './components/EvidenceTable';
import EvalView from './components/EvalView';
import { MOCK_DATASETS, SUGGESTED_QUERIES, SAMPLE_RESPONSES } from './mockData';

export default function App() {
  const [activeTab, setActiveTab] = useState('workbench'); // 'workbench' | 'eval'
  const [activeDataset, setActiveDataset] = useState('owid');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [currentResponse, setCurrentResponse] = useState(
    import.meta.env.VITE_USE_MOCKS === 'true'
      ? SAMPLE_RESPONSES["What are the top 5 countries by life expectancy in 2019?"]
      : null
  );
  const [evalSummary, setEvalSummary] = useState(null);

  // Try to load real eval summary from backend
  useEffect(() => {
    fetch('/eval-summary')
      .then(res => res.json())
      .then(data => setEvalSummary(data))
      .catch(() => {
        // Leave evalSummary null to show unverified draft notice
      });
  }, []);

  const handleAsk = async (questionToAsk) => {
    const q = questionToAsk || query;
    if (!q) return;

    setLoading(true);

    try {
      // 1. Hit the live FastAPI endpoint
      const response = await fetch('/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: q,
          dataset: activeDataset
        })
      });

      if (response.ok) {
        const data = await response.json();
        setCurrentResponse(data);
      } else {
        const errData = await response.json().catch(() => ({}));
        setCurrentResponse({
          status: "error",
          answer: errData.detail || "API returned an error.",
          sql: "",
          receipt: null,
          rows: null,
        });
      }
    } catch (err) {
      // 2. Mock fallback is opt-in only behind VITE_USE_MOCKS dev flag
      const useMock = import.meta.env.VITE_USE_MOCKS === 'true';
      if (useMock && SAMPLE_RESPONSES[q]) {
        setCurrentResponse(SAMPLE_RESPONSES[q]);
      } else {
        setCurrentResponse({
          status: "error",
          answer: "Unable to reach FastAPI backend service at /ask. Ensure the API server is running on port 8000.",
          sql: "",
          receipt: null,
          rows: null,
        });
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      <Header
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        activeDataset={activeDataset}
        setActiveDataset={setActiveDataset}
        datasets={MOCK_DATASETS}
      />

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {activeTab === 'workbench' ? (
          <div className="space-y-6">
            {/* Top Prompt Input */}
            <QueryInput
              query={query}
              setQuery={setQuery}
              onAsk={handleAsk}
              loading={loading}
              suggestedQueries={SUGGESTED_QUERIES[activeDataset] || []}
              activeDataset={activeDataset}
            />

            {/* Answer & Verification Card */}
            {currentResponse && (
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
                {/* Left: Answer prose + Receipt */}
                <div className="lg:col-span-7 space-y-6">
                  <AnswerCard response={currentResponse} />
                  <ReceiptCard sql={currentResponse.sql} receipt={currentResponse.receipt} />
                </div>

                {/* Right: Live Data Table */}
                <div className="lg:col-span-5">
                  <EvidenceTable rows={currentResponse.rows} />
                </div>
              </div>
            )}
          </div>
        ) : (
          <EvalView evalSummary={evalSummary} />
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-200 bg-white py-4 mt-auto">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col sm:flex-row items-center justify-between text-xs text-slate-500 gap-2 font-mono">
          <div>
            Team <strong className="text-slate-800">Code Blooded</strong> • Hacktoberfest Hack Day Dharwad × Vidkarya
          </div>
          <div>
            Zero Mocks in Production • Live Snowflake + Gemma 4 • MIT License
          </div>
        </div>
      </footer>
    </div>
  );
}
