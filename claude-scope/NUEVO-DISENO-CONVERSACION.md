# Nuevo diseño de la sección "Conversación"

Rediseño de la pestaña **💬 Conversación** del detalle de una sesión/trace, con
estilo inspector de invocaciones (chat con burbujas + panel "Details of
invocation"). Está en un fichero aparte para no tocar el original.

- **Fichero nuevo:** `claude-scope/index-nuevo-diseno.html` (copia de `index.html`
  + el rediseño). El `index.html` original **no se modifica**.
- **Idea de referencia:** `conversacion-detalle.png` (orientativa, no se calcan los
  campos).

## Cómo se comporta

- La **Conversación es siempre el nuevo chat** (`#chat-layout`). El render clásico
  (`#messages-container`) queda **retirado**: nunca se muestra. También se retiran sus
  accesorios (`#sticky-prompt`, las métricas de scroll y los checkboxes de filtro por
  rol, ocultos por CSS para no inflar la barra sticky).
- El `view-switcher` es de **selección múltiple**: el estado es un `Set` `traceViews`
  (claves `timeline/turns/observations/flow/map/transcript`). Cada botón hace toggle de
  su vista; **📦 Todo** activa/desactiva todas; nunca queda vacío (cae a `transcript`).
  Por defecto solo `transcript`. Visibilidad y estado `active` se calculan en
  `applyTraceViewMode`.
- **Búsqueda** (`doTranscriptSearch`) y **salto a mensaje** (`scrollToMessageIndex`,
  desde Mapa/Flujo) operan sobre el chat (`#chat-col`, burbujas con `data-msg-idx`);
  el salto además activa `transcript` y selecciona la observación.

## Estructura del layout (dos columnas)

- **Izquierda `#chat-col`**, stream de chat:
  - Tarjeta cabecera con título de la sesión + métricas (Cost / Input / Output / modelo).
  - Burbujas: **You** (derecha, tarjeta clara con acento), **Assistant** (izquierda,
    avatar ✦) y **System** (plan/skill/interrupt/continuation/notification/summary, tag).
  - Dentro del Assistant: cada `tool_use` se muestra **desplegado** (`<details open>`)
    pero con su tabla interna **plegada** (`renderToolStep(..., collapsed=true)` rinde
    Input/Output cerrados). El botón "Plegar todo" (`.ts-toggle-all`) y el global
    "Input / Output data" Show/Hide (`setAllToolStepSections`, ahora sobre `#chat-col`)
    pliegan/despliegan esas secciones. `thinking` va plegado aparte.
  - Las burbujas del Assistant con tools ocupan todo el ancho (`:has(.chat-tools)`).
  - Textos largos se recortan con "Read more / Read less" (`.msg-body.clampable`).
  - `<pre>`/`code` con fondo oscuro y texto claro (antes coincidían); burbuja **You**
    legible (texto oscuro sobre acento claro).
- **Derecha `#chat-details`**, panel "Details of …" (sticky). Tiene **4 niveles de
  granularidad** según `chatSel.kind`:
  - **session**: resumen de la sesión (gantt = un span por turno, clicables).
  - **turn** (`invocation`): un turno; gantt = fila raíz + un span por tool call
    (duración = ts del tool_result menos ts del tool_use). Cada span es clicable y baja
    a nivel tool.
  - **message** (`observation`): un mensaje del stream. Datos individuales tipo
    `.term-meta`: rol, modelo, tokens (in/out/cacheR/cacheW), hora, turno, desglose de
    bloques (texto/thinking/tools) y longitud de texto. Métricas Cost/Input/Output/CacheR.
  - **tool**: una ejecución concreta. tool_use_id, turno, hora, duración, input
    (keys · chars), output (chars · lines), is_error, modelo y tokens de la request
    padre. Gantt = barra del turno + barra de la tool.
  - **Cómo se selecciona:** clic en una burbuja (→ message), en **cualquier parte** de
    una card de tool (→ tool, sin romper sus controles internos), o en una barra del
    gantt (turno/tool). Además **scroll-spy**: el panel sigue al mensaje más centrado en
    el viewport al hacer scroll (`chatScrollSpyHandler`), salvo si hay un tool/turno
    "fijado"; se desactiva con el botón 🧲/📌 (`chatSpyEnabled`).
  - **Deseleccionar:** volver a pulsar la cabecera de una tool ya seleccionada (o una
    burbuja ya seleccionada) devuelve el panel a su **estado inicial**, la vista de
    sesión (`chatResetDetails` → `kind:'session'`, "Details of: session"). Los clics en
    el cuerpo del tool no deseleccionan (siguen seleccionando y dejan funcionar sus
    controles internos).
  - **Navegación:** barra `.cd-nav` con **Anterior / Siguiente** observación
    (`chatNavObservation`, recorre las burbujas en orden). El botón ← sube un nivel
    (`chatBackSel`: tool/message → turn → session). Toggle **UI / JSON** por nivel.
  - **Scroll del panel:** es `position: sticky` anclado **bajo la barra de filtros**
    (`top`/`max-height` con `var(--filters-height)`, que fija `updateStickyOffset` en
    `#chat-layout`); por eso ya no lo tapa la barra ni el retirado `#sticky-prompt`.
  - **Cabecera (`.cd-title`):** muestra `Details of: Turno #N - <resumen del prompt del
    turno>` (lo que antes salía en el sticky) más una etiqueta pequeña del tipo
    (`observation`/`tool`/`invocation`). El turno se obtiene de `headerTurn`
    (`chatTurnOf`/`turnIdx`); en sesión cae a `Details of: session`.

## Dónde está el código (buscar por nombre, no por línea)

Todo el código nuevo está marcado con el comentario `NUEVO DISEÑO`.

- **CSS:** bloque al final del `<style>`, cabecera `NUEVO DISEÑO · Conversación
  estilo chat`. Clases: `.chat-layout`, `.chat-head-card`, `.chat-metric`,
  `.msg-row/.msg-bubble/.msg-avatar` (`.you/.bot/.sys`), `.chat-tool`,
  `.chat-details`, `.cd-*`, `.gantt-*`, `.inv-*`.
- **HTML:** `#chat-layout` (con `#chat-col` y `#chat-details`) justo debajo de
  `#messages-container`.
- **Estado global:** `chatActiveSessionId`, `chatSel` (`{kind:'session'|'turn'|
  'message'|'tool', …}`), `chatBackSel`, `chatDetailsViewMode`, `chatSpyEnabled`, y
  `traceViews` (`Set`) + `TRACE_VIEW_ALL_KEYS` (multi-selección de vistas), junto a
  `let fullTextSessionIds`. Se fija `chatActiveSessionId` y se resetea `chatSel` en
  `loadTranscript`.
- **Vistas / visibilidad:** `applyTraceViewMode` lee `traceViews` (Set) y togglea cada
  panel; `#messages-container` siempre oculto; `#chat-layout` visible si
  `traceViews.has('transcript')`. El handler de `#trace-view-switcher` hace toggle
  multi-selección.
- **Helpers de nivel superior** (justo antes de `function renderTranscript`):
  `chatTsMs`, `chatDur`, `chatPalette`, `chatSessionTitle`, `chatTotals`,
  `chatResultTsMap` (ts/len/lines del resultado), `chatTurnSpans` (spans con
  msgIdx/toolUseId/tamaños), `chatGanttHtml` (barras de turno y de tool clicables),
  `chatMetaRow`, `chatBubble` (lleva `data-msg-idx`), `chatClampWrap`, `chatTurnOf`,
  `chatMessageObservation`, `chatToolObservation`, `chatHighlightSelection`,
  `chatSelect`, `chatScrollSpyHandler`, `chatNavObservation` (prev/next),
  `chatBindEvents` (delegación de clicks + listener de scroll, bound una sola vez),
  `renderInvocationDetails` (los 4 niveles + barra `.cd-nav`).
- **`renderToolStep(toolUse, resultEntry, allowResult, collapsed)`**: el 4º parámetro
  `collapsed` (lo pasa el chat como `true`) rinde Input/Output con `<details>` cerrados.
- **Construcción del chat:** `renderChatLayout()` está **anidada dentro** de
  `renderTranscript` (necesita los closures `renderMd`, `renderToolStep`,
  `toolResultByUseId`). Se llama al final de `renderTranscript` junto con
  `renderInvocationDetails()`, `chatBindEvents()` y `chatHighlightSelection()`.
  Solo lee estado; **no** muta `renderedToolUseIds` (eso es del render clásico).

## Datos reutilizados

`buildTurns()` (turnos con `startIdx/endIdx`, `tokens`, `model`, `tools`, `started`,
`ended`, `prompt`), `transcriptData`, `sessions`, `turnCost`, `shortModel`,
`classifyMessage`, `parseContentBlocks`, `textFromBlocks`, `formatCost/Number/Time/Ms`.

## Verificación realizada

Servido con `local_server.py` + chdb (ClickHouse embebido) y conducido por CDP (Chrome
headless): abre sesión → pestaña Conversación → 125 burbujas, 7 spans de sesión;
clic en burbuja → invocación con 9 spans + metadatos; toggle JSON OK. **Sin errores
de consola.** El `index.html` original quedó intacto.
