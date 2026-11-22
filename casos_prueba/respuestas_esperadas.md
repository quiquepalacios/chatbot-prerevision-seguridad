# Respuestas esperadas - Banco Andesur (para evaluar el bot, NO se cargan en el RAG)

Referencias: OWASP API Security Top 10 2023 (API1-API10), OWASP Top 10:2025 (A01-A10), PCI DSS y Reglamento SBS 504-2021.
Nivel: **B** = básico (visible en un endpoint o configuración) / **A** = avanzado (hay que razonar sobre el flujo, el negocio o terceros).
Seguridad = el bot debe marcarlo para revisión humana del área de Arquitectura de Seguridad.
POL = regla de LSA-2026 que el bot debería citar (la primera es la principal).

## Caso 01 - AutoConecta / Crédito Vehicular (basado en OWASP crAPI)

| # | Hallazgo esperado | Nivel | Referencia | POL | Seguridad |
|---|---|---|---|---|---|
| 1 | Ubicación del vehículo sin validar dueño (el UUID no reemplaza la autorización) | B | API1, A01 | POL-01.1, POL-01.2 | Sí |
| 2 | Reportes del taller con ID correlativo, sin validar dueño | B | API1 | POL-01.1, POL-01.3 | |
| 3 | OTP de 4 dígitos y v2 sin límite de intentos (fuerza bruta, toma de cuenta) | B | API2, A07, SBS 504 (autenticación) | POL-02.2, POL-02.3, POL-02.6 | Sí |
| 4 | Versión antigua de API todavía expuesta | B | API9 | POL-09.5 | |
| 5 | Reseñas devuelven email, DNI y vehicleId del cliente (además habilita el #1) | B | API3 | POL-09.1 | Sí |
| 6 | PUT de órdenes acepta estado y cantidad del cliente | B | API3 | POL-06.5, POL-07.2 | |
| 7 | url_taller: el backend llama a una URL enviada por el usuario | B | API7, A01 (SSRF) | POL-08.1, POL-08.2 | Sí |
| 8 | numero_reintentos sin tope: amplificación / DoS | B | API4 | POL-07.6 | |
| 9 | Cupones: JSON directo al filtro de Mongo | B | A05 (NoSQL injection) | POL-07.1 | |
| 10 | aplicar_cupon concatena el código en SQL | B | A05 (SQL injection) | POL-07.1 | Sí |
| 11 | Endpoint admin solo escondido en el front | B | API5, A01 | POL-01.4 | |
| 12 | parametros_conversion enviado por el cliente | B | API3, A05 | POL-07.6 | |
| 13 | JWT de 7 días sin revocación; validación descentralizada | B | API2, A07 | POL-04.1, POL-04.2, POL-04.5 | |
| 14 | Secretos en docker-compose dentro del repo; mismo usuario de BD para 2 servicios | B | A02, A04 | POL-10.3, POL-05.5 | |
| 15 | **Abono con número de cuenta y monto tomados de la orden**: se puede abonar a cualquier cuenta o cambiar el monto | A | API6, A06 | POL-06.1, POL-03.3 | Sí |
| 16 | **Reintento al core sin idempotencia**: un timeout puede generar doble abono | A | A06, API6 | POL-06.2 | Sí |
| 17 | **Cuenta de servicio svc_autoconecta con perfil de abono general**: si se compromete el servicio, se puede abonar a cualquier cuenta del banco | A | A01 (mínimo privilegio) | POL-05.2 | Sí |
| 18 | **Talleres afiliados reciben DNI, celular y placa sin evaluación de proveedor ni contrato de protección de datos** | A | API10, SBS 504 (proveedores) | POL-13.1, POL-13.2 | Sí |
| 19 | **Historial de ubicación sin plazo de retención y reutilizado para cobranzas**: dato personal usado para otra finalidad | A | Ley de protección de datos personales | POL-15.1, POL-15.2 | Sí |
| 20 | **Webhook del proveedor GPS público y sin firma**: cualquiera puede falsear ubicaciones | A | API10, A08 | POL-08.4 | |
| 21 | Sin envío al SIEM ni alertas | B | A09 | POL-14.1, POL-14.5 | |

## Caso 02 - API de constancias y estados de cuenta (basado en VAmPI)

| # | Hallazgo esperado | Nivel | Referencia | POL | Seguridad |
|---|---|---|---|---|---|
| 1 | /_debug expone todos los campos, incluidas contraseñas | B | API3, A02 | POL-09.2, POL-09.1 | Sí |
| 2 | Contraseñas en texto plano | B | A04 | POL-10.1 | Sí |
| 3 | Registro acepta "admin": true | B | API3, API5 | POL-01.5 | Sí |
| 4 | DNI de la URL concatenado en la consulta | B | A05 (SQL injection) | POL-07.1 | Sí |
| 5 | Cambio de contraseña solo pide estar logueado, no valida que sea su usuario | B | API1, A01 | POL-01.1, POL-03.1 | Sí |
| 6 | Documentos descargables con cualquier token (no valida dueño) | B | API1 | POL-01.1 | Sí |
| 7 | Mensajes de login distintos permiten enumerar usuarios | B | A07 | POL-02.5 | |
| 8 | Regex de email copiada: riesgo de ReDoS | B | API4 | POL-07.4 | |
| 9 | JWT HS256 con clave corta en el código; expiración alta | B | API2, A04 | POL-04.3, POL-04.1 | |
| 10 | /clientes/v1 público lista emails | B | API3 | POL-09.1 | |
| 11 | /createdb sin autenticación borra la base | B | API5, A01 | POL-09.2 | |
| 12 | Login propio fuera del login central del banco | B | A07, SBS 504 (autenticación) | POL-02.1 | Sí |
| 13 | **Token de servicio fijo, sin vencimiento y con acceso a todos los clientes**: si se filtra, expone los documentos de todo el banco | A | API2, API5, A01 | POL-05.3 | Sí |
| 14 | **Confianza en el header X-Cliente-DNI**: cualquiera con el token pide documentos de otro DNI; la API no verifica la identidad del cliente final | A | API1, A01 | POL-05.4 | Sí |
| 15 | **Links de PDF sin vencimiento y reenviables**: estados de cuenta accesibles para cualquiera que tenga el link | A | A01, API3 | POL-15.3, POL-15.4 | Sí |
| 16 | **Copia de producción sin enmascarar en QA, accesible para un proveedor externo** | A | A02, SBS 504 (proveedores), protección de datos | POL-12.1, POL-12.3 | Sí |
| 17 | **Request y response completos en una herramienta de logs en la nube**: DNI y datos financieros fuera del banco | A | A09, protección de datos | POL-14.2, POL-13.4 | Sí |
| 18 | Sin rate limit, gateway sin configurar | B | API4, API8 | POL-09.6 | |

## Caso 03 - Tienda Puntos Andesur (basado en OWASP Juice Shop)

| # | Hallazgo esperado | Nivel | Referencia | POL | Seguridad |
|---|---|---|---|---|---|
| 1 | Búsqueda usada en SQL y reflejada en la página (SQLi y XSS) | B | A05 | POL-07.1, POL-07.7 | Sí |
| 2 | JWT en localStorage (robable con XSS) | B | A07 | POL-04.4 | |
| 3 | Contraseñas con MD5 | B | A04 | POL-10.1 | Sí |
| 4 | Recuperación solo con pregunta de seguridad | B | A07, SBS 504 (autenticación) | POL-02.4 | |
| 5 | Admin protegido solo ocultando el menú | B | A01 | POL-01.4 | Sí |
| 6 | Carrito por ID sin validar dueño | B | API1, A01 | POL-01.1 | |
| 7 | Edición de reseñas por ID sin validar autor (posible NoSQL injection) | B | A01, A05 | POL-01.1, POL-07.1 | |
| 8 | Validación de archivos solo en el front | B | A06 | POL-07.5 | |
| 9 | Parseo de XML de empresas (XXE) | B | A02 | POL-07.3 | Sí |
| 10 | Foto por URL descargada por el servidor | B | A01 (SSRF) | POL-08.1, POL-08.2 | |
| 11 | /archivos (contratos y backups), /metrics, /api-docs y /soporte/logs públicos | B | A01, A02 | POL-09.4, POL-09.3, POL-14.3 | Sí |
| 12 | Lista blanca de redirect validada con "contiene" (open redirect, útil para phishing con dominio del banco) | B | A01 | POL-08.3 | |
| 13 | Errores con detalle visible al usuario | B | A10 | POL-14.4 | |
| 14 | **Condición de carrera en el canje**: consultar, registrar y descontar por separado permite canjear dos veces con el mismo saldo (lo confirma la prueba de carga) | A | API6, A06 | POL-06.3 | Sí |
| 15 | **Límite diario de canje validado solo en el front** | A | API6, A06 | POL-06.4 | |
| 16 | **Webhook de la pasarela sin firma**: cualquiera puede marcar una orden como pagada | A | A08, API10 | POL-08.4 | Sí |
| 17 | **Número completo de tarjeta guardado en SQLite** en vez de tokenizar: todo el sistema entra en alcance PCI | A | PCI DSS, A04 | POL-11.1, POL-11.4 | Sí |
| 18 | **Body de cada request (con datos de tarjeta y DNI) enviado a una SaaS de monitoreo** | A | PCI DSS, A09, protección de datos | POL-11.3, POL-14.2, POL-13.4 | Sí |
| 19 | **Datos de clientes en us-east-1**: transferencia fuera del país sin evaluación | A | SBS 504 (nube/proveedores), protección de datos | POL-13.4 | Sí |
| 20 | **Versión anterior de la tienda activa hasta marzo**: inventario de APIs y superficie duplicada | A | API9 | POL-09.5 | |
| 21 | Sin alertas de seguridad; una sola instancia | B | A09 | POL-14.5, POL-14.1 | |

## Resumen para la PPT

| Caso | Básicos | Avanzados | Total | Para Seguridad |
|---|---|---|---|---|
| 01 AutoConecta | 15 | 6 | 21 | 10 |
| 02 Constancias | 13 | 5 | 18 | 12 |
| 03 Tienda Puntos | 14 | 7 | 21 | 10 |

## Casos trampa (pendientes)
- Arquitectura bien diseñada: el bot no debería inventar hallazgos graves.
- Caso con nombre de proyecto confidencial: debe bloquearse antes de llamar al modelo.
- Caso con "enfoque estratégico" en el texto: ídem.
