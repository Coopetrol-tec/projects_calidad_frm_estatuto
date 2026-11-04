(function () {
  "use strict";

  const STORAGE_KEY = "estatuto_finalizacion_solicitada_v9";
  const MAX_AGE_MS = 60000;

  function textoElemento(elemento) {
    return String(
      elemento.textContent ||
      elemento.value ||
      ""
    ).trim().toLowerCase();
  }

  function marcarFinalizacion() {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        at: Date.now(),
        from: window.location.pathname
      })
    );

    // Si la finalización falla y permanecemos en la misma pantalla,
    // la marca se elimina para evitar una falsa confirmación posterior.
    window.setTimeout(function () {
      const actual = sessionStorage.getItem(STORAGE_KEY);
      if (!actual) return;

      const sigueEnPropuesta = Boolean(
        document.querySelector("#finalizarConfirmacionModal") ||
        document.querySelector('textarea[name="propuesta"]')
      );

      if (sigueEnPropuesta) {
        sessionStorage.removeItem(STORAGE_KEY);
      }
    }, 12500);
  }

  // No bloquea el click original.
  document.addEventListener(
    "click",
    function (event) {
      const elemento = event.target.closest(
        "button, a, input[type='button'], input[type='submit']"
      );

      if (!elemento) return;

      if (textoElemento(elemento) === "finalizar registro") {
        marcarFinalizacion();
      }
    },
    true
  );

  function esPantallaInicial() {
    return Boolean(
      document.querySelector('input[name="cedula"]') ||
      document.querySelector('form[action*="validar"]')
    );
  }

  function mostrarConfirmacionSiCorresponde() {
    const raw = sessionStorage.getItem(STORAGE_KEY);

    if (!raw || !esPantallaInicial()) {
      return;
    }

    let data;

    try {
      data = JSON.parse(raw);
    } catch (_error) {
      sessionStorage.removeItem(STORAGE_KEY);
      return;
    }

    const edad = Date.now() - Number(data.at || 0);

    if (edad < 0 || edad > MAX_AGE_MS) {
      sessionStorage.removeItem(STORAGE_KEY);
      return;
    }

    sessionStorage.removeItem(STORAGE_KEY);

    const modal = document.getElementById("confirmacionFinalRegistroV9");
    const aceptar = document.getElementById("aceptarConfirmacionFinalV9");

    if (!modal) return;

    modal.hidden = false;

    if (aceptar) {
      aceptar.focus();
      aceptar.addEventListener(
        "click",
        function () {
          modal.hidden = true;
        },
        { once: true }
      );
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener(
      "DOMContentLoaded",
      mostrarConfirmacionSiCorresponde
    );
  } else {
    mostrarConfirmacionSiCorresponde();
  }
})();