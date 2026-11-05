// Exercise the browser build's first-run behavior using the same test setup.
import { mergeConfig } from 'vite';
import base from './vite.config';

export default mergeConfig(base, {
  define: { __WEB_DEPLOYMENT__: true },
});
