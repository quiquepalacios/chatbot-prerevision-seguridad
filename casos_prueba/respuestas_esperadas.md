# Respuestas esperadas - Banco Andesur (para evaluar el bot, NO se cargan en el RAG)

## Por qué los casos están escritos a alto nivel

Una solicitud de revisión real no describe servicio por servicio ni el detalle de programación: el arquitecto o el líder técnico cuenta qué hace la solución, sus componentes, qué datos maneja, con quién se integra y cómo entran los usuarios. Muchas veces no conoce el código, y aunque lo conozca, no lo escribe.

Por eso los tres casos llegan como llegan en la realidad (un resumen de documento de diseño, un mensaje de chat y un formulario) y la hoja no espera "fallas de código" sino **riesgos y requisitos que un revisor puede deducir de lo que la solicitud sí dice**. Por ejemplo, "ver la ubicación del auto" obliga a validar que el auto sea del cliente (POL-01.1), y "abonos vía el core" obliga a la idempotencia (POL-06.2). Lo que la solicitud no cuenta, el bot lo pregunta. Los errores de programación (inyección SQL, contraseñas en texto plano, un endpoint de depuración) no se pueden ver en una solicitud: los cubren el análisis de código (SAST/DAST) y el pentest.

Los casos se inspiran en apps vulnerables de OWASP (crAPI, VAmPI y Juice Shop), llevadas a productos de un banco y a su forma de solicitud.

## Cómo leer la hoja

Referencias: OWASP API Security Top 10 2023 (API1-API10), OWASP Top 10:2025 (A01-A10), PCI DSS, Reglamento SBS 504-2021 y Ley 29733 de protección de datos personales.
Nivel: **B** = básico (se deduce de un componente o dato que la solicitud menciona) / **A** = avanzado (hay que razonar sobre el flujo, el dinero, el negocio o los terceros).
Seguridad = el bot debe marcarlo para revisión del área de Arquitectura de Seguridad (sección "Revisión obligatoria" de la política o severidad alta).
POL = regla de LSA-2026 que el bot debería citar (la primera es la principal).

## Caso 01 - AutoConecta / Crédito Vehicular (basado en OWASP crAPI)

| # | Riesgo o requisito esperado | Nivel | Referencia | POL | Seguridad |
|---|---|---|---|---|---|
| 1 | Ubicación e historial del vehículo: cada consulta debe validar que el auto sea del cliente (un identificador difícil de adivinar no basta) | B | API1, A01 | POL-01.1, POL-01.2 | Sí |
| 2 | Webhook del proveedor GPS: validar firma, marca de tiempo y origen; si no, se pueden falsear posiciones | B | API10, A08 | POL-08.4 | |
| 3 | Microservicios que se llaman por la red interna sin autenticarse entre sí | B | A01 | POL-05.1 | |
| 4 | Talleres con usuario y clave compartidos por todo su personal: credenciales propias, nominales y revocables | B | A07, API10 | POL-13.3 | Sí |
| 5 | Plazo de retención de las posiciones GPS sin definir (máximo 90 días salvo requerimiento legal) | B | Ley 29733 | POL-15.2 | |
| 6 | Datos de clientes en AWS: evaluación de nube y de ubicación de los datos | B | SBS 504 (nube y proveedores) | POL-13.4 | Sí |
| 7 | Versión piloto v1 todavía activa: inventario, fecha de apagado y mismos controles | B | API9 | POL-09.5 | |
| 8 | Logs solo en CloudWatch: sin envío al SIEM ni alertas de abuso | B | A09 | POL-14.1, POL-14.5 | |
| 9 | **Talleres reciben nombre, DNI, celular y placa**: evaluación del proveedor, contrato de datos y enviar solo lo mínimo (un código de cita) | A | API10, SBS 504 (proveedores) | POL-13.1, POL-13.2 | Sí |
| 10 | **Devoluciones abonadas vía el core**: monto y cuenta destino deben salir de registros del servidor, no de datos editables | A | API6, A06 | POL-06.1 | Sí |
| 11 | **Reintentos al core sin idempotencia**: un timeout puede abonar dos veces | A | A06, API6 | POL-06.2 | Sí |
| 12 | **Cuenta de servicio con acceso al core**: solo los permisos que su función necesita | A | A01 (mínimo privilegio) | POL-05.2 | Sí |
| 13 | **Cobranzas quiere usar el historial de ubicaciones**: otra finalidad que el cliente no autorizó | A | Ley 29733 | POL-15.1 | Sí |

## Caso 02 - API de constancias y estados de cuenta (basado en VAmPI)

| # | Riesgo o requisito esperado | Nivel | Referencia | POL | Seguridad |
|---|---|---|---|---|---|
| 1 | Registro y login propios fuera del login central del banco, aunque sea temporal | B | A07, SBS 504 (autenticación) | POL-02.1 | Sí |
| 2 | Documentos financieros: cada descarga debe validar que el documento sea del cliente | B | API1, A01 | POL-01.1 | Sí |
| 3 | Copia de producción sin enmascarar en QA | B | A02, Ley 29733 | POL-12.1 | Sí |
| 4 | Logs con peticiones y respuestas completas (DNI y datos financieros) | B | A09, Ley 29733 | POL-14.2 | |
| 5 | Documentos financieros en un bucket: cifrado en reposo con claves del banco | B | A04 | POL-10.2 | |
| 6 | Publicación hacia la app: API Gateway con límite de solicitudes (no se menciona: preguntar) | B | API4, API8 | POL-09.6 | |
| 7 | **La identidad del cliente llega como un DNI enviado por Banca Móvil**: la API confía en otro sistema; debe viajar en un token del IAM | A | API1, A01 | POL-05.4 | Sí |
| 8 | **Credencial de servicio de Banca Móvil**: vencimiento y alcance limitado; hoy da acceso a cualquier cliente | A | API2, API5 | POL-05.3 | Sí |
| 9 | **Link del PDF para compartir con terceros**: enlaces firmados que venzan en 24 horas y compartir con registro | A | A01, API3 | POL-15.3, POL-15.4 | Sí |
| 10 | **Fábrica de software con acceso a QA con datos reales**: contrato, cuentas nominales y registro de accesos | A | SBS 504 (proveedores) | POL-12.3, POL-13.1 | Sí |
| 11 | **Herramienta de logs en la nube con datos de clientes**: evaluación del servicio y de la ubicación de los datos | A | SBS 504 (nube) | POL-13.4 | Sí |

## Caso 03 - Tienda Puntos Andesur (basado en OWASP Juice Shop)

| # | Riesgo o requisito esperado | Nivel | Referencia | POL | Seguridad |
|---|---|---|---|---|---|
| 1 | Usuario y contraseña propios de la tienda, fuera del login central del banco | B | A07, SBS 504 (autenticación) | POL-02.1 | Sí |
| 2 | Recuperación de contraseña con pregunta de seguridad | B | A07 | POL-02.4 | Sí |
| 3 | Opciones de administración según el rol en la misma web: el servidor debe validar el rol, no solo ocultar opciones | B | API5, A01 | POL-01.4 | Sí |
| 4 | Catálogo en XML subido por las empresas: entidades externas deshabilitadas (XXE) | B | A05 | POL-07.3 | |
| 5 | Imagen descargada desde una URL que indica la empresa (SSRF) | B | API7, A01 | POL-08.1, POL-08.2 | |
| 6 | Tienda actual en paralelo hasta marzo: inventario, apagado y mismos controles | B | API9 | POL-09.5 | |
| 7 | Usuario de cada empresa con convenio: credenciales propias y revocables, tercero evaluado | B | API10 | POL-13.3, POL-13.1 | |
| 8 | **Canje en dos pasos (consultar saldo y luego descontar)**: dos canjes simultáneos usan el mismo saldo | A | API6, A06 | POL-06.3 | Sí |
| 9 | **Límite diario de canje validado en la app**: debe validarse en el servidor | A | API6, A06 | POL-06.4 | Sí |
| 10 | **Webhook de la pasarela confirma pagos**: sin firma, cualquiera puede marcar una orden como pagada | A | A08, API10 | POL-08.4 | Sí |
| 11 | **Se guarda la tarjeta del cliente**: usar tokenización; si no, todo entra en alcance PCI | A | PCI DSS, A04 | POL-11.1, POL-11.4 | Sí |
| 12 | **El SaaS de monitoreo registra peticiones**: puede recibir datos de tarjeta y DNI | A | PCI DSS, A09 | POL-11.3, POL-14.2 | Sí |
| 13 | **Nube en us-east-1**: datos de clientes fuera del país sin evaluación | A | SBS 504 (nube), Ley 29733 | POL-13.4 | Sí |

## Resumen para la PPT

| Caso | Básicos | Avanzados | Total | Para Seguridad |
|---|---|---|---|---|
| 01 AutoConecta | 8 | 5 | 13 | 8 |
| 02 Constancias | 6 | 5 | 11 | 8 |
| 03 Tienda Puntos | 7 | 6 | 13 | 9 |
| **Total** | **21** | **16** | **37** | **25** |

## Casos trampa

Ver `trampa_01` a `trampa_05` y `escenarios.json`: arquitectura bien diseñada (sin riesgos de severidad alta), proyecto confidencial y "enfoque estratégico" (se bloquean antes del modelo), instrucción escondida para que el bot apruebe (inyección) y datos sensibles pegados en la solicitud (se enmascaran).
