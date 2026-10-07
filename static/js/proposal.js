(function () {
  const viewer = document.getElementById('documentViewer');
  const data = window.STATUTE_DATA || {};
  const restrictions = Array.isArray(window.STATUTE_RESTRICTIONS) ? window.STATUTE_RESTRICTIONS : [];
  if (!viewer || !Array.isArray(data.pages)) return;

  const pages = data.pages.filter(page => page.number >= 4);
  const items = (data.items || []).filter(item => item.page >= 4);
  const searchInput = document.getElementById('statuteSearch');
  const searchResults = document.getElementById('statuteSearchResults');
  const viewerFeedback = document.getElementById('viewerFeedback');

  const modal = document.getElementById('proposalModal');
  const formState = document.getElementById('proposalFormState');
  const successState = document.getElementById('proposalSuccessState');
  const proposalForm = document.getElementById('proposalForm');
  const proposalErrors = document.getElementById('proposalErrors');
  const submitButton = document.getElementById('submitProposalButton');
  const newProposalButton = document.getElementById('newProposalButton');
  const successProposalId = document.getElementById('successProposalId');

  const selectionBox = document.getElementById('selectionBox');
  const selectionTitle = document.getElementById('selectionTitle');
  const selectionSnippet = document.getElementById('selectionSnippet');

  const previousCountNotice = document.getElementById('previousCountNotice');
  const previousCountValue = document.getElementById('previousCountValue');
  const previousCountPlural = document.getElementById('previousCountPlural');

  const fields = {
    capitulo: document.getElementById('capitulo'),
    articulo: document.getElementById('articulo'),
    paragrafo: document.getElementById('paragrafo'),
    numeral: document.getElementById('numeral'),
    literal: document.getElementById('literal'),
    pagina: document.getElementById('pagina'),
    referencia_texto: document.getElementById('referencia_texto')
  };

  let selectedItem = null;
  let lastFocusedElement = null;

  function norm(value) {
    return String(value || '').trim().toLocaleLowerCase('es');
  }

  function restrictionFor(item) {
    return restrictions.find(rule => {
      return ['capitulo', 'articulo', 'paragrafo', 'numeral', 'literal'].every(field => {
        const required = norm(rule[field]);
        return !required || required === norm(item[field]);
      });
    }) || null;
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
    parts.push(`Página ${item.page}`);
    return parts.join(' · ');
  }

  function clearReferenceFields() {
    Object.values(fields).forEach(field => {
      if (field) field.value = '';
    });
  }

  function clearVisualState() {
    document.querySelectorAll('.pdf-hitbox.is-selected, .pdf-hitbox.is-blocked, .pdf-hitbox.is-search-target')
      .forEach(el => el.classList.remove('is-selected', 'is-blocked', 'is-search-target'));
    if (selectionBox) selectionBox.classList.remove('is-blocked');
  }

  function hideViewerFeedback() {
    if (!viewerFeedback) return;
    viewerFeedback.hidden = true;
    viewerFeedback.classList.remove('is-error', 'is-success');
    viewerFeedback.textContent = '';
  }

  function showViewerFeedback(message, type) {
    if (!viewerFeedback) return;
    viewerFeedback.textContent = message;
    viewerFeedback.classList.remove('is-error', 'is-success');
    viewerFeedback.classList.add(type === 'error' ? 'is-error' : 'is-success');
    viewerFeedback.hidden = false;
  }

  function openModal() {
    if (!modal) return;
    lastFocusedElement = document.activeElement;
    formState.hidden = false;
    successState.hidden = true;
    proposalErrors.hidden = true;
    proposalErrors.innerHTML = '';
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    window.setTimeout(() => {
      const first = proposalForm && proposalForm.querySelector('select, textarea, button');
      if (first) first.focus();
    }, 40);
  }

  function closeModal() {
    if (!modal) return;
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
    if (lastFocusedElement && typeof lastFocusedElement.focus === 'function') {
      lastFocusedElement.focus();
    }
  }

  function showBlocked(item, rule, scrollToPage) {
    closeModal();
    selectedItem = null;
    clearVisualState();
    clearReferenceFields();
    document
      .querySelectorAll(`.pdf-hitbox[data-logical-id="${logicalId(item)}"]`)
      .forEach(hit => hit.classList.add('is-blocked'));

    const message = (rule && rule.mensaje) || 'Este texto no está habilitado para cambios.';
    showViewerFeedback(message, 'error');

    if (scrollToPage) {
      const page = document.getElementById(`pdf-page-${item.page}`);
      if (page) page.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  function selectItem(item, scrollToPage) {
    const blocked = restrictionFor(item);
    if (blocked) {
      showBlocked(item, blocked, scrollToPage);
      return;
    }

    selectedItem = item;
    clearVisualState();
    hideViewerFeedback();

    document
      .querySelectorAll(`.pdf-hitbox[data-logical-id="${logicalId(item)}"]`)
      .forEach(hit => hit.classList.add('is-selected'));

    fields.capitulo.value = item.capitulo || '';
    fields.articulo.value = item.articulo || '';
    fields.paragrafo.value = item.paragrafo || '';
    fields.numeral.value = item.numeral || '';
    fields.literal.value = item.literal || '';
    fields.pagina.value = item.page || '';
    fields.referencia_texto.value = item.snippet || item.label || '';

    selectionTitle.textContent = referenceLabel(item);
    const snippet = (item.snippet || '').trim();
    selectionSnippet.textContent = snippet
      ? snippet.slice(0, 390) + (snippet.length > 390 ? '…' : '')
      : 'Referencia seleccionada correctamente.';

    if (scrollToPage) {
      const page = document.getElementById(`pdf-page-${item.page}`);
      if (page) page.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    openModal();
  }

  function findSearchTargets(item) {
    const page = document.getElementById(`pdf-page-${item.page}`);
    if (!page) return { page: null, targets: [] };

    const pageHits = Array.from(page.querySelectorAll('.pdf-hitbox'));
    let targets = pageHits.filter(hit =>
      String(hit.dataset.itemId || '') === String(item.id || '')
    );

    // Compatibilidad con estructuras anteriores: solo se usa el logical_id
    // si no existe el id físico exacto en esa página.
    if (!targets.length) {
      targets = pageHits.filter(hit =>
        String(hit.dataset.logicalId || '') === logicalId(item)
      );
    }

    targets.sort((a, b) => {
      const ar = Number(a.dataset.regionIndex || 0);
      const br = Number(b.dataset.regionIndex || 0);
      return ar - br;
    });

    return { page, targets };
  }

  function scrollElementInsideViewer(element, behavior) {
    if (!element) return;

    // getBoundingClientRect() evita el error de offsetTop cuando el visor
    // no es el offsetParent del elemento. Ese desfase hacía que, por ejemplo,
    // Artículo 97 terminara mostrando Artículo 98/99.
    const viewerRect = viewer.getBoundingClientRect();
    const elementRect = element.getBoundingClientRect();

    // Colocamos el inicio exacto del apartado aproximadamente al 28% de la
    // altura visible. Así se ve algo de contexto anterior sin perder precisión.
    const anchorY = Math.max(72, viewer.clientHeight * 0.28);
    const delta = elementRect.top - viewerRect.top - anchorY;
    const destination = Math.max(0, viewer.scrollTop + delta);

    viewer.scrollTo({
      top: destination,
      behavior: behavior || 'auto'
    });
  }

  function locateItem(item) {
    // El buscador SOLO navega. No selecciona ni abre el formulario.
    closeModal();
    selectedItem = null;
    clearReferenceFields();
    clearVisualState();
    hideViewerFeedback();

    const found = findSearchTargets(item);
    if (!found.page) return;

    found.targets.forEach(hit => hit.classList.add('is-search-target'));

    // Si existe el hitbox exacto, se navega a su primera región (título/inicio
    // del apartado). Si no, se usa la parte superior de la página como respaldo.
    const target = found.targets[0] || found.page;
    scrollElementInsideViewer(target, 'smooth');

    // Correcciones cortas para imágenes lazy-load, fuentes y cambios de layout.
    // Se vuelve a calcular desde la geometría REAL en pantalla, no con offsetTop.
    [120, 360, 800].forEach(delay => {
      window.setTimeout(() => {
        const refreshed = findSearchTargets(item);
        if (!refreshed.page) return;
        refreshed.targets.forEach(hit => hit.classList.add('is-search-target'));
        scrollElementInsideViewer(refreshed.targets[0] || refreshed.page, 'auto');
      }, delay);
    });
  }
  function itemRegions(item) {
    if (Array.isArray(item.regions) && item.regions.length) return item.regions;
    if (Array.isArray(item.region) && item.region.length === 4) return [item.region];
    return [];
  }

  function buildViewer() {
    const fragment = document.createDocumentFragment();
    const availableWidth = Math.max(300, viewer.clientWidth - 28);

    pages.forEach(page => {
      const naturalWidth = Number(page.width) || 495;
      const naturalHeight = Number(page.height) || 765;
      const displayWidth = Math.min(Math.round(naturalWidth * 1.45), availableWidth);
      const displayHeight = Math.round(naturalHeight * (displayWidth / naturalWidth));

      const frame = document.createElement('section');
      frame.className = 'pdf-page';
      frame.id = `pdf-page-${page.number}`;
      frame.style.width = `${displayWidth}px`;

      const label = document.createElement('span');
      label.className = 'pdf-page__label';
      label.textContent = `Página ${page.number}`;
      frame.appendChild(label);

      const img = document.createElement('img');
      img.src = `${window.STATIC_URL || '/static/'}${page.image}`;
      img.alt = `Página ${page.number} del Estatuto`;
      img.loading = page.number <= 6 ? 'eager' : 'lazy';
      img.width = displayWidth;
      img.height = displayHeight;
      frame.appendChild(img);

      items.filter(item => item.page === page.number).forEach(item => {
        const scaleX = displayWidth / page.pdf_width;
        const scaleY = displayHeight / page.pdf_height;

        itemRegions(item).forEach((region, regionIndex) => {
          const [x0, y0, x1, y1] = region;
          const hit = document.createElement('button');
          hit.type = 'button';
          hit.className = 'pdf-hitbox';
          hit.dataset.itemId = item.id;
          hit.dataset.logicalId = logicalId(item);
          hit.dataset.regionIndex = regionIndex;
          hit.title = referenceLabel(item);
          hit.setAttribute('aria-label', `Seleccionar ${referenceLabel(item)}`);
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

    matches.slice(0, 16).forEach(item => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'search-result';
      const summary = (item.snippet || '').replace(/\s+/g, ' ').slice(0, 95);
      button.innerHTML = `<strong>${item.label}</strong><small>${summary}${summary.length >= 95 ? '…' : ''}</small>`;

      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();

        // El resultado de búsqueda solo ubica el texto en el PDF.
        // El popup se abrirá después, únicamente si el usuario hace clic
        // directamente sobre el texto/área resaltada dentro del visor.
        searchResults.classList.remove('is-open');
        searchInput.value = item.label;

        window.requestAnimationFrame(() => locateItem(item));
      });

      searchResults.appendChild(button);
    });
    searchResults.classList.add('is-open');
  }

  function showFormErrors(errors) {
    const messages = Array.isArray(errors) ? errors : ['No fue posible registrar la recomendación.'];
    proposalErrors.innerHTML = messages.map(message => `<div>${escapeHtml(message)}</div>`).join('');
    proposalErrors.hidden = false;
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function updatePreviousCount(count) {
    const value = Number(count || 0);
    if (!previousCountNotice || !previousCountValue || !previousCountPlural) return;
    previousCountValue.textContent = String(value);
    previousCountPlural.textContent = value === 1 ? '' : 'es';
    previousCountNotice.classList.toggle('is-hidden', value <= 0);
  }

  function resetForAnotherProposal() {
    if (proposalForm) proposalForm.reset();
    clearReferenceFields();
    clearVisualState();
    hideViewerFeedback();
    selectedItem = null;
    formState.hidden = false;
    successState.hidden = true;
    proposalErrors.hidden = true;
    proposalErrors.innerHTML = '';
    closeModal();
    if (searchInput) searchInput.focus();
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

  if (modal) {
    modal.querySelectorAll('[data-modal-close]').forEach(button => {
      button.addEventListener('click', closeModal);
    });
  }

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && modal && modal.classList.contains('is-open')) {
      closeModal();
    }
  });

  if (proposalForm) {
    proposalForm.addEventListener('submit', async event => {
      event.preventDefault();
      proposalErrors.hidden = true;
      proposalErrors.innerHTML = '';

      if (!selectedItem || !fields.articulo.value && !fields.paragrafo.value && !fields.numeral.value && !fields.literal.value && !fields.capitulo.value) {
        showFormErrors(['Seleccione en el Estatuto el apartado que desea ajustar.']);
        return;
      }

      if (!proposalForm.reportValidity()) return;

      const originalText = submitButton.textContent;
      submitButton.disabled = true;
      submitButton.textContent = 'Enviando…';

      try {
        const response = await fetch(proposalForm.action || window.location.pathname, {
          method: 'POST',
          body: new FormData(proposalForm),
          headers: {
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json'
          }
        });

        let payload = null;
        try {
          payload = await response.json();
        } catch (_) {
          payload = null;
        }

        if (!response.ok || !payload || !payload.ok) {
          showFormErrors(payload && payload.errors ? payload.errors : ['No fue posible registrar la recomendación. Intente nuevamente.']);
          return;
        }

        successProposalId.textContent = `#${payload.proposal_id}`;
        updatePreviousCount(payload.previous_count);
        formState.hidden = true;
        successState.hidden = false;
      } catch (error) {
        showFormErrors(['No fue posible conectar con el servidor. Verifique su conexión e intente nuevamente.']);
      } finally {
        submitButton.disabled = false;
        submitButton.textContent = originalText;
      }
    });
  }

  if (newProposalButton) {
    newProposalButton.addEventListener('click', resetForAnotherProposal);
  }

  const initial = window.STATUTE_INITIAL || {};
  if (initial.pagina || initial.articulo || initial.paragrafo) {
    const match = items.find(item =>
      String(item.page || '') === String(initial.pagina || '') &&
      String(item.articulo || '') === String(initial.articulo || '') &&
      String(item.paragrafo || '') === String(initial.paragrafo || '') &&
      String(item.numeral || '') === String(initial.numeral || '') &&
      String(item.literal || '') === String(initial.literal || '')
    );
    if (match) selectItem(match, false);
  }
})();
