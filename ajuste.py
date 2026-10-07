#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DEFAULT = Path('/home/calidad/Frm_Estatuto')

class PatchError(RuntimeError):
    pass

NEW_LOCATE = r'''  function findSearchTargets(item) {
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
'''


def replace_function_block(text: str) -> str:
    start_marker = '  function locateItem(item) {'
    end_marker = '\n  function itemRegions(item) {'
    start = text.find(start_marker)
    if start < 0:
        raise PatchError('No encontré function locateItem(item) en static/js/proposal.js.')
    end = text.find(end_marker, start)
    if end < 0:
        raise PatchError('No encontré el límite function itemRegions(item).')
    return text[:start] + NEW_LOCATE.rstrip() + text[end:]


def update_cache_version(text: str) -> str:
    old = "{{ url_for('static', filename='js/proposal.js') }}?v=5"
    new = "{{ url_for('static', filename='js/proposal.js') }}?v=6"
    if old in text:
        return text.replace(old, new, 1)

    # Acepta otra versión existente sin depender de un número concreto.
    import re
    pat = r"(\{\{\s*url_for\('static',\s*filename='js/proposal\.js'\)\s*\}\}\?v=)\d+"
    updated, count = re.subn(pat, r'\g<1>6', text, count=1)
    if count == 1:
        return updated

    raise PatchError('No encontré la referencia versionada a proposal.js en templates/proposal.html.')


def main() -> int:
    parser = argparse.ArgumentParser(description='Corrige la precisión del buscador dentro del visor PDF.')
    parser.add_argument('--project', default=str(PROJECT_DEFAULT))
    args = parser.parse_args()

    project = Path(args.project).resolve()
    js_path = project / 'static' / 'js' / 'proposal.js'
    tpl_path = project / 'templates' / 'proposal.html'

    print(f'Proyecto: {project}')
    if not js_path.exists() or not tpl_path.exists():
        print('ERROR: No encontré static/js/proposal.js o templates/proposal.html.')
        return 1

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = project / f'backup_precision_busqueda_{stamp}'
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(js_path, backup / 'proposal.js')
    shutil.copy2(tpl_path, backup / 'proposal.html')
    print(f'Respaldo: {backup}')

    try:
        js = js_path.read_text(encoding='utf-8')
        tpl = tpl_path.read_text(encoding='utf-8')

        js_new = replace_function_block(js)
        tpl_new = update_cache_version(tpl)

        # Validaciones antes de escribir.
        required = [
            'function findSearchTargets(item)',
            'function scrollElementInsideViewer(element, behavior)',
            'getBoundingClientRect()',
            "scrollElementInsideViewer(target, 'smooth')",
            '[120, 360, 800].forEach',
        ]
        for token in required:
            if token not in js_new:
                raise PatchError(f'Validación interna falló: falta {token}')
        if '?v=6' not in tpl_new:
            raise PatchError('No quedó aplicada la versión ?v=6 de proposal.js.')

        js_path.write_text(js_new, encoding='utf-8')
        tpl_path.write_text(tpl_new, encoding='utf-8')

        # Valida sintaxis JavaScript si Node está disponible.
        node = shutil.which('node')
        if node:
            result = subprocess.run([node, '--check', str(js_path)], capture_output=True, text=True)
            if result.returncode != 0:
                raise PatchError(result.stderr.strip() or 'proposal.js no pasó node --check.')

        print('  OK  Navegación calculada con getBoundingClientRect()')
        print('  OK  Se usa el item_id exacto antes del logical_id')
        print('  OK  Se centra el inicio real del apartado, no la página completa')
        print('  OK  Corrección automática tras lazy-load/cambios de layout')
        print('  OK  El buscador sigue sin abrir el popup')
        print('  OK  proposal.js actualizado a ?v=6')
        print('\nActualización aplicada correctamente.')
        print('\nEjecute ahora:')
        print('  sudo systemctl restart frm-estatuto.service')
        print('  sudo systemctl reload nginx')
        print('  Luego haga Ctrl + Shift + R en el navegador.')
        return 0

    except Exception as exc:
        print(f'\nERROR: {exc}')
        print('Restaurando archivos desde el respaldo...')
        shutil.copy2(backup / 'proposal.js', js_path)
        shutil.copy2(backup / 'proposal.html', tpl_path)
        print('Restauración completada.')
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
