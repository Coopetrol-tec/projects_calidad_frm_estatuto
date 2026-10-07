(function () {
  const buttons = Array.from(document.querySelectorAll('[data-admin-target]'));
  const panes = Array.from(document.querySelectorAll('[data-admin-view]'));
  const goButtons = Array.from(document.querySelectorAll('[data-admin-go]'));
  const title = document.getElementById('adminPageTitle');
  const subtitle = document.getElementById('adminPageSubtitle');

  const meta = {
    resumen: ['Resumen', 'Estado general del proceso de participación estatutaria.'],
    asociados: ['Base de asociados', 'Gestione las cédulas habilitadas para ingresar al formulario.'],
    estatuto: ['Estatuto', 'Actualice el documento y sus zonas interactivas.'],
    restricciones: ['Textos no editables', 'Seleccione visualmente qué apartados del Estatuto no podrán recibir recomendaciones.'],
    configuracion: ['Configuración', 'Administre los enlaces y textos institucionales.'],
    propuestas: ['Propuestas', 'Consulte los registros recibidos de los asociados.']
  };

  function openSection(section) {
    if (!meta[section]) section = 'resumen';
    buttons.forEach(btn => btn.classList.toggle('is-active', btn.dataset.adminTarget === section));
    panes.forEach(pane => pane.classList.toggle('is-active', pane.dataset.adminView === section));
    title.textContent = meta[section][0];
    subtitle.textContent = meta[section][1];
    const url = new URL(window.location.href);
    if (section === 'resumen') url.searchParams.delete('section');
    else url.searchParams.set('section', section);
    window.history.replaceState({}, '', url);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  buttons.forEach(btn => btn.addEventListener('click', () => openSection(btn.dataset.adminTarget)));
  goButtons.forEach(btn => btn.addEventListener('click', () => openSection(btn.dataset.adminGo)));

  const initial = new URLSearchParams(window.location.search).get('section') || 'resumen';
  openSection(initial);

  const search = document.getElementById('dashboardProposalSearch');
  const table = document.getElementById('dashboardProposalsTable');
  if (search && table) {
    search.addEventListener('input', function () {
      const term = this.value.trim().toLowerCase();
      table.querySelectorAll('tbody tr').forEach(row => {
        row.style.display = row.textContent.toLowerCase().includes(term) ? '' : 'none';
      });
    });
  }
})();
