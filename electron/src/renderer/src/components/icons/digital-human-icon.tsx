import { forwardRef } from 'react';
import type { LucideIcon, LucideProps } from 'lucide-react';

/** A speaking portrait, deliberately distinct from the dubbing filmstrip. */
export const DigitalHumanIcon: LucideIcon = forwardRef<SVGSVGElement, LucideProps>(
  function DigitalHumanIcon({size=24, strokeWidth=2, ...props}, ref) {
    return <svg ref={ref} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M6 3H4a1 1 0 0 0-1 1v3M18 3h2a1 1 0 0 1 1 1v3M3 17v3a1 1 0 0 0 1 1h3M17 21h3a1 1 0 0 0 1-1v-3"/>
      <path d="M7 19c.4-2.5 2.1-4 5-4 1.4 0 2.6.4 3.4 1.1"/>
      <rect x="8" y="5" width="8" height="9" rx="4"/>
      <path d="M10.5 10.5h3M18.5 10v3M21 9v5"/>
    </svg>;
  },
);
