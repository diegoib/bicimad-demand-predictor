# Preguntas de evaluación — Fase 5

Documentadas *antes* de ejecutar la evaluación (ver PLAN_MCP.md, Fase 5,
criterio de aceptación 1) para no ajustar a posteriori qué tool(s) se
esperaban. Mezcla deliberada de: tools individuales, resources (para
comprobar si el host los usa como contexto en vez de llamarlos como tool),
rutas de error, y preguntas compuestas que requieren encadenar varias
llamadas.

| # | Pregunta | Tool(s)/resource(s) esperada(s) | Nota |
|---|----------|----------------------------------|------|
| 1 | ¿Cuántos anclajes libres hay ahora mismo en la estación 1? | `get_station_status(1)` | Caso simple, un solo id válido. |
| 2 | ¿Está disponible la estación con id 999999? | `get_station_status(999999)` | Id inexistente — debe dar `is_error=True` con mensaje accionable, no un crash. |
| 3 | Busca estaciones que contengan "Atocha" en el nombre | `search_stations("Atocha")` | Búsqueda parcial case-insensitive. |
| 4 | ¿Qué estaciones hay cerca de la Puerta del Sol (40.4169, -3.7035)? | `find_nearest_stations(lat=40.4169, lon=-3.7035, k=...)` | El modelo tiene que elegir un `k` razonable sin que se le dé. |
| 5 | Dame las 3 estaciones más cercanas a (40.4168, -3.7038) con al menos 2 bicis disponibles | `find_nearest_stations(..., k=3, min_bikes=2)` | Comprueba que pasa el filtro `min_bikes`, no solo `k`. |
| 6 | ¿Habrá anclaje libre en la estación 1 dentro de una hora? | `forecast_availability(1)` | "dentro de una hora" debe mapear al horizonte fijo +1h, no a `get_station_status`. |
| 7 | ¿Cuál es la predicción de disponibilidad a +1h de la estación 50? | `forecast_availability(50)` | Pide explícitamente el horizonte del modelo. |
| 8 | Quiero ir en bici desde Sol hasta Atocha a las 18:00, ¿qué estaciones me recomiendas? | `search_stations`/`find_nearest_stations` (origen y destino) + `forecast_availability` si la hora cae dentro de +1h | Compuesta — varias tools encadenadas, igual que el prompt `plan_trip`, pero sin invocarlo explícitamente. |
| 9 | ¿Qué versión del modelo está en producción y qué MAE tiene? | resource `bicimad://model-card` | No es una tool — comprueba si el host carga el resource como contexto o inventa una respuesta sin consultarlo. |
| 10 | Dame el catálogo completo de estaciones de BiciMAD | resource `bicimad://stations` | Mismo chequeo que la 9, con el resource grande (634 estaciones). |
| 11 | ¿Está la estación 123456 disponible? | `get_station_status(123456)` | Segundo caso de id inexistente, con un número distinto al de la pregunta 2 para evitar caché de la pregunta anterior en la conversación. |
| 12 | ¿Hay alguna estación cerca de Plaza Mayor (40.4154, -3.7074) con sitio para dejar la bici ahora mismo? | `find_nearest_stations(..., min_docks=1)` | "ahora mismo" debe evitar que se use `forecast_availability` por error. |
| 13 | ¿Qué estaciones cerca de Gran Vía (40.4200, -3.7025) tienen bicis disponibles ahora? | `find_nearest_stations(..., min_bikes=1)` | Variante de la 12 con el filtro contrario (`min_bikes` en vez de `min_docks`). |
| 14 | Resume el estado actual y la predicción a 1 hora de la estación 10 | `get_station_status(10)` + `forecast_availability(10)` | Compuesta sobre una sola estación — equivalente a lo que haría el prompt `station_report`. |
| 15 | Compara la disponibilidad actual de las estaciones 1 y 2 | `get_station_status(1)` + `get_station_status(2)` | Dos llamadas a la misma tool con argumentos distintos, en el mismo turno o en turnos consecutivos. |

## Cómo se ejecuta

1. **Cliente propio (`src/mcp/client/agent.py`, Fase 4)**: lanzar
   `python -m src.mcp.client.agent` y pasar las 15 preguntas en orden, en la
   misma sesión (conversación acumulada). Anotar por cada una: tool(s)
   invocada(s) realmente, si coincide con lo esperado, y si la respuesta
   final es coherente con los datos reales devueltos.
2. **Claude Code / Claude Desktop**: registrar `src/mcp/server/server.py` sin
   modificar como servidor MCP en la configuración del host (`claude mcp add`
   para Claude Code, o `claude_desktop_config.json` para Claude Desktop) y
   repetir las mismas 15 preguntas, en una conversación nueva.
3. Resultados en `tests/test_mcp/eval_results.md` (tabla comparativa).
