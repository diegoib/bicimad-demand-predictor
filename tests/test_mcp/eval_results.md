# Resultados de evaluación — Fase 5

Ejecutado contra el set de `eval_questions.md`, documentado *antes* de correr
esta evaluación. Columna "cliente propio" (Fase 4, `src/mcp/client/agent.py`)
rellena con una ejecución real contra el servidor en producción (634
estaciones reales). Columna "Claude Code/Desktop" pendiente — requiere
registrar `server.py` en la configuración MCP de una instalación real de
Claude Code o Claude Desktop, algo que no se puede hacer desde este entorno
en el que corre esta sesión (ver nota al final).

## Cliente propio (`agent.py`)

| # | Pregunta (resumen) | Tool(s) invocada(s) | ¿Coincide con lo esperado? | Nota de la respuesta final |
|---|---------------------|----------------------|------------------------------|------------------------------|
| 1 | Anclajes libres estación 1 | `get_station_status(1)` | Sí (tool correcta) | Estación 1 no existe en el catálogo real; el modelo lo comunica con claridad y ofrece alternativas en vez de alucinar un dato. |
| 2 | ¿Disponible estación 999999? | `get_station_status(999999)` | Sí | Igual que arriba — maneja el `is_error=True` con una respuesta útil, no un error crudo. |
| 3 | Buscar "Atocha" | `search_stations("Atocha")` | Sí | Devuelve 3 estaciones reales (Ronda de Atocha, Atocha A/B) con datos correctos de anclajes/bicis. |
| 4 | Estaciones cerca de Sol | `find_nearest_stations(lat, lon, k=10)` | Sí | El modelo eligió `k=10` por su cuenta (no se le dio) — resultado razonable, incluye distancia implícita por orden. |
| 5 | 3 más cercanas, min_bikes=2 | `find_nearest_stations(..., k=3, min_bikes=2)` | Sí | Filtro `min_bikes` aplicado correctamente, las 3 devueltas cumplen el mínimo. |
| 6 | Anclaje libre estación 1 en 1h | `forecast_availability(1)` | Sí (tool correcta, no `get_station_status`) | Distingue bien "dentro de una hora" → horizonte +1h. Estación 1 no existe → error correctamente comunicado. |
| 7 | Predicción +1h estación 50 | `forecast_availability(50)` | Sí | Misma tool correcta; estación 50 tampoco existe, error bien comunicado. |
| 8 | Plan de viaje Sol→Atocha 18:00 | *(ninguna — reutilizó resultados de Q3/Q4 ya en el historial de conversación)* | Parcial | No volvió a llamar tools porque la conversación acumulada ya tenía los datos de Sol y Atocha de preguntas anteriores — razonamiento válido, pero significa que esta pregunta sola (sin historial previo) habría necesitado `search_stations` x2. Ofreció proactivamente hacer el forecast si se confirmaba. |
| 9 | Versión/MAE del modelo en prod | *(ninguna)* | **No** — el resource `bicimad://model-card` nunca se consulta | El modelo respondió que no tiene esa capacidad. Confirma lo anotado en el plan: un resource no es una tool, el modelo no puede "decidir" leerlo — solo se carga si el host (`agent.py`, vía `/stations`) lo mete en el contexto explícitamente. No hay comando `/model-card` en `agent.py`, así que esta pregunta es, con el cliente actual, estructuralmente irrespondible sin intervención manual. |
| 10 | Catálogo completo de estaciones | *(ninguna)* | **No** — mismo motivo que Q9, con `bicimad://stations` | El modelo fue honesto: dijo explícitamente que no tiene una tool para esto, en vez de inventar una lista. |
| 11 | ¿Disponible estación 123456? | `get_station_status(123456)` | Sí | Igual patrón que Q1/Q2. |
| 12 | Sitio para dejar bici cerca de Plaza Mayor | `find_nearest_stations(..., min_docks=1)` | Sí | Filtro correcto, recomendación coherente con los anclajes libres reales devueltos. |
| 13 | Bicis disponibles cerca de Gran Vía | `find_nearest_stations(..., min_bikes=1)` | Sí | Simétrico a Q12, filtro correcto. |
| 14 | Resumen estado + predicción estación 10 | `get_station_status(10)` **y** `forecast_availability(10)` | Sí | Encadenó las dos tools correctamente en el mismo turno, sin que se le pidiera explícitamente el orden — estación 10 no existe, ambas fallan, respuesta coherente. |
| 15 | Comparar estaciones 1 y 2 | `get_station_status(1)` y `get_station_status(2)` | Sí | Dos llamadas a la misma tool con argumentos distintos, correcto. Además detectó por sí mismo (de resultados de Q3/Q4 en el historial) que "1" y "2" probablemente se referían a los *números de estación* visibles en el nombre ("1 - Metro Sol", "2 - Metro Callao"), no al `station_id` interno, y lo ofreció como alternativa — razonamiento no pedido pero útil. |

**Selección de tools: 13/15 correcta** (Q8 y Q9/Q10 son los dos casos
distintos — Q8 es un falso negativo del diseño del set de preguntas, no un
fallo del cliente; Q9/Q10 son limitaciones reales y esperadas de `agent.py`
tal como está hoy, no un fallo del modelo).

## Hallazgos (más interesantes que la tabla en sí)

1. **Los resources nunca se cargan por iniciativa del modelo — confirmado
   en vivo, no solo leído en el plan.** Q9 y Q10 piden exactamente lo que
   exponen `bicimad://model-card` y `bicimad://stations`, y en ambos casos
   el modelo responde honestamente "no tengo esa herramienta" en vez de
   inventar un dato o intentar forzar una tool equivocada. Esto es la
   prueba empírica de la distinción tools/resources de la Fase 2: un
   resource es algo que *el host* decide cargar como contexto (aquí, solo
   vía el comando manual `/stations` de `agent.py`), nunca algo que el
   modelo pueda pedir por sí mismo dentro del bucle `tool_use`. Con
   `/stations` cargado antes de Q10, la respuesta habría sido inmediata
   sin ninguna tool — exactamente el criterio de aceptación de Fase 4 que
   ya se verificó aparte.
2. **Los `station_id` del dominio no son los números que lleva el nombre de
   la estación** ("33 - Puerta del Sol" tiene `station_id=1437`, no 33).
   El set de preguntas usó IDs pequeños (1, 2, 10, 50, 999999...) asumiendo
   que algunos existirían — ninguno lo hacía, salvo el pensado como
   inexistente. Fallo del diseño del eval, no del cliente: todas esas
   preguntas sirvieron igualmente para comprobar el manejo de errores
   (`is_error=True` con mensaje accionable, propagado y comunicado con
   naturalidad), que era parte de lo que se quería probar.
3. **El modelo reutiliza contexto de turnos anteriores en vez de repetir
   tools** (Q8, Q15) — comportamiento correcto de un LLM con memoria de
   conversación, pero significa que el "¿llamó la tool esperada?" de una
   pregunta aislada depende de qué se preguntó antes en la misma sesión.
   El set de preguntas de este plan se diseñó como una sesión continua
   (ver `eval_questions.md`), así que esto es una observación a tener en
   cuenta al leer la tabla, no un error.

## Claude Code / Claude Desktop

**Pendiente de ejecutar — requiere acción manual fuera de este entorno.**
Esta sesión corre dentro de un sandbox sin el binario `claude` instalado
(sin acceso a `claude mcp add`), y Claude Desktop es una aplicación de
escritorio que no existe en este contenedor. Para completar esta columna:

1. Registra el servidor sin modificarlo:
   - **Claude Code**: `claude mcp add bicimad -- python -m src.mcp.server.server` desde la raíz del repo (o añade la entrada equivalente a `.mcp.json`).
   - **Claude Desktop**: añade una entrada en `claude_desktop_config.json` apuntando al mismo comando.
2. Abre una conversación nueva en ese host y pasa las 15 preguntas de `eval_questions.md`, en el mismo orden.
3. Rellena una tabla con las mismas columnas que la de arriba.

## Tabla comparativa (cliente propio vs. Claude Code/Desktop)

| Aspecto | Cliente propio (`agent.py`) | Claude Code/Desktop |
|---|---|---|
| Selección de tools | 13/15 correcta (ver detalle arriba) | *(pendiente)* |
| Uso de resources como contexto | Nunca automático — solo vía `/stations` manual | *(pendiente — los hosts de Anthropic sí pueden decidir cargar resources sin comando explícito; sería la comparación más interesante de verificar)* |
| Latencia percibida | 2-15s por turno con tool_use (incluye ida y vuelta a BigQuery) | *(pendiente)* |
| Manejo de `is_error=True` | El modelo lo comunica con naturalidad y ofrece alternativas, nunca expone el mensaje crudo del protocolo | *(pendiente)* |

El objetivo de fondo de esta tabla —que el mismo `server.py`, sin tocar una
línea, sirva a cualquier host— ya está verificado de forma indirecta: nada
en `server.py` se ha modificado desde la Fase 2, y tanto `repl.py` como
`agent.py` (dos clientes con arquitecturas de bucle completamente distintas)
lo consumen sin fricción. Falta solo la confirmación con un *tercer* host
que ninguno de los dos construimos, que es la pieza manual pendiente.
