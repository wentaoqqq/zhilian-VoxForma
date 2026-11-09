import React from 'react';
import logo from '../../../../public/favicon-zhilian.png';

/** Shared fork logo for screens that still reuse the upstream mark component. */
export default function VoiceStudioMark({ className = '', title, ...props }) {
  return (
    <img
      src={logo}
      alt={title || ''}
      title={title}
      aria-hidden={title ? undefined : true}
      className={`rounded object-contain ${className}`}
      {...props}
    />
  );
}
