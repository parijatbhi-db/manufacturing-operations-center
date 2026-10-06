// Tiny inline SVG icon set — no external dependency.
import React from 'react';

type P = { size?: number; className?: string };
const svg = (size: number, className: string | undefined, children: React.ReactNode) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={1.8}
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
  >
    {children}
  </svg>
);

export const IconDashboard = ({ size = 18, className }: P) =>
  svg(size, className, (
    <>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </>
  ));

export const IconGenie = ({ size = 18, className }: P) =>
  svg(size, className, (
    <>
      <path d="M12 3l1.8 4 4.2.6-3 3 .8 4.4-3.8-2-3.8 2 .8-4.4-3-3 4.2-.6L12 3z" />
    </>
  ));

export const IconAgent = ({ size = 18, className }: P) =>
  svg(size, className, (
    <>
      <rect x="4" y="6" width="16" height="13" rx="2.5" />
      <path d="M8 11h.01M16 11h.01" />
      <path d="M9 16h6" />
      <path d="M12 3v3" />
      <path d="M5 10H3M21 10h-2" />
    </>
  ));

export const IconSend = ({ size = 18, className }: P) =>
  svg(size, className, (
    <>
      <path d="M22 2L11 13" />
      <path d="M22 2l-7 20-4-9-9-4 20-7z" />
    </>
  ));

export const IconExternal = ({ size = 14, className }: P) =>
  svg(size, className, (
    <>
      <path d="M14 4h6v6" />
      <path d="M10 14L21 3" />
      <path d="M20 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5" />
    </>
  ));

export const IconFactory = ({ size = 22, className }: P) =>
  svg(size, className, (
    <>
      <path d="M3 21V10l5 3V10l5 3V10l5 3v8H3z" />
      <path d="M9 21v-4M14 21v-4M19 21v-4" />
      <path d="M3 10l1-7h3l1 7" />
    </>
  ));

export const IconRefresh = ({ size = 14, className }: P) =>
  svg(size, className, (
    <>
      <path d="M21 12a9 9 0 1 1-3-6.7" />
      <path d="M21 4v5h-5" />
    </>
  ));

export const IconWafer = ({ size = 18, className }: P) =>
  svg(size, className, (
    <>
      <path d="M12 3a9 9 0 1 0 9 9" />
      <path d="M21 12h-3" />
      <path d="M8 9h2v2H8zM12 9h2v2h-2zM8 13h2v2H8zM12 13h2v2h-2z" />
    </>
  ));

export const IconPipeline = ({ size = 18, className }: P) =>
  svg(size, className, (
    <>
      <circle cx="5" cy="6" r="2" />
      <circle cx="5" cy="18" r="2" />
      <circle cx="19" cy="12" r="2" />
      <path d="M7 6h4a3 3 0 0 1 3 3v0a3 3 0 0 0 3 3" />
      <path d="M7 18h4a3 3 0 0 0 3-3v0a3 3 0 0 1 3-3" />
    </>
  ));
