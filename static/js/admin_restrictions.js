(function () {
  const viewer = document.getElementById('adminDocumentViewer');
  if (!viewer) return;

  const data = window.ADMIN_STATUTE_DATA || {};
  const rules = Array.isArray(window.ADMIN_STATUTE_RESTRICTIONS)
    ? window.ADMIN_STATUTE_RESTRICTIONS
    : [];
  const pages = Array.isArray(data.pages) ? data.pages.filter(page => page.number >= 4) : [];
  const items = Array.isArray(data.items) ? data.items.filter(item => item.page >= 4) : [];

  const searchInput = document.getElementById('adminStatuteSearch');
  const searchResults = document.getElementById('adminStatuteSearchResults');
  const selectionBox = document.getElementById('adminRestrictionSelection');
  const selectionTitle = document.getElementById('adminRestrictionTitle');
  const selectionSnippet = document.getElementById('adminRestrictionSnippet');
  const saveButton = document.getElementById('saveRestrictionButton');
  const messageField = document.getElementById('restrictionMessage');

  const fields = {
    capitulo: document.getElementById('restrictionCapitulo'),
    articulo: document.getElementById('restrictionArticulo'),
    paragrafo: document.getElementById('restrictionParagrafo'),
    numeral: document.getElementById('restrictionNumeral'),
    literal: document.getElementById('restrictionLiteral')
  };

  function refValue(value) {
    return String(value || '').trim().toLocaleLowerCase('es');
  }

  function ruleMatches(item, rule) {
    return ['capitulo', 'articulo', 'paragrafo', 'numeral', 'literal'].every(field => {
      const required = refValue(rule[field]);
      return !required || required === refValue(item[field]);
    });
  }

  function exactRuleFor(item) {
    return rules.find(rule =>
      ['capitulo', 'articulo', 'paragrafo', 'numeral', 'literal'].every(
        field => refValue(rule[field]) === refValue(item[field])
      )
    ) || null;
  }

  function blockingRuleFor(item) {
    return rules.find(rule => ruleMatches(item, rule)) || null;
  }

  function logicalId(item) {
    return String(item.logical_id || item.id || '');
  }

  function referenceLabel(item) {
    const parts = [];
    if (item.capitulo) parts.push(item.capitulo);
    if (item.articulo) parts.push(`Artículo ${item.articulo}`);
    if (item.paragrafo) parts.push(`Parágrafo ${item.paragrafo}`);
    if (item.numeral) parts.push(`Numeral ${item.numeral}`);
    if (item.literal) parts.push(`Literal ${item.literal}`);
    if (item.continuation) parts.push('Continuación');
    if (item.page) parts.push(`Página ${item.page}`);
    return parts.join(' · ');
  }

  function itemRegions(item) {
    if (Array.isArray(item.regions) && item.regions.length) return item.regions;
    if (Array.isArray(item.region) && item.region.length === 4) return [item.region];
    return [];
  }

  function setSelection(item) {
    document.querySelectorAll('#adminDocumentViewer .pdf-hitbox.is-admin-selected')
      .forEach(el => el.classList.remove('is-admin-selected'));
    document.querySelectorAll(`#adminDocumentViewer .pdf-hitbox[data-logical-id="${logicalId(item)}"]`)
      .forEach(hit => hit.classList.add('is-admin-selected'));

    Object.entries(fields).forEach(([field, input]) => {
      if (input) input.value = item[field] || '';
    });

    const exactRule = exactRuleFor(item);
    const inheritedRule = blockingRuleFor(item);
    const snippet = String(item.snippet || '').trim();

    selectionTitle.textContent = referenceLabel(item);
    selectionSnippet.textContent = snippet
      ? snippet.slice(0, 330) + (snippet.length > 330 ? '…' : '')
      : 'Referencia seleccionada correctamente.';

    selectionBox.classList.toggle('is-blocked', Boolean(inheritedRule));

    if (exactRule) {
      if (messageField) messageField.value = exactRule.mensaje || 'Este texto no está habilitado para cambios.';
      saveButton.disabled = false;
      saveButton.textContent = 'Actualizar mensaje de restricción';
    } else if (inheritedRule) {
      if (messageField) messageField.value = inheritedRule.mensaje || 'Este texto no está habilitado para cambios.';
      saveButton.disabled = true;
      saveButton.textContent = 'Ya está bloqueado por una regla superior';
    } else {
      if (messageField && !messageField.value.trim()) {
        messageField.value = 'Este texto no está habilitado para cambios.';
      }
      saveButton.disabled = false;
      saveButton.textContent = 'Guardar como no editable';
    }
  }

  function selectItem(item, scrollToPage) {
    setSelection(item);
    if (scrollToPage) {
      const page = document.getElementById(`admin-pdf-page-${item.page}`);
      if (page) page.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function buildViewer() {
    const fragment = document.createDocumentFragment();

    pages.forEach(page => {
      const frame = document.createElement('section');
      frame.className = 'pdf-page';
      frame.id = `admin-pdf-page-${page.number}`;
      frame.style.width = `${page.width}px`;

      const label = document.createElement('span');
      label.className = 'pdf-page__label';
      label.textContent = `Página ${page.number}`;
      frame.appendChild(label);

      const img = document.createElement('img');
      img.src = `${window.STATIC_URL || '/static/'}${page.image}`;
      img.alt = `Página ${page.number} del Estatuto`;
      img.loading = page.number <= 6 ? 'eager' : 'lazy';
      img.width = page.width;
      img.height = page.height;
      frame.appendChild(img);

      items.filter(item => item.page === page.number).forEach(item => {
        const scaleX = page.width / page.pdf_width;
        const scaleY = page.height / page.pdf_height;
        const blocked = Boolean(blockingRuleFor(item));

        itemRegions(item).forEach((region, regionIndex) => {
          const [x0, y0, x1, y1] = region;
          const hit = document.createElement('button');
          hit.type = 'button';
          hit.className = `pdf-hitbox${blocked ? ' is-blocked' : ''}`;
          hit.dataset.itemId = item.id;
          hit.dataset.logicalId = logicalId(item);
          hit.dataset.regionIndex = regionIndex;
          hit.title = blocked
            ? `No editable · ${referenceLabel(item)}`
            : `Seleccionar ${referenceLabel(item)}`;
          hit.setAttribute('aria-label', hit.title);
          hit.style.left = `${x0 * scaleX}px`;
          hit.style.top = `${y0 * scaleY}px`;
          hit.style.width = `${Math.max(10, (x1 - x0) * scaleX)}px`;
          hit.style.height = `${Math.max(8, (y1 - y0) * scaleY)}px`;
          hit.addEventListener('click', () => selectItem(item, false));
          frame.appendChild(hit);
        });
      });

      fragment.appendChild(frame);
    });

    viewer.appendChild(fragment);
  }

  function showResults(matches) {
    if (!searchResults) return;
    searchResults.innerHTML = '';
    if (!matches.length) {
      searchResults.classList.remove('is-open');
      return;
    }

    matches.slice(0, 18).forEach(item => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `search-result${blockingRuleFor(item) ? ' is-blocked-result' : ''}`;
      const summary = String(item.snippet || '').replace(/\s+/g, ' ').slice(0, 95);
      const state = blockingRuleFor(item) ? '<em>No editable</em>' : '';
      button.innerHTML = `<strong>${item.label || referenceLabel(item)} ${state}</strong><small>${summary}${summary.length >= 95 ? '…' : ''}</small>`;
      button.addEventListener('click', () => {
        selectItem(item, true);
        searchResults.classList.remove('is-open');
        if (searchInput) searchInput.value = item.label || referenceLabel(item);
      });
      searchResults.appendChild(button);
    });
    searchResults.classList.add('is-open');
  }

  function focusRestriction(index) {
    const rule = rules[index];
    if (!rule) return;
    const item = items.find(candidate => ruleMatches(candidate, rule));
    if (item) selectItem(item, true);
  }

  buildViewer();

  if (searchInput) {
    searchInput.addEventListener('input', function () {
      const term = this.value.trim().toLowerCase();
      if (term.length < 2) {
        searchResults.classList.remove('is-open');
        searchResults.innerHTML = '';
        return;
      }
      const matches = items.filter(item => {
        const haystack = `${item.label || ''} ${item.capitulo || ''} ${item.snippet || ''}`.toLowerCase();
        return haystack.includes(term);
      });
      showResults(matches);
    });

    document.addEventListener('click', event => {
      if (searchResults && !searchResults.contains(event.target) && event.target !== searchInput) {
        searchResults.classList.remove('is-open');
      }
    });
  }

  document.querySelectorAll('[data-focus-restriction]').forEach(button => {
    button.addEventListener('click', () => focusRestriction(Number(button.dataset.focusRestriction)));
  });
})();
