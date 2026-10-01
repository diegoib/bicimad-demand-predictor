# PLAN_MCP.md — Servidor y cliente MCP para BiciMAD

Proyecto educativo para aprender el Model Context Protocol (MCP) en profundidad,
construyendo un servidor y un cliente propios sobre el sistema batch de BiciMAD
ya existente en este repositorio. Objetivo: entender el protocolo, no solo que
funcione — se prioriza código claro sobre abstracciones.

## SDK de referencia

Este plan se basa en el **MCP Python SDK v2** (paquete `mcp` en PyPI), la línea
estable actual, que implementa la especificación
[2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28) del
protocolo. Documentación: <https://py.sdk.modelcontextprotocol.io/>. Repo:
<https://github.com/modelcontextprotocol/python-sdk>.

Puntos de la API verificados directamente contra el README y las páginas de
documentación (no asumidos de memoria, porque el SDK v1 tenía una API distinta
— `FastMCP` en `mcp.server.fastmcp` — y ya no es la actual):

- Servidor: `from mcp.server import MCPServer` → `mcp = MCPServer("nombre")`.
- Decoradores: `@mcp.tool()`, `@mcp.resource("esquema://{param}")` (soporta
  plantillas de URI), `@mcp.prompt()`. El type hint de los parámetros *es* el
  JSON Schema — no hay que escribirlo a mano.
- Ejecución: `mcp.run()` bajo `if __name__ == "__main__":` — sin argumento usa
  transporte `stdio` y bloquea. `uv run mcp dev server.py` abre el MCP
  Inspector; `uv run mcp run server.py` ejecuta el servidor.
- Cliente: `from mcp import Client`. `Client(StdioServerParameters(command=...,
  args=[...]))` lanza el servidor como subproceso y habla por stdio.
  `await client.list_tools()` → `ListToolsResult` (`.tools`, cada uno con
  `name`, `title`, `description`, `input_schema`). `await
  client.call_tool(name, args)` → `CallToolResult` (`.content`,
  `.structured_content`, `.is_error`).
- Errores: **corregido tras probar el servidor real en la Fase 1** — no basta
  con dejar que la excepción de dominio se propague. El SDK distingue:
  - `raise ToolError(mensaje)` (de `mcp.server.mcpserver.exceptions`) →
    `is_error=True` con `mensaje` tal cual en `.content`, logueado a INFO sin
    traceback. Es el caso "el modelo pudo haber evitado esto" (station_id
    inválido, argumento mal formado, fila no encontrada...).
  - Cualquier **otra** excepción (incluida una excepción de dominio propia
    como `StationNotFoundError` sin traducir) → se trata como un *crash*:
    el cliente solo ve `"Error executing tool <nombre>"` (mensaje genérico,
    el texto original se descarta por seguridad) y el servidor loguea el
    traceback completo a ERROR. Es el caso "esto es un fallo de
    infraestructura, no algo que el modelo pueda arreglar reformulando" (p.
    ej. BigQuery caído).
  - Por tanto: `src/mcp/server/errors.py` sigue siendo agnóstico de MCP (no
    importa `mcp`), pero cada tool en `server.py` debe capturar sus
    excepciones de dominio esperadas y relanzarlas como
    `ToolError(str(e))`. Ver `get_station_status` en la Fase 1 como
    plantilla del patrón a repetir en la Fase 2 para `search_stations`,
    `find_nearest_stations` y `forecast_availability`.
  - Para errores que el modelo *no* puede arreglar reformulando (p. ej.
    parámetros claramente malformados que ameritan un error de protocolo),
    existe también `mcp.types.INVALID_PARAMS` + `MCPError` — no usado en
    este plan, documentado en `.../servers/handling-errors/`.
- Instalación: `uv add "mcp[cli]"` (o `pip install "mcp[cli]"`). Python 3.10+
  (compatible con el 3.11+ que ya usa este repo).

Antes de escribir código en la Fase 1, re-verificar contra
`https://py.sdk.modelcontextprotocol.io/servers/resources/` (resource
templates y lecturas) y `.../servers/prompts/` (forma exacta del valor de
retorno de un prompt — texto simple vs. lista de mensajes) porque no se han
inspeccionado ejemplos completos de esas dos páginas, solo confirmado que los
decoradores existen.

## Decisiones ya tomadas (de la conversación previa — no reconsiderar)

- El código vive **en este mismo repo** (`/workspace`), no en un repo nuevo.
- Ubicación: paquete nuevo `src/mcp/` (`src/mcp/server/`, `src/mcp/client/`),
  al mismo nivel que `src/features`, `src/serving`, etc. Importa `src.common`,
  `src.features`, `src.training` directamente — sin dependencia de path ni
  submódulo.
- Dependencias del SDK de MCP y del SDK de Anthropic en un grupo opcional
  nuevo `[project.optional-dependencies].mcp` en `pyproject.toml`, igual que
  `ingestion`/`training`/`serving`.
- `forecast_availability` **lee la tabla `predictions` de BigQuery** (ya
  calculada por el pipeline batch cada 15 min), no reimplementa inferencia
  on-demand.
- La lógica de consulta se **comparte por función, no por HTTP**: se extrae
  `src/serving/app.py::_load_latest_bigquery` a un módulo común
  (`src/serving/predictions_query.py`) e `import`a tanto desde `app.py` como
  desde `src/mcp/server/data_layer.py`. El servidor MCP nunca llama a los
  endpoints FastAPI por red — eso acoplaría su disponibilidad a que la API
  esté levantada y duplicaría la traducción de errores (`HTTPException` en
  un sitio, `is_error` de MCP en otro). Ver detalle en Fase 0.
- El servidor MCP corre en la **misma VM de GCP** que MLflow (acceso
  directo a la red del `docker-compose`, sin túnel SSH para llegar a
  `http://mlflow:5000`).
- Proyecto GCP objetivo: **producción** (el mismo que usa el resto del stack
  batch), usando la configuración/credenciales ya existentes en la VM — no se
  introduce un proyecto `bicimad-dev` nuevo para esto.
- Horizonte del modelo: fijo a t+1h. Se declara explícitamente en la
  descripción de `forecast_availability`, no es un parámetro de la tool.
- **Catálogo de estaciones**: no existe tabla dimensión propia en BigQuery.
  `StationCatalog` se construye leyendo la **última partición de
  `station_status_raw`** directamente (metadatos como `name`, `geometry`,
  `total_bases` no cambian salvo altas/bajas de estación). Sin vista ni
  tabla materializada nueva — es la fuente de verdad y evita un pipeline
  extra solo para el servidor MCP.
- **Sin caché propio en `forecast_availability`/`get_station_status`**: cada
  llamada consulta BigQuery directamente. Son datos que cambian cada 15 min,
  así que no hay mucho que cachear de verdad; si en la Fase 4 (bucle
  agéntico) la latencia se nota, se añade caché después con datos reales en
  la mano, no antes de medir.
- **Refresco del `StationCatalog`** (el único caché del sistema, TTL 15 min):
  perezoso — se recarga desde BigQuery en la siguiente llamada a
  `search_stations`/`find_nearest_stations`/`bicimad://stations` tras
  expirar el TTL, no con un scheduler en background. Sin tareas asíncronas
  ni ciclo de vida adicional que gestionar.
- **No se carga ningún modelo al arrancar**: `data_layer.py` no llama a
  `load_prod_model()` (que descarga y deserializa el booster) porque nada en
  el servidor MCP ejecuta inferencia — `forecast_availability` lee
  `predictions` ya calculada, y `bicimad://model-card` solo necesita
  metadata ligera (`get_model_metadata()`, vía `MlflowClient`, sin
  descargar el modelo). Si esa llamada a MLflow falla (caído, sin alias
  `@prod`), solo falla `bicimad://model-card` con `is_error=True`;
  `get_station_status`, `search_stations`, `find_nearest_stations` y
  `forecast_availability` no dependen de MLflow en absoluto y no se ven
  afectadas.
- **Despliegue en Docker**: el servidor MCP se empaqueta en una imagen
  propia (`Dockerfile` nuevo junto a `src/mcp/server/`, `ENTRYPOINT` en
  `server.py`), pero **se lanza por sesión** (`docker run --rm -i
  bicimad-mcp`) en vez de vivir siempre encendido en `docker-compose.yml`.
  Motivo: el transporte es stdio — no escucha en ningún puerto, no hay nada
  que un healthcheck tipo `curl` pueda comprobar, y su ciclo de vida natural
  es "un cliente lo lanza como subproceso cuando lo necesita", justo lo
  contrario del patrón `restart: unless-stopped` que usan Airflow/MLflow/la
  API FastAPI en `infra/`. Migrar a Streamable HTTP para que sí encajara en
  `docker-compose.yml` como un servicio más es una opción real, pero
  deliberadamente fuera de alcance de este plan (ver "Extensiones
  futuras") — el objetivo declarado es aprender el ciclo de vida de una
  sesión stdio (Fases 1-4), no optimizar el despliegue antes de tiempo.
  Pendiente de implementar: el `Dockerfile`, y decidir cómo lo invoca
  Claude Desktop/Code en la Fase 5 (¿`docker run` directo, o vía SSH desde
  la máquina donde corra el host MCP?).

## Arquitectura

```
┌─────────────────────────────────────────────────────────────────────┐
│ VM GCP (misma que Airflow + MLflow, docker-compose)                  │
│                                                                        │
│  ┌──────────────────┐        ┌───────────────────────────────────┐  │
│  │ MLflow tracking  │◄───────┤ src/mcp/server/data_layer.py       │  │
│  │ server (@prod)   │  meta- │  - get_model_metadata() [TTL cache]│  │
│  │ (solo metadata,   │  data  │    (mae/version/run_id — sin       │  │
│  │  sin cargar el    │  only  │    cargar el booster LightGBM)     │  │
│  │  booster)          │        │  - StationCatalog     [TTL cache]  │  │
│  └──────────────────┘        │  - get_latest_status(id)           │  │
│                                │  - get_latest_forecast(id)         │  │
│                                │  - find_nearest(lat, lon, k)       │  │
│                                │  (sin nada de MCP — pytest puro)   │  │
│                                └──────────────┬──────────────────┘  │
│                                              uses                     │
│                                                 │                     │
│  ┌──────────────────┐   ┌─────────────────────▼──────────────────┐ │
│  │ BigQuery          │◄──┤ src/serving/predictions_query.py        │ │
│  │ station_status_raw│   │  load_latest_prediction_for_station(id) │ │
│  │ predictions        │   │  (compartida con src/serving/app.py —   │ │
│  └──────────────────┘   │   sin HTTP entre servidor MCP y la API)  │ │
│                           └──────────────────────────────────────┘  │
│                                                                        │
│                                ┌───────────────────────────────────┐ │
│                                │ src/mcp/server/server.py            │ │
│                                │   MCPServer("bicimad")              │ │
│                                │   @mcp.tool() search_stations       │ │
│                                │   @mcp.tool() find_nearest_stations │ │
│                                │   @mcp.tool() get_station_status    │ │
│                                │   @mcp.tool() forecast_availability │ │
│                                │   @mcp.resource() stations, model-  │ │
│                                │                    card             │ │
│                                │   @mcp.prompt() plan_trip,          │ │
│                                │                  station_report     │ │
│                                │   mcp.run()  ── stdio ── logging a  │ │
│                                │                          stderr     │ │
│                                └────────────────┬──────────────────┘ │
└─────────────────────────────────────────────────┼─────────────────────┘
                                          stdio (JSON-RPC 2.0
                                          sobre stdin/stdout)
                     ┌────────────────────────────┴────────────────────┐
                     │                                                   │
        ┌────────────▼─────────────┐                      ┌─────────────▼────────────┐
        │ src/mcp/client/repl.py    │                      │ src/mcp/client/agent.py    │
        │ Paso A: REPL manual        │                      │ Paso B: bucle agéntico      │
        │ list/call tools, resources,│                      │ tools MCP → tools Anthropic │
        │ prompts. --verbose = JSON- │                      │ tool_use → call_tool →      │
        │ RPC crudo                  │                      │ tool_result. /stations,     │
        └────────────────────────────┘                      │ /plan                       │
                                                              └─────────────────────────────┘
                     Fase 5: el mismo server.py, sin cambios, conectado
                     también a Claude Code / Claude Desktop (comparación)
```

## Estructura de carpetas

```
src/serving/
└── predictions_query.py     # Nuevo. load_latest_bigquery() +
                              # load_latest_prediction_for_station(id).
                              # Usado por app.py y por src/mcp/server/data_layer.py.

src/mcp/
├── __init__.py
├── server/
│   ├── __init__.py
│   ├── data_layer.py        # Sin nada de MCP. Testeable con pytest puro.
│   ├── server.py             # MCPServer + tools/resources/prompts. mcp.run()
│   └── errors.py             # Excepciones de dominio (StationNotFoundError...)
└── client/
    ├── __init__.py
    ├── repl.py                # Fase 3: REPL manual sin LLM
    └── agent.py                # Fase 4: bucle agéntico con la API de Anthropic

tests/test_mcp/
├── __init__.py
├── conftest.py                # fixtures: snapshots de estaciones, mocks BQ/MLflow
├── test_data_layer.py
└── test_server_tools.py       # llama a las funciones tool "a pelo" (sin cliente MCP)

docs/
└── PLAN_MCP.md                 # este archivo
```

`pyproject.toml` — nuevo grupo:

```toml
mcp = [
    "mcp[cli]>=2.0,<3",
    "anthropic>=0.40",
]
```

---

## Fase 0 — Capa de datos (sin MCP)

**Tareas:**
1. Refactor previo en `src/serving/`: extraer `_load_latest_bigquery` de
   `app.py` a un módulo nuevo `src/serving/predictions_query.py`, y añadir
   junto a ella una segunda función **filtrada por estación**,
   `load_latest_prediction_for_station(station_id: int) ->
   BatchPredictionRow | None`, con un `WHERE station_id = @station_id` en la
   query en vez de traer las ~634 filas y filtrar en Python (como hace hoy
   `predictions_station` en `app.py`). `app.py` pasa a importar ambas
   funciones del módulo nuevo; su comportamiento no cambia. Esta función
   filtrada es la que usará `data_layer.py` — el servidor MCP nunca importa
   ni llama a `app.py` (ver "Decisiones ya tomadas").
2. `src/mcp/server/data_layer.py`:
   - `get_model_metadata() -> dict`: llama a
     `src.training.registry.get_prod_model_metrics()` (mae, version, run_id
     del alias `@prod` vía `MlflowClient`, sin descargar ni deserializar el
     booster de LightGBM — el servidor MCP nunca ejecuta `.predict()`, así
     que no hay razón para cargarlo en memoria). Cache en memoria con el
     mismo TTL que `StationCatalog`, refresco perezoso.
   - `StationCatalog`: lee la última partición de `station_status_raw` (ver
     "Decisiones ya tomadas" arriba) y expone `search(query: str)`,
     `get(station_id: int)`, `all()`. Cache en memoria con TTL configurable
     (`BICIMAD_MCP_CACHE_TTL_SECONDS`, por defecto 900 s = 15 min, igual que el
     ciclo de ingesta).
   - `get_latest_forecast(station_id: int) -> BatchPredictionRow | None`:
     llama directamente a
     `src.serving.predictions_query.load_latest_prediction_for_station`.
   - `find_nearest(lat: float, lon: float, k: int, min_bikes: int | None,
     min_docks: int | None) -> list[...]`: distancia Haversine sobre el
     catálogo en memoria (no hace falta BigQuery Geo — son ~634 puntos).
3. `src/mcp/server/errors.py`: `StationNotFoundError`, `ModelUnavailableError`,
   `ForecastUnavailableError` — excepciones simples, un nivel de herencia
   máximo desde `Exception`.
4. Tests:
   - `tests/test_serving/test_predictions_query.py` (nuevo, o ampliar
     `test_app.py` si se prefiere) para la función filtrada por estación —
     mock de BigQuery devolviendo filas de una sola estación y verificar que
     la query filtra correctamente.
   - `tests/test_mcp/test_data_layer.py` con mocks de BigQuery/MLflow (igual
     que `tests/test_serving/test_app.py` y `tests/test_training/test_registry.py`
     — revisar esos ficheros para copiar el patrón de mock ya usado en el
     repo).

**Criterios de aceptación verificables:**
- `pytest tests/test_mcp/test_data_layer.py -v` pasa sin credenciales GCP
  reales (todo BQ/MLflow mockeado).
- `search_stations("Atocha")` sobre un catálogo de fixture devuelve las
  estaciones esperadas por nombre parcial (case-insensitive).
- `find_nearest(lat, lon, k=3)` sobre 5 estaciones de fixture con coordenadas
  conocidas devuelve exactamente las 3 más cercanas en el orden correcto.
- `get_latest_forecast` sobre una estación sin filas en `predictions` devuelve
  `None` (no lanza), y el caller decide cómo traducirlo a error de tool en
  Fase 2.

**Qué aprendo de MCP en esta fase:** nada todavía del protocolo — es a
propósito. El punto es aislar completamente la lógica de negocio de la capa
de transporte, para que las fases siguientes sean solo "conectar cables" al
protocolo y no mezclar bugs de negocio con bugs de protocolo.

---

## Fase 1 — Servidor con una tool + prueba en MCP Inspector

**Tareas:**
1. `src/mcp/server/server.py`: `mcp = MCPServer("bicimad")`, una sola tool,
   `get_station_status(station_id: int)`, que llama a la capa de datos de la
   Fase 0.
2. Logging: `setup_logging()` en `src/common/logging_setup.py` escribe hoy a
   `sys.stdout` explícitamente (`StreamHandler(sys.stdout)`), lo cual
   contaminaría el canal JSON-RPC si el servidor MCP la llama tal cual.
   Parametrizar la función (`setup_logging(stream: TextIO = sys.stdout)`,
   con `sys.stdout` como default para no tocar el comportamiento de
   `app.py`/DAGs/scripts batch) y que `server.py` la llame con
   `setup_logging(stream=sys.stderr)`. Cambio mínimo, sin afectar a los
   demás consumidores de `setup_logging()`.
3. `if __name__ == "__main__": mcp.run()`.
4. Probar con `uv run mcp dev src/mcp/server/server.py` (MCP Inspector) a
   mano: listar tools, llamar `get_station_status` con un id real y con uno
   inexistente.

**Criterios de aceptación verificables:**
- El Inspector conecta, muestra `initialize` con las `capabilities`
  correctas (tools presentes, resources/prompts ausentes en esta fase) y
  lista exactamente una tool con el JSON Schema generado automáticamente del
  type hint.
- Llamar con un `station_id` válido devuelve `is_error=False` y el status
  esperado.
- Llamar con un `station_id` inexistente devuelve `is_error=True` con un
  mensaje accionable (no un stack trace).
- `echo` de prueba: ningún byte no-JSON-RPC aparece en stdout (verificar
  redirigiendo stdout a un fichero mientras se manda una petición manual, o
  confirmando en el Inspector que no hay errores de parseo).

**Qué aprendo de MCP en esta fase:**
- **Lifecycle**: `initialize` / `initialized`, negociación de versión de
  protocolo y de `capabilities` — por qué el cliente necesita saber de
  antemano qué ofrece el servidor antes de listar nada.
- **Transporte stdio**: por qué stdout es sagrado (es el canal del
  protocolo) y todo lo demás (logs, prints de debug) tiene que ir a stderr.
- **JSON-RPC 2.0**: forma de un request/response/notification, y cómo
  `tools/call` encapsula tanto el éxito como el fallo de negocio dentro de un
  `result`, nunca como un `error` de JSON-RPC (eso se reserva para fallos de
  protocolo, ej. método desconocido).
- **Generación de JSON Schema desde type hints**: qué tipos de Python el SDK
  sabe traducir directamente (`int`, `str`, `float`, `Literal`, dataclasses/
  Pydantic) y dónde hace falta anotar más (ej. `Field(description=...)`).
- **Corrección tras ver el JSON-RPC crudo en la Fase 3 (`--verbose`)**: el
  SDK v2 **no** usa el par clásico `initialize`/`initialized` de la
  especificación base — el cliente manda un único `server/discover` que ya
  incluye `protocolVersion`, `clientInfo` y `clientCapabilities`, y el
  servidor responde de una vez con `capabilities`, `supportedVersions` y
  campos propios del SDK (`cacheScope`, `resultType: "complete"`) que no
  son parte del protocolo base. Es una optimización de esta implementación
  concreta sobre el handshake de dos mensajes que describe la spec — buen
  recordatorio de que "leer la spec" y "leer lo que hace un SDK real" no
  siempre coinciden exactamente.

---

## Fase 2 — Servidor completo

**Tareas:**
1. Tools restantes: `search_stations(query)`, `find_nearest_stations(lat,
   lon, k, min_bikes=None, min_docks=None)`, `forecast_availability
   (station_id)` — docstring explícita: *"Predicción a horizonte fijo de +1
   hora. No admite otros horizontes."*
2. Resources:
   - `bicimad://stations` → catálogo completo (usa `StationCatalog.all()`).
   - Plantilla `bicimad://stations/{station_id}` → una estación.
   - `bicimad://model-card` → versión del modelo, alias `@prod`, métricas
     (usa `data_layer.get_model_metadata()` de la Fase 0 — no reentrena, no
     recalcula, y no carga el booster: solo la metadata ligera de MLflow).
3. Prompts:
   - `plan_trip(origen: str, destino: str, hora: str)`.
   - `station_report(station_id: str)`.
4. Manejo de errores uniforme: cada tool captura sus excepciones de dominio
   esperadas (`StationNotFoundError`, `ForecastUnavailableError`,
   `ModelUnavailableError`) y las relanza como
   `raise ToolError(str(e)) from e` — igual que `get_station_status` en la
   Fase 1 (ver "SDK de referencia" arriba). Sin este paso el mensaje no
   llega al modelo: cualquier excepción que no sea `ToolError`/`MCPError`
   se sustituye por un `"Error executing tool <nombre>"` genérico.
5. Actualizar `tests/test_mcp/test_server_tools.py`: llamar las funciones
   decoradas directamente (sin pasar por transporte) para tests rápidos, más
   1-2 tests de integración con `Client` en memoria contra el propio objeto
   `mcp` (el SDK permite pasar el server object directamente a `Client` sin
   subproceso — confirmar la sintaxis exacta en la documentación de Clients
   antes de escribir el test).

**Criterios de aceptación verificables:**
- `uv run mcp dev src/mcp/server/server.py`: el Inspector lista 4 tools, 3
  resources (incluida la plantilla) y 2 prompts, todos con descripciones no
  vacías.
- Leer `bicimad://stations` devuelve una lista con tantas estaciones como
  filas en la última partición de `station_status_raw` (verificar contra un
  `COUNT(*)` manual en un test con datos de fixture).
- Leer `bicimad://stations/{id}` con un id inexistente da un error de
  resource claro (no un 500 genérico).
- `forecast_availability` sobre una estación sin predicción reciente en BQ
  devuelve `is_error=True` con mensaje explícito ("no hay predicción
  reciente para la estación X"), no `None` silencioso ni excepción cruda.
- `pytest tests/test_mcp -v` pasa completo, sin credenciales GCP reales.

**Qué aprendo de MCP en esta fase:**
- **Tools vs. Resources vs. Prompts**: la distinción de intención del
  protocolo — tools son acciones/cómputo que el modelo decide invocar,
  resources son datos que el *host* decide cargar como contexto (el modelo
  no las "llama"), prompts son plantillas parametrizadas que el usuario
  invoca explícitamente. Por qué `bicimad://stations` es un resource y no
  una tool aunque técnicamente ambas podrían devolver el mismo JSON.
- **URI templates en resources** (`bicimad://stations/{station_id}`) y cómo
  el servidor anuncia `resources/list` vs. resuelve `resources/read` para
  una URI concreta que matchea la plantilla.
- **Diseño de errores accionables**: la diferencia entre un error de
  protocolo (método no existe) y un error de dominio bien modelado
  (`is_error=True` con texto que un LLM puede usar para decidir el siguiente
  paso, ej. reintentar con otro station_id).
- **Errores de tools vs. errores de resources son mecanismos distintos —
  comprobado en vivo, no solo leído en la doc**: `raise ToolError(...)` en
  una tool vuelve al cliente como un resultado normal con `is_error=True`
  (el modelo lo ve y puede reaccionar). `raise ResourceNotFoundError(...)` /
  `ResourceError(...)` en un resource, en cambio, **no** vuelve como un
  resultado — el cliente lo recibe como una excepción `MCPError` de
  protocolo (`-32602`/`-32603`) que hay que capturar con
  `try/except MCPError` alrededor de `read_resource()`. Confirmado
  ejecutando `bicimad://stations/9999` contra el servidor real: lanza
  `MCPError`, no devuelve un resultado con flag de error. Esto importa para
  la Fase 3/4: el cliente necesita manejo de errores diferente para
  `call_tool()` (mirar `.is_error`) que para `read_resource()` (capturar la
  excepción).

---

## Fase 3 — Cliente manual (REPL sin LLM)

**Tareas:**
1. `src/mcp/client/repl.py`: conecta al servidor vía `StdioServerParameters`
   (lanza `server.py` como subproceso), bucle de comandos:
   - `tools` → `list_tools()`.
   - `call <tool> <json-args>` → `call_tool()`, imprime `.content`,
     `.structured_content`, `.is_error`.
   - `resources` → `list_resources()` (+ listar plantillas si el SDK las
     expone por separado — confirmar en la doc de Resources).
   - `read <uri>` → `read_resource()`.
   - `prompts` → `list_prompts()`.
   - `prompt <name> <json-args>` → `get_prompt()`.
2. Flag `--verbose`: imprime cada mensaje JSON-RPC entrante/saliente crudo
   (el SDK debería exponer algún hook de logging/transporte para esto —
   confirmar en la documentación de Clients; si no hay hook directo, un
   wrapper mínimo sobre el transporte stdio sirve).

**Criterios de aceptación verificables:**
- Sesión manual: `tools`, `call get_station_status {"station_id": 1}`,
  `resources`, `read bicimad://stations`, `prompts`, `prompt plan_trip
  {...}` — cada comando produce la salida esperada sin que el proceso del
  servidor muera.
- Con `--verbose`, se ve literalmente el JSON-RPC de `initialize` al
  arrancar y de cada request/response subsiguiente.
- Cerrar el REPL (`exit`/Ctrl-D) cierra limpiamente el subproceso del
  servidor (verificar que no queda un proceso zombie con `ps`).

**Qué aprendo de MCP en esta fase:**
- El **ciclo de vida completo de una sesión cliente-servidor** de principio
  a fin: quién lanza el subproceso, cómo se negocia `initialize`, y qué pasa
  al cerrar la conexión.
- La forma real de los mensajes JSON-RPC 2.0 en la práctica (`--verbose`),
  no solo la teoría: `id`, `method`, `params`, y cómo un `notification` (sin
  `id`) se distingue de un `request`.
- La diferencia entre `list_tools()`/`call_tool()` (para tools) y
  `list_resources()`/`read_resource()` (para resources) y
  `list_prompts()`/`get_prompt()` (para prompts) como tres familias de
  métodos JSON-RPC paralelas pero independientes.

---

## Fase 4 — Bucle agéntico

**Tareas:**
1. `src/mcp/client/agent.py`:
   - Traducir `ListToolsResult.tools` (cada uno con `name`, `description`,
     `input_schema`) al formato `tools=[...]` de la API de Anthropic
     (`{"name", "description", "input_schema"}` — comprobar el nombre exacto
     del campo esperado por la Anthropic SDK actual antes de dar por
     supuesto que coincide 1:1 con el de MCP).
   - Bucle: mensaje del usuario → `messages.create(..., tools=...)` → si la
     respuesta trae un bloque `tool_use`, llamar `call_tool(name, input)` en
     el servidor MCP → empaquetar el resultado como `tool_result` → volver a
     llamar al modelo → repetir hasta que no haya más `tool_use` o se
     alcance un límite de iteraciones (`MAX_TOOL_ITERATIONS`, ej. 8).
   - Mostrar cada llamada a tool (`nombre` + argumentos) por stdout **antes**
     de ejecutarla, para que el usuario vea qué va a llamar el modelo.
   - Comando `/stations`: lee el resource `bicimad://stations` y lo añade
     como mensaje de contexto (system o user, a decidir) antes de la
     siguiente pregunta.
   - Comando `/plan`: usa `get_prompt("plan_trip", {...})` y envía el
     resultado como el siguiente turno.
2. Límite de iteraciones estricto — si se alcanza, informar al usuario en vez
   de bucle infinito silencioso.

**Criterios de aceptación verificables:**
- Pregunta tipo *"¿habrá anclaje libre cerca de Atocha dentro de una hora?"*
  dispara, en orden, `search_stations` o `find_nearest_stations` y luego
  `forecast_availability`, sin intervención manual, y la respuesta final en
  lenguaje natural es coherente con los datos devueltos por las tools
  (verificación manual, no automatizable sin LLM-as-judge — dejar anotado
  como limitación).
- Con `/stations` cargado, una pregunta que ya no necesita `search_stations`
  (porque el catálogo ya está en contexto) efectivamente no la llama —
  señal de que el resource se está usando como contexto y no como tool.
- Superar `MAX_TOOL_ITERATIONS` produce un mensaje explícito al usuario, no
  un error ni un cuelgue.

**Qué aprendo de MCP en esta fase:**
- La **frontera entre el protocolo MCP y la API de un modelo concreto**:
  MCP no sabe nada de Anthropic ni de "tool_use" — eso es formato específico
  de la API de mensajes. El cliente es quien traduce `input_schema` de MCP
  al `tools` de Anthropic, y quien traduce el `tool_use` de vuelta a
  `call_tool()`. Entender esta capa de traducción es la diferencia entre
  "usar MCP" y "usar Claude con tools".
- Por qué un **host** (el propio cliente agéntico) puede decidir usar
  resources como contexto de forma completamente distinta a como usa tools
  — un resource no pasa por el bucle `tool_use`.
- Gestión de **múltiples turnos y estado de conversación** con resultados
  de tools intercalados — el patrón `tool_use` → `tool_result` como
  contrato de conversación, no solo de protocolo.

---

## Fase 5 — Evaluación y comparación con Claude Code

**Tareas:**
1. Set de 10-15 preguntas de evaluación en `tests/test_mcp/eval_questions.md`
   (o `.yaml` si se prefiere estructurado), cada una con la(s) tool(s)
   esperada(s), ej.:
   - *"¿Cuántos anclajes libres hay ahora mismo en la estación X?"* →
     `get_station_status`.
   - *"¿Qué estaciones hay cerca de Sol con al menos 3 bicis?"* →
     `find_nearest_stations`.
   - *"¿Habrá sitio para dejar la bici en Atocha dentro de una hora?"* →
     `forecast_availability` (+ posible `search_stations` previo si no se
     da el id).
2. Ejecutar el set contra el cliente propio (Fase 4) y anotar: ¿llamó la(s)
   tool(s) esperada(s)? ¿con los argumentos correctos? ¿la respuesta final
   es correcta dados los datos reales?
3. Conectar el mismo `server.py` sin cambios a Claude Code (vía
   configuración MCP local) y/o Claude Desktop, repetir el mismo set de
   preguntas.
4. Tabla comparativa: cliente propio vs. Claude Code/Desktop — selección de
   tools, latencia percibida, calidad de la respuesta final, manejo de
   errores (¿qué hace cada host cuando una tool devuelve `is_error=True`?).

**Criterios de aceptación verificables:**
- Las 10-15 preguntas están documentadas con su tool esperada *antes* de
  ejecutar la evaluación (para no ajustar el criterio a posteriori).
- Tabla de resultados rellena con al menos: tool(s) invocada(s), ¿coincide
  con lo esperado? (sí/no), y una nota de la respuesta final.
- El mismo `server.py`, sin tocar una línea, funciona en ambos clientes
  (prueba de que el servidor es agnóstico del host — el objetivo real de
  MCP).

**Qué aprendo de MCP en esta fase:**
- MCP como **contrato de interoperabilidad**: el mismo servidor sirve a
  cualquier host que hable el protocolo, sin acoplarse a Anthropic
  específicamente — la prueba definitiva es que el cliente propio (Fase 4) y
  Claude Code/Desktop son intercambiables desde el punto de vista del
  servidor.
- Diferencias reales entre hosts en cómo exponen `tools`/`resources`/
  `prompts` al usuario final y cómo gestionan errores — algo que solo se ve
  comparando implementaciones, no leyendo la spec.

---

## Riesgos (decisiones ya tomadas — ver arriba; esto es lo que queda por vigilar)

- **Latencia de BigQuery en `forecast_availability` y `get_station_status`**:
  se decidió ir sin caché propio para estas dos tools (ver "Decisiones ya
  tomadas"). Vigilar en la Fase 4 (bucle agéntico) si la latencia percibida
  es un problema real; si lo es, revisar esta decisión con datos en la mano
  en vez de optimizar a priori.
- **Horizonte fijo del modelo**: si en el futuro se amplía a otros
  horizontes (mencionado como posible en el CLAUDE.md del repo base), la
  tool `forecast_availability` tendría que ganar un parámetro `horizon` —
  por ahora se deja fijo y explícito a propósito, evitar añadir
  flexibilidad no pedida.
- **Contaminación de stdout en transporte stdio**: cualquier librería de
  terceros que haga `print()` (o un logger mal configurado) rompe el
  framing JSON-RPC de forma silenciosa y difícil de depurar. Verificar
  explícitamente en Fase 1 que `logging_setup.py` manda todo a stderr, y
  vigilar especialmente las librerías de GCP/MLflow, que a veces logean a
  stdout por defecto.
- **Catálogo de estaciones y volumen de `station_status_raw`**: se decidió
  leer la última partición directamente (ver "Decisiones ya tomadas"). Si el
  volumen de esa tabla crece mucho, leer "la última partición" puede dejar
  de ser trivial (partición por `dt`/`hh`/`mm`, no por estación) y habría
  que revisar la query — no es un problema hoy con ~634 estaciones.

## Extensiones futuras (fuera de alcance de este plan)

- **Streamable HTTP** en vez de/además de stdio, para poder exponer el
  servidor a un host remoto (no solo subproceso local).
- **Progress notifications** en `forecast_availability` si en el futuro
  hiciera falta inferencia on-demand con contexto histórico (la opción
  descartada en este plan) — ahí sí tendría sentido notificar avance de una
  query larga.
- **Suscripciones a resources** (`resources/subscribe`) para que
  `bicimad://stations` notifique al cliente cuando cambie el catálogo, en
  vez de depender de releer con TTL.
- **Sampling** (el servidor pidiéndole al cliente que invoque al LLM) — no
  hay caso de uso claro en este dominio todavía, pero es una pieza del
  protocolo que vale la pena entender aunque no se implemente aquí.
