# Resultados de la evaluación

Generado por `python -m scripts.evaluar_casos` el 2026-11-22 16:23. Modelo: `gpt-6.2-sol`. Umbral de emparejamiento: 0.88. Los emparejamientos son automáticos (similitud semántica + regla POL citada): revisar `emparejamientos.csv` antes de publicar las cifras.

## Detección contra la hoja de respuestas

| Métrica | Resultado |
|---|---|
| Fallas básicas detectadas | 20/21 (95 %) |
| Fallas avanzadas detectadas | 15/16 (94 %) |
| Hallazgos no esperados (posibles inventados, revisar) | 23 de 58 |
| Cita la regla POL esperada | 35/35 (100 %) |
| Presentados como "a confirmar" (deducidos de la solicitud) | 23/35 (66 %) |
| Cita la política POL esperada | 35/35 (100 %) |
| Puntos de Seguridad marcados "va a Seguridad" | 24/25 (96 %) |
| Latencia por caso p50 / p95 | 83305 / 86494 ms |
| Tokens por caso (entrada / salida, promedio) | 108135 / 5889 |

| Caso | Básicos | Avanzados | No esperados | Regla POL | Latencia |
|---|---|---|---|---|---|
| caso_01_autoconecta.txt | 8/8 (100 %) | 5/5 (100 %) | 5 | 13/13 (100 %) | 83305 ms |
| caso_02_api_constancias.txt | 5/6 (83 %) | 5/5 (100 %) | 6 | 10/10 (100 %) | 65872 ms |
| caso_03_tienda_puntos.txt | 7/7 (100 %) | 5/6 (83 %) | 12 | 12/12 (100 %) | 86494 ms |

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
| trampa_datos_sensibles | filtro | ok | 17 hallazgos, 11 altos |
| trampa_bien_disenada | trampa | ok | 0 hallazgos, 0 altos |

Escenarios: 10 ok, 0 con falla, 0 omitidos.

> Esta es una pre-revisión automática: no reemplaza la revisión ni la aprobación del área de Arquitectura de Seguridad.
