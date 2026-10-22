// components/ScannerDownload.tsx
// Download cards for the SC Price Scanner + overlay.
// Scanner assets have stable filenames, so they can be linked from
// /releases/latest/download/. Overlay installers include the version in their
// filename, so those links go to the latest release page instead.

import { Monitor, Apple, Terminal, ShieldCheck } from 'lucide-react';

const REPO = 'https://github.com/animusqt/sc-price-scanner';
const LATEST = `${REPO}/releases/latest`;
const DOWNLOAD = `${LATEST}/download`;

interface ScannerDownloadProps {
  /** Show overlay installers instead of the CLI scanner */
  overlay?: boolean;
}

const SCANNER_FILES = [
  { os: 'Windows', file: 'SC-Price-Scanner.exe', icon: Monitor, note: 'F8 calibrate, F9 scan' },
  { os: 'macOS', file: 'SC-Price-Scanner-mac', icon: Apple, note: 'chmod +x, run in Terminal' },
  { os: 'Linux', file: 'SC-Price-Scanner-linux', icon: Terminal, note: 'chmod +x, run in Terminal' },
];

const OVERLAY_FILES = [
  { os: 'Windows', file: 'NSIS installer (.exe)', icon: Monitor },
  { os: 'macOS', file: 'Disk image (.dmg)', icon: Apple },
  { os: 'Linux', file: 'AppImage', icon: Terminal },
];

export function ScannerDownload({ overlay = false }: ScannerDownloadProps) {
  const files = overlay ? OVERLAY_FILES : SCANNER_FILES;

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        {files.map((f) => {
          const Icon = f.icon;
          const href = overlay ? LATEST : `${DOWNLOAD}/${f.file}`;
          return (
            <a
              key={f.os}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="flex flex-col items-center gap-2 rounded-lg border border-border bg-card p-4 text-center transition-colors hover:border-primary/60 hover:bg-muted/40"
            >
              <Icon className="h-8 w-8 text-primary" />
              <span className="font-medium">{f.os}</span>
              <span className="text-xs text-muted-foreground">{f.file}</span>
              {'note' in f && f.note ? (
                <span className="text-[11px] text-muted-foreground">{f.note}</span>
              ) : null}
            </a>
          );
        })}
      </div>

      <p className="flex items-start gap-2 text-xs text-muted-foreground">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-green-400" />
        <span>
          No Overwolf, no game injection, no memory reads — just screen capture and
          OCR. Requires the free <strong>Tesseract OCR</strong> engine installed.
          The overlay looks for the scanner next to itself or in the same folder.
        </span>
      </p>
    </div>
  );
}
