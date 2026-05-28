# Claude Code Panel

Dashboard local de observabilidad para sesiones de Claude Code. Lee tus
JSONL en `~/.claude/projects/` con `clickhouse-local` y los explora en un
navegador. **Nada sale de tu equipo.**

Funcionalidades:

- **Trazas y observaciones** por sesión, turno y "trace" (turnos agrupados).
- **Coste real facturable** por modelo: input, output, cache R/W 5m + 1h y
  server tools, ajustado por service tier. Coste deduplicado por `requestId`
  (coincide al céntimo con Langfuse).
- **Detalle por sesión** con tarjetas (coste total, tokens, cache), mapa SVG
  del flujo opcional, agrupación visual `comando ↔ resultado`, transcripción
  en tema claro.
- **Filtros**: rango temporal con presets (1h / 6h / 1d / 7d / 30d / custom),
  proyecto, modelo, herramienta.
- Pantalla de bienvenida si todavía no hay sesiones de Claude Code.

## Cómo ejecutarlo

Sólo necesitas **Python 3.8 o superior**. El propio panel descarga
`clickhouse-local` automáticamente la primera vez (~150 MB) y abre el
navegador solo.

Descomprime la carpeta que te han pasado y abre una terminal **dentro de
ella** (la que contiene `installer/` y `claude-scope/`). Después:

### Linux

```bash
python3 installer/launcher.py
```

Python ya viene en casi todas las distros. Si falta, en Debian/Ubuntu:
`sudo apt install python3`.

### macOS

```bash
python3 installer/launcher.py
```

Si no tienes Python, instálalo desde
[python.org](https://www.python.org/downloads/) o con Homebrew
(`brew install python`).

### Windows

ClickHouse no tiene versión nativa para Windows, así que el panel lo ejecuta
a través de **WSL** (Windows Subsystem for Linux). Sólo se activa una vez:

```powershell
# PowerShell como Administrador — sólo la primera vez
wsl --install
# Reinicia el equipo cuando lo pida.
```

Instala Python desde
[python.org](https://www.python.org/downloads/windows/) (marca *"Add python.exe
to PATH"* en el instalador). Después, en PowerShell o CMD dentro de la carpeta:

```powershell
python installer\launcher.py
```

---

- La **primera ejecución** necesita internet para bajar ClickHouse; después
  funciona sin conexión.
- Para **parar** el panel: pulsa `Ctrl + C` en la terminal (o usa el botón de
  apagado dentro del propio panel).

## Qué contiene la carpeta

Estos son los únicos ficheros necesarios para ejecutar el panel:

```
installer/
└─ launcher.py           # Arranca el panel (descarga ClickHouse + abre navegador)

claude-scope/            # El panel propiamente dicho
├─ index.html            # SPA single-page
├─ local_server.py       # Bridge HTTP → clickhouse-local
├─ pricing.json          # Tarifas Anthropic
└─ assets/vendor/        # marked + ansi_up vendorizados (offline)
```

## Alternativa avanzada (sin launcher)

Si ya tienes `clickhouse-local` en el `PATH` y prefieres arrancar el servidor a
mano (no abre el navegador solo):

```bash
python3 claude-scope/local_server.py
# luego abre http://127.0.0.1:8765
```

## Licencia

Apache 2.0 — ver `LICENSE`.
