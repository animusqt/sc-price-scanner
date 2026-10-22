// components/ScanImport.tsx
import { useState, DragEvent, ChangeEvent } from 'react';
import { Upload, Check, X, FileText, Loader2 } from 'lucide-react';

interface ScanData {
  timestamp: number;
  station: string;
  commodities: Array<{
    commodity: string;
    buy: number | null;
    sell: number | null;
    stock: number | null;
    demand: number | null;
  }>;
  source: string;
  version: string;
}

interface ScanImportProps {
  onImport: (scan: ScanData) => Promise<void>;
}

function EditableNumber({
  value,
  onChange,
  className,
}: {
  value: number | null;
  onChange: (v: number | null) => void;
  className?: string;
}) {
  return (
    <input
      type="number"
      inputMode="numeric"
      min={0}
      value={value ?? ''}
      placeholder="—"
      onChange={(e) => {
        const raw = e.target.value;
        if (raw === '') return onChange(null);
        const n = Math.floor(Number(raw));
        onChange(Number.isFinite(n) && n >= 0 ? n : null);
      }}
      className={`w-24 text-right bg-transparent border border-transparent rounded px-1 py-0.5 outline-none hover:border-border focus:border-primary ${className ?? ''}`}
    />
  );
}

export function ScanImport({ onImport }: ScanImportProps) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ScanData | null>(null);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);

  const validateScan = (data: any): data is ScanData => {
    return (
      data &&
      typeof data.timestamp === 'number' &&
      typeof data.station === 'string' &&
      Array.isArray(data.commodities) &&
      data.commodities.every((c: any) =>
        typeof c.commodity === 'string' &&
        (c.buy === null || typeof c.buy === 'number') &&
        (c.sell === null || typeof c.sell === 'number')
      )
    );
  };

  const handleFile = async (f: File) => {
    setError(null);
    try {
      const text = await f.text();
      const parsed = JSON.parse(text);
      if (!validateScan(parsed)) throw new Error('Invalid scan format');
      setPreview(parsed);
      setFile(f);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to parse JSON');
      setPreview(null);
    }
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragActive(false);
    const f = e.dataTransfer.files[0];
    if (f?.name.endsWith('.json')) handleFile(f);
  };

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragActive(true);
  };

  const handleDragLeave = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragActive(false);
  };

  const handleImport = async () => {
    if (!preview) return;
    setImporting(true);
    setError(null);
    try {
      await onImport(preview);
      setPreview(null);
      setFile(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setImporting(false);
    }
  };

  const formatTime = (ts: number) => {
    const secs = Math.floor(Date.now() / 1000 - ts);
    if (secs < 60) return `${secs}s ago`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    return `${Math.floor(secs / 3600)}h ago`;
  };

  if (!preview) {
    return (
      <div
        className={`border-2 rounded-lg p-6 text-center transition-colors ${
          dragActive
            ? 'border-primary bg-primary/5'
            : 'border-border hover:border-primary/50'
        }`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
      >
        <input
          type="file"
          accept=".json"
          className="hidden"
          id="scan-import"
          onChange={(e: ChangeEvent<HTMLInputElement>) => e.target.files?.[0] && handleFile(e.target.files[0])}
        />
        <label htmlFor="scan-import" className="cursor-pointer">
          <Upload className="mx-auto h-12 w-12 text-muted-foreground mb-4" />
          <p className="text-lg font-medium">Drop SC-Price-Scanner JSON here</p>
          <p className="text-sm text-muted-foreground mt-1">or click to browse</p>
          <p className="text-xs text-muted-foreground mt-4">
            Scans saved to <code>~/SC-Scans/</code> by the standalone scanner
          </p>
        </label>
      </div>
    );
  }

  const updateCell = (
    idx: number,
    field: 'buy' | 'sell' | 'stock' | 'demand',
    v: number | null
  ) => {
    setPreview((p) =>
      p
        ? {
            ...p,
            commodities: p.commodities.map((c, i) =>
              i === idx
                ? ({ ...c, [field]: v } as ScanData['commodities'][number])
                : c
            ),
          }
        : p
    );
  };

  const validRows = preview.commodities.filter((c) => c.buy && c.sell);
  const marginRows = preview.commodities
    .map((c, idx) => ({
      ...c,
      _idx: idx,
      margin:
        c.buy && c.sell ? Number((((c.sell - c.buy) / c.buy) * 100).toFixed(1)) : null,
    }))
    .sort((a, b) => (b.margin ?? -Infinity) - (a.margin ?? -Infinity));

  return (
    <div className="border rounded-lg overflow-hidden bg-card">
      <div className="flex items-center justify-between p-4 border-b bg-muted/30">
        <div className="flex items-center gap-3">
          <FileText className="h-5 w-5 text-primary" />
          <div>
            <h4 className="font-semibold">Scan Preview</h4>
            <p className="text-sm text-muted-foreground">
              {preview.station} • {preview.commodities.length} commodities • {formatTime(preview.timestamp)}
            </p>
          </div>
        </div>
        <button
          onClick={() => { setPreview(null); setFile(null); }}
          className="text-muted-foreground hover:text-foreground"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      <div className="p-4 max-h-96 overflow-auto">
        <p className="text-xs text-muted-foreground mb-3">
          Values are editable — correct any OCR misreads before merging.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="pb-2 pr-4">Commodity</th>
                <th className="pb-2 pr-4 text-right">Buy</th>
                <th className="pb-2 pr-4 text-right">Sell</th>
                <th className="pb-2 pr-4 text-right">Margin</th>
                <th className="pb-2 pr-4 text-right">Stock</th>
                <th className="pb-2 text-right">Demand</th>
              </tr>
            </thead>
            <tbody>
              {marginRows.slice(0, 20).map((c) => (
                <tr key={c._idx} className="border-b border-border/50 hover:bg-muted/30">
                  <td className="py-2 pr-4 font-medium">{c.commodity}</td>
                  <td className="py-2 pr-4 text-right">
                    <EditableNumber
                      value={c.buy}
                      onChange={(v) => updateCell(c._idx, 'buy', v)}
                      className="text-green-400"
                    />
                  </td>
                  <td className="py-2 pr-4 text-right">
                    <EditableNumber
                      value={c.sell}
                      onChange={(v) => updateCell(c._idx, 'sell', v)}
                      className="text-red-400"
                    />
                  </td>
                  <td className="py-2 pr-4 text-right font-medium text-yellow-300">
                    {c.margin === null ? '—' : `${c.margin.toFixed(1)}%`}
                  </td>
                  <td className="py-2 pr-4 text-right">
                    <EditableNumber
                      value={c.stock}
                      onChange={(v) => updateCell(c._idx, 'stock', v)}
                    />
                  </td>
                  <td className="py-2 text-right">
                    <EditableNumber
                      value={c.demand}
                      onChange={(v) => updateCell(c._idx, 'demand', v)}
                    />
                  </td>
                </tr>
              ))}
              {marginRows.length > 20 && (
                <tr>
                  <td colSpan={6} className="py-2 text-center text-muted-foreground text-xs">
                    +{marginRows.length - 20} more rows
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {error && (
          <div className="mt-4 p-3 text-sm text-red-400 bg-red-400/10 border border-red-400/20 rounded">
            {error}
          </div>
        )}

        <button
          onClick={handleImport}
          disabled={importing}
          className="w-full mt-4 btn-primary disabled:opacity-50 flex items-center justify-center gap-2"
        >
          {importing ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Merging to Market…
            </>
          ) : (
            <>
              <Check className="h-4 w-4" />
              Merge {validRows.length} Commodities to Market
            </>
          )}
        </button>
      </div>
    </div>
  );
}