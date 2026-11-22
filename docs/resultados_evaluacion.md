# Resultados de la evaluación

Generado por `python -m scripts.evaluar_casos` el 2026-11-21 19:16. Modelo: `sin modelo (modo demo)`. Umbral de emparejamiento: 0.88. Los emparejamientos son automáticos (similitud semántica + regla POL citada): revisar `emparejamientos.csv` antes de publicar las cifras.

## Detección contra la hoja de respuestas

No evaluado: no hay modelo configurado; define LLM_BASE_URL, LLM_API_KEY y LLM_MODEL en el .env.

## Escenarios

| Escenario | Grupo | Resultado | Detalle |
|---|---|---|---|
| error_vacio | error | ok | error de entrada: La descripción está vacía. Cuéntame cómo está armada tu solución: componentes, endpoints, autenticación, datos e integraciones. |
| fuera_aprobacion | fuera de alcance | ok | aprobacion |
| fuera_codigo | fuera de alcance | ok | codigo |
| fuera_cliente | fuera de alcance | ok | cliente |
| faq_proceso | faq | ok | FAQ (FAQ-PEDIR-REVISION) |
| trampa_confidencial | filtro | ok | proyecto_confidencial |
| trampa_estrategico | filtro | ok | tema_estrategico |
| trampa_inyeccion | filtro | ok | inyeccion |
| trampa_datos_sensibles | filtro | ok | modo demo · lineamientos |
| trampa_bien_disenada | trampa | omitido | necesita el modelo (configura el .env) |

Escenarios: 9 ok, 0 con falla, 1 omitidos.

> Esta es una pre-revisión automática: no reemplaza la revisión ni la aprobación del área de Arquitectura de Seguridad.
