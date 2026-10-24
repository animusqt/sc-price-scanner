// ============================================================
// INTEGRATION GUIDE: Adding ScanImport to your Market page
// ============================================================
// Your live Market page (from https://sc-truckers.lovable.app/market) has:
// - "Kiosk scanner" section with screenshot upload
// - Commodity table with live UEX data
// - Parts exchange table
//
// Add the JSON import BELOW the screenshot upload, ABOVE the commodity table.

// -----------------------------------------------------------------------------
// FILE: src/pages/Market.tsx (or wherever your Market page lives)
// -----------------------------------------------------------------------------

import { ScanImport } from '@/components/ScanImport';
import { useSupabase } from '@/hooks/useSupabase'; // or your data hook
import { toast } from 'sonner'; // or your toast lib

export function MarketPage() {
  const { user, refetchMarket } = useSupabase(); // adjust to your hooks
  
  // ---- Handler for imported scans ----
  const handleScanImport = async (scan: ScanData) => {
    const supabase = createClient(); // your supabase client
    
    // 1. Insert raw scan for audit trail
    const { error: scanError } = await supabase
      .from('player_scans')
      .insert({
        station: scan.station,
        commodities: scan.commodities,
        source: 'player-scan',
        scanned_at: new Date(scan.timestamp * 1000).toISOString(),
        submitted_by: user?.id ?? null
      });
    
    if (scanError) throw scanError;
    
    // 2. Merge into live commodity_prices (upsert fresher data only)
    const { error: mergeError } = await supabase.rpc('merge_player_scan', {
      p_station: scan.station,
      p_commodities: scan.commodities,
      p_scanned_at: new Date(scan.timestamp * 1000).toISOString(),
      p_submitted_by: user?.id ?? null
    });
    
    if (mergeError) throw mergeError;
    
    // 3. Refresh UI
    await refetchMarket();
    toast.success(`Merged ${scan.commodities.length} prices from ${scan.station}`, {
      description: `Scanned ${formatTimeAgo(scan.timestamp)} • Source: Player scan`
    });
  };

  // ---- Render ----
  return (
    <div className="container mx-auto px-4 py-6">
      {/* ... existing header, stats cards, etc. ... */}
      
      {/* ========== EXISTING: Kiosk Scanner (screenshot upload) ========== */}
      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-4">Kiosk Scanner</h2>
        <div className="border rounded-lg p-4 bg-muted/50">
          {/* ... your existing screenshot dropzone ... */}
        </div>
      </section>

      {/* ========== NEW: JSON Scan Import (add this) ========== */}
      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Download className="h-5 w-5 text-primary" />
          Import Local Scan (.json)
        </h2>
        <p className="text-sm text-muted-foreground mb-3">
          Use the <a href="/tools" className="underline hover:text-primary">SC Price Scanner</a> 
          standalone app (F9 in-game) → drag the JSON file here
        </p>
        <ScanImport onImport={handleScanImport} />
      </section>

      {/* ========== EXISTING: Commodity Table ========== */}
      <section>
        <CommodityTable data={marketData} />
      </section>
    </div>
  );
}

// -----------------------------------------------------------------------------
// FILE: src/components/ScanImport.tsx (copy from sc_price_scanner/scanner-engine/ScanImport.tsx)
// -----------------------------------------------------------------------------
// Already created - just drop into your components folder

// -----------------------------------------------------------------------------
// SUPABASE: RPC function for merging scans (run in SQL editor)
// -----------------------------------------------------------------------------
/*
create or replace function merge_player_scan(
  p_station text,
  p_commodities jsonb,
  p_scanned_at timestamptz,
  p_submitted_by uuid
) returns void language plpgsql as $$
begin
  insert into commodity_prices (station, commodity, buy_price, sell_price, stock, demand, source, updated_at)
  select 
    p_station,
    c->>'commodity' as commodity,
    (c->>'buy')::int as buy_price,
    (c->>'sell')::int as sell_price,
    (c->>'stock')::int as stock,
    (c->>'demand')::int as demand,
    'player-scan' as source,
    p_scanned_at
  from jsonb_array_elements(p_commodities) c
  where (c->>'buy') is not null and (c->>'sell') is not null
  on conflict (station, commodity) do update set
    buy_price = excluded.buy_price,
    sell_price = excluded.sell_price,
    stock = excluded.stock,
    demand = excluded.demand,
    source = excluded.source,
    updated_at = excluded.updated_at
  where excluded.updated_at > commodity_prices.updated_at;
end;
$$;
*/

// -----------------------------------------------------------------------------
// DOWNLOAD PAGE: src/pages/Tools.tsx (or add to existing tools page)
// -----------------------------------------------------------------------------
/*
import { ScannerDownload } from '@/components/ScannerDownload';

export function ToolsPage() {
  return (
    <div className="container mx-auto px-4 py-12">
      <h1 className="text-3xl font-bold mb-4">Tools & Downloads</h1>
      
      <section className="mb-12">
        <h2 className="text-xl font-semibold mb-4">SC Price Scanner</h2>
        <p className="text-muted-foreground mb-6 max-w-2xl">
          Standalone app for scanning commodity terminals in-game. 
          No Overwolf, no injection, zero ban risk. Hotkey-based (F9), saves JSON.
        </p>
        <ScannerDownload />
      </section>
      
      <section className="mb-12">
        <h2 className="text-xl font-semibold mb-4">SC Price Scanner Overlay</h2>
        <p className="text-muted-foreground mb-6 max-w-2xl">
          Always-on-top Electron overlay with GUI. Shows last scan, logs, one-click upload.
        </p>
        <ScannerDownload overlay={true} />
      </section>
    </div>
  );
}
*/

// -----------------------------------------------------------------------------
// COMPONENT: copy from sc_price_scanner/scanner-engine/ScannerDownload.tsx
// -----------------------------------------------------------------------------
/*
import { Download, Monitor, Apple, Terminal } from 'lucide-react';

interface ScannerDownloadProps {
  overlay?: boolean;
}

export function ScannerDownload({ overlay = false }: ScannerDownloadProps) {
  const base = 'https://github.com/YOUR_USERNAME/sc-price-scanner/releases/latest/download';
  
  const files = overlay ? [
    { os: 'Windows', file: 'overlay-win.exe', icon: Monitor, color: 'text-blue-400' },
    { os: 'macOS', file: 'overlay-mac.dmg', icon: Apple, color: 'text-gray-400' },
    { os: 'Linux', file: 'overlay-linux.AppImage', icon: Terminal, color: 'text-orange-400' },
  ] : [
    { os: 'Windows', file: 'scanner-win.exe', icon: Monitor, color: 'text-blue-400' },
    { os: 'macOS', file: 'scanner-mac', icon: Apple, color: 'text-gray-400' },
    { os: 'Linux', file: 'scanner-linux', icon: Terminal, color: 'text-orange-400' },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {files.map(f => {
        const Icon = f.icon;
        return (
          <a 
            key={f.os} 
            href={`${base}/${f.file}`} 
            className="btn-border flex flex-col items-center gap-2 p-4"
            target="_blank" 
            rel="noopener"
          >
            <Icon className={`h-8 w-8 ${f.color}`} />
            <span className="font-medium">{f.os}</span>
            <span className="text-xs text-muted-foreground">{f.file}</span>
          </a>
        );
      })}
    </div>
  );
}
*/

// -----------------------------------------------------------------------------
// DEPLOY: Push to GitHub → Vercel/Netlify (no Lovable credits)
// -----------------------------------------------------------------------------
/*
# 1. Clone your Lovable repo (or download ZIP)
git clone https://github.com/YOUR_USERNAME/sc-truckers.git
cd sc-truckers

# 2. Copy new files
cp -r ../sc_price_scanner/scanner-engine/ScanImport.tsx src/components/
cp -r ../sc_price_scanner/scanner-engine/ScannerDownload.tsx src/components/
# Edit src/pages/Market.tsx to add ScanImport section
# Create src/pages/Tools.tsx with ScannerDownload

# 3. Commit & push
git add -A
git commit -m "Add player scan import + scanner downloads"
git push

# 4. Deploy (one-time setup)
# Vercel: vercel --prod
# Netlify: netlify deploy --prod
# Cloudflare: npx wrangler pages deploy dist

# 5. Tag release for cross-platform builds
git tag v1.0.0 && git push origin v1.0.0
# → GitHub Actions builds all 3 platforms → creates Release with downloads
*/