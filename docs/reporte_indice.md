# Reporte del índice de lineamientos

Generado por `python -m scripts.indexar` el 2026-11-21 18:44. Embeddings: `ibm-granite/granite-embedding-97m-multilingual-r2` (dimensión 384). Fragmentos de hasta 150 palabras con 25 de solape; las reglas POL y los requisitos ASVS se indexan enteros (uno por fragmento).

**Total: 36 documentos y 1841 fragmentos** (palabras por fragmento: mínimo 12, mediana 106, máximo 150).

| Fuente | Documentos | Fragmentos | Palabras (mín / mediana / máx) |
|---|---:|---:|---|
| Lineamientos LSA-2026 (ficticio) | 1 | 73 | 18 / 30 / 52 |
| NIST SP 800-204, 207, 218, 63B | 4 | 866 | 12 / 132 / 150 |
| OWASP API Security Top 10 2023 | 10 | 91 | 12 / 94 / 150 |
| OWASP ASVS 5.0 | 1 | 345 | 15 / 42 / 109 |
| OWASP Cheat Sheets | 10 | 357 | 13 / 110 / 150 |
| OWASP Top 10:2025 (es) | 10 | 109 | 35 / 127 / 150 |

## Limpieza

| Qué se quitó o corrigió | Cantidad |
|---|---:|
| Encabezados y pies de página repetidos | 1057 |
| Líneas con etiquetas HTML | 392 |
| Filas separadoras de tablas | 19 |
| Fragmentos repetidos entre documentos | 1 |
| Palabras cortadas con guion, unidas | 99 |
| Imágenes de Markdown | 15 |
| Líneas de índice (tabla de contenido) | 235 |
| Números de página | 32 |
| Páginas de PDF procesadas | 274 |
| Párrafos duplicados dentro de un documento | 6 |
| Secciones sin valor (cifras, CWE, referencias) | 35 |

## Preguntas de control

Acierto: **16/16** (la cita esperada aparece entre los 3 fragmentos que llegan al prompt). Mejor similitud en tema, mínima: 0.8302; fuera de tema, máxima: 0.8012. Umbral configurado: 0.79; sugerido: 0.816.

| Pregunta | ¿Acierta? | Citas recuperadas | Similitud |
|---|---|---|---:|
| ¿Cómo evito que un cliente vea la cuenta de otro cambiando el ID en la URL? | sí | OWASP Cheat Sheet: Authorization, API2:2023, POL-02.3 | 0.8582 |
| El código OTP tiene 4 dígitos y no hay límite de intentos | sí | POL-02.2, NIST SP 800-63B-4, NIST SP 800-63B-4 | 0.8889 |
| El backend descarga una imagen desde una URL que envía el usuario | sí | API7:2023, ASVS V3.6.1, POL-08.2 | 0.8363 |
| Las contraseñas se guardan en texto plano en la base de datos | sí | POL-10.1, OWASP Cheat Sheet: File Upload, NIST SP 800-63B-4 | 0.8445 |
| Guardamos el token JWT en el localStorage del navegador | sí | OWASP Cheat Sheet: JSON Web Token, POL-04.4, OWASP Cheat Sheet: JSON Web Token | 0.8659 |
| El endpoint de registro acepta un campo admin igual a true en el JSON | sí | API3:2023, OWASP Cheat Sheet: JSON Web Token, POL-08.4 | 0.842 |
| Usamos una copia de los datos reales de clientes en el ambiente de QA | sí | POL-12.1, OWASP Cheat Sheet: Logging, ASVS V14.2.2 | 0.834 |
| Los logs no se envían al SIEM y no hay alertas de seguridad | sí | A09:2025, ASVS V16.2.3, POL-14.2 | 0.8805 |
| Si el core bancario no responde, reintentamos el abono sin control de duplicados | sí | A10:2025, NIST SP 800-63B-4, POL-06.2 | 0.8302 |
| La aplicación guarda el número completo de la tarjeta y el CVV | sí | POL-11.2, NIST SP 800-63B-4, ASVS V14.2.6 | 0.8547 |
| How should microservices authenticate each other inside the cluster? | sí | NIST SP 800-204, NIST SP 800-204, POL-05.1 | 0.9059 |
| Un proveedor externo recibe el DNI y el celular de los clientes | sí | NIST SP 800-63B-4, NIST SP 800-63B-4, POL-13.2 | 0.8514 |
| BOLA: validar en el servidor que el recurso pertenece al usuario autenticado | sí | POL-01.1, POL-02.5, POL-02.3 | 0.8798 |
| broken object level authorization IDOR ownership check | sí | API1:2023, OWASP Cheat Sheet: Authorization, POL-01.2 | 0.9165 |
| SSRF el backend llama a una URL enviada por el cliente | sí | OWASP Cheat Sheet: Server Side Request Forgery Prevention, ASVS V3.4.5, POL-08.1 | 0.9193 |
| asignación masiva: el cliente puede enviar el campo rol o admin al registrarse | sí | OWASP Cheat Sheet: Mass Assignment, OWASP Cheat Sheet: Mass Assignment, POL-01.5 | 0.8688 |
| *(fuera de tema)* ¿Cuál es la receta del ceviche? | — | POL-08.5 | 0.7239 |
| *(fuera de tema)* ¿Quién ganó el partido de fútbol de ayer? | — | POL-11.2 | 0.7073 |
| *(fuera de tema)* ¿Cuál es el saldo de mi cuenta de ahorros? | — | POL-11.1 | 0.8012 |
| *(fuera de tema)* Recomiéndame una película para el fin de semana | — | POL-15.3 | 0.7117 |
| *(fuera de tema)* ¿Cuánto cuesta un pasaje a Cusco? | — | POL-11.2 | 0.7257 |
