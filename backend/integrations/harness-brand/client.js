window.__ModuleLoader__.load({
  id: '@zhilian/harness-brand',
  factory: (require) => {
    const { jsx } = require('react/jsx-runtime');
    const sourceName = /DeepSeek[\s-]*Harness/gi;
    async function apply(ctx) {
      const response = await fetch('/harness-brand.json', { cache: 'no-store' });
      if (!response.ok) throw new Error('Workbench branding is unavailable.');
      const brand = await response.json();
      function Mark({ size }) {
        const pixels = typeof size === 'number' ? size : 32;
        return jsx('img', {
          src: brand.logo, alt: brand.name,
          style: { width: pixels, height: pixels, objectFit: 'contain', borderRadius: '22%' },
        });
      }
      function Name() {
        return jsx('span', { children: brand.name, style: { fontSize: 19, fontWeight: 700, whiteSpace: 'nowrap' } });
      }
      ctx.slots.inject('sidebar.brand.mark', () => ctx.slots.inject('sidebar.brand.name', function* () {
        yield ctx.slots.register({ name: 'sidebar.brand.mark' }, Mark);
        yield ctx.slots.register({ name: 'sidebar.brand.name' }, Name);
      }));
      ctx.slots.inject('conversation.hero.brand.mark', function* () {
        yield ctx.slots.register({ name: 'conversation.hero.brand.mark' }, Mark);
      });
      // UI copy only: stored conversations, provider IDs and technical code stay intact.
      function update(node) {
        if (node.nodeType === Node.TEXT_NODE) {
          if (!node.parentElement?.closest('script,style,pre,code')) {
            const isHeroHeadline = node.nodeValue === '\u63a2\u7d22\u672a\u81f3\u4e4b\u5883'
              && node.parentElement?.closest('[class*="titleGroup"]');
            const next = isHeroHeadline && brand.welcome
              ? brand.welcome : node.nodeValue.replace(sourceName, brand.name);
            if (next !== node.nodeValue) node.nodeValue = next;
          }
          return;
        }
        if (node.nodeType !== Node.ELEMENT_NODE && node.nodeType !== Node.DOCUMENT_NODE) return;
        for (const attr of ['title', 'aria-label', 'alt']) {
          const value = node.getAttribute?.(attr);
          if (value) {
            const next = value.replace(sourceName, brand.name);
            if (next !== value) node.setAttribute(attr, next);
          }
        }
        for (const child of node.childNodes) update(child);
      }
      update(document.documentElement);
      document.title = document.title.replace(sourceName, brand.name);
      for (const icon of document.querySelectorAll('link[rel~="icon"]')) icon.href = brand.logo;
      const observer = new MutationObserver((records) => {
        for (const record of records) {
          if (record.type === 'childList') for (const node of record.addedNodes) update(node);
          else update(record.target);
        }
      });
      observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true,
        attributes: true, attributeFilter: ['title', 'aria-label', 'alt'] });
      ctx.effect(() => () => observer.disconnect());
    }
    return { apply, inject: ['slots'] };
  },
});
